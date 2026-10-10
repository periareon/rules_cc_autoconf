"""gnu_autoconf_conformance_test

Runs GNU `aclocal`, `autoconf` and `configure` on a `configure.ac` with the
same compiler and flags the `autoconf` rule uses, then compares the rendered
`config.h` / `subst.h` against the Bazel-generated files.  Both sides of
every comparison, the diff, config.log, tool output and the environment are
written to the test's undeclared outputs directory.
"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "AUTOCONF_EXEC_GROUP",
    "collect_transitive_results",
    "create_config_dict",
    "get_cc_toolchain_info",
    "get_environment_variables",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load(":toolchain.bzl", "SH_TOOLCHAIN_TYPE", "TOOLCHAIN_TYPE", "autotools_runtime", "rlocationpath", "shell_path")

def _tool_rlocationpath(path, workspace_name):
    """Convert an execroot-relative tool path into an rlocationpath.

    Absolute paths (host toolchains such as `/usr/bin/gcc`) are returned as is.
    """
    if path.startswith("/") or (len(path) > 2 and path[1] == ":"):
        return path
    if path.startswith("bazel-out/"):
        # bazel-out/<config>/bin/<rest>
        parts = path.split("/", 3)
        path = parts[3] if len(parts) == 4 else path
    if path.startswith("external/"):
        return path[len("external/"):]
    return "{}/{}".format(workspace_name, path)

def _optional_file(ctx, attr_name):
    files = getattr(ctx.files, attr_name)
    if not files:
        return None
    if len(files) != 1:
        fail("{}: expected a single file, got {}".format(attr_name, files))
    return files[0]

def _gnu_autoconf_conformance_test_impl(ctx):
    ws = ctx.workspace_name
    tester = ctx.executable._tester
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, tester.extension).strip("."))
    ctx.actions.symlink(target_file = tester, output = executable, is_executable = True)

    # --- Compiler parity: the exact helper the `autoconf` rule uses. ---
    toolchain_info = get_cc_toolchain_info(ctx)
    config = create_config_dict(toolchain_info)
    cc_env = get_environment_variables(ctx, toolchain_info)
    cc_json = {
        "c_compiler": _tool_rlocationpath(config["c_compiler"], ws),
        "c_flags": config["c_flags"],
        "c_link_flags": config["c_link_flags"],
        "compiler_type": config["compiler_type"],
        "cpp_compiler": _tool_rlocationpath(config["cpp_compiler"], ws),
        "cpp_flags": config["cpp_flags"],
        "cpp_link_flags": config["cpp_link_flags"],
        "env": cc_env,
        "linker": _tool_rlocationpath(config["linker"], ws),
    }
    cc_env_file = ctx.actions.declare_file("{}.cc_env.json".format(ctx.label.name))
    ctx.actions.write(cc_env_file, json.encode_indent(cc_json, indent = "  ") + "\n")

    # --- Inputs for the GNU run. ---
    m4_list = ctx.actions.declare_file("{}.m4_files.txt".format(ctx.label.name))
    ctx.actions.write(m4_list, "\n".join([rlocationpath(f, ws) for f in ctx.files.m4_files]) + "\n")

    runtime = autotools_runtime(ctx)

    direct_files = [
        executable,
        cc_env_file,
        m4_list,
        ctx.file.configure_ac,
        ctx.file.config_h_in,
        # Not read by the tester; present so a compiler or SDK upgrade on the
        # host invalidates cached results.
        ctx.file._host_fingerprint,
    ] + ctx.files.m4_files + ctx.files.aux_files + ctx.files.build_aux

    env = dict(runtime.env)
    env.update({
        "FORCE_UNSAFE_CONFIGURE": "1",
        "TEST_AUX_FILES": " ".join([rlocationpath(f, ws) for f in ctx.files.aux_files]),
        "TEST_BUILD_AUX_FILES": " ".join([rlocationpath(f, ws) for f in ctx.files.build_aux]),
        "TEST_CC_ENV": rlocationpath(cc_env_file, ws),
        "TEST_CONFIGURE_AC": rlocationpath(ctx.file.configure_ac, ws),
        "TEST_CONFIGURE_ARGS": json.encode(ctx.attr.configure_args),
        "TEST_CONFIG_H_IN": rlocationpath(ctx.file.config_h_in, ws),
        "TEST_KNOWN_DIVERGENCES": json.encode(ctx.attr.known_divergences),
        "TEST_M4_LIST": rlocationpath(m4_list, ws),
    })

    if ctx.attr.verify_variables:
        env["VERIFY_VARIABLES"] = "1"

    subst_h_in = _optional_file(ctx, "subst_h_in")
    if subst_h_in:
        env["TEST_SUBST_H_IN"] = rlocationpath(subst_h_in, ws)
        direct_files.append(subst_h_in)

    for attr_name, var in [
        ("bazel_config_h", "TEST_BAZEL_CONFIG_H"),
        ("bazel_subst_h", "TEST_BAZEL_SUBST_H"),
    ]:
        file = _optional_file(ctx, attr_name)
        if file:
            env[var] = rlocationpath(file, ws)
            direct_files.append(file)

    # Per-check results from the Bazel side, for the cache variable report.
    if ctx.attr.autoconf_target:
        info = ctx.attr.autoconf_target[CcAutoconfInfo]
        results = collect_transitive_results([info] + info.deps.to_list())
        manifest = {name: rlocationpath(f, ws) for name, f in results["cache"].items()}
        manifest_file = ctx.actions.declare_file("{}.cache_manifest.json".format(ctx.label.name))
        ctx.actions.write(manifest_file, json.encode_indent(manifest, indent = "  ") + "\n")
        env["TEST_CACHE_MANIFEST"] = rlocationpath(manifest_file, ws)
        direct_files.append(manifest_file)
        direct_files.extend(results["cache"].values())

    shell = shell_path(ctx)
    if shell:
        env["CONFIG_SHELL"] = shell

    runfiles = ctx.attr._tester[DefaultInfo].default_runfiles.merge(
        ctx.runfiles(
            files = direct_files,
            transitive_files = depset(transitive = [
                runtime.files,
                toolchain_info.cc_toolchain.all_files,
            ]),
        ),
    )

    return [
        DefaultInfo(
            files = depset([executable]),
            runfiles = runfiles,
            executable = executable,
        ),
        RunEnvironmentInfo(
            environment = env,
            inherited_environment = ["PATH"],
        ),
    ]

_gnu_autoconf_conformance_test = rule(
    doc = """\
