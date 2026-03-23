"""autoconf_toolchain

Provides default autoconf checks that can be overridden by targets.
"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load("//autoconf/private:autoconf_config.bzl", "collect_deps", "collect_transitive_results")
load("//autoconf/private:autoconf_library.bzl", "COMMON_ATTRS", "autoconf_impl_common")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _autoconf_toolchain_impl(ctx):
    # Collect cache_deps results (for action deduplication)
    cache_deps = collect_deps(ctx.attr.cache_deps)
    cache_dep_infos = cache_deps.to_list()
    cache_results = collect_transitive_results(cache_dep_infos)

    # Collect defaults results (for autoconf_hdr rendering)
    defaults_deps = collect_deps(ctx.attr.defaults)
    defaults_dep_infos = defaults_deps.to_list()
    defaults_results = collect_transitive_results(defaults_dep_infos)

    # Build defaults_by_label for filtering (only defaults, not cache_deps)
    defaults_by_label = {}
    for direct_dep in ctx.attr.defaults:
        direct_info = direct_dep[CcAutoconfInfo]
        direct_deps = collect_deps([direct_dep])
        direct_dep_infos = direct_deps.to_list()
        direct_results = collect_transitive_results(direct_dep_infos)
        defaults_by_label[direct_info.owner] = struct(
            cache = direct_results["cache"],
            define = direct_results["define"],
            subst = direct_results["subst"],
            unquoted_defines = direct_results["unquoted_defines"],
        )

    # Unified content cache: cache_deps + defaults (for content-based action dedup)
    unified_content_cache = cache_results["content_cache"] | defaults_results["content_cache"]

    return [
        platform_common.ToolchainInfo(
            label = ctx.label,
            autoconf_cache = unified_content_cache,
            autoconf_defaults = struct(
                cache = defaults_results["cache"],
                define = defaults_results["define"],
                subst = defaults_results["subst"],
                unquoted_defines = defaults_results["unquoted_defines"],
                defaults_by_label = defaults_by_label,
            ),
        ),
    ]

autoconf_toolchain = rule(
    doc = """\
Define an autoconf toolchain providing cached check results and rendering defaults.

The toolchain has two separate concepts:

- **cache_deps**: Check results available for action deduplication. When an
  `autoconf` target lists a check whose cache variable name already has a result
  in `cache_deps`, the checker action is skipped and the existing result file is
  reused.
- **defaults**: Baseline values for `autoconf_hdr` rendering. When
  `autoconf_hdr` has `defaults = True`, these values are merged into the
  generated header.

Both `cache_deps` and `defaults` contribute to the unified cache used by the
`autoconf` rule for action deduplication. Only `defaults` contributes to the
`defaults_by_label` map used by `autoconf_hdr` for include/exclude filtering.

Targets listed in `cache_deps` and `defaults` must use `autoconf_library`
(not `autoconf`) to avoid a dependency cycle.

Example:

```python
load("@rules_cc_autoconf//autoconf:defs.bzl", "autoconf_cache", "autoconf_toolchain")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

autoconf_cache(
    name = "gnulib_defaults",
    checks = [
        checks.AC_SUBST("GNULIB_VFPRINTF_POSIX", "0"),
        checks.AC_SUBST("REPLACE_POSIX_SPAWN", "0"),
    ],
)

autoconf_toolchain(
    name = "gnulib_toolchain_impl",
    defaults = [":gnulib_defaults"],
)

toolchain(
    name = "gnulib_toolchain",
    toolchain = ":gnulib_toolchain_impl",
    toolchain_type = "@rules_cc_autoconf//autoconf:toolchain_type",
)
```
""",
    implementation = _autoconf_toolchain_impl,
    attrs = {
        "cache_deps": attr.label_list(
            doc = "Targets whose check results are available for action deduplication in `autoconf` targets.",
            providers = [CcAutoconfInfo],
        ),
        "defaults": attr.label_list(
            doc = "Targets whose results provide baseline values for `autoconf_hdr` rendering.",
            providers = [CcAutoconfInfo],
        ),
    },
)

def _autoconf_cache_impl(ctx):
    return autoconf_impl_common(ctx, resolve_toolchain = False)

autoconf_cache = rule(
    implementation = _autoconf_cache_impl,
    doc = """\
Run autoconf-like checks without resolving the autoconf toolchain.

Identical to ``autoconf`` except that it does **not** resolve the
``autoconf_toolchain``.  Use this rule for targets that are listed as
``cache_deps`` or ``defaults`` of an ``autoconf_toolchain`` -- using the
regular ``autoconf`` rule in that position would create a dependency cycle.

Dep-level caching still applies: if a check's cache variable name already
has a result in transitive ``deps``, the action is skipped.
""",
    attrs = COMMON_ATTRS,
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
    provides = [CcAutoconfInfo],
)
