"""# package_info"""

load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _package_info_impl(ctx):
    results = {
        "PACKAGE_BUGREPORT": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_BUGREPORT")),
        "PACKAGE_NAME": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_NAME")),
        "PACKAGE_STRING": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_STRING")),
        "PACKAGE_TARNAME": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_TARNAME")),
        "PACKAGE_URL": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_URL")),
        "PACKAGE_VERSION": ctx.actions.declare_file("{}/{}.results.json".format(ctx.label.name, "PACKAGE_VERSION")),
    }

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

        ctx.actions.run(
            mnemonic = "ModuleBazelParse",
            outputs = [
                results["PACKAGE_NAME"],
                results["PACKAGE_VERSION"],
                results["PACKAGE_STRING"],
                results["PACKAGE_TARNAME"],
            ],
            executable = ctx.executable._parser,
            arguments = [args],
            inputs = depset([ctx.file.module_bazel]),
        )

    else:
        # Write JSON files in check result format for PACKAGE_NAME
        # Use regular string (not json.encode) so it goes through .dump() in check.cc
        ctx.actions.write(
            output = results["PACKAGE_NAME"],
            content = json.encode_indent({
                "PACKAGE_NAME": {
                    "success": True,
                    "value": json.encode(ctx.attr.package_name),
                },
            }, indent = " " * 4) + "\n",
        )

        # Write JSON files in check result format for PACKAGE_VERSION
        ctx.actions.write(
            output = results["PACKAGE_VERSION"],
            content = json.encode_indent({
                "PACKAGE_VERSION": {
                    "success": True,
                    "value": json.encode(ctx.attr.package_version),
                },
            }, indent = " " * 4) + "\n",
        )

        # Write PACKAGE_STRING as "package_name package_version"
        package_string = ctx.attr.package_name + " " + ctx.attr.package_version
        ctx.actions.write(
            output = results["PACKAGE_STRING"],
            content = json.encode_indent({
                "PACKAGE_STRING": {
                    "success": True,
                    "value": json.encode(package_string),
                },
            }, indent = " " * 4) + "\n",
        )

        ctx.actions.write(
            output = results["PACKAGE_TARNAME"],
            content = json.encode_indent({
                "PACKAGE_TARNAME": {
                    "success": True,
                    "value": json.encode(
                        ctx.attr.package_tarname if ctx.attr.package_tarname else ctx.attr.package_name,
                    ),
                },
            }, indent = " " * 4) + "\n",
        )

    ctx.actions.write(
        output = results["PACKAGE_BUGREPORT"],
        content = json.encode_indent({
            "PACKAGE_BUGREPORT": {
                "success": True,
                "value": json.encode(ctx.attr.package_bugreport),
            },
        }, indent = " " * 4) + "\n",
    )

    ctx.actions.write(
        output = results["PACKAGE_URL"],
        content = json.encode_indent({
            "PACKAGE_URL": {
                "success": True,
                "value": json.encode(ctx.attr.package_url),
            },
        }, indent = " " * 4) + "\n",
    )

    # Package info creates defines (for config.h), so put them in define_results
    # They're not cache variables or subst values
    return [
        CcAutoconfInfo(
            owner = ctx.label,
            deps = depset(),
            cache_results = {},
            define_results = results,
            subst_results = {},
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
        "_parser": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/module_parser"),
        ),
    },
)
