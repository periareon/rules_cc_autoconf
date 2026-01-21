"""# autoconf"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "create_config_dict",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "write_config_json",
)
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

def _checks_equivalent(a, b):
    """Check if two check dictionaries are equivalent.

    Two checks are equivalent if all their parameters match exactly.
    This is used for deduplication when the same check appears in multiple
    if_true/if_false blocks.

    Args:
        a: First check dictionary.
        b: Second check dictionary.

    Returns:
        True if the checks are equivalent, False otherwise.
    """

    # Compare all fields that affect check behavior
    fields = [
        "type",
        "subst",
        "name",
        "define",
        "language",
        "code",
        "file",
        "file_path",
        "headers",
        "define_value",
        "define_value_fail",
        "library",
        "requires",
        "flag",
    ]
    for field in fields:
        if a.get(field) != b.get(field):
            return False
    return True

def _flatten_checks(raw_checks, label):
    """Process checks and handle deduplication.

    Args:
        raw_checks: List of JSON-encoded check strings.
        label: The label of the target (for error messages).

    Returns:
        A dictionary mapping define names to check dictionaries.
    """
    result = {}

    # Process all checks
    for check_json in raw_checks:
        check = json.decode(check_json)
        define = check["define"]

        # if_true and if_false are now values, not lists of checks
        # They are stored in the check dict and used by the checker to set values
        # No need to extract or process them here

        # Handle uniqueness/deduplication
        if define in result:
            existing = result[define]
            if _checks_equivalent(existing, check):
                # Identical check - skip (silently merge)
                pass
            elif existing.get("type") == "m4_variable":
                # m4_define doesn't generate output, so it can coexist with other types
                # Keep the non-m4_define check as the one that generates output
                if check.get("type") == "m4_variable":
                    # This m4_define is for computation only, existing check handles output
                    pass
                else:
                    # Replace m4_define with the check that generates output
                    result[define] = check
            elif ((existing.get("type") == "define" or existing.get("type") == "define_unquoted" or existing.get("type") == "decl") and check.get("subst") == True) or \
                 (existing.get("type") == "m4_variable" and (check.get("type") == "define" or check.get("type") == "define_unquoted" or check.get("type") == "decl")):
                # AC_DEFINE/AC_DEFINE_UNQUOTED/AC_CHECK_DECL generates config.h defines, AC_SUBST generates @VAR@ substitutions
                # AC_DEFINE/AC_CHECK_DECL values are already used for @VAR@ replacement, so skip subst
                # Keep the define/decl check (it handles both outputs)
                if check.get("subst") == True:
                    # Skip subst - define/decl already provides @VAR@ replacement
                    pass
                else:
                    # Keep define/decl (replaces subst)
                    result[define] = check
            elif existing.get("subst") == True and check.get("subst") == True:
                # Both are subst - allow if equivalent, otherwise conflict
                if _checks_equivalent(existing, check):
                    # Identical subst - skip (silently merge)
                    pass
                else:
                    fail(("Conflicting subst definitions for '{}' in '{}'. " +
                          "The same subst variable is used with different parameters. " +
                          "Use a custom 'define' parameter to disambiguate.").format(
                        define,
                        label,
                    ))
            elif (existing.get("subst") == True and (check.get("type") == "define" or check.get("type") == "define_unquoted" or check.get("type") == "decl")):
                # Existing is subst, new is define/decl - keep define/decl (replaces subst)
                result[define] = check
            else:
                fail(("Conflicting check definitions for '{}' in '{}'. " +
                      "The same define name is used with different parameters. " +
                      "Use a custom 'define' parameter to disambiguate.").format(
                    define,
                    label,
                ))
        else:
            result[define] = check

    return result

def _autoconf_impl(ctx):
    """Implementation of the autoconf rule that only runs checks."""

    # Get cc_toolchain info
    toolchain_info = get_cc_toolchain_info(ctx)

    if len(ctx.files.data) != len(ctx.attr.data):
        fail("`data` targets cannot represent multiple files. Please investigate {}".format(
            ctx.label,
        ))

    data_files = {}
    for target, file in zip(ctx.attr.data, ctx.files.data):
        data_files[str(target.label)] = file

    # Flatten nested checks (if_true/if_false) into a flat list with requirements
    checks = _flatten_checks(ctx.attr.checks, ctx.label)

    # Process file dependencies after flattening
    check_inputs = {}
    for define_name, check in checks.items():
        # Collect file labels for dependency resolution
        if "file" in check:
            if check["file"] not in data_files:
                fail("Check `{}` requires file `{}` but it was not provided. Options are: `{}`".format(
                    check["define"],
                    check["file"],
                    data_files.keys(),
                ))

            check_inputs[check["define"]] = data_files[check["file"]]
            check["file_path"] = check_inputs[check["define"]].path
            check.pop("file")

    # Get transitive dependencies to compute compile_defines paths
    deps = collect_deps(ctx.attr.deps)
    transitive_checks = collect_transitive_results(ctx.label, deps.to_list())

    # Declare result files first (needed for all_results)
    results = {}
    for check_key, check in checks.items():
        define_name = check["define"]
        if define_name in transitive_checks:
            # Check if this is a define/subst conflict, which is allowed
            # AC_DEFINE/AC_DEFINE_UNQUOTED and AC_SUBST with the same name can coexist
            # (one generates config.h defines, the other generates @VAR@ substitutions)
            check_type = check.get("type")
            is_define_type = check_type in ["define", "define_unquoted"]
            is_subst_type = check.get("subst") == True

            # If current check is a define/subst type, allow it to coexist with transitive checks
            # The _flatten_checks logic already handles define/subst conflicts within the same target
            # We assume transitive checks of the same name are likely subst if we're creating a define,
            # or define if we're creating a subst, which is the common pattern
            if is_define_type or is_subst_type:
                # Allow define/subst to coexist with transitive checks
                # This matches the behavior in _flatten_checks which allows define and subst with same name
                pass
            else:
                providers = []
                for dep_info in deps.to_list():
                    if define_name in dep_info.results:
                        providers.append(str(dep_info.owner))

                fail("Check `{}` is duplicating a dependent check for `{}`: {}".format(
                    define_name,
                    ctx.label,
                    providers,
                ))

        check_result_file = ctx.actions.declare_file("{}/{}.json".format(ctx.label.name, define_name))
        results[define_name] = check_result_file

    all_results = results | transitive_checks

    # Convert compile_defines from define names to file paths before creating config
    # This allows compile_defines to resolve values from transitive dependencies
    for define_name, check in checks.items():
        compile_defines_list = check.get("compile_defines", [])
        if compile_defines_list:
            compile_defines_paths = []
            for compile_def in compile_defines_list:
                if compile_def in all_results:
                    compile_def_file = all_results[compile_def]

                    # Store the file path as a string (not File object) for JSON encoding
                    compile_defines_paths.append(compile_def_file.path)

            # Store the paths (not names) in the check for C++ code to read
            if compile_defines_paths:
                check["compile_defines"] = compile_defines_paths

    # Create config dictionary (now with compile_defines paths included)
    config_dict = create_config_dict(
        toolchain_info = toolchain_info,
        checks = checks.values(),
    )

    # Write config to JSON
    config_json = write_config_json(ctx, config_dict)

    # Get environment variables from the toolchain (like LIB, INCLUDE, PATH for MSVC)
    # We need environment variables from both compile and link actions since the autoconf
    # runner performs both compilation and linking. For MSVC, the INCLUDE environment
    # variable from the compile action is crucial for finding standard headers like stdint.h
    env = get_environment_variables(ctx, toolchain_info)

    # Create individual CcAutoconfCheck actions for each check
    # Each check is identified by its unique define name

    # Process all checks - those with subst=True will create both define and subst results
    for check_key, check in checks.items():
        define_name = check["define"]
        check_result_file = results[define_name]

        inputs = [config_json]
        if define_name in check_inputs:
            inputs.append(check_inputs[define_name])

        args = ctx.actions.args()
        args.use_param_file("@%s", use_always = True)
        args.set_param_file_format("multiline")
        args.add("--config", config_json)
        args.add("--results", check_result_file)
        args.add("--check-define", define_name)

        # Helper function to extract base define name from a requirement/condition string
        def _extract_define_name(expr):
            """Extract base define name from requirement/condition expression.

            Handles: "FOO", "!FOO", "FOO==value", "FOO!=value", "FOO=value"
            """
            required_define = expr

            if required_define.startswith("!"):
                # Handle negation prefix
                required_define = required_define[1:]

            if "!=" in required_define:
                # Handle != operator
                required_define = required_define.split("!=")[0]
            elif "==" in required_define:
                # Handle == operator
                required_define = required_define.split("==")[0]
            elif "=" in required_define:
                # Handle legacy = operator
                required_define = required_define.split("=")[0]

            return required_define

        # Collect all required defines from both requires and condition
        required_defines = list(check.get("requires", []))
        if "condition" in check:
            condition = check["condition"]

            # Parse condition to extract the define name it depends on
            condition_define = _extract_define_name(condition)

            # Add to required_defines if not already there (to ensure it's in inputs)
            if condition not in required_defines:
                required_defines.append(condition)

        for required in required_defines:
            # Extract base define name
            # Extract base define name (handles "FOO", "!FOO", "FOO==value", etc.)
            required_define = _extract_define_name(required)

            if required_define not in all_results:
                fail("Check `{}` requires `{}` but it's not provided. Please update `{}`".format(
                    define_name,
                    required_define,
                    ctx.label,
                ))

            required_check = all_results[required_define]
            inputs.append(required_check)
            args.add("--required", required_check)

        # Add compile_defines result files to inputs (paths are strings in check dict, need File objects)
        compile_defines_paths = check.get("compile_defines", [])
        for compile_def_path_str in compile_defines_paths:
            # Find the File object from all_results using the path
            for result_define, result_file in all_results.items():
                if result_file.path == compile_def_path_str:
                    inputs.append(result_file)
                    break

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = inputs,
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + define_name,
            env = env | ctx.configuration.default_shell_env,
            tools = toolchain_info.cc_toolchain.all_files,
        )

    # Return a dict mapping define names to result files
    return [
        CcAutoconfInfo(
            owner = ctx.label,
            deps = deps,
            results = results,
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
        "deps": attr.label_list(
            doc = "Additional `autoconf` or `package_info` dependencies.",
            providers = [CcAutoconfInfo],
        ),
        "_checker": attr.label(
            cfg = "exec",
            executable = True,
            default = Label("//autoconf/private/checker:checker_bin"),
        ),
    },
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
    provides = [CcAutoconfInfo],
)
