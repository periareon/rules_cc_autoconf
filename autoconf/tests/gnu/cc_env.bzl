"""The C++ toolchain as a JSON description, for tools that run GNU configure.

`cc_env_dict` is what `gnu_autoconf_conformance_test` serializes as
`<name>.cc_env.json`; `autotools_cc_binary` makes the same description (and
the toolchain's files) available to a `bazel run` program through the
`CONVERT_CC_ENV` environment variable, so `//tools/convert`'s `check` can
drive `./configure` with the compiler the rules' probes use.
"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "AUTOCONF_EXEC_GROUP",
    "create_config_dict",
    "get_cc_toolchain_info",
    "get_environment_variables",
)
load(":toolchain.bzl", "SH_TOOLCHAIN_TYPE", "TOOLCHAIN_TYPE", "autotools_runtime", "rlocationpath", "shell_path")

def tool_rlocationpath(path, workspace_name):
    """Convert an execroot-relative tool path into an rlocationpath.

    Absolute paths (host toolchains such as `/usr/bin/gcc`) are returned as is.

    Args:
        path (str): the tool path as the C++ toolchain reports it.
        workspace_name (str): `ctx.workspace_name`.

    Returns:
        str: an rlocationpath, or the absolute path.
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

def cc_env_dict(ctx, toolchain_info):
    """The compiler, flags and environment of the C++ toolchain, as JSON-able data.

    Args:
        ctx (ctx): a rule context with the `AUTOCONF_EXEC_GROUP` exec group.
        toolchain_info (struct): `get_cc_toolchain_info(ctx)`.

    Returns:
        dict: keys `c_compiler`, `c_flags`, `c_link_flags`, `compiler_type`,
        `cpp_compiler`, `cpp_flags`, `cpp_link_flags`, `env`, `linker`.
    """
    ws = ctx.workspace_name
    config = create_config_dict(toolchain_info)
    return {
        "c_compiler": tool_rlocationpath(config["c_compiler"], ws),
        "c_flags": config["c_flags"],
        "c_link_flags": config["c_link_flags"],
        "compiler_type": config["compiler_type"],
        "cpp_compiler": tool_rlocationpath(config["cpp_compiler"], ws),
        "cpp_flags": config["cpp_flags"],
        "cpp_link_flags": config["cpp_link_flags"],
        "env": get_environment_variables(ctx, toolchain_info),
        "linker": tool_rlocationpath(config["linker"], ws),
    }

def _autotools_cc_binary_impl(ctx):
    ws = ctx.workspace_name
    runner = ctx.executable.runner
    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, runner.extension).strip("."))
    ctx.actions.symlink(target_file = runner, output = executable, is_executable = True)

    toolchain_info = get_cc_toolchain_info(ctx)
    cc_env_file = ctx.actions.declare_file("{}.cc_env.json".format(ctx.label.name))
    ctx.actions.write(cc_env_file, json.encode_indent(cc_env_dict(ctx, toolchain_info), indent = "  ") + "\n")

    runtime = autotools_runtime(ctx)
    env = dict(runtime.env)
    env["CONVERT_CC_ENV"] = rlocationpath(cc_env_file, ws)
    shell = shell_path(ctx)
    if shell:
        env["CONFIG_SHELL"] = shell

    runfiles = ctx.attr.runner[DefaultInfo].default_runfiles.merge(
        ctx.runfiles(
            files = [executable, cc_env_file],
            transitive_files = depset(transitive = [runtime.files, toolchain_info.cc_toolchain.all_files]),
        ),
    )
    return [
        DefaultInfo(executable = executable, runfiles = runfiles, files = depset([executable])),
        RunEnvironmentInfo(environment = env, inherited_environment = ["PATH"]),
    ]

autotools_cc_binary = rule(
    doc = """\
Run a Python program with the autotools environment and the C++ toolchain.

Like `autotools_binary`, plus `CONVERT_CC_ENV`: the rlocationpath of a JSON
description of the current C++ toolchain (compiler, flags, environment), with
the toolchain's files in the runfiles.""",
    implementation = _autotools_cc_binary_impl,
    attrs = {
        "runner": attr.label(
            doc = "Python program to run.",
            executable = True,
            cfg = "target",
            mandatory = True,
        ),
    },
    fragments = ["cpp"],
    exec_groups = {AUTOCONF_EXEC_GROUP: exec_group(toolchains = use_cc_toolchain())},
    toolchains = [
        config_common.toolchain_type(TOOLCHAIN_TYPE),
        config_common.toolchain_type(SH_TOOLCHAIN_TYPE, mandatory = False),
    ],
    executable = True,
)
