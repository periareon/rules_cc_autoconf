"""diff_test"""

load("@rules_venv//python:py_test.bzl", "py_test")

def diff_test(*, name, file1, file2, **kwargs):
    """A replacement for `bazel_skylib` `diff_test` that shows diffs on all platforms.

    Args:
        name (str): The name of the target.
        file1 (Label): The left file to test.
        file2 (Label): The right file to test.
        **kwargs (dict): Additional keyword arguments.
    """

    py_test(
        name = name,
        srcs = [Label("//autoconf/private/tests:diff_tester.py")],
        main = Label("//autoconf/private/tests:diff_tester.py"),
        data = [file1, file2],
        env = {
            "TEST_FILE_1": "$(rlocationpath {})".format(file1),
            "TEST_FILE_2": "$(rlocationpath {})".format(file2),
        },
        deps = [Label("//autoconf/private/tests:diff_tester")],
        **kwargs
    )
