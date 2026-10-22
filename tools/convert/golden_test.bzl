"""convert_golden_test: the converter's output for one gnulib module is fixed.

Runs the converter on a `configure.ac` (the module's compat test input) with
the given m4 files, translating only the module's own m4 file and resolving
everything else through the hand ports (the `index.toml` snapshot), lints the
emitted BUILD file with buildifier (every warning enabled) and diffs it
against `expected`.  Update an expected file with

    bazel run //tools/convert:<name> -- --update
"""

load("//autoconf/tests/gnu:toolchain.bzl", "SH_TOOLCHAIN_TYPE", "TOOLCHAIN_TYPE", "autotools_runtime", "rlocationpath", "shell_path")

def _convert_golden_test_impl(ctx):
    ws = ctx.workspace_name
    runner = ctx.executable._runner
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, runner.extension).strip("."))
    ctx.actions.symlink(target_file = runner, output = executable, is_executable = True)

    m4_list = ctx.actions.declare_file("{}.m4_files.txt".format(ctx.label.name))
    ctx.actions.write(m4_list, "\n".join([rlocationpath(f, ws) for f in ctx.files.m4_files]) + "\n")

    runtime = autotools_runtime(ctx)
    env = dict(runtime.env)
    env.update({
        "TEST_CONFIGURE_AC": rlocationpath(ctx.file.configure_ac, ws),
        "TEST_EXPECTED": rlocationpath(ctx.file.expected, ws),
        "TEST_EXPECTED_PATH": ctx.file.expected.short_path,
        "TEST_M4_LIST": rlocationpath(m4_list, ws),
        "TEST_ONLY": " ".join(ctx.attr.only),
    })
    env["TEST_BUILDIFIER"] = rlocationpath(ctx.executable._buildifier, ws)
    env["TEST_ANNOTATE"] = "1" if ctx.attr.annotate else ""
    decisions_files = []
    if ctx.file.decisions:
        env["TEST_DECISIONS"] = rlocationpath(ctx.file.decisions, ws)
        decisions_files.append(ctx.file.decisions)
    if ctx.file.expected_decisions:
        env["TEST_EXPECTED_DECISIONS"] = rlocationpath(ctx.file.expected_decisions, ws)
        env["TEST_EXPECTED_DECISIONS_PATH"] = ctx.file.expected_decisions.short_path
        decisions_files.append(ctx.file.expected_decisions)
    shell = shell_path(ctx)
    if shell:
        env["CONFIG_SHELL"] = shell

    runfiles = ctx.attr._runner[DefaultInfo].default_runfiles.merge(
        ctx.attr._buildifier[DefaultInfo].default_runfiles,
    ).merge(
        ctx.runfiles(
            files = [executable, m4_list, ctx.file.configure_ac, ctx.file.expected, ctx.executable._buildifier] +
                    decisions_files + ctx.files.m4_files,
            transitive_files = runtime.files,
        ),
    )
    return [
        DefaultInfo(executable = executable, runfiles = runfiles, files = depset([executable])),
        RunEnvironmentInfo(environment = env, inherited_environment = ["PATH"]),
    ]

def _convert_project_golden_test_impl(ctx):
    ws = ctx.workspace_name
    runner = ctx.executable._runner
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, runner.extension).strip("."))
    ctx.actions.symlink(target_file = runner, output = executable, is_executable = True)

    # The source tree, as rlocationpath<TAB>path-inside-the-tree lines: the
    # runner recreates the directory (runfiles may be a manifest, not a tree).
    lines = []
    for f in ctx.files.sources:
        # external/<repo>/<path> or bazel-out/.../<path>: keep the part after the repo root.
        rloc = rlocationpath(f, ws)
        relative = rloc.split("/", 1)[1] if "/" in rloc else rloc
        lines.append("{}\t{}".format(rloc, relative))
    listing = ctx.actions.declare_file("{}.sources.txt".format(ctx.label.name))
    ctx.actions.write(listing, "\n".join(lines) + "\n")

    runtime = autotools_runtime(ctx)
    env = dict(runtime.env)
    env.update({
        "TEST_EXPECTED": rlocationpath(ctx.file.expected, ws),
        "TEST_EXPECTED_MODULE": rlocationpath(ctx.file.expected_module, ws),
        "TEST_EXPECTED_MODULE_PATH": ctx.file.expected_module.short_path,
        "TEST_EXPECTED_PATH": ctx.file.expected.short_path,
        "TEST_PROJECT": "1",
        "TEST_SOURCES": rlocationpath(listing, ws),
    })
    env["TEST_BUILDIFIER"] = rlocationpath(ctx.executable._buildifier, ws)
    env["TEST_ANNOTATE"] = "1" if ctx.attr.annotate else ""
    decisions_files = []
    if ctx.file.decisions:
        env["TEST_DECISIONS"] = rlocationpath(ctx.file.decisions, ws)
        decisions_files.append(ctx.file.decisions)
    if ctx.file.expected_decisions:
        env["TEST_EXPECTED_DECISIONS"] = rlocationpath(ctx.file.expected_decisions, ws)
        env["TEST_EXPECTED_DECISIONS_PATH"] = ctx.file.expected_decisions.short_path
        decisions_files.append(ctx.file.expected_decisions)
    shell = shell_path(ctx)
    if shell:
        env["CONFIG_SHELL"] = shell

    runfiles = ctx.attr._runner[DefaultInfo].default_runfiles.merge(
        ctx.attr._buildifier[DefaultInfo].default_runfiles,
    ).merge(
        ctx.runfiles(
            files = [executable, listing, ctx.file.expected, ctx.file.expected_module, ctx.executable._buildifier] +
                    decisions_files + ctx.files.sources,
            transitive_files = runtime.files,
        ),
    )
    return [
        DefaultInfo(executable = executable, runfiles = runfiles, files = depset([executable])),
        RunEnvironmentInfo(environment = env, inherited_environment = ["PATH"]),
    ]

