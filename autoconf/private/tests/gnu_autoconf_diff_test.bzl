"""Macro for testing GNU autoconf configure.ac files against golden outputs."""

load("@rules_venv//python:py_test.bzl", "py_test")

def gnu_autoconf_diff_test(
        *,
        name,
        configure_ac,
        golden_file,
        template = None,
        **kwargs):
    """Test macro that runs GNU autoconf and compares output to a golden file.

    Args:
        name (str): Name of the test target
        configure_ac (Label): The configure.ac file to test
        golden_file (Label): The golden config.h file to compare against
        template (Label): Optional template file (e.g., config.h.in) for AC_CONFIG_HEADERS
        **kwargs: Additional arguments to pass to py_test
    """
    env = {
        "TEST_SRC_CONFIGURE_AC": "$(rlocationpath {})".format(configure_ac),
        "TEST_SRC_GOLDEN_FILE": "$(rlocationpath {})".format(golden_file),
    }

    data = [configure_ac, golden_file]

    if template:
        data.append(template)
        env["TEST_SRC_TEMPLATE_FILE"] = "$(rlocationpath {})".format(template)

    py_test(
        name = name,
        srcs = [Label("//autoconf/private/tests:gnu_autoconf_diff_tester.py")],
        main = Label("//autoconf/private/tests:gnu_autoconf_diff_tester.py"),
        data = data,
        env = env,
        deps = [Label("//autoconf/private/tests:gnu_autoconf_diff_tester")],
        **kwargs
    )
