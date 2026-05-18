"""Transition rule to inject extra --copt flags into an autoconf target."""

load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _extra_copts_transition_impl(settings, attr):
    new_copts = list(settings["//command_line_option:copt"])
    new_copts.extend(attr.extra_copts)
    return {"//command_line_option:copt": new_copts}

_extra_copts_transition = transition(
    implementation = _extra_copts_transition_impl,
    inputs = ["//command_line_option:copt"],
    outputs = ["//command_line_option:copt"],
)

def _autoconf_with_copts_impl(ctx):
    return [ctx.attr.actual[0][CcAutoconfInfo]]

autoconf_with_copts = rule(
    doc = "Wraps an autoconf target with a configuration transition that appends extra copts. " +
          "This allows testing autoconf checks under non-standard compiler flags (e.g. flags " +
          "containing shell metacharacters) without affecting the rest of the build.",
    implementation = _autoconf_with_copts_impl,
    attrs = {
        "actual": attr.label(
            doc = "The autoconf target to wrap. The transition applies to this target's configuration.",
            cfg = _extra_copts_transition,
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "extra_copts": attr.string_list(
            doc = "Additional --copt flags to inject into the transitioned configuration.",
            default = [],
        ),
        "_allowlist_function_transition": attr.label(
            default = Label("@bazel_tools//tools/allowlists/function_transition_allowlist"),
        ),
    },
    provides = [CcAutoconfInfo],
)
