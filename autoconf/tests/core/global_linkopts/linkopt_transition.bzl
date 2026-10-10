"""Transition rule to inject extra --copt and --linkopt flags into a target."""

load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

_COPT = "//command_line_option:copt"
_LINKOPT = "//command_line_option:linkopt"

def _extra_flags_transition_impl(settings, attr):
    return {
        _COPT: list(settings[_COPT]) + attr.extra_copts,
        _LINKOPT: list(settings[_LINKOPT]) + attr.extra_linkopts,
    }

_extra_flags_transition = transition(
    implementation = _extra_flags_transition_impl,
    inputs = [_COPT, _LINKOPT],
    outputs = [_COPT, _LINKOPT],
)

def _autoconf_with_flags_impl(ctx):
    return [ctx.attr.actual[0][CcAutoconfInfo]]

autoconf_with_flags = rule(
    doc = "Wraps an autoconf target with a configuration transition that appends extra " +
          "--copt and --linkopt flags.",
    implementation = _autoconf_with_flags_impl,
    attrs = {
        "actual": attr.label(
            doc = "The autoconf target to wrap.",
            cfg = _extra_flags_transition,
            mandatory = True,
            providers = [CcAutoconfInfo],
        ),
        "extra_copts": attr.string_list(
            doc = "Additional --copt flags to inject.",
            default = [],
        ),
        "extra_linkopts": attr.string_list(
            doc = "Additional --linkopt flags to inject.",
            default = [],
        ),
        "_allowlist_function_transition": attr.label(
            default = Label("@bazel_tools//tools/allowlists/function_transition_allowlist"),
        ),
    },
    provides = [CcAutoconfInfo],
)
