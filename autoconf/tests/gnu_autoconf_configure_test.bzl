"""gnu_autoconf_configure_test"""

load("@rules_venv//python:py_test.bzl", "py_test")

def gnu_autoconf_configure_test(
        *,
        name,
        configure_ac,
        config_h_in,
        golden_config_h,
        subst_h_in = None,
        golden_subst_h = None,
        verify_variables = False,
        m4_files = [],
        aux_files = [],
        tags = [],
        target_compatible_with = None,
        **kwargs):
    """Single test that runs GNU autoconf and compares output with golden files.

    Args:
        name (str): Name of the test target
        configure_ac (Label): The configure.ac file specific to this m4 module
        config_h_in (Label): Template for AC_DEFINE (#undef patterns)
        golden_config_h (Label): Expected config.h output
        subst_h_in (Label): Template for AC_SUBST (@FOO@ patterns)
        golden_subst_h (Label): Expected gnulib_*.h output
        verify_variables (bool): If True, template files will be required to have all autoconf produced variables.
        m4_files (list[Label]): ALL m4 files needed to run autoconf (including deps)
        aux_files (list[Label]): Auxiliary files (e.g., config.rpath) to copy to work directory root
        tags (list[str]): Test tags
        target_compatible_with: Platform constraint (default: Linux and macOS)
        **kwargs: Additional arguments
    """
    env = {
        "TEST_CONFIGURE_AC": "$(rlocationpath {})".format(configure_ac),
        "TEST_CONFIG_H_IN": "$(rlocationpath {})".format(config_h_in),
        "TEST_GOLDEN_CONFIG_H": "$(rlocationpath {})".format(golden_config_h),

        "TEST_M4_FILES": " ".join([
            "$(rlocationpaths {})".format(m4)
            for m4 in m4_files
        ]),
    }

    if verify_variables:
        env["VERIFY_VARIABLES"] = "1"

    # Add aux_files to environment if provided
    if aux_files:
        env["TEST_AUX_FILES"] = " ".join([
            "$(rlocationpaths {})".format(aux)
            for aux in aux_files
        ])

    data = [
        configure_ac,
        config_h_in,
        golden_config_h,
    ] + m4_files + aux_files

    if subst_h_in:
        env.update({
            "TEST_GOLDEN_SUBST_H": "$(rlocationpath {})".format(golden_subst_h),
            "TEST_SUBST_H_IN": "$(rlocationpath {})".format(subst_h_in),
        })

        data.extend([
            subst_h_in,
            golden_subst_h,
        ])

    # Platform-specific test selection (no Windows support for autoconf)
    if target_compatible_with == None:
        target_compatible_with = select({
            "@platforms//os:linux": [],
            "@platforms//os:macos": [],
            "//conditions:default": ["@platforms//:incompatible"],
        })

    py_test(
        name = name,
        srcs = [Label("//autoconf/tests:gnu_autoconf_configure_tester.py")],
        main = Label("//autoconf/tests:gnu_autoconf_configure_tester.py"),
        data = data,
        env = env,
        deps = [Label("//autoconf/tests:gnu_autoconf_configure_tester")],
        tags = tags + ["requires-autoconf"],
        target_compatible_with = target_compatible_with,
        **kwargs
    )
