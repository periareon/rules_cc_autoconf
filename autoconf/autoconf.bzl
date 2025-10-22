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
    """Flatten nested conditional checks into a flat list with requirements.

    This processes checks with if_true/if_false nested checks and converts
    them into a flat list where nested checks have their parent's define
    added to their requires list.

    Args:
        raw_checks: List of JSON-encoded check strings.
        label: The label of the target (for error messages).

    Returns:
        A dictionary mapping define names to flattened check dictionaries.
    """
    result = {}

    # Queue entries are (check_dict, inherited_requires)
    queue = []

    # Initialize queue with top-level checks
    for check_json in raw_checks:
        check = json.decode(check_json)
        queue.append((check, []))

    for _ in range(10000):  # Prevent infinite loops
        if not queue:
            break

        check, inherited_reqs = queue.pop(0)
        define = check["define"]

        # Extract nested checks before processing
        if_true_checks = check.pop("if_true", [])
        if_false_checks = check.pop("if_false", [])

        # Merge inherited requires with check's own requires
        own_requires = check.get("requires", [])
        all_requires = inherited_reqs + own_requires

        if all_requires:
            check["requires"] = all_requires
        elif "requires" in check:
            check.pop("requires")

        # Handle uniqueness/deduplication
        if define in result:
            existing = result[define]
            if _checks_equivalent(existing, check):
                # Identical check - skip (silently merge)
                pass
            else:
                fail(("Conflicting check definitions for '{}' in '{}'. " +
                      "The same define name is used with different parameters. " +
                      "Use a custom 'define' parameter to disambiguate.").format(
                    define,
                    label,
                ))
        else:
            result[define] = check

        # Queue if_true checks - they require this check to succeed
        for nested in if_true_checks:
            queue.append((nested, all_requires + [define]))

        # Queue if_false checks - they require this check to fail
        for nested in if_false_checks:
            queue.append((nested, all_requires + ["!" + define]))

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

    # Create config dictionary
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

    deps = collect_deps(ctx.attr.deps)
    transitive_checks = collect_transitive_results(ctx.label, deps.to_list())

    # Create individual CcAutoconfCheck actions for each check
    # Each check is identified by its unique define name
    # First, declare all result files
    results = {}
    for check in checks.values():
        define_name = check["define"]
        if define_name in transitive_checks:
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

    for check in checks.values():
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

        for required in check.get("requires", []):
            # Extract base define name
            # Handle various syntaxes:
            #   - "FOO" - simple success check
            #   - "!FOO" - negated check (failure)
            #   - "FOO==value" - value equality
            #   - "FOO!=value" - value inequality
            #   - "FOO=value" - legacy value equality
            required_define = required

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

            if required_define not in all_results:
                fail("Check `{}` requires `{}` but it's not provided. Please update `{}`".format(
                    define_name,
                    required_define,
                    ctx.label,
                ))

            required_check = all_results[required_define]
            inputs.append(required_check)
            args.add("--required", required_check)

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = inputs,
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
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
