"""Regression tests for compiler probes with automatic execution groups."""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")

def _auto_exec_groups_test_impl(ctx):
    env = analysistest.begin(ctx)
    checks = [
        action
        for action in analysistest.target_actions(env)
        if action.mnemonic == "CcAutoconfCheck"
    ]
    asserts.equals(env, ctx.attr.expected_checks, len(checks))
    return analysistest.end(env)

auto_exec_groups_test = analysistest.make(
    _auto_exec_groups_test_impl,
    attrs = {"expected_checks": attr.int(default = 1)},
    config_settings = {
        "//command_line_option:extra_toolchains": [
            str(Label("//autoconf/tests/core/auto_exec_groups:toolchain")),
        ],
        "//command_line_option:incompatible_auto_exec_groups": True,
    },
)
