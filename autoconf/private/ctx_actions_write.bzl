"""Wrappers for writing files"""

load("@bazel_features//:features.bzl", "bazel_features")

def write(actions, output, content):
    # TODO: https://github.com/periareon/rules_cc_autoconf/issues/148
    if bazel_features.rules.write_action_has_execution_requirements:
        actions.write(
            output = output,
            content = content,
            execution_requirements = {"supports-path-mapping": ""},
        )
    else:
        actions.write(
            output = output,
            content = content,
        )
