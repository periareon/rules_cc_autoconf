"""Macros for testing gnulib m4 ports against GNU autoconf.

This module provides:
- gnu_gnulib_diff_test_suite: Complete test suite for a gnulib module
"""

load("@rules_cc//cc:cc_test.bzl", "cc_test")
load("//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("//autoconf/tests/gnu:conformance.bzl", "gnu_autoconf_conformance_test")

def gnu_gnulib_diff_test_suite(
        *,
        name,
        configure_ac,
        m4_files,
        config_h_in,
        subst_h_in,
        bazel_autoconf_target,
        test_c,
        aux_files = [],
        known_divergences = {},
        defaults_exclude = [],
        size = "medium",
        tags = [],
        **kwargs):
    """Complete test suite for a gnulib module.

    This creates:
    1. `{name}_gnu_conformance`: runs GNU aclocal/autoconf/configure with the
       Bazel C++ toolchain's compiler and flags and requires the
       Bazel-generated headers to match byte for byte. The GNU and Bazel
       headers, config.log, tool output and environment are left in the
       test's undeclared outputs directory for review. There are no
       checked-in expected outputs; GNU autoconf produces them on the machine
       running the test.
    2. `{name}_compile`: the Bazel-generated headers compile with `test_c`.
    3. `{name}`: a test suite of the above.

    Args:
        name (str): Name of the test suite (typically "{module}_test")
        configure_ac (Label): The configure.ac file specific to this m4 module
        m4_files (list[Label]): ALL m4 files needed to run autoconf (including deps)
        config_h_in (Label): Template for AC_DEFINE (#undef patterns)
        subst_h_in (Label): Template for AC_SUBST (@FOO@ patterns)
        bazel_autoconf_target (Label): The autoconf target from //gnulib/m4/{name}
        test_c (Label): C file to compile with the generated headers
        aux_files (list[Label]): Auxiliary files (e.g., config.rpath) to copy to work directory root
        known_divergences (dict[str, str]): Variable name -> reason for intentional
            differences between the Bazel port and upstream m4. Masked in the
            GNU comparison and always reported in the test log.
        defaults_exclude (list[Label]): Toolchain `defaults` targets (e.g.
            `//gnulib/m4/unistd_h:defaults`) left out of the Bazel subst.h
            rendering. Use it when the module's m4 graph never AC_REQUIREs the
            corresponding gl_*_H_DEFAULTS, so GNU leaves those substitution
            variables as literal `@VAR@` placeholders.
        size (str): Test size (default: "medium")
        tags (list[str]): Test tags
        **kwargs: Additional arguments
    """

    unix_only = select({
        "@platforms//os:windows": ["@platforms//:incompatible"],
        "//conditions:default": [],
    })

    # --- 1. Bazel autoconf_hdr targets ---
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
        defaults_exclude = defaults_exclude,
        mode = "subst",
    )

    # --- 2. GNU conformance (the oracle) ---
    gnu_autoconf_conformance_test(
        name = name + "_gnu_conformance",
        configure_ac = configure_ac,
        m4_files = m4_files,
        config_h_in = config_h_in,
        subst_h_in = subst_h_in,
        aux_files = aux_files,
        bazel_config_h = ":{}_bazel_config_h".format(name),
        bazel_subst_h = ":{}_bazel_subst_h".format(name),
        autoconf_target = bazel_autoconf_target,
        known_divergences = known_divergences,
        verify_variables = True,
        size = size,
        tags = tags,
    )

    # --- 3. Compile Test ---
    cc_test(
        name = name + "_compile",
        srcs = [
            test_c,
            ":{}_bazel_config_h".format(name),
            ":{}_bazel_subst_h".format(name),
        ],
        target_compatible_with = unix_only,
        tags = tags,
    )

    # --- 4. Test Suite ---
    native.test_suite(
        name = name,
        tests = [
            ":{}_gnu_conformance".format(name),
            ":{}_compile".format(name),
        ],
        tags = tags,
        **kwargs
    )
