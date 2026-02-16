"""autoconf_toolchain

Provides default autoconf checks that can be overridden by targets.
"""

load("//autoconf/private:autoconf_config.bzl", "collect_deps", "collect_transitive_results")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _autoconf_toolchain_impl(ctx):
    # Collect transitive dependencies (like autoconf rule does)
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()

    # Collect all results from transitive deps with duplicate detection
    dep_results = collect_transitive_results(dep_infos)

    # Build defaults_by_label for filtering - each label includes its transitive results
    # This represents "this label brings in all these checks" including transitive deps
    defaults_by_label = {}

    # For each direct dependency, collect its transitive results
    for direct_dep in ctx.attr.deps:
        direct_info = direct_dep[CcAutoconfInfo]

        # Collect transitive deps for this direct dep
        direct_deps = collect_deps([direct_dep])
        direct_dep_infos = direct_deps.to_list()

        # Get transitive results for this label (includes the label itself and all its deps)
        direct_results = collect_transitive_results(direct_dep_infos)

        # Store transitive results for this label
        defaults_by_label[direct_info.owner] = struct(
            cache = direct_results["cache"],
            define = direct_results["define"],
            subst = direct_results["subst"],
        )

    return [
        platform_common.ToolchainInfo(
            label = ctx.label,
            autoconf_defaults = struct(
                cache = dep_results["cache"],
                define = dep_results["define"],
                subst = dep_results["subst"],
                defaults_by_label = defaults_by_label,
            ),
        ),
    ]

autoconf_toolchain = rule(
    doc = """\
Define default autoconf checks that can be overridden by targets.

The toolchain collects results from `autoconf` target dependencies and provides them
as defaults. When `autoconf_hdr` or `autoconf_srcs` rules process checks, toolchain
defaults are considered first but any checks from the actual targets will override
the defaults (no error on conflict).

**Duplicate Detection**: The toolchain disallows duplicate variables across
different defaults targets. If the same variable (cache, define, or subst) is
defined in multiple defaults targets with different result files, the toolchain
construction will fail with a clear error message indicating which targets conflict.
Duplicate variables must point to the same result file (same check result).

Example:

```python
load("@rules_cc_autoconf//autoconf:defs.bzl", "autoconf", "autoconf_toolchain")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

# Define default checks
autoconf(
    name = "gnulib_defaults",
    checks = [
        checks.AC_SUBST("GNULIB_VFPRINTF_POSIX", "0"),
        checks.AC_SUBST("REPLACE_POSIX_SPAWN", "0"),
        # ... many more defaults
    ],
)

# Create toolchain with defaults
autoconf_toolchain(
    name = "gnulib_toolchain_impl",
    deps = [":gnulib_defaults"],
)

toolchain(
    name = "gnulib_toolchain",
    toolchain = ":gnulib_toolchain_impl",
    toolchain_type = "@rules_cc_autoconf//autoconf:toolchain_type",
)
```

Then in consuming targets:

```python
autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    deps = [":my_checks"],  # These will override any defaults from the toolchain
)
```
""",
    implementation = _autoconf_toolchain_impl,
    attrs = {
        "deps": attr.label_list(
            doc = "List of `autoconf` targets providing default checks. These defaults can be overridden by targets using the toolchain.",
            providers = [CcAutoconfInfo],
        ),
    },
)
