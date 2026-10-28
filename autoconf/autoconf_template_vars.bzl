"""# autoconf_template_vars

Build settings as autoconf substitutions and make variables.

A configure option that takes a value (`--with-runtime-dir=DIR`) ends up in a
substituted variable, and from there in the generated files (`@PPPD_RUNTIME_DIR@`)
and in the compiler flags the Makefile passes (`-DPPPD_RUNTIME_DIR="..."`).  This
rule gives such a variable a Bazel build setting: each entry of `vars` names the
variable and the target whose `BuildSettingInfo` value it takes, a `string_flag`
usually.  The value is rendered as a pre-computed result, so the target is a
`deps` entry of `autoconf`, `autoconf_hdr` and `autoconf_srcs` like any check, and
it is a make variable of the same name, so a `cc_*` target listing this rule in
`toolchains` reads it with `$(PPPD_RUNTIME_DIR)` in `local_defines` or `copts`.

Example:

```python
load("@bazel_skylib//rules:common_settings.bzl", "string_flag")
load("@rules_cc_autoconf//autoconf:autoconf_template_vars.bzl", "autoconf_template_vars")

string_flag(
    name = "runtime_dir",
    build_setting_default = "/usr/local/var/run/pppd",
)

autoconf_template_vars(
    name = "dirs",
    vars = {"PPPD_RUNTIME_DIR": ":runtime_dir"},
)

autoconf_hdr(
    name = "config",
    out = "pppd/config.h",
    template = "pppd/config.h.in",
    deps = [":autoconf", ":dirs"],
)

cc_binary(
    name = "pppd",
    srcs = ["pppd/main.c"],
    local_defines = ["PPPD_RUNTIME_DIR=\\\"$(PPPD_RUNTIME_DIR)\\\""],
    toolchains = [":dirs"],
)
```

`--//:runtime_dir=/run/pppd` then changes both the header and the define, as
`./configure --with-runtime-dir=/run/pppd` would.
"""

load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("//autoconf/private:ctx_actions_write.bzl", "write")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _text(value):
    """The make-variable text of a setting's value.

    A string as it is; a number or boolean as the substitution renders it, its JSON spelling.
    """
    return value if type(value) == "string" else json.encode(value)

def _autoconf_template_vars_impl(ctx):
    cache_results = {}
    subst_results = {}
    content_cache = {}
    variables = {}
    for name, target in ctx.attr.vars.items():
        value = target[BuildSettingInfo].value
        output = ctx.actions.declare_file("{}/{}.result.cache.json".format(ctx.label.name, name))

        # The result as `checks.AC_SUBST` writes it: the substitution's text itself, which the
        # header renderer places as it is (`encode_result` quotes a string, as a define wants).
        write(
            actions = ctx.actions,
            output = output,
            content = json.encode_indent({"success": True, "value": _text(value)}, indent = " " * 4) + "\n",
        )

        # The names a `checks.AC_SUBST` of the variable would publish: the subst and its cache
        # variable, so `requires` and source conditions can read it either way.
        subst_results[name] = output
        cache_results["ac_cv_subst_" + name] = output
        content_cache[json.encode(["build_setting", str(target.label), value])] = output
        variables[name] = _text(value)
    return [
        DefaultInfo(files = depset(subst_results.values())),
        CcAutoconfInfo(
            owner = ctx.label,
            cache_results = cache_results,
            content_cache = content_cache,
            subst_results = subst_results,
        ),
        platform_common.TemplateVariableInfo(variables),
    ]

autoconf_template_vars = rule(
    doc = "Build settings as autoconf substitutions (`@VAR@`) and make variables (`$(VAR)`); see the module docstring.",
    implementation = _autoconf_template_vars_impl,
    attrs = {
        "vars": attr.string_keyed_label_dict(
            doc = "Variable name -> the build setting whose value it takes: any target providing " +
                  "`BuildSettingInfo` (a `string_flag`, `int_flag` or `bool_flag`).  The name is the " +
                  "substitution's (`@VAR@`), its cache variable's (`ac_cv_subst_VAR`) and the make variable's (`$(VAR)`).",
            mandatory = True,
            providers = [BuildSettingInfo],
        ),
    },
    provides = [CcAutoconfInfo, platform_common.TemplateVariableInfo],
)
