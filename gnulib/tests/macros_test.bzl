"""Tests for translated gnulib helper macros."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("//gnulib:macros.bzl", gl_macros = "macros")

def _conditional_next_headers_test_impl(ctx):
    env = unittest.begin(ctx)
    checks = gl_macros.GL_NEXT_HEADERS(
        ["errno.h"],
        condition = "!HAVE_COMPLETE_ERRNO_H",
    )

    asserts.equals(env, 2, len(checks))
    for encoded_check in checks:
        check = json.decode(encoded_check)
        asserts.equals(env, "GL_NEXT_HEADER", check["type"])
        asserts.equals(env, ["!HAVE_COMPLETE_ERRNO_H"], check["requires"])
        asserts.equals(env, ["INCLUDE_NEXT"], check["input_deps"])

    return unittest.end(env)

conditional_next_headers_test = unittest.make(_conditional_next_headers_test_impl)
