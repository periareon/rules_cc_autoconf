"""# autoconf_hdr"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "create_config_dict",
    "create_package_info_file",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "write_config_json",
)
load(":providers.bzl", "CcAutoconfInfo")

def _autoconf_hdr_impl(ctx):
    """Implementation of the autoconf_hdr rule."""

    # Get results files from autoconf target if provided
    autoconf_results_files = []
    if ctx.attr.autoconf:
        ac_info = ctx.attr.autoconf[CcAutoconfInfo]

        # Convert depset to list for iteration
        autoconf_results_files = ac_info.results.to_list()

    # Determine package info: use local if provided, otherwise use from autoconf target (if provided)
    if ctx.attr.package_info_file and (ctx.attr.package_name or ctx.attr.package_version):
        fail("`package_info_file` and `package_name` with `package_version` are mutually exclusive. Please update {} to provide only one".format(
            ctx.label,
        ))

    package_info_file = ctx.file.package_info_file
    if not package_info_file:
        # Use package info from autoconf target if not provided locally and autoconf is provided
        if ctx.attr.autoconf and not ctx.attr.package_name and not ctx.attr.package_version:
            ac_info = ctx.attr.autoconf[CcAutoconfInfo]
            package_info_file = ac_info.package_info
        else:
            # Generate package info from local package_name/package_version
            # If autoconf is not provided, package_name and package_version should be set
            if not ctx.attr.autoconf and not ctx.attr.package_name and not ctx.attr.package_version:
                fail("Either `autoconf`, `package_info_file`, or `package_name`/`package_version` must be provided. Please update {} to provide at least one".format(
                    ctx.label,
                ))
            package_info_file = create_package_info_file(
                ctx,
                package_name = ctx.attr.package_name,
                package_version = ctx.attr.package_version,
            )

    # Get cc_toolchain info to generate config for additional checks
    toolchain_info = get_cc_toolchain_info(ctx)

    # Parse checks from JSON strings and collect file dependencies
    checks_list = []
    data_labels = []

    for check_json in ctx.attr.checks:
        check = json.decode(check_json)
        checks_list.append(check)

        # Collect file labels for dependency resolution
        if "file" in check:
            data_labels.append(check["file"])

    # Process file references
    # Build a map from data label to files
    file_map = {}
    for label_attr in ctx.attr.data:
        label_str = str(label_attr.label)
        files = label_attr.files.to_list()

        # Store by full label
        if len(files) == 1:
            file_map[label_str] = files[0]

            # Also store without @@ prefix
            if label_str.startswith("@@"):
                file_map[label_str[2:]] = files[0]

            # Extract target name and store by that too (for relative refs like ":target")
            target_name = label_str.split(":")[-1]
            if target_name:
                file_map[":" + target_name] = files[0]
        elif len(files) > 1:
            fail("File label %s has multiple files, cannot determine which to use" % label_str)

    # Replace file label strings with actual file paths and add to inputs
    additional_inputs = []
    for check in checks_list:
        if "file" in check:
            file_label = check["file"]
            if file_label in file_map:
                file_obj = file_map[file_label]

                # Set the file_path instead of code
                check["file_path"] = file_obj.path
                additional_inputs.append(file_obj)

                # Remove the file key since we've processed it
                check.pop("file")
            else:
                fail("File referenced in check not found in data: " + file_label + ". Available: " + str(file_map.keys()))

    processed_checks = checks_list

    # Generate config JSON (with checks from attribute - runner will also add checks from template if needed)
    config_dict = create_config_dict(
        toolchain_info = toolchain_info,
        checks = processed_checks,  # Checks from attribute - runner will merge with template checks
        package_info_path = package_info_file.path,
    )

    config_json = write_config_json(ctx, config_dict)

    # Create individual check actions for each check in autoconf_hdr's checks attribute
    check_result_files = []
    num_checks = len(processed_checks)

    # Prepare inputs that are common to all checks
    common_inputs = [package_info_file, config_json] + additional_inputs
    common_inputs_depset = depset(
        direct = common_inputs,
        transitive = [toolchain_info.cc_toolchain.all_files],
    )

    # Get environment variables for running additional checks
    env = get_environment_variables(ctx, toolchain_info)

    # Create individual CcAutoconfCheck actions for each check
    for i in range(num_checks):
        check_result_file = ctx.actions.declare_file("{}.check_{}.json".format(ctx.label.name, i))
        check_result_files.append(check_result_file)

        args = ctx.actions.args()
        args.add("--config", config_json)
        args.add("--results", check_result_file)
        args.add("--check", str(i))

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = common_inputs_depset,
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            env = env | ctx.configuration.default_shell_env,
        )

    # Collect all results files (from autoconf target + from this rule's checks)
    all_results_files = autoconf_results_files + check_result_files

    # Always create a results JSON file to store the merged/final results
    # This allows autoconf_hdr to be used by downstream rules like autoconf_srcs
    # The resolver will write this when generating the header
    output_results_json = ctx.actions.declare_file("{}.ac.results.json".format(ctx.label.name))

    # Use resolver to generate header from individual results files
    # The resolver will merge them internally, run additional checks from template if needed,
    # and also write the merged results to output_results_json
    resolver_inputs = [package_info_file] + all_results_files
    if ctx.file.template:
        resolver_inputs.append(ctx.file.template)

        # Include config_json if we have a template (for running additional template-based checks)
        resolver_inputs.append(config_json)

    # Pass all individual results files directly to resolver (it merges internally)
    resolver_args = ctx.actions.args()
    for results_file_path in all_results_files:
        resolver_args.add("--results", results_file_path)
    resolver_args.add("--package-info", package_info_file)
    resolver_args.add("--output", ctx.outputs.out)
    resolver_args.add("--output-results", output_results_json)  # Resolver writes merged results
    if ctx.file.template:
        resolver_args.add("--template", ctx.file.template)
        resolver_args.add("--config", config_json)  # Pass config for template-based checks

    resolver_inputs_depset = depset(
        direct = resolver_inputs,
        transitive = [
            toolchain_info.cc_toolchain.all_files,
        ],
    )

    ctx.actions.run(
        executable = ctx.executable._resolver,
        arguments = [resolver_args],
        inputs = resolver_inputs_depset,
        outputs = [ctx.outputs.out, output_results_json],
        mnemonic = "CcAutoconfHdr",
        env = env | ctx.configuration.default_shell_env,
    )

    # Return a depset of all individual check result files (from autoconf target + this rule's checks)
    # The merged output_results_json is still created for backward compatibility, but the provider
    # now carries the depset of individual files
    return [
        DefaultInfo(
            files = depset([ctx.outputs.out]),
        ),
        CcAutoconfInfo(
            results = depset(all_results_files),
            package_info = package_info_file,
        ),
    ]

autoconf_hdr = rule(
    implementation = _autoconf_hdr_impl,
    doc = """\
