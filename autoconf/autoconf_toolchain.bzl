"""autoconf_toolchain

Provides default autoconf checks that can be overridden by targets.
"""

load("//autoconf/private:providers.bzl", "CcAutoconfInfo", "CcAutoconfToolchainInfo")

def _autoconf_toolchain_impl(ctx):
    # Collect all results from default deps
    defaults = {}
    defaults_by_label = {}
    for dep in ctx.attr.deps:
        info = dep[CcAutoconfInfo]
        defaults = defaults | info.results
        # Track which label each set of defaults came from
        defaults_by_label[info.owner] = info.results

    return [
        platform_common.ToolchainInfo(
            label = ctx.label,
            autoconf_defaults = CcAutoconfToolchainInfo(
                defaults = defaults,
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

Example:

```python
load("@rules_cc_autoconf//autoconf:defs.bzl", "autoconf", "autoconf_toolchain")
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")

# Define default checks
autoconf(
    name = "gnulib_defaults",
    checks = [
        macros.AC_SUBST("GNULIB_VFPRINTF_POSIX", "0"),
        macros.AC_SUBST("REPLACE_POSIX_SPAWN", "0"),
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
