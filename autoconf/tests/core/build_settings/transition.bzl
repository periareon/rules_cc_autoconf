"""Transition wrapper that overrides the build_settings flags for testing.

Used to verify that values flowing through `checks.AC_BUILD_SETTING` actually
re-render the produced header when the underlying build setting changes
configuration -- not just when the user passes a different flag at the
command line.
"""

def _transition_impl(_settings, _attr):
    return {
        "//autoconf/tests/core/build_settings:enable_threads": False,
        "//autoconf/tests/core/build_settings:log_level": 5,
        "//autoconf/tests/core/build_settings:package_name": "altproj",
    }

_transition = transition(
    implementation = _transition_impl,
    inputs = [],
    outputs = [
        "//autoconf/tests/core/build_settings:package_name",
        "//autoconf/tests/core/build_settings:enable_threads",
        "//autoconf/tests/core/build_settings:log_level",
    ],
)

def _transitioned_file_impl(ctx):
    src = ctx.attr.actual[0][DefaultInfo].files.to_list()[0]
    out = ctx.actions.declare_file(ctx.label.name + ".h")
    ctx.actions.symlink(output = out, target_file = src)
    return [DefaultInfo(files = depset([out]))]

transitioned_file = rule(
    doc = "Wraps a target with a configuration transition that overrides the " +
          "build_settings-test flags to non-default values, then re-exports its " +
          "single output file under this rule's name.",
    implementation = _transitioned_file_impl,
    attrs = {
        "actual": attr.label(
            doc = "Target whose default output is re-emitted after the transition.",
            cfg = _transition,
            mandatory = True,
        ),
        "_allowlist_function_transition": attr.label(
            default = Label("@bazel_tools//tools/allowlists/function_transition_allowlist"),
        ),
    },
)
