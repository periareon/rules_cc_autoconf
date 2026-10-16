"""# autoconf_defaults"""

load("//autoconf/private:ctx_actions_write.bzl", "write")
load("//autoconf/private:providers.bzl", "CcAutoconfDefaultsInfo")

def _value(text):
    """A decimal integer is stored as a number, as `checks.AC_SUBST("X", 1)` stores it."""
    digits = text[1:] if text.startswith("-") else text
    if digits and digits.isdigit():
        return int(text)
    return text

def _autoconf_defaults_impl(ctx):
    cache_results = {}
    define_results = {}
    subst_results = {}
    content = {}
    unquoted = {}
    owner = {}

    def take(kind, bucket, name, file):
        existing = bucket.get(name)
        if existing != None and existing.path != file.path and content[existing.path] != content[file.path]:
            fail("`{}`: default {} '{}' is given two values:\n  {}: {}\n  {}: {}".format(
                ctx.label,
                kind,
                name,
                owner[existing.path],
                json.decode(content[existing.path])["value"],
                owner[file.path],
                json.decode(content[file.path])["value"],
            ))
        bucket[name] = file

    for dep in ctx.attr.deps:
        info = dep[CcAutoconfDefaultsInfo]
        for path, text in info.content.items():
            content[path] = text
            owner[path] = info.owner
        for name, file in info.cache_results.items():
            take("variable", cache_results, name, file)
        for name, file in info.define_results.items():
            take("define", define_results, name, file)
        for name, file in info.subst_results.items():
            take("subst", subst_results, name, file)
        for uq in info.unquoted_defines:
            unquoted[uq] = True

    own = []

    def emit(cache_name, kind, text):
        file = ctx.actions.declare_file("{}/{}.result.cache.json".format(ctx.label.name, cache_name))

        # The same `{success, type, value}` file the checker writes for a
        # literal `AC_SUBST` / `M4_VARIABLE` / `AC_DEFINE`, so consumers cannot
        # tell a default from a probe result.
        body = json.encode_indent({
            "success": True,
            "type": kind,
            "value": _value(text),
        }, indent = " " * 4) + "\n"
        write(actions = ctx.actions, output = file, content = body)
        content[file.path] = body
        owner[file.path] = ctx.label
        own.append(file)
        return file

    for name, text in ctx.attr.substs.items():
        file = emit("ac_cv_subst_" + name, "m4_variable", text)
        take("variable", cache_results, "ac_cv_subst_" + name, file)
        take("subst", subst_results, name, file)
    for name, text in ctx.attr.variables.items():
        take("variable", cache_results, name, emit(name, "m4_variable", text))
    for name, text in ctx.attr.defines.items():
        file = emit("ac_cv_define_" + name, "define", text)
        take("variable", cache_results, "ac_cv_define_" + name, file)
        take("define", define_results, name, file)

    return [
        DefaultInfo(files = depset(own)),
        CcAutoconfDefaultsInfo(
            owner = ctx.label,
            cache_results = cache_results,
            define_results = define_results,
            subst_results = subst_results,
            unquoted_defines = sorted(unquoted.keys()),
            content = content,
        ),
    ]

autoconf_defaults = rule(
    implementation = _autoconf_defaults_impl,
    doc = """\
Default values for autoconf results.

A default is not a check: nothing is probed, no toolchain is resolved, and the
values are written at analysis time.  A target reads defaults through
`default_deps`, beneath every ordinary result, so a probe of the same name
overrides the default silently where a second probe would be a duplicate
error.  `deps` rejects defaults targets; they only enter through `default_deps`.

This is how gnulib's `*_H_DEFAULTS` macros work: `gl_UNISTD_H_DEFAULTS` sets
`HAVE_DUP3=1`, `REPLACE_DUP2=0` and so on, and the probes that run later
reassign only what they find wanting.  Port such a macro as one
`autoconf_defaults` target and list it in the `default_deps` of every target
whose m4 `AC_REQUIRE`s it:

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_defaults.bzl", "autoconf_defaults")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

# gl_UNISTD_H_DEFAULTS
autoconf_defaults(
    name = "defaults",
    substs = {
        "HAVE_DUP3": "1",
        "REPLACE_DUP2": "0",
    },
)

# gl_FUNC_DUP2: AC_REQUIRE([gl_UNISTD_H_DEFAULTS]), then may set REPLACE_DUP2=1
autoconf(
    name = "dup2",
    checks = [
        checks.AC_SUBST("REPLACE_DUP2", 1, condition = "!gl_cv_func_dup2_works"),
    ],
    default_deps = [":defaults"],
)
```

Defaults compose: `deps` lists other `autoconf_defaults` targets whose values
this one carries along (gnulib's `_H_REQUIRE_DEFAULTS` on top of
`_H_DEFAULTS`), and an `autoconf` target passes the defaults it reads on to
its dependents.  Two defaults of one name must agree.
""",
    attrs = {
        "defines": attr.string_dict(
            doc = "Default `AC_DEFINE` values: define name to value.  Decimal integers are stored as numbers, " +
                  "as `checks.AC_DEFINE(\"X\", 1)` stores them; anything else as text.",
        ),
        "deps": attr.label_list(
            doc = "Other `autoconf_defaults` targets whose values this target carries along.",
            providers = [CcAutoconfDefaultsInfo],
        ),
        "substs": attr.string_dict(
            doc = "Default `AC_SUBST` values: variable name to value (`{\"HAVE_DUP3\": \"1\"}`), stored under " +
                  "the cache name `ac_cv_subst_<NAME>` as `checks.AC_SUBST` does.  Decimal integers are stored " +
                  "as numbers; anything else as text.",
        ),
        "variables": attr.string_dict(
            doc = "Default shell variables that are neither defines nor substitutions (`checks.M4_VARIABLE`): " +
                  "name to value, read by `requires` and `condition` expressions and by " +
                  "`cc_gnulib_conditional_hdrs` conditions, e.g. `{\"GL_GENERATE_ICONV_H\": \"false\"}`.",
        ),
    },
    provides = [CcAutoconfDefaultsInfo],
)
