"""Source-generation tests for AC_LANG_CALL([main])."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("//autoconf:checks.bzl", "checks")

_C_MAIN_CODE = "int main(void) { return main(); }\n"
_CPP_MAIN_CODE = """\
namespace conftest {
extern "C" int main();
}
int main(void) { return conftest::main(); }
"""
_CUSTOM_CODE = "int main(void) { return 23; }"

def _spec(check):
    return json.decode(check)

def _main_check_test_impl(ctx):
    env = unittest.begin(ctx)

    lib = _spec(checks.AC_CHECK_LIB(
        "m",
        "main",
        define = "HAVE_LIBM",
    ))
    search = _spec(checks.AC_SEARCH_LIBS("main", ["missing"]))
    cpp_lib = _spec(checks.AC_CHECK_LIB("m", "main", language = "cpp"))
    cpp_search = _spec(checks.AC_SEARCH_LIBS("main", ["missing"], language = "cpp"))

    asserts.equals(env, "ac_cv_lib_m_main", lib["name"])
    asserts.equals(env, "m", lib["library"])
    asserts.equals(env, "HAVE_LIBM", lib["define"])
    asserts.equals(env, _C_MAIN_CODE, lib["code"])
    asserts.equals(env, _C_MAIN_CODE, search["code"])
    asserts.equals(env, _CPP_MAIN_CODE, cpp_lib["code"])
    asserts.equals(env, _CPP_MAIN_CODE, cpp_search["code"])

    asserts.equals(env, _CUSTOM_CODE, _spec(checks.AC_CHECK_LIB(
        "m",
        "main",
        code = _CUSTOM_CODE,
    ))["code"])
    asserts.equals(env, "", _spec(checks.AC_CHECK_LIB(
        "m",
        "main",
        code = "",
    ))["code"])
    asserts.equals(env, _CUSTOM_CODE, _spec(checks.AC_SEARCH_LIBS(
        "main",
        ["missing"],
        code = _CUSTOM_CODE,
    ))["code"])
    asserts.equals(env, "", _spec(checks.AC_SEARCH_LIBS(
        "main",
        ["missing"],
        code = "",
    ))["code"])

    return unittest.end(env)

main_check_test = unittest.make(_main_check_test_impl)
