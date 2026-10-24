"""Every check is encoded with LF line endings, whatever the BUILD file's checkout spelled."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("//autoconf:checks.bzl", "checks")

def _crlf_code_test_impl(ctx):
    env = unittest.begin(ctx)
    check = json.decode(checks.AC_TRY_COMPILE(
        name = "gl_cv_x",
        code = "#if \\\r\n    (A)\r\n#endif\r\nint main(void) { return 0; }\r\n",
    ))
    asserts.equals(env, "#if \\\n    (A)\n#endif\nint main(void) { return 0; }\n", check["code"])
    return unittest.end(env)

crlf_code_test = unittest.make(_crlf_code_test_impl)

def _crlf_in_lists_test_impl(ctx):
    env = unittest.begin(ctx)

    # A header check folds its includes into `code`; list fields (`copts` here) are strings too.
    check = json.decode(checks.AC_CHECK_HEADER("foo.h", includes = ["a.h\r\n", "b.h"], copts = ["-DX=1\r\n"]))
    asserts.true(env, "\r" not in check["code"], "includes folded into code keep CRLF: " + check["code"])
    asserts.equals(env, ["-DX=1\n"], check["copts"])
    return unittest.end(env)

crlf_in_lists_test = unittest.make(_crlf_in_lists_test_impl)

def _lone_cr_kept_test_impl(ctx):
    env = unittest.begin(ctx)
    check = json.decode(checks.AC_TRY_COMPILE(name = "gl_cv_y", code = "char c = '\\r';\rint main(void) { return 0; }\n"))
    asserts.equals(env, "char c = '\\r';\rint main(void) { return 0; }\n", check["code"])
    return unittest.end(env)

lone_cr_kept_test = unittest.make(_lone_cr_kept_test_impl)

def _lf_unchanged_test_impl(ctx):
    env = unittest.begin(ctx)
    code = "#include <stddef.h>\nint main(void) { return 0; }\n"
    asserts.equals(env, code, json.decode(checks.AC_TRY_COMPILE(name = "gl_cv_z", code = code))["code"])
    return unittest.end(env)

lf_unchanged_test = unittest.make(_lf_unchanged_test_impl)

def line_endings_test_suite(name):
    """The check encoder's line-ending canonicalization.

    Args:
        name: The suite's name.
    """
    unittest.suite(
        name,
        crlf_code_test,
        crlf_in_lists_test,
        lone_cr_kept_test,
        lf_unchanged_test,
    )