convert_project_golden_test = rule(
    doc = "The converter's project-mode output for a source tree equals `expected`.",
    implementation = _convert_project_golden_test_impl,
    attrs = {
        "annotate": attr.bool(
            doc = "Comment the output with the m4 each check, dep and rule came from (`--annotate`).",
            default = False,
        ),
        "decisions": attr.label(
            doc = "The decisions file the conversion runs with (every request must be decided).",
            allow_single_file = [".bzl"],
        ),
        "expected": attr.label(
            doc = "The expected autoconf.bzl.",
            allow_single_file = True,
            mandatory = True,
        ),
        "expected_decisions": attr.label(
            doc = "The decisions file the converter writes for this input when run without one.",
            allow_single_file = True,
        ),
        "expected_module": attr.label(
            doc = "The expected MODULE.bazel.",
            allow_single_file = True,
            mandatory = True,
        ),
        "sources": attr.label(
            doc = "The project's source tree (a filegroup of an http_archive).",
            allow_files = True,
            mandatory = True,
        ),
        "_buildifier": attr.label(
            doc = "Lints the converter's output with every warning enabled.",
            default = Label("@buildifier_prebuilt//:buildifier"),
            executable = True,
            cfg = "exec",
        ),
        "_runner": attr.label(
            default = Label("//tools/convert:golden_runner"),
            executable = True,
            cfg = "target",
        ),
    },
    toolchains = [
        TOOLCHAIN_TYPE,
        config_common.toolchain_type(SH_TOOLCHAIN_TYPE, mandatory = False),
    ],
    test = True,
)

convert_golden_test = rule(
    doc = "The converter's BUILD output for one module equals `expected`.",
    implementation = _convert_golden_test_impl,
    attrs = {
        "annotate": attr.bool(
            doc = "Comment the output with the m4 each check, dep and rule came from (`--annotate`).",
            default = False,
        ),
        "configure_ac": attr.label(
            doc = "The configure.ac that invokes the module's macros.",
            allow_single_file = True,
            mandatory = True,
        ),
        "decisions": attr.label(
            doc = "The decisions file the conversion runs with (every request must be decided).",
            allow_single_file = [".bzl"],
        ),
        "expected": attr.label(
            doc = "The expected BUILD file; the test also lints the output with buildifier.",
            allow_single_file = True,
            mandatory = True,
        ),
        "expected_decisions": attr.label(
            doc = "The decisions file the converter writes for this input when run without one.",
            allow_single_file = True,
        ),
        "m4_files": attr.label_list(
            doc = "m4 files staged into `m4/` (aclocal picks the needed ones).",
            allow_files = [".m4"],
            mandatory = True,
        ),
        "only": attr.string_list(
            doc = "m4 file basenames whose AC_DEFUNs are translated; the rest resolve to ports.",
            mandatory = True,
        ),
        "_buildifier": attr.label(
            doc = "Lints the converter's output with every warning enabled.",
            default = Label("@buildifier_prebuilt//:buildifier"),
            executable = True,
            cfg = "exec",
        ),
        "_runner": attr.label(
            default = Label("//tools/convert:golden_runner"),
            executable = True,
            cfg = "target",
        ),
    },
    toolchains = [
        TOOLCHAIN_TYPE,
        config_common.toolchain_type(SH_TOOLCHAIN_TYPE, mandatory = False),
    ],
    test = True,
)
