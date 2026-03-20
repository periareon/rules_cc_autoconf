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

def _coerce_name(name, value):
    if type(value) == "string":
        return value

    return name

def _extract_define_name(expr):
    """Extract base define name from requirement/condition expression.

    Handles: "FOO", "!FOO", "FOO==value", "FOO!=value", "FOO<value",
    "FOO>value", "FOO<=value", "FOO>=value", "FOO=value"
    """
    required_define = expr

    if required_define.startswith("!"):
        required_define = required_define[1:]

    for op in ["<=", ">=", "!=", "==", "<", ">", "="]:
        if op in required_define:
            required_define = required_define.split(op)[0]
            break

    return required_define

def _add_all_checks(ctx, all_info):
    # Process all checks
    for check_json in ctx.attr.checks:
        check = json.decode(check_json)

        if "name" not in check:
            fail("Check in '{}' is missing 'name' field (cache variable name). All checks must have a 'name' field.".format(
                ctx.label,
            ))

        name = check["name"]
        define = check.get("define")
        define_name = _coerce_name(name, define)
        subst = check.get("subst")

        # Subst will prefer the define name if it's available.
        subst_name = _coerce_name(name, _coerce_name(define_name, subst))

        output = ctx.actions.declare_file("{}/{}.result.cache.json".format(ctx.label.name, name))

        # Check for truthy value
        if define:
            check["define"] = define_name

            if define_name in all_info.define_results:
                fail("Define variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
                    define_name,
                    ctx.label,
                    all_info.define_checks[define_name],
                    check,
                ))

            all_info.define_checks[define_name] = check

            # Use cache file directly - no symlink needed
            all_info.define_results[define_name] = output

        # Check for truthy value
        if subst:
            check["subst"] = subst_name

            if subst_name in all_info.subst_checks:
                fail("Subst variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
                    subst_name,
                    ctx.label,
                    all_info.subst_checks[subst_name],
                    check,
                ))

            all_info.subst_checks[subst_name] = check

            # Use cache file directly - no symlink needed
            all_info.subst_results[subst_name] = output

        # Always add cache variable to cache_results, even if define/subst are set
        # This allows other checks to reference the cache variable in conditions
        if name in all_info.cache_results:
            fail("Cache variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
                name,
                ctx.label,
                all_info.cache_checks[name],
                check,
            ))

        all_info.cache_checks[name] = check
        all_info.cache_results[name] = output

        check_json = ctx.actions.declare_file("{}/{}.check.json".format(ctx.label.name, name))
        ctx.actions.write(
            output = check_json,
            content = json.encode_indent(check, indent = " " * 4) + "\n",
        )

        if name in all_info.actions:
            fail("Duplicate action identified `{}`. Please update `{}`".format(name, ctx.label))

        all_info.actions[name] = struct(
            output = output,
            check = check,
            input = check_json,
        )

def _check_duplicate(all_info, dep_results):
    # Check for conflicts when merging local results with dependency results
    # Same cache variable/define/subst from different sources should point to the same file
    for cache_name, cache_file in dep_results["cache"].items():
        if cache_name in all_info.cache_results:
            existing_file = all_info.cache_results[cache_name]
            if existing_file.path != cache_file.path:
                fail("Cache variable '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:       {}\nThis indicates duplicate checks. Consider removing the local check or using a different cache variable name.".format(
                    cache_name,
                    existing_file.path,
                    cache_file.path,
                ))

    for define_name, define_file in dep_results["define"].items():
        if define_name in all_info.define_results:
            existing_file = all_info.define_results[define_name]
            if existing_file.path != define_file.path:
                fail("Define '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:       {}\nThis indicates duplicate defines. Consider removing the local define or using a different name.".format(
                    define_name,
                    existing_file.path,
                    define_file.path,
                ))

    for subst_name, subst_file in dep_results["subst"].items():
        if subst_name in all_info.subst_results:
            existing_file = all_info.subst_results[subst_name]
            if existing_file.path != subst_file.path:
                fail("Subst '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:       {}\nThis indicates duplicate subst. Consider removing the local subst or using a different name.".format(
                    subst_name,
                    existing_file.path,
                    subst_file.path,
                ))
    pass