Generate configuration headers, optionally using results from an `autoconf` target.

This rule can work in two modes:
1. **Standalone mode**: When `autoconf` is not provided, it runs checks from its own
   `checks` attribute and generates a header file directly.
2. **Merge mode**: When `autoconf` is provided, it takes existing check results from
   that target and merges them with additional checks (from `checks` attribute or
   automatically derived from template defines).

Examples:

**Standalone mode:**
```python
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")

autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    package_name = "myproject",
    package_version = "1.0.0",
    checks = [
        macros.AC_CHECK_HEADER("stdio.h"),
        macros.AC_CHECK_HEADER("stdlib.h"),
        macros.AC_CHECK_FUNC("printf"),
    ],
)
```

**Merge mode:**
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
    package_name = "myproject",
    package_version = "1.0.0",
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    autoconf = ":config_checks",
    template = "config.h.in",
    # Optional: run additional checks beyond what's in the template
    checks = [
        macros.AC_CHECK_HEADER("unistd.h"),
    ],
)
```

This allows you to either run checks and generate headers in one step (standalone mode)
or run checks once and generate multiple header files from the same results (merge mode).
""",
    attrs = {
        "autoconf": attr.label(
            doc = "Optional `autoconf` target which provides check results. If not provided, `autoconf_hdr` will run checks independently using its own `checks` attribute.",
            providers = [CcAutoconfInfo],
        ),
        "checks": attr.string_list(
            doc = "List of JSON-encoded checks from macros (e.g., `macros.AC_CHECK_HEADER('stdio.h')`). These checks will be merged with existing results from the `autoconf` target and any checks derived from the template.",
            default = [],
        ),
        "data": attr.label_list(
            doc = "Files referenced by checks (via the file parameter).",
            allow_files = True,
        ),
        "out": attr.output(
            doc = "The output config file (typically `config.h`).",
            mandatory = True,
        ),
        "package_info_file": attr.label(
            doc = "A json file containing a map `{\"name\": \"{PACKAGE_NAME}\", \"version\": \"{PACKAGE_VERSION\"}`. Note that each field is optional. This attribute is mutually exclusive with `package_name` and `package_version`. If not provided, the package info from the `autoconf` target will be used.",
            allow_single_file = True,
        ),
        "package_name": attr.string(
            doc = "Package name for `PACKAGE_NAME` define. This attribute is mutually exclusive with `package_info_file`. If not provided and `autoconf` is provided, the package name from the `autoconf` target will be used. Required if `autoconf` is not provided.",
            default = "",
        ),
        "package_version": attr.string(
            doc = "Package version for `PACKAGE_VERSION` define. This attribute is mutually exclusive with `package_info_file`. If not provided and `autoconf` is provided, the package version from the `autoconf` target will be used. Required if `autoconf` is not provided.",
            default = "",
        ),
        "template": attr.label(
            doc = "Optional template file (`config.h.in`) to use as base. If provided, the runner will automatically generate checks for any defines referenced in the template that don't already have results.",
            allow_single_file = True,
        ),
        "_checker": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/checker:checker_bin"),
        ),
        "_resolver": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/resolver:resolver_bin"),
        ),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
