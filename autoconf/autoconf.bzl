"""# autoconf"""

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

def _autoconf_impl(ctx):
    """Implementation of the autoconf rule that only runs checks."""

    if ctx.attr.package_info_file and (ctx.attr.package_name or ctx.attr.package_version):
        fail("`package_info_file` and `package_name` with `package_version` are mutually exclusive. Please update {} to provide only one".format(
            ctx.label,
        ))

    package_info_file = ctx.file.package_info_file
    if not package_info_file:
        package_info_file = create_package_info_file(
            ctx,
            package_name = ctx.attr.package_name,
            package_version = ctx.attr.package_version,
        )

    # Get cc_toolchain info
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

    # Create config dictionary
    config_dict = create_config_dict(
        toolchain_info = toolchain_info,
        checks = processed_checks,
        package_info_path = package_info_file.path,
    )

    # Write config to JSON
    config_json = write_config_json(ctx, config_dict)

    # Create individual check actions for each check
    check_result_files = []
    num_checks = len(processed_checks)

    # Prepare inputs that are common to all checks
    common_inputs = [config_json, package_info_file] + additional_inputs
    common_inputs_depset = depset(
        direct = common_inputs,
        transitive = [toolchain_info.cc_toolchain.all_files],
    )

    # Get environment variables from the toolchain (like LIB, INCLUDE, PATH for MSVC)
    # We need environment variables from both compile and link actions since the autoconf
    # runner performs both compilation and linking. For MSVC, the INCLUDE environment
    # variable from the compile action is crucial for finding standard headers like stdint.h
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

    # Return a depset of individual check result files
    return [
        CcAutoconfInfo(
            results = depset(check_result_files),
            package_info = package_info_file,
        ),
    ]

autoconf = rule(
    implementation = _autoconf_impl,
    doc = """\
Run autoconf-like checks and produce results.

This rule runs compilation checks against the configured cc_toolchain and produces
a JSON results file containing the check outcomes. Unlike `autoconf_hdr`, this rule
does not generate any header files - it only performs checks.

Example:

```python
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")

autoconf(
    name = "config",
    checks = [
        macros.AC_CHECK_HEADER("stdio.h"),
        macros.AC_CHECK_HEADER("stdlib.h"),
        macros.AC_CHECK_FUNC("printf"),
    ],
    package_name = "myproject",
    package_version = "1.0.0",
)
```

The results can then be used by `autoconf_hdr` or `autoconf_srcs` to generate headers
or wrapped source files.
""",
    attrs = {
        "checks": attr.string_list(
            doc = "List of JSON-encoded checks from macros (e.g., `macros.AC_CHECK_HEADER('stdio.h')`).",
            default = [],
        ),
        "data": attr.label_list(
            doc = "Files referenced by checks (via the file parameter).",
            allow_files = True,
        ),
        "package_info_file": attr.label(
            doc = "A json file containing a map `{\"name\": \"{PACKAGE_NAME}\", \"version\": \"{PACKAGE_VERSION\"}`. Note that each field is optional. This attribute is mutually exclusive with `package_name` and `package_version`.",
            allow_single_file = True,
        ),
        "package_name": attr.string(
            doc = "Package name for `PACKAGE_NAME` define. This attribute is mutually exclusive with `package_info_file`.",
            default = "",
        ),
        "package_version": attr.string(
            doc = "Package version for `PACKAGE_VERSION` define. This attribute is mutually exclusive with `package_info_file`.",
            default = "",
        ),
        "_checker": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/checker:checker_bin"),
        ),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
