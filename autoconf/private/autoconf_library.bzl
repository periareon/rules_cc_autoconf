"""autoconf implementation"""

load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "create_config_dict",
    "get_autoconf_toolchain_cache",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "write_config_json",
)
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

_CONTENT_KEY_FIELDS = (
    "type",
    "code",
    "language",
    "define_value",
    "define_value_fail",
    "if_true",
    "if_false",
    "library",
    "libraries",
    "requires",
    "condition",
    "compile_defines",
    "includes",
    "members",
)

def _check_content_key(check):
    """Compute a deterministic content key from a check's implementation fields.

    Consumer metadata (name, define, subst, unquote) is excluded so that
    identical checks with different consumer names share the same key.
    """
    pairs = sorted([
        (k, check[k])
        for k in _CONTENT_KEY_FIELDS
        if k in check
    ])
    return json.encode(pairs)

def _same_content_key(file_a, file_b, path_to_content_key):
    """Return True when two files represent the same check implementation.

    Looks up both files in the reverse content-cache map.  If both resolve
    to the same content key the conflict is a benign sibling duplication
    (two independent targets ran an identical check) rather than a genuine
    implementation mismatch.
    """
    key_a = path_to_content_key.get(file_a.path)
    key_b = path_to_content_key.get(file_b.path)
    if key_a == None or key_b == None:
        return False
    return key_a == key_b

def _coerce_name(name, value):
    if type(value) == "string":
        return value

    return name

