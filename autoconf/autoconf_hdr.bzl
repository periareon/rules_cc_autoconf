"""# autoconf_hdr"""

load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _autoconf_hdr_impl(ctx):
    """Implementation of the autoconf_hdr rule."""

    deps = collect_deps(ctx.attr.deps)
    transitive_checks = collect_transitive_results(ctx.label, deps.to_list())

    # Use resolver to generate header from individual results files
    # The resolver will merge them internally and write the merged results to output_results_json
    inputs = list(transitive_checks.values()) + [ctx.file.template]

    # Process inlines: collect files and create mappings
    inline_files = []
    inline_mappings = []
    if ctx.attr.inlines:
        for search_string, inline_file in ctx.attr.inlines.items():
            files_list = inline_file.files.to_list()
            if len(files_list) != 1:
                fail("Inline file label %s must have exactly one file" % str(inline_file.label))
            inline_file_obj = files_list[0]
            inline_files.append(inline_file_obj)
            inline_mappings.append((search_string, inline_file_obj.path))

    inputs.extend(inline_files)

    # Pass all individual results files directly to resolver (it merges internally)
    args = ctx.actions.args()
    args.use_param_file("@%s", use_always = True)
    args.set_param_file_format("multiline")
    for results_file_path in transitive_checks.values():
        args.add("--results", results_file_path)
    args.add("--output", ctx.outputs.out)
    args.add("--template", ctx.file.template)

    # Add inline mappings: --inline <search_string> <file_path>
    for search_string, file_path in inline_mappings:
        args.add("--inline")
        args.add(search_string)
        args.add(file_path)

    ctx.actions.run(
        executable = ctx.executable._resolver,
        arguments = [args],
        inputs = inputs,
        outputs = [ctx.outputs.out],
        mnemonic = "CcAutoconfHdr",
        env = ctx.configuration.default_shell_env,
    )

    # Return a dict mapping define names to result files (from autoconf deps)
    # The merged output_results_json is still created for backward compatibility, but the provider
    # now carries the dict of define names to files
    return [
        DefaultInfo(
            files = depset([ctx.outputs.out]),
        ),
    ]

autoconf_hdr = rule(
    implementation = _autoconf_hdr_impl,
    doc = """\
Generate configuration headers from results provided by `autoconf` targets.

This rule takes check results from `autoconf` targets (via `deps`) and generates a header file.
The template file (if provided) is used to format the output header.

Example:

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")

autoconf(
    name = "config_checks",
    checks = [
        macros.AC_CHECK_HEADER("stdio.h"),
        macros.AC_CHECK_HEADER("stdlib.h"),
        macros.AC_CHECK_FUNC("printf"),
    ],
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    deps = [":config_checks"],
    template = "config.h.in",
)
```

This allows you to run checks once and generate multiple header files from the same results.
""",
    attrs = {
        "deps": attr.label_list(
            doc = "List of `autoconf` targets which provide check results. Results from all deps will be merged together, and duplicate define names will produce an error. If not provided, an empty results file will be created.",
            providers = [CcAutoconfInfo],
        ),
        "inlines": attr.string_keyed_label_dict(
            doc = """A mapping of strings to files for replace within the content of the given `template` attribute.

            The exact string in the template is replaced with the content of the associated file.
            Any `@VAR@` style placeholders in the inline file content will be replaced with their
            corresponding define values (package info, check results, etc.) after insertion.""",
            allow_files = True,
        ),
        "out": attr.output(
            doc = "The output config file (typically `config.h`).",
            mandatory = True,
        ),
        "template": attr.label(
            doc = "Template file (`config.h.in`) to use as base for generating the header file. The template is used to format the output header, but does not generate any checks.",
            allow_single_file = True,
            mandatory = True,
        ),
        "_resolver": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/resolver:resolver_bin"),
        ),
    },
)