Check that Bazel reproduces GNU autoconf's output for a `configure.ac`.

GNU `aclocal`, `autoconf` and `configure` run with the pinned autotools
toolchain and with the compiler, flags and environment of the current C++
toolchain, exactly as the `autoconf` rule computes them.  The resulting
`config.h` and `subst.h` must match `bazel_config_h` / `bazel_subst_h` byte
for byte, except for variables listed in `known_divergences`.

Everything needed to review a result is left in the undeclared outputs
directory: `gnu/` (the configure work tree with config.h, subst.h and
config.log), `bazel/` (the Bazel headers), `*.diff` when they differ, the
configure environment, tool versions and the host toolchain fingerprint.
The staged m4 files and aux scripts (pinned inputs) and `autom4te.cache`
are removed after configure runs.
""",
    implementation = _gnu_autoconf_conformance_test_impl,
    attrs = {
        "autoconf_target": attr.label(
            doc = "The `autoconf` target under test; its cache results feed the divergence report.",
            providers = [CcAutoconfInfo],
        ),
        "aux_files": attr.label_list(
            doc = "Auxiliary files (e.g. `config.rpath`) copied to the work directory root.",
            allow_files = True,
        ),
        "bazel_config_h": attr.label(
            doc = "Bazel-generated `config.h` (from `autoconf_hdr`, mode `defines`).",
            allow_single_file = True,
        ),
        "bazel_subst_h": attr.label(
            doc = "Bazel-generated subst header (from `autoconf_hdr`, mode `subst`).",
            allow_single_file = True,
        ),
        "build_aux": attr.label_list(
            doc = "`config.guess` / `config.sub` and friends, from the pinned gnulib.",
            allow_files = True,
            default = [Label("@gnulib//:build-aux")],
        ),
        "config_h_in": attr.label(
            doc = "Template for AC_DEFINE (`#undef` patterns).",
            allow_single_file = True,
            mandatory = True,
        ),
        "configure_ac": attr.label(
            doc = "The `configure.ac` under test.",
            allow_single_file = True,
            mandatory = True,
        ),
        "configure_args": attr.string_list(
            doc = """\
Extra arguments for `./configure`. The default keeps configure in the same
language mode Bazel compiles in: autoconf 2.72+ `AC_PROG_CC` would otherwise
probe for C23 support and append `-std=gnu23` to `CC`, which changes results
such as `HAVE_C_BOOL` and gnulib's `NEXT_ASSERT_H` relative to the Bazel
toolchain's default standard. Likewise the C11 probe is told the default
mode already suffices: cl.exe is not C11 by default and configure would
append `-std:c11`, which enables `<threads.h>` and C11 math declarations
that the Bazel checks, running without that flag, do not see.""",
            default = [
                "ac_cv_prog_cc_c23=no",
                "ac_cv_prog_cc_c11=",
            ],
        ),
        "known_divergences": attr.string_dict(
            doc = "Variable name -> reason, for intentional differences from upstream m4. Masked in the diff.",
        ),
        "m4_files": attr.label_list(
            doc = "Candidate m4 files; `aclocal` selects the ones actually needed.",
            allow_files = True,
        ),
        "subst_h_in": attr.label(
            doc = "Template for AC_SUBST (`@FOO@` patterns).",
            allow_single_file = True,
        ),
        "verify_variables": attr.bool(
            doc = "Require the templates to contain every variable configure produced.",
        ),
        "_host_fingerprint": attr.label(
            default = Label("@host_cc_fingerprint//:fingerprint.json"),
            allow_single_file = True,
        ),
        "_tester": attr.label(
            executable = True,
            cfg = "target",
            default = Label("//autoconf/tests/gnu:conformance_tester"),
        ),
    },
    fragments = ["cpp"],
    # `get_cc_toolchain_info` resolves the C++ toolchain through the same exec
    # group the `autoconf` rule declares, so the oracle sees the compiler the
    # probes run with.
    exec_groups = {AUTOCONF_EXEC_GROUP: exec_group(toolchains = use_cc_toolchain())},
    toolchains = [
        config_common.toolchain_type(TOOLCHAIN_TYPE),
        config_common.toolchain_type(SH_TOOLCHAIN_TYPE, mandatory = False),
    ],
    test = True,
)

def gnu_autoconf_conformance_test(*, name, tags = [], **kwargs):
    """Wrapper adding the `gnu_autoconf_test` tag.

    The oracle runs on every platform.  On Windows it runs under the
    rules_shell bash with MSVC driven through gnulib's `compile` wrapper.

    Args:
        name (str): target name.
        tags (list[str]): extra tags.
        **kwargs: forwarded to the rule.
    """
    _gnu_autoconf_conformance_test(
        name = name,
        tags = tags + ["gnu_autoconf_test"],
        **kwargs
    )
