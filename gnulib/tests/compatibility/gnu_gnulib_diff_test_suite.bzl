"""Macros for testing gnulib m4 files against golden outputs.

This module provides:
- gnu_gnulib_diff_test: Single test comparing GNU autoconf output with golden files
- gnu_gnulib_diff_test_suite: Complete test suite for a gnulib module
"""

load("@rules_cc//cc:cc_test.bzl", "cc_test")
load("//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("//autoconf/tests:diff_test.bzl", "diff_test")
load("//autoconf/tests:gnu_autoconf_configure_test.bzl", "gnu_autoconf_configure_test")

# Platform constraint mappings
_PLATFORM_CONSTRAINTS = {
    "linux": select({
        "@platforms//os:linux": [],
        "//conditions:default": ["@platforms//:incompatible"],
    }),
    "macos": select({
        "@platforms//os:macos": [],
        "//conditions:default": ["@platforms//:incompatible"],
    }),
    "unix": select({
        "@platforms//os:windows": ["@platforms//:incompatible"],
        "//conditions:default": [],
    }),
    "windows": select({
        "@platforms//os:windows": [],
        "//conditions:default": ["@platforms//:incompatible"],
    }),
}

def _is_dict(value):
    """Check if value is a dict (platform-specific golden files)."""
    return type(value) == "dict"

def _get_golden_entries(golden_param):
    """Convert golden parameter to list of (platform, label) tuples.

    Args:
        golden_param: Either a Label string or a dict with platform keys

    Returns:
        List of (platform_name, label, target_compatible_with) tuples.
        For simple labels, returns [("", label, None)].
        For dicts, returns [(platform, label, constraint), ...] for each entry.
    """
    if _is_dict(golden_param):
        entries = []
        for platform, label in golden_param.items():
            if platform not in _PLATFORM_CONSTRAINTS:
                fail("Unknown platform '{}'. Valid platforms: {}".format(
                    platform,
                    ", ".join(_PLATFORM_CONSTRAINTS.keys()),
                ))
            entries.append((platform, label, _PLATFORM_CONSTRAINTS[platform]))
        return entries
    else:
        # Simple label - no platform suffix, no constraint
        return [("", golden_param, None)]

def _get_golden_for_platform(golden_dict, platform):
    """Get the golden file for a specific platform with fallback logic.

    Fallback order:
    - linux: linux -> unix -> _default
    - macos: macos -> unix -> _default
    - windows: windows -> _default
    - unix: unix -> _default

    Args:
        golden_dict: Dict mapping platform keys to labels
        platform: The platform to get golden for

    Returns:
        The label for the golden file, or None if not found.
    """
    if platform in golden_dict:
        return golden_dict[platform]

    # Unix fallback for linux/macos
    if platform in ("linux", "macos") and "unix" in golden_dict:
        return golden_dict["unix"]

    # Default fallback
    return golden_dict.get("_default")

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

    When using dict golden files, separate test targets are created for each platform
    with appropriate target_compatible_with constraints.

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

    # Get platform entries for config and gnulib golden files
    config_entries = _get_golden_entries(golden_config_h)
    gnulib_entries = _get_golden_entries(golden_subst_h)

    # Track all test names for the test suite
    all_tests = []

    # --- 1. GNU Autoconf Tests ---
    # For gnu_autoconf tests, we need matching config and gnulib goldens
    # If both are simple labels, create one test
    # If either is a dict, create platform-specific tests for matching platforms
    if not _is_dict(golden_config_h) and not _is_dict(golden_subst_h):
        # Simple case: single golden files for both
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
            tags = tags,
            **kwargs
        )
        all_tests.append(":{}_gnu_autoconf".format(name))
    else:
        # Platform-specific: find matching platforms between config and gnulib
        config_dict = golden_config_h if _is_dict(golden_config_h) else {"_default": golden_config_h}
        gnulib_dict = golden_subst_h if _is_dict(golden_subst_h) else {"_default": golden_subst_h}

        # Determine which concrete platforms to create tests for
        # We want to create tests for linux, macos (and windows when supported)
        # not for abstract platforms like "unix"
        concrete_platforms = []
        all_keys = set(list(config_dict.keys()) + list(gnulib_dict.keys()))
        if "linux" in all_keys or "unix" in all_keys:
            concrete_platforms.append("linux")
        if "macos" in all_keys or "unix" in all_keys:
            concrete_platforms.append("macos")
        if "windows" in all_keys:
            concrete_platforms.append("windows")

        for platform in concrete_platforms:
            config_golden = _get_golden_for_platform(config_dict, platform)
            gnulib_golden = _get_golden_for_platform(gnulib_dict, platform)

            if config_golden and gnulib_golden:
                test_name = "{}_gnu_autoconf_{}".format(name, platform)

                # Use platform-specific constraint so test only runs on correct platform
                gnu_autoconf_configure_test(
                    name = test_name,
                    configure_ac = configure_ac,
                    m4_files = m4_files,
                    config_h_in = config_h_in,
                    subst_h_in = subst_h_in,
                    golden_config_h = config_golden,
                    golden_subst_h = gnulib_golden,
                    aux_files = aux_files,
                    verify_variables = True,
                    size = size,
                    tags = tags,
                    target_compatible_with = _PLATFORM_CONSTRAINTS.get(platform),
                    **kwargs
                )
                all_tests.append(":{}".format(test_name))

    # --- 2. Bazel autoconf_hdr targets ---
    # Output names match what test_c files include: config.h and subst.h
    # Use defaults = False to test module output in isolation without toolchain defaults
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

    # --- 3. Diff tests for config.h ---
    for platform, golden_label, constraint in config_entries:
        suffix = "_{}".format(platform) if platform else ""
        test_name = "{}_bazel_config_diff{}".format(name, suffix)

        diff_kwargs = {
            "file1": golden_label,
            "file2": ":{}_bazel_config_h".format(name),
            "name": test_name,
            "size": "small",
            "tags": tags,
        }
        if constraint != None:
            diff_kwargs["target_compatible_with"] = constraint

        diff_test(**diff_kwargs)
        all_tests.append(":{}".format(test_name))

    # --- 4. Diff tests for subst_*.h ---
    for platform, golden_label, constraint in gnulib_entries:
        suffix = "_{}".format(platform) if platform else ""
        test_name = "{}_bazel_subst_diff{}".format(name, suffix)

        diff_kwargs = {
            "file1": golden_label,
            "file2": ":{}_bazel_subst_h".format(name),
            "name": test_name,
            "size": "small",
            "tags": tags,
        }
        if constraint != None:
            diff_kwargs["target_compatible_with"] = constraint

        diff_test(**diff_kwargs)
        all_tests.append(":{}".format(test_name))

    # --- 5. Compile Test ---
    # Verify that the Bazel-generated headers compile correctly.
    # Uses the autoconf_hdr outputs (config.h and subst.h), NOT the golden files.
    # Golden files are only for diff testing.
    # test_c files use repo-relative includes (e.g., "gnulib/tests/compatibility/foo/config.h")
    # which Bazel resolves automatically when generated headers are in srcs.
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
    )
