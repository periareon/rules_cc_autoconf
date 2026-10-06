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

Some modules expose **linker-related** subst variables via **`AC_SEARCH_LIBS`** (for
example clock/time helpers that may need `-lrt`). Bazel does not run `libtool` like
Autoconf; add an **`autoconf_linkopts`** target that lists those subst names and depend
on it from your `cc_library` / `cc_binary` / `cc_test` targets. See
[Migrating M4 macros — Pattern 7b](./migration.md#pattern-7b-ac_search_libs-and-autoconf_linkopts)
and the rule reference [autoconf_linkopts](./autoconf_linkopts.md).

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

## How the gnulib ports are tested

Every `//gnulib/m4/<module>` port has a suite under `//gnulib/tests/compat/<module>`
created by `gnu_gnulib_diff_test_suite`. The oracle is **GNU autoconf itself**: the
`<module>_test_gnu_conformance` test runs the pinned `aclocal`, `autoconf` and
`configure` on the module's `configure.ac` using the m4 files from the pinned gnulib
commit, with the **same compiler, flags and environment** the `autoconf` rule uses
(both read them from the same Starlark helper), and requires the rendered `config.h`
and `subst.h` to match the Bazel-generated headers byte for byte. There are no
checked-in expected outputs for Linux and macOS; GNU autoconf produces them on the
machine running the test.

The autotools are hermetic dev dependencies: autoconf and automake tarballs are
fetched and installed by `//autoconf/tests/gnu:deps.bzl`, perl comes from
`rules_perl`, m4 from the Bazel Central Registry `m4` module, and the shell from
`rules_shell`. Only the POSIX shell and userland come from the host. Run the bundled
tools directly with, for example, `bazel run //autoconf/tests/gnu:autoconf -- --version`.

Every comparison leaves its inputs where a reviewer can read them. A conformance
test's undeclared outputs directory (`bazel-testlogs/.../test.outputs/`) contains
`gnu/` (the configure work tree with `config.h`, `subst.h`, `config.log`, tool logs
and the exact environment configure ran with), `bazel/` (the Bazel headers), a
`config.h.diff` / `subst.h.diff` when they differ, `versions.json` and
`fingerprint.json`. A `diff_test` leaves `expected/`, `actual/` and `diff.patch`.

On Windows the same conformance test runs under the MSYS2 bash from `rules_shell`,
with Strawberry Perl from `rules_perl` and MSVC driven through gnulib's `compile`
wrapper. There are no checked-in expected outputs on any platform.

When a port intentionally differs from upstream m4, list the variable in the suite's
`known_divergences` with a reason; it is masked from the comparison and always
printed in the test log. Every gnulib module has a conformance test; there is no
opt-out.
