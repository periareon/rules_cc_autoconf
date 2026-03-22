"""# package_info"""

load(
    ":cc_autoconf_info.bzl",
    "CcAutoconfInfo",
    "encode_result",
)

_RUNNER_SOURCES = [
    "PACKAGE_NAME",
    "PACKAGE_VERSION",
    "PACKAGE_STRING",
    "PACKAGE_TARNAME",
]

def _package_info_impl(ctx):
    results = {
        "PACKAGE_BUGREPORT": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_BUGREPORT")),
        "PACKAGE_NAME": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_NAME")),
        "PACKAGE_STRING": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_STRING")),
        "PACKAGE_TARNAME": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_TARNAME")),
        "PACKAGE_URL": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_URL")),
        "PACKAGE_VERSION": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_VERSION")),
    }

    extra_results = {}
    for key, value in ctx.attr.aliases.items():
        if key in results:
            fail("`{}` already a builtin result. Please update `aliases` for `{}` to drop any of: {}".format(
                key,
                ctx.label,
                sorted(results.keys()),
            ))
        if value not in results:
            fail("`{}` is trying to mirror `{}` but this result doesn't exist. Please update `{}` to use one of: {}".format(
                key,
                value,
                ctx.label,
                sorted(results.keys()),
            ))

        extra_results[key] = ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, key))

    if ctx.attr.module_bazel:
        args = ctx.actions.args()
        args.add("--out-name", results["PACKAGE_NAME"])
        args.add("--out-version", results["PACKAGE_VERSION"])
        args.add("--out-string", results["PACKAGE_STRING"])
        args.add("--out-tarname", results["PACKAGE_TARNAME"])
        args.add("--module-bazel", ctx.file.module_bazel)

        if ctx.attr.package_name:
            args.add("--force-name", ctx.attr.package_name)

        if ctx.attr.package_version:
            args.add("--force-version", ctx.attr.package_version)

        if ctx.attr.package_tarname:
            args.add("--force-tarname", ctx.attr.package_tarname)

        if ctx.attr.strip_bcr_version:
            args.add("--strip-bcr-version")

        runner_alias_outputs = []
        for key, source in ctx.attr.aliases.items():
            if source in _RUNNER_SOURCES:
                args.add("--alias", "{}={}={}".format(key, source, extra_results[key].path))
                runner_alias_outputs.append(extra_results[key])

        ctx.actions.run(
            mnemonic = "ModuleBazelParse",
            outputs = [
                results["PACKAGE_NAME"],
                results["PACKAGE_VERSION"],
                results["PACKAGE_STRING"],
                results["PACKAGE_TARNAME"],
            ] + runner_alias_outputs,
            executable = ctx.executable._parser,
            arguments = [args],
            inputs = depset([ctx.file.module_bazel]),
        )

        starlark_alias_values = {
            "PACKAGE_BUGREPORT": ctx.attr.package_bugreport,
            "PACKAGE_URL": ctx.attr.package_url,
        }
        for key, source in ctx.attr.aliases.items():
            if source not in _RUNNER_SOURCES:
                ctx.actions.write(
                    output = extra_results[key],
                    content = encode_result(starlark_alias_values[source]),
                )

    else:
        ctx.actions.write(
            output = results["PACKAGE_NAME"],
            content = encode_result(ctx.attr.package_name),
        )

        ctx.actions.write(
            output = results["PACKAGE_VERSION"],
            content = encode_result(ctx.attr.package_version),
        )

        package_string = ctx.attr.package_name + " " + ctx.attr.package_version
        ctx.actions.write(
            output = results["PACKAGE_STRING"],
            content = encode_result(package_string),
        )

        ctx.actions.write(
            output = results["PACKAGE_TARNAME"],
            content = encode_result(
                ctx.attr.package_tarname if ctx.attr.package_tarname else ctx.attr.package_name,
            ),
        )

        all_values = {
            "PACKAGE_BUGREPORT": ctx.attr.package_bugreport,
            "PACKAGE_NAME": ctx.attr.package_name,
            "PACKAGE_STRING": ctx.attr.package_name + " " + ctx.attr.package_version,
            "PACKAGE_TARNAME": ctx.attr.package_tarname if ctx.attr.package_tarname else ctx.attr.package_name,
            "PACKAGE_URL": ctx.attr.package_url,
            "PACKAGE_VERSION": ctx.attr.package_version,
        }
        for key, source in ctx.attr.aliases.items():
            ctx.actions.write(
                output = extra_results[key],
                content = encode_result(all_values[source]),
            )

    ctx.actions.write(
        output = results["PACKAGE_BUGREPORT"],
        content = encode_result(ctx.attr.package_bugreport),
    )

    ctx.actions.write(
        output = results["PACKAGE_URL"],
        content = encode_result(ctx.attr.package_url),
    )

    return [
        CcAutoconfInfo(
            owner = ctx.label,
            define_results = results | extra_results,
        ),
    ]

package_info = rule(
    doc = """\
A rule for parsing module info from `MODULE.bazel` files.

This rule is most useful when paired with the `autoconf` rule.

Example:

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:package_info.bzl", "package_info")

package_info(
    name = "package_info",
    module_bazel = "//:MODULE.bazel",
)

autoconf(
    name = "config_h",
    out = "config.h",
    checks = [
        # ...
    ],
    deps = [
        ":package_info",
    ],
)
```
""",
    implementation = _package_info_impl,
    attrs = {
        "aliases": attr.string_dict(
            doc = "Additional variables to define that are mirrored by the provided value variable.",
        ),
        "module_bazel": attr.label(
            doc = "A `MODULE.bazel` file to parse module information from. Mutually exclusive with `package_name` and `package_version`.",
            allow_single_file = True,
        ),
        "package_bugreport": attr.string(
            doc = "The package bug report email/URL. Used to populate `PACKAGE_BUGREPORT` define.",
            default = "",
        ),
        "package_name": attr.string(
            doc = "The package name. Must be provided together with `package_version` if `module_bazel` is not provided.",
        ),
        "package_tarname": attr.string(
            doc = "Exactly tarname, possibly generated from package.",
            default = "",
        ),
        "package_url": attr.string(
            doc = "The package home page URL. Used to populate `PACKAGE_URL` define.",
            default = "",
        ),
        "package_version": attr.string(
            doc = "The package version. Must be provided together with `package_name` if `module_bazel` is not provided.",
        ),
        "strip_bcr_version": attr.bool(
            doc = "Whether or not to strip `.bcr.*` suffixes from `module_bazel` parsed versions.",
        ),
        "_parser": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/module_parser"),
        ),
    },
)
