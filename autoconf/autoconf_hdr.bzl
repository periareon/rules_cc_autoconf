"""# autoconf_hdr"""

load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "filter_defaults",
    "get_autoconf_toolchain_defaults",
    "get_autoconf_toolchain_defaults_by_label",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _autoconf_hdr_impl(ctx):
    """Implementation of the autoconf_hdr rule."""

    # Validate that defaults_include and defaults_exclude are mutually exclusive
    if ctx.attr.defaults_include and ctx.attr.defaults_exclude:
        fail("defaults_include and defaults_exclude are mutually exclusive")

    # Get toolchain defaults based on the defaults attribute
    defaults = struct(cache = {}, define = {}, subst = {})
    if ctx.attr.defaults:
        # Only use label-based filtering if include/exclude is specified
        if ctx.attr.defaults_include or ctx.attr.defaults_exclude:
            # Filtering needed - rebuild filtered list from labels
            defaults_by_label = get_autoconf_toolchain_defaults_by_label(ctx)
            if defaults_by_label:
                include_labels = [dep.label for dep in ctx.attr.defaults_include] if ctx.attr.defaults_include else []
                exclude_labels = [dep.label for dep in ctx.attr.defaults_exclude] if ctx.attr.defaults_exclude else []
                defaults = filter_defaults(defaults_by_label, include_labels, exclude_labels)
        else:
            # No filtering - use already-flattened collection directly from toolchain
            defaults = get_autoconf_toolchain_defaults(ctx)

    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    all_define_checks = defaults.define | dep_results["define"]
    all_subst_checks = defaults.subst | dep_results["subst"]

    inputs = depset([ctx.file.template] + all_subst_checks.values() + all_define_checks.values())

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

    inputs = depset(inline_files, transitive = [inputs])

    # Pass all individual results files directly to resolver (it merges internally)
    # Include both defaults and transitive checks so resolver can merge them
    # Use separate flags for each bucket
    args = ctx.actions.args()
    args.use_param_file("@%s", use_always = True)
    args.set_param_file_format("multiline")

    # Add define results
    for results_file_path in all_define_checks.values():
        args.add("--define-result", results_file_path)

    # Add subst results
    for results_file_path in all_subst_checks.values():
        args.add("--subst-result", results_file_path)

    args.add("--output", ctx.outputs.out)
    args.add("--template", ctx.file.template)
    args.add("--mode", ctx.attr.mode)

    # Add inline mappings: --inline <search_string> <file_path>
    for search_string, file_path in inline_mappings:
        args.add("--inline")
        args.add(search_string)
        args.add(file_path)

    # Add substitutions: --subst <name> <value>
    for name, value in ctx.attr.substitutions.items():
        args.add("--subst")
        args.add(name)
        args.add(value)

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
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

autoconf(
    name = "config_checks",
    checks = [
        checks.AC_CHECK_HEADER("stdio.h"),
        checks.AC_CHECK_HEADER("stdlib.h"),
        checks.AC_CHECK_FUNC("printf"),
    ],
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    deps = [":config_checks"],
    template = "config.h.in",
)
```

Valid `mode` values:
- `"defines"` (default): Only process defines (AC_DEFINE, AC_CHECK_*, etc.), not substitution variables (AC_SUBST). This is for config.h files.
- `"subst"`: Only process substitution variables (AC_SUBST), not defines. This is for subst.h files.
- `"all"`: Process both defines and substitution variables.
```

This allows you to run checks once and generate multiple header files from the same results.
""",
    attrs = {
        "defaults": attr.bool(
            doc = """Whether to include toolchain defaults.

            When False (the default), no toolchain defaults are included and only
            the explicit deps provide check results. When True, defaults from the
            autoconf toolchain are included, subject to filtering by defaults_include
            or defaults_exclude.""",
            default = False,
        ),
        "defaults_exclude": attr.label_list(
            doc = """Labels to exclude from toolchain defaults.

            Only effective when defaults=True. If specified, defaults from these
            labels are excluded. Labels not found in the toolchain are silently
            ignored. Mutually exclusive with defaults_include.""",
            providers = [CcAutoconfInfo],
        ),
        "defaults_include": attr.label_list(
            doc = """Labels to include from toolchain defaults.

            Only effective when defaults=True. If specified, only defaults from
            these labels are included. An error is raised if a specified label
            is not found in the toolchain. Mutually exclusive with defaults_exclude.""",
            providers = [CcAutoconfInfo],
        ),
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
        "mode": attr.string(
            doc = """Processing mode that determines what should be replaced within the file.""",
            default = "defines",
            values = ["defines", "subst", "all"],
        ),
        "out": attr.output(
            doc = "The output config file (typically `config.h`).",
            mandatory = True,
        ),
        "substitutions": attr.string_dict(
            doc = """A mapping of exact strings to replacement values.

            Each entry performs an exact text replacement in the template - the key
            string is replaced with the value string. No special patterns or wrappers
            are added.

            Example:
            ```python
            autoconf_hdr(
                name = "config",
                out = "config.h",
                template = "config.h.in",
                substitutions = {
                    "@MY_VERSION@": "1.2.3",
                    "@BUILD_TYPE@": "release",
                    "PLACEHOLDER_TEXT": "actual_value",
                },
                deps = [":checks"],
            )
            ```

            This would replace the exact string `@MY_VERSION@` with `1.2.3`,
            `@BUILD_TYPE@` with `release`, and `PLACEHOLDER_TEXT` with `actual_value`.""",
            default = {},
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
    toolchains = [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
)
