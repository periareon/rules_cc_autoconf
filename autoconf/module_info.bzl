"""# module_info"""

def _module_info_impl(ctx):
    name = ctx.label.name
    if not name.endswith(".json"):
        name += ".json"
    output = ctx.actions.declare_file(name)

    args = ctx.actions.args()
    args.add(ctx.file.module_bazel)
    args.add(output)

    ctx.actions.run(
        mnemonic = "ModuleBazelParse",
        outputs = [output],
        executable = ctx.executable._parser,
        arguments = [args],
        inputs = depset([ctx.file.module_bazel]),
    )

    return [DefaultInfo(
        files = depset([output]),
    )]

module_info = rule(
    doc = """\
A rule for parsing module info from `MODULE.bazel` files.

This rule is most useful when paired with the `autoconf` rule.

Example:

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:module_info.bzl", "module_info")

module_info(
    name = "module_info.json",
    module_bazel = "//:MODULE.bazel",
)

autoconf(
    name = "config_h",
    out = "config.h",
    checks = [
        # ...
    ],
    package_info_file = ":module_info.json",
)
```
""",
    implementation = _module_info_impl,
    attrs = {
        "module_bazel": attr.label(
            doc = "A `MODULE.bazel` file to parse module information from.",
            allow_single_file = True,
            mandatory = True,
        ),
        "_parser": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/module_parser"),
        ),
    },
)
