"""autoconf implementation"""

load("@bazel_features//:features.bzl", "bazel_features")
load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load(
    "//autoconf/private:autoconf_config.bzl",
    "collect_deps",
    "collect_transitive_results",
    "create_config_dict",
    "encode_result",
    "get_autoconf_toolchain_cache",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "write_config_json",
)
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars", "strip_macro_args")
load("//autoconf/private:ctx_actions_write.bzl", "write")
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
    "copts",
    "linkopts",
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

def _assert_no_duplicate(kind, name, registry, ctx, new_source):
    """Fail when `name` is already registered for `kind` (Define or Subst).

    Shared by the JSON-check loop and the build-settings loop so both produce
    the same error shape on a duplicate symbol within a single target.
    """
    if name in registry:
        fail("{} variable `{}` is duplicated on `{}`\nLEFT:  {}\nRIGHT: {}".format(
            kind,
            name,
            ctx.label,
            registry[name],
            new_source,
        ))

def _process_build_settings(
        ctx,
        cache_results,
        define_results,
        subst_results,
        content_cache,
        unquoted_defines,
        cache_checks,
        define_checks,
        subst_checks,
        available_content_cache):
    """Render each `build_settings` entry as a pre-computed check result.

    For each (target, metadata_json) pair, read the BuildSettingInfo value,
    write a result file containing ``encode_result(value)``, and register it
    in the same cache/define/subst/content_cache dicts the JSON-check loop
    populates. Content-key dedup applies the same way: two targets that
    reference the same flag with the same resolved value share one file.
    """
    for target, metadata_json in ctx.attr.build_settings.items():
        metadata = json.decode(metadata_json)
        name = metadata.get("name")
        if not name:
            fail("Build setting entry for `{}` on `{}` is missing 'name'.".format(
                target.label,
                ctx.label,
            ))
        define = metadata.get("define")
        subst = metadata.get("subst")
        unquote = metadata.get("unquote", False)

        define_full = _coerce_name(name, define)
        define_key = strip_macro_args(define_full)
        subst_name = _coerce_name(name, _coerce_name(define_key, subst))

        value = target[BuildSettingInfo].value
        content_key = json.encode([
            "build_setting",
            str(target.label),
            value,
        ])

        if content_key in content_cache and name in cache_results:
            continue

        if content_key in available_content_cache:
            output = available_content_cache[content_key]
        elif content_key in content_cache:
            output = content_cache[content_key]
        else:
            output = ctx.actions.declare_file("{}/{}.result.cache.json".format(ctx.label.name, name))
            write(
                actions = ctx.actions,
                output = output,
                content = encode_result(value),
            )

        source_desc = {
            "build_setting": str(target.label),
            "define": define,
            "name": name,
            "subst": subst,
        }

        if define:
            _assert_no_duplicate("Define", define_key, define_checks, ctx, source_desc)
            define_checks[define_key] = source_desc
            define_results[define_full] = output
            if unquote:
                unquoted_defines.append(define_full)

        if subst:
            _assert_no_duplicate("Subst", subst_name, subst_checks, ctx, source_desc)
            subst_checks[subst_name] = source_desc
            subst_results[subst_name] = output

        cache_checks[name] = source_desc
        cache_results[name] = output
        content_cache[content_key] = output

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

        # `define_full` (e.g. "FOO(a, b)") flows through the manifest so the
        # emitted `#define` keeps its arg list; `define_key` ("FOO") is what
        # duplicate detection and the resolver's `#undef` lookup key on.
        define_full = _coerce_name(name, define)
        define_key = strip_macro_args(define_full)
        subst = check.get("subst")

        # Subst will prefer the define name if it's available.
        subst_name = _coerce_name(name, _coerce_name(define_key, subst))

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
            write(
                actions = ctx.actions,
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
            check["define"] = define_full
            _assert_no_duplicate("Define", define_key, define_checks, ctx, check)
            define_checks[define_key] = check

            # Keyed by full text — flows into the manifest as `result.define`.
            define_results[define_full] = output

            if check.get("unquote", False):
                unquoted_defines.append(define_full)

        if subst:
            check["subst"] = subst_name
            _assert_no_duplicate("Subst", subst_name, subst_checks, ctx, check)
            subst_checks[subst_name] = check
            subst_results[subst_name] = output

        cache_checks[name] = check
        cache_results[name] = output
        content_cache[content_key] = output

    _process_build_settings(
        ctx,
        cache_results = cache_results,
        define_results = define_results,
        subst_results = subst_results,
        content_cache = content_cache,
        unquoted_defines = unquoted_defines,
        cache_checks = cache_checks,
        define_checks = define_checks,
        subst_checks = subst_checks,
        available_content_cache = available_content_cache,
    )

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
                    fail("Define '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:      {}\nThis indicates duplicate defines. Consider removing the local define or using a different name.".format(
                        define_name,
                        existing_file.path,
                        define_file.path,
                    ))

    for subst_name, subst_file in dep_results["subst"].items():
        if subst_name in subst_results:
            existing_file = subst_results[subst_name]
            if existing_file.path != subst_file.path:
                if not _same_content_key(existing_file, subst_file, path_to_content_key):
                    fail("Subst '{}' is defined both locally and in dependencies with different result files:\n  Local:    {}\n  Dep:      {}\nThis indicates duplicate subst. Consider removing the local subst or using a different name.".format(
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
            args.add_all([file_path], before_each = "--dep", format_each = "{}=%s".format(lookup_name))

        ctx.actions.run(
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = depset(inputs + [check_json] + check_deps),
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + check_name,
            env = env | ctx.configuration.default_shell_env,
            tools = toolchain_info.cc_toolchain.all_files,
            # TODO: https://github.com/periareon/rules_cc_autoconf/issues/148
            execution_requirements = {"supports-path-mapping": ""} if bazel_features.rules.write_action_has_execution_requirements else {},
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

def to_build_settings_dict(entries):
    """Convert a list of `checks.AC_BUILD_SETTING(...)` structs to the dict the rule attribute requires.

    The `build_settings` attribute on `autoconf` / `autoconf_cache` is typed
    `attr.label_keyed_string_dict` -- the only pre-Bazel-9 attribute type that
    carries `(Label, str)` pairs natively. Users author build settings as a
    list of `checks.AC_BUILD_SETTING(target=..., name=..., define=...)` calls
    (so each entry's keyword-argument signature is self-documenting); the
    public `autoconf` / `autoconf_cache` macros call this helper to fold that
    list into the `{label_str: metadata_json}` shape the rule attribute
    accepts.

    Once Bazel 9 is the minimum supported version, switch the attribute to
    `string_keyed_label_dict` (key = metadata JSON, value = build-setting
    target), have `AC_BUILD_SETTING` return a one-key dict the user composes
    with `|`, and delete this helper together with the wrapping macros.

    Args:
        entries: List of structs produced by `checks.AC_BUILD_SETTING(...)`,
            or `None` / empty for no entries.

    Returns:
        A `dict[str, str]` mapping build-setting label strings to JSON
        metadata blobs, suitable for the rule's `build_settings` attribute.
    """
    if not entries:
        return {}
    result = {}
    for entry in entries:
        if entry.target in result:
            fail("Duplicate build_settings target: {}".format(entry.target))
        result[entry.target] = entry.metadata
    return result

COMMON_ATTRS = {
    "build_settings": attr.label_keyed_string_dict(
        doc = "Mapping of Bazel build setting targets (any rule providing `BuildSettingInfo`) " +
              "to JSON metadata produced by `checks.AC_BUILD_SETTING`.",
        providers = [BuildSettingInfo],
        default = {},
    ),
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

Use ``autoconf_cache`` instead for targets that feed into ``autoconf_toolchain``
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

def _autoconf_cache_impl(ctx):
    return autoconf_impl_common(ctx, resolve_toolchain = False)

autoconf_cache = rule(
    implementation = _autoconf_cache_impl,
    doc = """\
Run autoconf-like checks without resolving the autoconf toolchain.

Identical to ``autoconf`` except that it does **not** resolve the
``autoconf_toolchain``.  Use this rule for targets that are listed as
``cache_deps`` or ``defaults`` of an ``autoconf_toolchain`` -- using the
regular ``autoconf`` rule in that position would create a dependency cycle.

Dep-level caching still applies: if a check's cache variable name already
has a result in transitive ``deps``, the action is skipped.
""",
    attrs = COMMON_ATTRS,
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
    provides = [CcAutoconfInfo],
)
