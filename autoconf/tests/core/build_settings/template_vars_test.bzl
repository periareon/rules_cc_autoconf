"""Analysis test for `template_vars` naming a check's result.

Only a build setting is known at analysis time; a check's result is an action
output, so naming one fails the analysis with a message that says so rather
than rendering an empty `$(VAR)`.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

def _names_a_checker_result_test_impl(ctx):
    env = analysistest.begin(ctx)
    asserts.expect_failure(env, "names `ac_cv_header_stdio_h`, which is not the `name` of a `build_settings` entry")
    return analysistest.end(env)

names_a_checker_result_test = analysistest.make(
    _names_a_checker_result_test_impl,
    expect_failure = True,
)
