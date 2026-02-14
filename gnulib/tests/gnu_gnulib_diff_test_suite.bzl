"""Macros for testing gnulib m4 files against golden outputs.

This module provides:
- gnu_gnulib_diff_test: Single test comparing GNU autoconf output with golden files
- gnu_gnulib_diff_test_suite: Complete test suite for a gnulib module
"""

load("@rules_cc//cc:cc_test.bzl", "cc_test")
load("//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("//autoconf/tests:diff_test.bzl", "diff_test")
load("//autoconf/tests:gnu_autoconf_configure_test.bzl", "gnu_autoconf_configure_test")

def gnu_gnulib_diff_test_suite(
        *,
        name,
        configure_ac,
        m4_files,
        config_h_in,
        subst_h_in,
        golden_config_h,
        golden_subst_h,
        bazel_autoconf_target,
        test_c,
        aux_files = [],
        size = "medium",
        tags = [],
        **kwargs):
    """Complete test suite for a gnulib module.

    This creates:
    1. gnu_autoconf test - Runs GNU autoconf and compares with golden
    2. bazel_config_diff - Compares Bazel-generated config.h with golden
    3. bazel_gnulib_diff - Compares Bazel-generated gnulib_*.h with golden
    4. compile test - Verifies golden headers compile
    5. Test suite combining all above

    Golden files can be specified as either:
    - A simple Label (same golden for all platforms)
    - A dict with platform keys: {"linux": "golden_linux.h.in", "macos": "golden_macos.h.in", ...}

    Valid platform keys:
    - "linux": Only runs on Linux
    - "macos": Only runs on macOS
    - "windows": Only runs on Windows
    - "unix": Runs on all non-Windows platforms (Linux, macOS, BSD, etc.)

    When using dict golden files, a single test target is created per test type; the
    golden file is chosen via select(). If only linux and/or macos are specified
    (no windows), target_compatible_with is set so the test does not run on Windows.

    Args:
        name (str): Name of the test suite (typically "{module}_test")
        configure_ac (Label): The configure.ac file specific to this m4 module
        m4_files (list[Label]): ALL m4 files needed to run autoconf (including deps)
        config_h_in (Label): Template for AC_DEFINE (#undef patterns)
        subst_h_in (Label): Template for AC_SUBST (@FOO@ patterns)
        golden_config_h (Label or dict): Expected config.h output.
            Can be a Label or dict with platform keys.
        golden_subst_h (Label or dict): Expected gnulib_*.h output.
            Can be a Label or dict with platform keys.
        bazel_autoconf_target (Label): The autoconf target from //gnulib/m4/{name}
        test_c (Label): C file to compile with golden headers
        aux_files (list[Label]): Auxiliary files (e.g., config.rpath) to copy to work directory root
        size (str): Test size (default: "medium")
        tags (list[str]): Test tags
        **kwargs: Additional arguments
    """

    # Platform-specific test selection for compile tests
    unix_compatible = select({
        "@platforms//os:windows": ["@platforms//:incompatible"],
        "//conditions:default": [],
    })

    # Track all test names for the test suite
    all_tests = []

    # --- 1. GNU Autoconf Tests ---
    gnu_autoconf_configure_test(
        name = name + "_gnu_autoconf",
        configure_ac = configure_ac,
        m4_files = m4_files,
        config_h_in = config_h_in,
        subst_h_in = subst_h_in,
        verify_variables = True,
        golden_config_h = golden_config_h,
        golden_subst_h = golden_subst_h,
        aux_files = aux_files,
        size = size,
        tags = tags + ["gnu_autoconf_test"],
        target_compatible_with = unix_compatible,
    )
    all_tests.append(":{}_gnu_autoconf".format(name))

    # --- 2. Bazel autoconf_hdr targets ---
    autoconf_hdr(
        name = name + "_bazel_config_h",
        template = config_h_in,
        out = "config.h",
        deps = [bazel_autoconf_target],
        defaults = False,
        mode = "defines",
    )

    autoconf_hdr(
        name = name + "_bazel_subst_h",
        template = subst_h_in,
        out = "subst.h",
        deps = [bazel_autoconf_target],
        defaults = True,
        mode = "subst",
    )

    # --- 3. Diff test for config.h (single target with select() for file1) ---
    diff_test(
        name = "{}_bazel_config_diff".format(name),
        file1 = golden_config_h,
        file2 = ":{}_bazel_config_h".format(name),
        size = "small",
        tags = tags,
    )
    all_tests.append(":{}_bazel_config_diff".format(name))

    # --- 4. Diff test for subst_*.h (single target with select() for file1) ---
    diff_test(
        name = "{}_bazel_subst_diff".format(name),
        file1 = golden_subst_h,
        file2 = ":{}_bazel_subst_h".format(name),
        size = "small",
        tags = tags,
    )
    all_tests.append(":{}_bazel_subst_diff".format(name))

    # --- 5. Compile Test ---
    cc_test(
        name = name + "_compile",
        srcs = [
            test_c,
            ":{}_bazel_config_h".format(name),
            ":{}_bazel_subst_h".format(name),
        ],
        target_compatible_with = unix_compatible,
        tags = tags,
    )
    all_tests.append(":{}_compile".format(name))

    # --- 6. Test Suite ---
    native.test_suite(
        name = name,
        tests = all_tests,
        tags = tags,
        **kwargs
    )
