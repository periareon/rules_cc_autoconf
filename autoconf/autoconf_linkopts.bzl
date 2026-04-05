"""# autoconf_linkopts

Resolve AC_SEARCH_LIBS / AC_SUBST results into linker flags at build time.

This rule reads the JSON result files from autoconf subst variables and
produces a linker response file containing only the non-empty flags. The
response file is returned via CcInfo so that cc_binary/cc_library targets
can depend on it to get the correct platform-specific linkopts.

Example:

```python
load("@rules_cc_autoconf//autoconf:autoconf_linkopts.bzl", "autoconf_linkopts")

autoconf_linkopts(
    name = "linkopts",
    vars = [
        "LIBPTHREAD",
        "FDATASYNC_LIB",
        "CLOCK_TIME_LIB",
    ],
    deps = [":autoconf"],
)

cc_binary(
    name = "my_binary",
    srcs = ["main.c"],
    deps = [":gnulib", ":linkopts"],
)
```

On modern Linux (glibc 2.34+) where pthread/rt functions are in libc,
AC_SEARCH_LIBS produces empty values and no extra -l flags are added.
On older systems, the appropriate flags (-lpthread, -lrt) are added
automatically.
"""

load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "get_autoconf_toolchain_defaults",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _autoconf_linkopts_impl(ctx):
    """Implementation of the autoconf_linkopts rule."""

    # Get toolchain defaults
    defaults = struct(cache = {}, define = {}, subst = {})
    if ctx.attr.defaults:
        defaults = get_autoconf_toolchain_defaults(ctx)

    # Collect transitive results from deps
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    all_subst = defaults.subst | dep_results["subst"]

    # Build args for the linkopts_gen tool
    flags_file = ctx.actions.declare_file(ctx.label.name + ".flags")
    inputs = []
    args = ctx.actions.args()

    found_vars = []
    for var_name in ctx.attr.vars:
        if var_name in all_subst:
            result_file = all_subst[var_name]
            args.add("--var", "{}={}".format(var_name, result_file.path))
            inputs.append(result_file)
            found_vars.append(var_name)

    # Also check cache results for variables that are stored as cache
    # (some AC_SEARCH_LIBS results use the cache variable name)
    all_cache = defaults.cache | dep_results["cache"]
    for var_name in ctx.attr.vars:
        if var_name not in found_vars:
            cache_name = "ac_cv_subst_" + var_name
            if cache_name in all_cache:
                result_file = all_cache[cache_name]
                args.add("--var", "{}={}".format(var_name, result_file.path))
                inputs.append(result_file)
                found_vars.append(var_name)

    args.add("--output", flags_file)

    ctx.actions.run(
        executable = ctx.executable._linkopts_gen,
        arguments = [args],
        inputs = inputs,
        outputs = [flags_file],
        mnemonic = "CcAutoconfLinkopts",
    )

    linker_input = cc_common.create_linker_input(
        owner = ctx.label,
        user_link_flags = depset(["@" + flags_file.path]),
        additional_inputs = depset([flags_file]),
    )
    linking_context = cc_common.create_linking_context(
        linker_inputs = depset([linker_input]),
    )

    return [
        DefaultInfo(files = depset([flags_file])),
        CcInfo(linking_context = linking_context),
    ]

autoconf_linkopts = rule(
    implementation = _autoconf_linkopts_impl,
    doc = """\
Resolve AC_SEARCH_LIBS / AC_SUBST results into linker flags.

Reads subst result JSON files from autoconf deps and produces a
linker response file. Returns CcInfo with the flags, so consumers
can simply add this target to their deps list.

Variables not found in the deps are silently ignored (they may
be provided by a different module or not needed on this platform).
""",
    attrs = {
        "defaults": attr.bool(
            doc = "Whether to include toolchain defaults.",
            default = False,
        ),
        "deps": attr.label_list(
            doc = "List of autoconf targets providing check results.",
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "vars": attr.string_list(
            doc = """\
List of AC_SUBST variable names to resolve (e.g., ["LIBPTHREAD", "FDATASYNC_LIB"]).
Each variable is looked up in the subst results from deps. Non-empty values
(like "-lpthread") are added to linkopts; empty values are skipped.
""",
            mandatory = True,
        ),
        "_linkopts_gen": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/linkopts_gen"),
        ),
    },
    toolchains = [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
)
