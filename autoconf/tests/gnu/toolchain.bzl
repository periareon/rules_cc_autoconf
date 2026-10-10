"""Toolchain exposing the pinned GNU autotools, perl and m4 to test rules."""

TOOLCHAIN_TYPE = str(Label("//autoconf/tests/gnu:toolchain_type"))
PERL_TOOLCHAIN_TYPE = "@rules_perl//perl:toolchain_type"
SH_TOOLCHAIN_TYPE = "@rules_shell//shell:toolchain_type"

def shell_path(ctx):
    """The host POSIX shell from rules_shell, or None if none is registered.

    Args:
        ctx (ctx): a rule context that declared `SH_TOOLCHAIN_TYPE` (optional).

    Returns:
        str | None: absolute shell path.
    """
    sh_toolchain = ctx.toolchains[SH_TOOLCHAIN_TYPE]
    if sh_toolchain and getattr(sh_toolchain, "path", ""):
        return sh_toolchain.path
    return None

AutotoolsInfo = provider(
    doc = "GNU autotools runtime: interpreters, scripts and support files.",
    fields = {
        "aclocal": "File: automake's `bin/aclocal` perl script.",
        "autoconf": "File: autoconf's `bin/autoconf` perl script.",
        "autoconf_version": "str: pinned autoconf version.",
        "automake_api_version": "str: pinned automake API version (e.g. `1.18`).",
        "automake_version": "str: pinned automake version.",
        "files": "depset[File]: every file needed at run time.",
        "m4": "File: GNU m4 executable.",
        "perl": "File: perl interpreter.",
    },
)

def rlocationpath(file, workspace_name):
    """Compute the runfiles path for a file.

    Args:
        file (File): the file.
        workspace_name (str): `ctx.workspace_name`.

    Returns:
        str: the rlocationpath.
    """
    if file.short_path.startswith("../"):
        return file.short_path[len("../"):]
    return "{}/{}".format(workspace_name, file.short_path)

def _autotools_toolchain_impl(ctx):
    perl = ctx.toolchains[PERL_TOOLCHAIN_TYPE].perl_runtime
    m4 = ctx.executable.m4

    files = depset(
        [ctx.file.autoconf, ctx.file.aclocal, m4, perl.interpreter],
        transitive = [
            ctx.attr.autoconf_files[DefaultInfo].files,
            ctx.attr.automake_files[DefaultInfo].files,
            ctx.attr.m4[DefaultInfo].default_runfiles.files,
            perl.runtime,
        ],
    )

    info = AutotoolsInfo(
        aclocal = ctx.file.aclocal,
        autoconf = ctx.file.autoconf,
        autoconf_version = ctx.attr.autoconf_version,
        automake_api_version = ctx.attr.automake_api_version,
        automake_version = ctx.attr.automake_version,
        files = files,
        m4 = m4,
        perl = perl.interpreter,
    )

    return [
        platform_common.ToolchainInfo(autotools = info),
        DefaultInfo(files = files),
    ]

autotools_toolchain = rule(
    doc = "Bundle the pinned autoconf and automake sources with perl and m4.",
    implementation = _autotools_toolchain_impl,
    attrs = {
        "aclocal": attr.label(
            doc = "automake's `bin/aclocal` script.",
            allow_single_file = True,
            mandatory = True,
        ),
        "autoconf": attr.label(
            doc = "autoconf's `bin/autoconf` script.",
            allow_single_file = True,
            mandatory = True,
        ),
        "autoconf_files": attr.label(
            doc = "All autoconf runtime files (`bin/*`, `lib/**`).",
            mandatory = True,
        ),
        "autoconf_version": attr.string(mandatory = True),
        "automake_api_version": attr.string(mandatory = True),
        "automake_files": attr.label(
            doc = "All automake runtime files needed by aclocal.",
            mandatory = True,
        ),
        "automake_version": attr.string(mandatory = True),
        "m4": attr.label(
            doc = "GNU m4 executable.",
            executable = True,
            cfg = "target",
            mandatory = True,
        ),
    },
    toolchains = [PERL_TOOLCHAIN_TYPE],
)

def autotools_runtime(ctx):
    """Resolve the autotools toolchain into test environment and runfiles.

    The returned environment is consumed by `autoconf/tests/gnu/autotools.py`.

    Args:
        ctx (ctx): a rule context that declared `TOOLCHAIN_TYPE`.

    Returns:
        struct: `env` (dict[str, str]) and `files` (depset[File]).
    """
    info = ctx.toolchains[TOOLCHAIN_TYPE].autotools
    env = {
        "AUTOTOOLS_ACLOCAL": rlocationpath(info.aclocal, ctx.workspace_name),
        "AUTOTOOLS_AUTOCONF": rlocationpath(info.autoconf, ctx.workspace_name),
        "AUTOTOOLS_AUTOCONF_VERSION": info.autoconf_version,
        "AUTOTOOLS_AUTOMAKE_API_VERSION": info.automake_api_version,
        "AUTOTOOLS_AUTOMAKE_VERSION": info.automake_version,
        "AUTOTOOLS_M4": rlocationpath(info.m4, ctx.workspace_name),
        "AUTOTOOLS_PERL": rlocationpath(info.perl, ctx.workspace_name),
    }
    return struct(env = env, files = info.files)

def _autotools_runner_impl(ctx):
    runtime = autotools_runtime(ctx)
    runner = ctx.executable.runner

    executable = ctx.actions.declare_file("{}.{}".format(ctx.label.name, runner.extension).strip("."))
    ctx.actions.symlink(target_file = runner, output = executable, is_executable = True)

    env = dict(runtime.env)
    env["AUTOTOOLS_TOOL"] = ctx.attr.tool
    shell = shell_path(ctx)
    if shell:
        env["CONFIG_SHELL"] = shell

    runfiles = ctx.attr.runner[DefaultInfo].default_runfiles.merge(
        ctx.runfiles(files = [executable], transitive_files = runtime.files),
    )

    return [
        DefaultInfo(executable = executable, runfiles = runfiles, files = depset([executable])),
        RunEnvironmentInfo(environment = env, inherited_environment = ["PATH"]),
    ]

_RUNNER_ATTRS = {
    "runner": attr.label(
        doc = "Python program to run inside the autotools environment.",
        executable = True,
        cfg = "target",
        mandatory = True,
    ),
    "tool": attr.string(
        doc = "Tool name passed as `AUTOTOOLS_TOOL` (used by `run_tool.py`).",
        default = "",
    ),
}

_RUNNER_TOOLCHAINS = [
    TOOLCHAIN_TYPE,
    config_common.toolchain_type(SH_TOOLCHAIN_TYPE, mandatory = False),
]

autotools_binary = rule(
    doc = "Run a Python program with the autotools environment (for `bazel run`).",
    implementation = _autotools_runner_impl,
    attrs = _RUNNER_ATTRS,
    toolchains = _RUNNER_TOOLCHAINS,
    executable = True,
)

autotools_test = rule(
    doc = "Run a Python test with the autotools environment.",
    implementation = _autotools_runner_impl,
    attrs = _RUNNER_ATTRS,
    toolchains = _RUNNER_TOOLCHAINS,
    test = True,
)