def autoconf_impl_common(ctx, resolve_toolchain):
    """Shared implementation for autoconf and autoconf_library rules.

    Args:
        ctx: The rule's context object.
        resolve_toolchain: Whether or not to use the toolchain cache.

    Returns:
        The target providers.
    """

    # Get cc_toolchain info
    toolchain_info = get_cc_toolchain_info(ctx)

    # Get transitive dependencies to compute compile_defines paths
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    # Content-based cache: reuse results for checks with identical implementation
    # regardless of consumer naming (define/subst/name).
    available_content_cache = dict(dep_results["content_cache"])
    if resolve_toolchain:
        tc_content_cache = get_autoconf_toolchain_cache(ctx)
        available_content_cache = tc_content_cache | available_content_cache

    cache_checks = {}
    define_checks = {}
    subst_checks = {}

    cache_results = {}
    content_cache = {}
    define_results = {}
    subst_results = {}
    unquoted_defines = []

    actions = {}

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

        # Compute content key from implementation fields only
        content_key = _check_content_key(check)

        # Same check implementation already processed in this target — idempotent skip
        if content_key in content_cache and name in cache_results:
            continue

        # Reuse result from deps or toolchain when content key matches (cache hit)
        if content_key in available_content_cache:
            output = available_content_cache[content_key]
        elif content_key in content_cache:
            output = content_cache[content_key]
        else:
            output = ctx.actions.declare_file("{}/{}.result.cache.json".format(ctx.label.name, name))

            check_spec = ctx.actions.declare_file("{}/{}.check.json".format(ctx.label.name, name))
            ctx.actions.write(
                output = check_spec,
                content = json.encode_indent(check, indent = " " * 4) + "\n",
            )

            actions[name] = struct(
                output = output,
                check = check,
                input = check_spec,
            )

        # Define/subst conflict detection: different cache variable claiming same symbol = error
        if define:
            check["define"] = define_name

            if define_name in define_results:
                fail("Define variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
                    define_name,
                    ctx.label,
                    define_checks[define_name],
                    check,
                ))

            define_checks[define_name] = check
            define_results[define_name] = output

            if check.get("unquote", False):
                unquoted_defines.append(define_name)

        if subst:
            check["subst"] = subst_name

            if subst_name in subst_checks:
                fail("Subst variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
                    subst_name,
                    ctx.label,
                    subst_checks[subst_name],
                    check,
                ))

            subst_checks[subst_name] = check
            subst_results[subst_name] = output

        cache_checks[name] = check
        cache_results[name] = output
        content_cache[content_key] = output

    # Write config to JSON
    config_json = write_config_json(ctx, create_config_dict(
        toolchain_info = toolchain_info,
    ))

    # Get environment variables from the toolchain (like LIB, INCLUDE, PATH for MSVC)
    # We need environment variables from both compile and link actions since the autoconf
    # runner performs both compilation and linking. For MSVC, the INCLUDE environment
    # variable from the compile action is crucial for finding standard headers like stdint.h
    env = get_environment_variables(ctx, toolchain_info)

    inputs = [config_json]

    # Check for conflicts when merging local results with dependency results.
    # Cache variable conflicts are relaxed: same name from different actions is
    # fine because content-based dedup handles true identity.
    # Define/subst conflicts remain strict: same symbol from different checks
    # is a real error — unless both files share the same content key, which
    # indicates a benign sibling duplication (identical implementations that
    # were run independently because the targets don't depend on each other).
    path_to_content_key = {}
    for ckey, cfile in content_cache.items():
        path_to_content_key[cfile.path] = ckey
    for ckey, cfile in dep_results["content_cache"].items():
        path_to_content_key[cfile.path] = ckey

    for define_name, define_file in dep_results["define"].items():
        if define_name in define_results:
            existing_file = define_results[define_name]
            if existing_file.path != define_file.path:
                if not _same_content_key(existing_file, define_file, path_to_content_key):
                    fail("Define '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:       {}\nThis indicates duplicate defines. Consider removing the local define or using a different name.".format(
                        define_name,
                        existing_file.path,
                        define_file.path,
                    ))

    for subst_name, subst_file in dep_results["subst"].items():
        if subst_name in subst_results:
            existing_file = subst_results[subst_name]
            if existing_file.path != subst_file.path:
                if not _same_content_key(existing_file, subst_file, path_to_content_key):
                    fail("Subst '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:       {}\nThis indicates duplicate subst. Consider removing the local subst or using a different name.".format(
                        subst_name,
                        existing_file.path,
                        subst_file.path,
                    ))

    all_results = {
        "cache": cache_results | dep_results["cache"],
        "define": define_results | dep_results["define"],
        "subst": subst_results | dep_results["subst"],
    }

    # Create individual CcAutoconfCheck actions for each cache variable
    # All checks sharing the same cache variable are processed together
    # (checks is already grouped by cache_name from _flatten_checks)
    for check_name, action in actions.items():
        check_result_file = action.output
        check = action.check
        check_json = action.input

        all_required_defines = []

        for required in check.get("requires", []):
            all_required_defines.extend(extract_condition_vars(required))

        for dep_name in check.get("input_deps", []):
            all_required_defines.extend(extract_condition_vars(dep_name))

        condition = check.get("condition")
        if condition:
            all_required_defines.extend(extract_condition_vars(condition))

        for required in check.get("compile_defines", []):
            all_required_defines.extend(extract_condition_vars(required))

        args = ctx.actions.args()
        args.use_param_file("@%s", use_always = True)
        args.set_param_file_format("multiline")
        args.add("--config", config_json)
        args.add("--check", check_json)
        args.add("--results", check_result_file)

        # Collect dependencies for all required defines
        # Build a dictionary mapping lookup_name -> file_path
        # This ensures strict deduplication before passing to C++
        name_to_file = {}  # lookup_name -> file_path

        for required_define in depset(all_required_defines).to_list():
            dep_results_file = None
            for group_name in ["cache", "define", "subst"]:
                if required_define in all_results[group_name]:
                    candidate_file = all_results[group_name][required_define]
                    if dep_results_file:
                        # Check if it's the same file (legitimate duplicate from AC_DEFINE with subst=True)
                        if dep_results_file != candidate_file:
                            # Check if this is a legitimate duplicate: same variable in both define and subst groups
                            # When AC_DEFINE has subst=True and define_name == subst_name, both reference
                            # the same cache file directly, so they're the same result
                            is_legitimate_duplicate = (
                                required_define in all_results["define"] and
                                required_define in all_results["subst"] and
                                group_name in ["define", "subst"]
                            )
                            if is_legitimate_duplicate:
                                # Same variable in both define and subst - they reference the same cache file
                                # Use the define file (arbitrary but consistent choice)
                                if group_name == "subst":
                                    continue  # Skip subst, use define

                                # If we already have define, skip this (shouldn't happen, but be safe)
                                if dep_results_file == all_results["define"][required_define]:
                                    continue

                            # Different files - real conflict
                            all_duplicates = {
                                "cache": sorted([k for k in all_results["cache"].keys() if k == required_define]),
                                "define": sorted([k for k in all_results["define"].keys() if k == required_define]),
                                "subst": sorted([k for k in all_results["subst"].keys() if k == required_define]),
                            }
                            fail("Duplicate results were found for check `{}`. Please update `{}`.\n Available options: {}".format(
                                required_define,
                                ctx.label,
                                json.encode_indent(all_duplicates, indent = " " * 4) + "\n",
                            ))

                        # Same file - no conflict, continue (AC_DEFINE with subst=True case)
                    else:
                        dep_results_file = candidate_file

            if not dep_results_file:
                all_available = {
                    "cache": sorted(all_results["cache"].keys()),
                    "define": sorted(all_results["define"].keys()),
                    "subst": sorted(all_results["subst"].keys()),
                }
                fail("No results were found for check `{}`. Please update `{}`.\n Available options: {}".format(
                    required_define,
                    ctx.label,
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

        # Add --dep arguments with explicit name=file format
        check_deps = []
        for lookup_name, file_path in name_to_file.items():
            check_deps.append(file_path)
            args.add("--dep", "{}={}".format(lookup_name, file_path.path))

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = depset(inputs + [check_json] + check_deps),
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + check_name,
            env = env | ctx.configuration.default_shell_env,
            tools = toolchain_info.cc_toolchain.all_files,
        )

    # Return provider with result buckets and content cache for dedup
    return [
        CcAutoconfInfo(
            owner = ctx.label,
            deps = deps,
            cache_results = cache_results,
            content_cache = content_cache,
            define_results = define_results,
            subst_results = subst_results,
            unquoted_defines = unquoted_defines,
        ),
        OutputGroupInfo(
            autoconf_checks = depset([action.input for action in actions.values()]),
            autoconf_results = depset(cache_results.values() + define_results.values() + subst_results.values()),
        ),
    ]

def _autoconf_impl(ctx):
    return autoconf_impl_common(ctx, resolve_toolchain = True)

COMMON_ATTRS = {
    "checks": attr.string_list(
        doc = "List of JSON-encoded checks from checks (e.g., `checks.AC_CHECK_HEADER('stdio.h')`).",
        default = [],
    ),
    "deps": attr.label_list(
        doc = "Additional `autoconf`, `autoconf_library`, or `package_info` dependencies.",
        providers = [CcAutoconfInfo],
    ),
    "_checker": attr.label(
        cfg = "exec",
        executable = True,
        default = Label("//autoconf/private/checker:checker_bin"),
    ),
}

autoconf = rule(
    implementation = _autoconf_impl,
    doc = """\
Run autoconf-like checks and produce results.

This rule resolves the ``autoconf_toolchain`` (when registered) to skip redundant
checker actions.  If a check's cache variable name already has a result in the
toolchain or in transitive ``deps``, the existing result file is reused.

Use ``autoconf_library`` instead for targets that feed into ``autoconf_toolchain``
to avoid a dependency cycle.

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
    attrs = COMMON_ATTRS,
    fragments = ["cpp"],
    toolchains = use_cc_toolchain() + [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
    provides = [CcAutoconfInfo],
)
