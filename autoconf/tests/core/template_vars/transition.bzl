"""A transition setting the template_vars test flags, so the rendered header is checked against
values other than the defaults."""

def _transition_impl(_settings, _attr):
    return {
        "//autoconf/tests/core/template_vars:max_links": 16,
        "//autoconf/tests/core/template_vars:runtime_dir": "/run/pppd",
    }

_transition = transition(
    implementation = _transition_impl,
    inputs = [],
    outputs = [
        "//autoconf/tests/core/template_vars:runtime_dir",
        "//autoconf/tests/core/template_vars:max_links",
    ],
)

def _transitioned_file_impl(ctx):
    src = ctx.attr.actual[0][DefaultInfo].files.to_list()[0]
    out = ctx.actions.declare_file(ctx.label.name + ".h")
    ctx.actions.symlink(output = out, target_file = src)
    return [DefaultInfo(files = depset([out]))]

transitioned_file = rule(
    doc = "Re-exports a target's single output after the transition that sets the test flags.",
    implementation = _transitioned_file_impl,
    attrs = {
        "actual": attr.label(
            doc = "Target whose default output is re-emitted after the transition.",
            cfg = _transition,
            mandatory = True,
        ),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)
