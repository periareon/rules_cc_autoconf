"""Autoconf rule implementation."""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")

def _autoconf_impl(ctx):
    """Implementation of the autoconf rule."""

    # Get the cc_toolchain
    cc_toolchain = find_cpp_toolchain(ctx)
    feature_configuration = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features,
    )

    # Get compiler and flags
    c_compiler_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.c_compile,
    )

    cpp_compiler_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_compile,
    )

    # Get linker tool
    linker_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
    )

    # Get compile variables
    compile_variables = cc_common.create_compile_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts,
    )

    # Get command line flags for compilation
    c_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.c_compile,
        variables = compile_variables,
    )

    cpp_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_compile,
        variables = compile_variables,
    )

    # Get link variables and flags
    link_variables = cc_common.create_link_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        is_linking_dynamic_library = False,
        is_static_linking_mode = True,
    )

    c_link_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
        variables = link_variables,
    )

    cpp_link_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
        variables = link_variables,
    )

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

    # Update config with processed checks
    config = {
        "c_compiler": c_compiler_path,
        "c_flags": c_flags,
        "c_link_flags": c_link_flags,
        "checks": processed_checks,
        "compiler_type": cc_toolchain.compiler,
        "cpp_compiler": cpp_compiler_path,
        "cpp_flags": cpp_flags,
        "cpp_link_flags": cpp_link_flags,
        "linker": linker_path,
        "package_name": ctx.attr.package_name,
        "package_version": ctx.attr.package_version,
    }

    # Write config to JSON
    config_json = ctx.actions.declare_file("{}.ac.json".format(ctx.label.name))
    ctx.actions.write(
        output = config_json,
        content = json.encode_indent(config, indent = " " * 4) + "\n",
    )

    # Prepare inputs
    direct_inputs = [config_json] + additional_inputs
    if ctx.file.template:
        direct_inputs.append(ctx.file.template)

    inputs = depset(
        direct = direct_inputs,
        transitive = [cc_toolchain.all_files],
    )

    # Run the autoconf checker
    args = ctx.actions.args()
    args.add("--config", config_json)
    args.add("--output", ctx.outputs.out)
    if ctx.file.template:
        args.add("--template", ctx.file.template)

    # Get environment variables from the toolchain (like LIB, INCLUDE, PATH for MSVC)
    # We need environment variables from both compile and link actions since the autoconf
    # runner performs both compilation and linking. For MSVC, the INCLUDE environment
    # variable from the compile action is crucial for finding standard headers like stdint.h
    compile_variables_for_env = cc_common.create_compile_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts,
    )
    compile_env = cc_common.get_environment_variables(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.c_compile,
        variables = compile_variables_for_env,
    )
    link_variables_for_env = cc_common.create_link_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        is_linking_dynamic_library = False,
        is_static_linking_mode = True,
    )
    link_env = cc_common.get_environment_variables(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
        variables = link_variables_for_env,
    )

    # Merge compile and link environment variables, with compile taking precedence
    # since it has the critical INCLUDE paths for MSVC
    env = link_env | compile_env

    ctx.actions.run(
        executable = ctx.executable._runner,
        arguments = [args],
        inputs = inputs,
        outputs = [ctx.outputs.out],
        mnemonic = "CcAutoconf",
        env = env | ctx.configuration.default_shell_env,
    )

    return [DefaultInfo(files = depset([ctx.outputs.out]))]

autoconf = rule(
    implementation = _autoconf_impl,
    doc = """\
Generate configuration headers using autoconf-like checks.

This rule mimics autoconf functionality by running compilation checks
against the configured cc_toolchain and generating a config.h file with
the results.

Example:

```python
autoconf(
    name = "config",
    out = "config.h",
    checks = [
        macros.AC_CHECK_HEADER("stdio.h"),
        macros.AC_CHECK_HEADER("stdlib.h"),
        macros.AC_CHECK_FUNC("printf"),
    ],
    package_name = "myproject",
    package_version = "1.0.0",
)
```

With file-based checks:

```python
autoconf(
    name = "config",
    out = "config.h",
    checks = [
        macros.AC_TRY_COMPILE(
            define = "CUSTOM_CHECK",
            file = ":custom_test.c",
        ),
    ],
    data = [":custom_test.c"],
)
```
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
        "out": attr.output(
            doc = "The output config file (typically `config.h`).",
            mandatory = True,
        ),
        "package_name": attr.string(
            doc = "Package name for `PACKAGE_NAME` define.",
            default = "",
        ),
        "package_version": attr.string(
            doc = "Package version for `PACKAGE_VERSION` define.",
            default = "",
        ),
        "template": attr.label(
            doc = "Optional template file (`config.h.in`) to use as base.",
            allow_single_file = [
                ".c.in",
                ".cc.in",
                ".cpp.in",
                ".h.in",
                ".hh.in",
                ".hpp.in",
            ],
        ),
        "_runner": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/autoconf"),
        ),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
)
