"""defaults_test_suite

Test suite for verifying autoconf_hdr defaults functionality.
"""

load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("//autoconf:autoconf_toolchain.bzl", "autoconf_toolchain")
load("//autoconf:checks.bzl", "checks")
load("//autoconf:package_info.bzl", "package_info")
load("//autoconf/tests:diff_test.bzl", "diff_test")

def _toolchain_transition_impl(_settings, attr):
    """Transition that sets extra_toolchains to use the test toolchain."""
    return {
        "//command_line_option:extra_toolchains": [attr.toolchain],
    }

_toolchain_transition = transition(
    implementation = _toolchain_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:extra_toolchains"],
)

def _autoconf_hdr_with_toolchain_impl(ctx):
    """Wrapper rule that transitions autoconf_hdr to use a specific toolchain."""

    # Forward all attributes to autoconf_hdr
    autoconf_hdr_rule = ctx.attr.autoconf_hdr[0]

    return [
        DefaultInfo(
            files = depset([autoconf_hdr_rule[DefaultInfo].files.to_list()[0]]),
        ),
    ]

_autoconf_hdr_with_toolchain = rule(
    implementation = _autoconf_hdr_with_toolchain_impl,
    attrs = {
        "autoconf_hdr": attr.label(
            cfg = _toolchain_transition,
            mandatory = True,
        ),
        "toolchain": attr.string(
            mandatory = True,
            doc = "Label of the toolchain to use (e.g., '//autoconf/tests/core/defaults:test_toolchain')",
        ),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)

def defaults_test_suite(*, name, **kwargs):
    """Test suite for autoconf_hdr defaults functionality.

    Args:
        name (str): The name of the test suite.
        **kwargs (dict): Additional keyword arguments.
    """
    tests = []

    # Package info for test autoconf targets
    package_info(
        name = "package_info",
        package_name = "test_defaults",
        package_version = "1.0.0",
    )

    # ============================================================================
    # Test defaults: Two separate defaults targets for testing include/exclude
    # ============================================================================

    # Defaults set 1: module_a defaults
    # Tagged as manual so they're not built unless explicitly requested
    autoconf(
        name = "module_a_defaults",
        checks = [
            checks.AC_DEFINE("DEFAULT_FROM_A", 1),
            checks.AC_DEFINE("DEFAULT_FROM_A_STRING", "value_a"),
            checks.AC_SUBST("SUBST_FROM_A", "a_value"),
            checks.AC_SUBST("SUBST_FROM_A_NUM", 42),
        ],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # Defaults set 2: module_b defaults
    # Tagged as manual so they're not built unless explicitly requested
    autoconf(
        name = "module_b_defaults",
        checks = [
            checks.AC_DEFINE("DEFAULT_FROM_B", 1),
            checks.AC_DEFINE("DEFAULT_FROM_B_STRING", "value_b"),
            checks.AC_SUBST("SUBST_FROM_B", "b_value"),
            checks.AC_SUBST("SUBST_FROM_B_NUM", 99),
        ],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # The common autoconf values
    autoconf(
        name = "test_autoconf",
        checks = [
            # This will override DEFAULT_FROM_A if defaults are merged
            checks.AC_DEFINE("DEFAULT_FROM_A", 2),
            # This is unique to the test target
            checks.AC_DEFINE("TARGET_SPECIFIC", 100),
            checks.AC_SUBST("SUBST_TARGET_SPECIFIC", "target_value"),
        ],
        deps = [":package_info"],
    )

    # Test toolchain with both defaults
    # Note: toolchain() declaration must be in BUILD.bazel, not in a .bzl file
    # Tagged as manual so it's not built unless explicitly requested
    autoconf_toolchain(
        name = "test_toolchain_impl",
        deps = [
            ":module_a_defaults",
            ":module_b_defaults",
        ],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # Test toolchain for defaults testing
    # The toolchain_impl is created by defaults_test_suite macro
    # Tagged as manual so it's not built unless explicitly requested
    native.toolchain(
        name = "test_toolchain",
        toolchain = ":test_toolchain_impl",
        toolchain_type = "//autoconf:toolchain_type",
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # ============================================================================
    # Test Case 1: defines mode, defaults OFF
    # ============================================================================

    write_file(
        name = "config_defines_no_defaults_in",
        out = "config_defines_no_defaults.h.in",
        content = [
            "/* config.h.in - defines mode, no defaults */",
            "#undef DEFAULT_FROM_A",
            "#undef DEFAULT_FROM_B",
            "#undef TARGET_SPECIFIC",
            "",
        ],
    )

    autoconf_hdr(
        name = "config_defines_no_defaults",
        out = "config_defines_no_defaults.h",
        mode = "defines",
        template = ":config_defines_no_defaults_in",
        deps = [":test_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_config_defines_no_defaults",
        out = "golden_config_defines_no_defaults.h.in",
        content = [
            "/* config.h.in - defines mode, no defaults */",
            "#define DEFAULT_FROM_A 2",
            "/* #undef DEFAULT_FROM_B */",
            "#define TARGET_SPECIFIC 100",
            "",
        ],
    )

    diff_test(
        name = "test_defines_no_defaults",
        file1 = ":golden_config_defines_no_defaults",
        file2 = ":config_defines_no_defaults",
    )
    tests.append(":test_defines_no_defaults")

    # ============================================================================
    # Test Case 2: defines mode, defaults ON
    # ============================================================================

    write_file(
        name = "config_defines_with_defaults_in",
        out = "config_defines_with_defaults.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults */",
            "#undef DEFAULT_FROM_A",
            "#undef DEFAULT_FROM_B",
            "#undef TARGET_SPECIFIC",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "config_defines_with_defaults_wrapper",
        autoconf_hdr = ":config_defines_with_defaults_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "config_defines_with_defaults_hdr",
        out = "config_defines_with_defaults.h",
        mode = "defines",
        template = ":config_defines_with_defaults_in",
        deps = [":test_autoconf"],
        defaults = True,
        tags = ["manual"],
    )

    write_file(
        name = "golden_config_defines_with_defaults",
        out = "golden_config_defines_with_defaults.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults */",
            "#define DEFAULT_FROM_A 2",
            "#define DEFAULT_FROM_B 1",
            "#define TARGET_SPECIFIC 100",
            "",
        ],
    )

    diff_test(
        name = "test_defines_with_defaults",
        file1 = ":golden_config_defines_with_defaults",
        file2 = ":config_defines_with_defaults_wrapper",
    )
    tests.append(":test_defines_with_defaults")

    # ============================================================================
    # Test Case 3: subst mode, defaults OFF
    # ============================================================================

    write_file(
        name = "subst_no_defaults_in",
        out = "subst_no_defaults.h.in",
        content = [
            "/* subst.h.in - subst mode, no defaults */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",
            "#define SUBST_TARGET_SPECIFIC \"@SUBST_TARGET_SPECIFIC@\"",
            "",
        ],
    )

    autoconf_hdr(
        name = "subst_no_defaults",
        out = "subst_no_defaults.h",
        mode = "subst",
        template = ":subst_no_defaults_in",
        deps = [":test_autoconf"],
        defaults = False,
        tags = ["manual"],
    )

    write_file(
        name = "golden_subst_no_defaults",
        out = "golden_subst_no_defaults.h.in",
        content = [
            "/* subst.h.in - subst mode, no defaults */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",  # Not replaced (no defaults)
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",  # Not replaced (no defaults)
            "#define SUBST_TARGET_SPECIFIC \"target_value\"",  # From target
            "",
        ],
    )

    diff_test(
        name = "test_subst_no_defaults",
        file1 = ":golden_subst_no_defaults",
        file2 = ":subst_no_defaults",
    )
    tests.append(":test_subst_no_defaults")

    # ============================================================================
    # Test Case 4: subst mode, defaults ON
    # ============================================================================

    write_file(
        name = "subst_with_defaults_in",
        out = "subst_with_defaults.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",
            "#define SUBST_TARGET_SPECIFIC \"@SUBST_TARGET_SPECIFIC@\"",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "subst_with_defaults_wrapper",
        autoconf_hdr = ":subst_with_defaults_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "subst_with_defaults_hdr",
        out = "subst_with_defaults.h",
        mode = "subst",
        template = ":subst_with_defaults_in",
        deps = [":test_autoconf"],
        defaults = True,
        tags = ["manual"],
    )

    write_file(
        name = "golden_subst_with_defaults",
        out = "golden_subst_with_defaults.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults */",
            "#define SUBST_FROM_A \"a_value\"",  # From defaults
            "#define SUBST_FROM_B \"b_value\"",  # From defaults
            "#define SUBST_TARGET_SPECIFIC \"target_value\"",  # From target
            "",
        ],
    )

    diff_test(
        name = "test_subst_with_defaults",
        file1 = ":golden_subst_with_defaults",
        file2 = ":subst_with_defaults_wrapper",
    )
    tests.append(":test_subst_with_defaults")

    # ============================================================================
    # Test Case 5: subst mode, defaults ON, with defaults_include (only module_a)
    # ============================================================================

    write_file(
        name = "subst_with_include_in",
        out = "subst_with_include.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults_include (module_a only) */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "subst_with_include_wrapper",
        autoconf_hdr = ":subst_with_include_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "subst_with_include_hdr",
        out = "subst_with_include.h",
        mode = "subst",
        template = ":subst_with_include_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_include = [":module_a_defaults"],
        tags = ["manual"],
    )

    write_file(
        name = "golden_subst_with_include",
        out = "golden_subst_with_include.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults_include (module_a only) */",
            "#define SUBST_FROM_A \"a_value\"",  # From module_a (included)
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",  # Not replaced (module_b excluded)
            "",
        ],
    )

    diff_test(
        name = "test_subst_with_include",
        file1 = ":golden_subst_with_include",
        file2 = ":subst_with_include_wrapper",
    )
    tests.append(":test_subst_with_include")

    # ============================================================================
    # Test Case 6: subst mode, defaults ON, with defaults_exclude (exclude module_b)
    # ============================================================================

    write_file(
        name = "subst_with_exclude_in",
        out = "subst_with_exclude.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults_exclude (exclude module_b) */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "subst_with_exclude_wrapper",
        autoconf_hdr = ":subst_with_exclude_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "subst_with_exclude_hdr",
        out = "subst_with_exclude.h",
        mode = "subst",
        template = ":subst_with_exclude_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_exclude = [":module_b_defaults"],
        tags = ["manual"],
    )

    write_file(
        name = "golden_subst_with_exclude",
        out = "golden_subst_with_exclude.h.in",
        content = [
            "/* subst.h.in - subst mode, with defaults_exclude (exclude module_b) */",
            "#define SUBST_FROM_A \"a_value\"",  # From module_a (not excluded)
            "#define SUBST_FROM_B \"@SUBST_FROM_B@\"",  # Not replaced (module_b excluded)
            "",
        ],
    )

    diff_test(
        name = "test_subst_with_exclude",
        file1 = ":golden_subst_with_exclude",
        file2 = ":subst_with_exclude_wrapper",
    )
    tests.append(":test_subst_with_exclude")

    # ============================================================================
    # Test Case 7: defines mode, defaults ON, with defaults_include
    # ============================================================================

    write_file(
        name = "config_defines_with_include_in",
        out = "config_defines_with_include.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults_include (module_a only) */",
            "#undef DEFAULT_FROM_A",
            "#undef DEFAULT_FROM_B",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "config_defines_with_include_wrapper",
        autoconf_hdr = ":config_defines_with_include_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "config_defines_with_include_hdr",
        out = "config_defines_with_include.h",
        mode = "defines",
        template = ":config_defines_with_include_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_include = [":module_a_defaults"],
        tags = ["manual"],
    )

    write_file(
        name = "golden_config_defines_with_include",
        out = "golden_config_defines_with_include.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults_include (module_a only) */",
            "#define DEFAULT_FROM_A 2",  # From target (overrides default, but target sets it)
            "/* #undef DEFAULT_FROM_B */",  # Not included (module_b excluded)
            "",
        ],
    )

    diff_test(
        name = "test_defines_with_include",
        file1 = ":golden_config_defines_with_include",
        file2 = ":config_defines_with_include_wrapper",
    )
    tests.append(":test_defines_with_include")

    # ============================================================================
    # Test Case 8: defines mode, defaults ON, with defaults_exclude
    # ============================================================================

    write_file(
        name = "config_defines_with_exclude_in",
        out = "config_defines_with_exclude.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults_exclude (exclude module_b) */",
            "#undef DEFAULT_FROM_A",
            "#undef DEFAULT_FROM_B",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "config_defines_with_exclude_wrapper",
        autoconf_hdr = ":config_defines_with_exclude_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain",
    )

    autoconf_hdr(
        name = "config_defines_with_exclude_hdr",
        out = "config_defines_with_exclude.h",
        mode = "defines",
        template = ":config_defines_with_exclude_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_exclude = [":module_b_defaults"],
        tags = ["manual"],
    )

    write_file(
        name = "golden_config_defines_with_exclude",
        out = "golden_config_defines_with_exclude.h.in",
        content = [
            "/* config.h.in - defines mode, with defaults_exclude (exclude module_b) */",
            "#define DEFAULT_FROM_A 2",  # From target (overrides default)
            "/* #undef DEFAULT_FROM_B */",  # Not included (module_b excluded)
            "",
        ],
    )

    diff_test(
        name = "test_defines_with_exclude",
        file1 = ":golden_config_defines_with_exclude",
        file2 = ":config_defines_with_exclude_wrapper",
    )
    tests.append(":test_defines_with_exclude")

    # ============================================================================
    # Test Case 9: Transitive dependencies - module_c depends on module_a
    # ============================================================================

    # Transitive dependency: module_c depends on module_a
    # This tests that defaults_by_label includes transitive results
    autoconf(
        name = "module_c_transitive_dep",
        checks = [
            checks.AC_DEFINE("DEFAULT_FROM_C", 1),
            checks.AC_SUBST("SUBST_FROM_C", "c_value"),
        ],
        deps = [":module_a_defaults"],  # Transitive dependency
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # Test toolchain with module_c (which transitively includes module_a)
    autoconf_toolchain(
        name = "test_toolchain_transitive_impl",
        deps = [
            ":module_c_transitive_dep",  # This should include module_a transitively
        ],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    native.toolchain(
        name = "test_toolchain_transitive",
        toolchain = ":test_toolchain_transitive_impl",
        toolchain_type = "//autoconf:toolchain_type",
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    # Test that filtering by module_c includes transitive results from module_a
    write_file(
        name = "subst_transitive_in",
        out = "subst_transitive.h.in",
        content = [
            "/* subst.h.in - transitive dependencies test */",
            "#define SUBST_FROM_A \"@SUBST_FROM_A@\"",  # From transitive dep (module_a)
            "#define SUBST_FROM_C \"@SUBST_FROM_C@\"",  # From direct dep (module_c)
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "subst_transitive_wrapper",
        autoconf_hdr = ":subst_transitive_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain_transitive",
    )

    autoconf_hdr(
        name = "subst_transitive_hdr",
        out = "subst_transitive.h",
        mode = "subst",
        template = ":subst_transitive_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_include = [":module_c_transitive_dep"],  # Should include transitive results from module_a
        tags = ["manual"],
    )

    write_file(
        name = "golden_subst_transitive",
        out = "golden_subst_transitive.h.in",
        content = [
            "/* subst.h.in - transitive dependencies test */",
            "#define SUBST_FROM_A \"a_value\"",  # From module_a (transitive via module_c)
            "#define SUBST_FROM_C \"c_value\"",  # From module_c (direct)
            "",
        ],
    )

    diff_test(
        name = "test_subst_transitive",
        file1 = ":golden_subst_transitive",
        file2 = ":subst_transitive_wrapper",
    )
    tests.append(":test_subst_transitive")

    # Test that defines also work with transitive dependencies
    write_file(
        name = "config_defines_transitive_in",
        out = "config_defines_transitive.h.in",
        content = [
            "/* config.h.in - transitive dependencies test */",
            "#undef DEFAULT_FROM_A",
            "#undef DEFAULT_FROM_C",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "config_defines_transitive_wrapper",
        autoconf_hdr = ":config_defines_transitive_hdr",
        toolchain = "//autoconf/tests/core/defaults:test_toolchain_transitive",
    )

    autoconf_hdr(
        name = "config_defines_transitive_hdr",
        out = "config_defines_transitive.h",
        mode = "defines",
        template = ":config_defines_transitive_in",
        deps = [":test_autoconf"],
        defaults = True,
        defaults_include = [":module_c_transitive_dep"],  # Should include transitive results from module_a
        tags = ["manual"],
    )

    write_file(
        name = "golden_config_defines_transitive",
        out = "golden_config_defines_transitive.h.in",
        content = [
            "/* config.h.in - transitive dependencies test */",
            "#define DEFAULT_FROM_A 2",  # From target (overrides default from module_a transitive)
            "#define DEFAULT_FROM_C 1",  # From module_c (direct)
            "",
        ],
    )

    diff_test(
        name = "test_defines_transitive",
        file1 = ":golden_config_defines_transitive",
        file2 = ":config_defines_transitive_wrapper",
    )
    tests.append(":test_defines_transitive")

    # ============================================================================
    # Test Suite
    # ============================================================================

    native.test_suite(
        name = name,
        tests = tests,
        **kwargs
    )
