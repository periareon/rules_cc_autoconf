# Gnulib

The `@rules_cc_autoconf//gnulib` module provides a collection of pre-built autoconf checks
based on [GNU Gnulib](https://www.gnu.org/software/gnulib/), a portability library for
Unix-like systems. Instead of manually writing checks for common functions, headers, and
types, you can reuse these well-tested, platform-aware implementations.

## What is it?

Gnulib is a collection of M4 macros and C code that provides portability checks and
replacements for common POSIX functions. The `@rules_cc_autoconf//gnulib` module translates
many of these M4 macros into Bazel `autoconf` targets that you can use as dependencies.

Each target in `@rules_cc_autoconf//gnulib/m4/` corresponds to a gnulib M4 macro and
provides the same checks and defines that you would get from using that macro in a
traditional `configure.ac` file.

## Using Gnulib Targets

Instead of manually writing checks, you can add gnulib reusable targets as dependencies to your
`autoconf` rule:

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")
load("@rules_cc_autoconf//autoconf:package_info.bzl", "package_info")

package_info(
    name = "package",
    package_name = "my_package",
    package_version = "1.0.0",
)

autoconf(
    name = "autoconf",
    checks = [
        # Only add custom checks that aren't available in gnulib
        checks.AC_DEFINE("CUSTOM_FEATURE", "1"),
    ],
    deps = [
        ":package",
        "@rules_cc_autoconf//gnulib/m4/lstat",      # Provides AC_CHECK_FUNC("lstat")
        "@rules_cc_autoconf//gnulib/m4/access",     # Provides AC_CHECK_FUNC("access")
        "@rules_cc_autoconf//gnulib/m4/unistd_h",   # Provides AC_CHECK_HEADER("unistd.h")
        "@rules_cc_autoconf//gnulib/m4/sys_stat_h", # Provides AC_CHECK_HEADER("sys/stat.h")
    ],
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    deps = [":autoconf"],
)
```