def _unique_name_file_map(label, all_required_defines, all_results):
    # Collect dependencies for all required defines
    # Build a dictionary mapping lookup_name -> file_path
    # This ensures strict deduplication before passing to C++
    name_to_file = {}  # lookup_name -> file_path

    for required_define in depset(all_required_defines).to_list():
        dep_results_file = None
        for group_name in ["cache", "define", "subst"]:
            if required_define in getattr(all_results, group_name):
                candidate_file = getattr(all_results, group_name)[required_define]
                if dep_results_file:
                    # Check if it's the same file (legitimate duplicate from AC_DEFINE with subst=True)
                    if dep_results_file != candidate_file:
                        # Check if this is a legitimate duplicate: same variable in both define and subst groups
                        # When AC_DEFINE has subst=True and define_name == subst_name, both reference
                        # the same cache file directly, so they're the same result
                        is_legitimate_duplicate = (
                            required_define in all_results.define and
                            required_define in all_results.subst and
                            group_name in ["define", "subst"]
                        )
                        if is_legitimate_duplicate:
                            # Same variable in both define and subst - they reference the same cache file
                            # Use the define file (arbitrary but consistent choice)
                            if group_name == "subst":
                                continue  # Skip subst, use define

                            # If we already have define, skip this (shouldn't happen, but be safe)
                            if dep_results_file == all_results.define[required_define]:
                                continue

                        # Different files - real conflict
                        all_duplicates = {
                            "cache": sorted([k for k in all_results.cache.keys() if k == required_define]),
                            "define": sorted([k for k in all_results.define.keys() if k == required_define]),
                            "subst": sorted([k for k in all_results.subst.keys() if k == required_define]),
                        }
                        fail("Duplicate results were found for check `{}`. Please update `{}`.\n Available options: {}".format(
                            required_define,
                            label,
                            json.encode_indent(all_duplicates, indent = " " * 4) + "\n",
                        ))

                    # Same file - no conflict, continue (AC_DEFINE with subst=True case)
                else:
                    dep_results_file = candidate_file

        if not dep_results_file:
            all_available = {
                "cache": sorted(all_results.cache.keys()),
                "define": sorted(all_results.define.keys()),
                "subst": sorted(all_results.subst.keys()),
            }
            fail("No results were found for check `{}`. Please update `{}`.\n Available options: {}".format(
                required_define,
                label,
                json.encode_indent(all_available, indent = " " * 4) + "\n",
            ))

        # Deduplicate: check if this name is already mapped
        if required_define in name_to_file:
            if name_to_file[required_define] != dep_results_file:
                fail("Duplicate lookup name '{}' maps to different files:\n  {} -> {}\n  {} -> {}\nThis indicates a bug in dependency resolution.".format(
                    required_define,
                    required_define,
                    name_to_file[required_define],
                    required_define,
                    dep_results_file,
                ))

            # Same name, same file - idempotent, skip
            continue

        # Add mapping
        name_to_file[required_define] = dep_results_file
    return name_to_file

def _collect_required_defines(check):
    all_required_defines = []

    for required in check.get("requires", []):
        required_define = _extract_define_name(required)
        all_required_defines.append(required_define)

    condition = check.get("condition")
    if condition:
        required_define = _extract_define_name(condition)
        all_required_defines.append(required_define)

    for required in check.get("compile_defines", []):
        required_define = _extract_define_name(required)
        all_required_defines.append(required_define)

    return all_required_defines

def _checks_to_build_info(ctx):
    """Implementation of the autoconf rule that only runs checks."""

    # Get cc_toolchain info
    toolchain_info = get_cc_toolchain_info(ctx)

    # Get transitive dependencies to compute compile_defines paths
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    # Write config to JSON
    config_json = write_config_json(ctx, create_config_dict(
        toolchain_info = toolchain_info,
    ))

    # Get environment variables from the toolchain (like LIB, INCLUDE, PATH for MSVC)
    # We need environment variables from both compile and link actions since the autoconf
    # runner performs both compilation and linking. For MSVC, the INCLUDE environment
    # variable from the compile action is crucial for finding standard headers like stdint.h
    env = get_environment_variables(ctx, toolchain_info)

    all_info = struct(
        env = env,
        config_json = config_json,
        toolchain_info = toolchain_info,
        deps = deps,
        cache_checks = {},
        define_checks = {},
        subst_checks = {},
        cache_results = {},
        define_results = {},
        subst_results = {},
        actions = {},
        cache = {},
        define = {},
        subst = {},
    )

    _add_all_checks(ctx, all_info)
    _check_duplicate(all_info, dep_results)

    all_info.cache.update(all_info.cache_results | dep_results["cache"])
    all_info.define.update(all_info.define_results | dep_results["define"])
    all_info.subst.update(all_info.subst_results | dep_results["subst"])

    return all_info

def _autoconf_impl(ctx):
    all_info = _checks_to_build_info(ctx)

    # Create individual CcAutoconfCheck actions for each cache variable
    # All checks sharing the same cache variable are processed together
    # (checks is already grouped by cache_name from _flatten_checks)
    for check_name, action in all_info.actions.items():
        check_result_file = action.output
        check = action.check
        check_json = action.input

        args = ctx.actions.args()
        args.use_param_file("@%s", use_always = True)
        args.set_param_file_format("multiline")
        args.add("--config", all_info.config_json)
        args.add("--check", check_json)
        args.add("--results", check_result_file)

        all_required_defines = _collect_required_defines(check)
        name_to_file = _unique_name_file_map(ctx.label, all_required_defines, all_info)

        # Add --dep arguments with explicit name=file format
        check_deps = []
        for lookup_name, file_path in name_to_file.items():
            check_deps.append(file_path)
            args.add("--dep", "{}={}".format(lookup_name, file_path.path))

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = depset([all_info.config_json] + [check_json] + check_deps),
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + check_name,
            env = all_info.env | ctx.configuration.default_shell_env,
            tools = all_info.toolchain_info.cc_toolchain.all_files,
        )

    # Return provider with three separate buckets
    return [
        CcAutoconfInfo(
            owner = ctx.label,
            deps = all_info.deps,
            cache_results = all_info.cache_results,
            define_results = all_info.define_results,
            subst_results = all_info.subst_results,
        ),
        OutputGroupInfo(
            autoconf_checks = depset([action.input for action in all_info.actions.values()]),
            autoconf_results = depset(all_info.cache_results.values() + all_info.define_results.values() + all_info.subst_results.values()),
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
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

autoconf(
    name = "config",
    checks = [
        checks.AC_CHECK_HEADER("stdio.h"),
        checks.AC_CHECK_HEADER("stdlib.h"),
        checks.AC_CHECK_FUNC("printf"),
    ],
)
```

The results can then be used by `autoconf_hdr` or `autoconf_srcs` to generate headers
or wrapped source files.
""",
    attrs = {
        "checks": attr.string_list(
            doc = "List of JSON-encoded checks from checks (e.g., `checks.AC_CHECK_HEADER('stdio.h')`).",
            default = [],
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
