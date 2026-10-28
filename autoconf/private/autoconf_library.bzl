"""autoconf implementation"""

load("@bazel_features//:features.bzl", "bazel_features")
load("@bazel_skylib//rules:common_settings.bzl", "BuildSettingInfo")
load("@rules_cc//cc:find_cc_toolchain.bzl", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_info.bzl", "CcInfo")
load(
    "//autoconf/private:autoconf_config.bzl",
    "AUTOCONF_EXEC_GROUP",
    "cc_deps_args",
    "collect_cc_deps",
    "collect_defaults",
    "collect_deps",
    "collect_transitive_results",
    "constant_check_result",
    "create_config_dict",
    "encode_result",
    "get_autoconf_toolchain_cache",
    "get_cc_toolchain_info",
    "get_environment_variables",
    "merge_defaults",
    "write_config_json",
)
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars", "strip_macro_args")
load("//autoconf/private:ctx_actions_write.bzl", "write")
load("//autoconf/private:providers.bzl", "CcAutoconfDefaultsInfo", "CcAutoconfInfo")

_CONTENT_KEY_FIELDS = (
    "type",
    "code",
    "code_must_fail",
    "language",
    "define_value",
    "define_value_fail",
    "error",
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

def _check_content_key(check, target_flags = None):
    """Compute a deterministic content key from a check's implementation fields.

    Consumer metadata (name, define, subst, unquote) is excluded so that
    identical checks with different consumer names share the same key.
    `target_flags` (the target's copts / conlyopts / cxxopts) are part of it
    when set, since they change the result.
    """
    pairs = sorted([
        (k, check[k])
        for k in _CONTENT_KEY_FIELDS
        if k in check
    ])
    if target_flags:
        # The target's own copts change what every check of it sees: a result
        # computed under other flags (a dependency's, the toolchain cache's)
        # is not this target's.  Flagless targets keep the plain key.
        pairs.append(("target_flags", target_flags))
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

def _duplicate_message(kind, symbol, local_check, local_file, dep_file):
    """The error for a define or subst both this target and a dependency produce.

    Result files are shared by content: a literal `AC_SUBST("X", 0)` reuses
    the file of any earlier check with the same value, so the local file is
    named after whichever check first produced that content.  Name the local
    check itself, so the message points at the producer to remove.
    """
    local_name = local_check.get("name") if type(local_check) == "dict" else None
    return (
        "{kind} '{symbol}' is produced both by this target and by a dependency, from different checks:\n" +
        "  Local:    check `{local}` (result file {local_file}, shared by content with other checks of the same value)\n" +
        "  Dep:      {dep_file}\n" +
        "Two producers of one {lower} are rejected. Remove the local check, or drop the dependency that provides it."
    ).format(
        kind = kind,
        symbol = symbol,
        local = local_name or "?",
        local_file = local_file.path,
        dep_file = dep_file.path,
        lower = kind.lower(),
    )

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
        available_content_cache,
        settings):
    """Render each `build_settings` entry as a pre-computed check result.

    For each (target, metadata_json) pair, read the BuildSettingInfo value,
    write a result file containing ``encode_result(value)``, and register it
    in the same cache/define/subst/content_cache dicts the JSON-check loop
    populates. Content-key dedup applies the same way: two targets that
    reference the same flag with the same resolved value share one file.
    `settings` collects each entry as `name` -> (text, published names): the
    text of its value and the define key and subst name it renders under,
    for the make variables.
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
        text = value if type(value) == "string" else json.encode(value)

        # A define wants the JSON spelling, a string quoted as `#define X "..."` is; a
        # substitution wants the text itself, which is what the checker writes for an AC_SUBST
        # result and what the header renderer places at `@X@`.  An entry that is both gets a
        # second file for the substitution.
        quoted = encode_result(value)
        raw = json.encode_indent({"success": True, "value": text}, indent = " " * 4) + "\n"
        content_key = json.encode([
            "build_setting",
            str(target.label),
            value,
            "define" if define else "subst",
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
                content = quoted if define else raw,
            )
        subst_output = output
        if define and subst:
            subst_key = json.encode(["build_setting", str(target.label), value, "subst"])
            if subst_key in available_content_cache:
                subst_output = available_content_cache[subst_key]
            elif subst_key in content_cache:
                subst_output = content_cache[subst_key]
            else:
                subst_output = ctx.actions.declare_file("{}/{}.subst.result.cache.json".format(ctx.label.name, name))
                write(
                    actions = ctx.actions,
                    output = subst_output,
                    content = raw,
                )
            content_cache[subst_key] = subst_output

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
            subst_results[subst_name] = subst_output

        cache_checks[name] = source_desc
        cache_results[name] = output
        content_cache[content_key] = output
        published = [key for key in [define_key if define else None, subst_name if subst else None] if key]
        settings[name] = struct(text = text, published = published)

def _template_variables(ctx, settings):
    """The `TemplateVariableInfo` of the build settings.

    Every `build_settings` entry is a make variable under each name it renders as, its define
    key and its subst name, so a `cc_*` target listing this target in `toolchains` reads the
    setting's value as `$(VAR)` in `local_defines` or `copts` the way `autoconf_hdr` reads
    `@VAR@` (a Makefile's `-DPLUGIN_DIR="..."` following `--with-plugin-dir`).  `template_vars`
    adds names: make variable -> the `name` of the entry whose value it carries.  Nothing else
    can be a make variable: a check's result is an action output, unknown at analysis time.

    Args:
        ctx: The rule context.
        settings: Entry name -> struct(text, published), from `_process_build_settings`.

    Returns:
        A `platform_common.TemplateVariableInfo`.
    """
    variables = {}
    sources = {}

    def publish(variable, name):
        if variable in sources and sources[variable] != name:
            fail("Make variable `{}` of `{}` is produced by two build settings, `{}` and `{}`.".format(
                variable,
                ctx.label,
                sources[variable],
                name,
            ))
        sources[variable] = name
        variables[variable] = settings[name].text

    for name, setting in settings.items():
        for variable in setting.published:
            publish(variable, name)
    for variable, name in ctx.attr.template_vars.items():
        if name not in settings:
            fail(("template_vars of `{}`: `{}` names `{}`, which is not the `name` of a `build_settings` entry " +
                  "of this target. Only a build setting is known at analysis time; a check's result is " +
                  "produced by an action and cannot be a make variable.").format(
                ctx.label,
                variable,
                name,
            ))
        publish(variable, name)
    return platform_common.TemplateVariableInfo(variables)

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

    # `cc_deps`: configure's CPPFLAGS and LIBS.  Their flags are part of every
    # check's identity (content key): a check compiled against gmp's headers
    # shares no result with the same check compiled without them.
    cc_deps = collect_cc_deps(getattr(ctx.attr, "cc_deps", []), toolchain_info.compiler_type, ctx.bin_dir.path)
    target_flags = [getattr(ctx.attr, name, []) for name in ("copts", "conlyopts", "cxxopts")]
    target_flags.append(cc_deps.key)
    target_flags = target_flags if any(target_flags) else None

    # Get transitive dependencies to compute compile_defines paths
    deps = collect_deps(ctx.attr.deps)
    dep_infos = deps.to_list()
    dep_results = collect_transitive_results(dep_infos)

    # Defaults (`default_deps`, plus those the deps carry) are read beneath
    # everything else and passed on apart from the results, so a dependent
    # never mistakes them for probe results.
    default_infos = collect_defaults(ctx.attr.default_deps, dep_infos)
    defaults = merge_defaults(default_infos.to_list())

    # Content-based cache: reuse results for checks with identical implementation
    # regardless of consumer naming (define/subst/name).
    available_content_cache = dep_results["content_cache"]
    if resolve_toolchain:
        tc_content_cache = get_autoconf_toolchain_cache(ctx)
        available_content_cache = tc_content_cache | available_content_cache

    cache_checks = {}
    define_checks = {}
    subst_checks = {}

    # Checks with `error`: their result is wanted for its side effect, the
    # build stopping when the probe fails, whether or not a header consumes
    # it.  They go into the `_validation` output group, which Bazel builds
    # whenever the target is built, directly or as a dependency.
    validation_outputs = []

    cache_results = {}
    content_cache = {}
    define_results = {}
    subst_results = {}
    unquoted_defines = []

    actions = {}
    check_specs = []

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
        content_key = _check_content_key(check, target_flags)

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

            # The spec is written for every check resolved here so the
            # `autoconf_checks` output group lists the same files whether or
            # not the checker runs.  For constants nothing consumes it: it is a
            # default output of this target, so a direct build pays one
            # FileWrite, but reaching the target through `deps` never does.
            check_spec = ctx.actions.declare_file("{}/{}.check.json".format(ctx.label.name, name))
            write(
                actions = ctx.actions,
                output = check_spec,
                content = json.encode_indent(check, indent = " " * 4) + "\n",
            )
            check_specs.append(check_spec)

            # Literal defines/substs with no runtime inputs are written
            # directly: the checker would only echo the value back, so the
            # process spawn buys nothing.  Everything else gets a checker action.
            constant_content = constant_check_result(check)
            if constant_content != None:
                write(
                    actions = ctx.actions,
                    output = output,
                    content = constant_content,
                )
            else:
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
        if check.get("error"):
            validation_outputs.append(output)

    settings = {}
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
        settings = settings,
    )
    template_variables = _template_variables(ctx, settings)

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
                    fail(_duplicate_message("Define", define_name, define_checks[strip_macro_args(define_name)], existing_file, define_file))

    for subst_name, subst_file in dep_results["subst"].items():
        if subst_name in subst_results:
            existing_file = subst_results[subst_name]
            if existing_file.path != subst_file.path:
                if not _same_content_key(existing_file, subst_file, path_to_content_key):
                    fail(_duplicate_message("Subst", subst_name, subst_checks[subst_name], existing_file, subst_file))

    # What this target's checks may read: the dependencies' defaults first,
    # so an ordinary result of the same name wins.
    all_results = {
        "cache": defaults.cache | dep_results["cache"] | cache_results,
        "define": defaults.define | dep_results["define"] | define_results,
        "subst": defaults.subst | dep_results["subst"] | subst_results,
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
        cc_deps_args(args, cc_deps)

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
            exec_group = AUTOCONF_EXEC_GROUP,
            executable = ctx.executable._checker,
            arguments = [args],
            inputs = depset(inputs + [check_json] + check_deps, transitive = [cc_deps.inputs]),
            outputs = [check_result_file],
            mnemonic = "CcAutoconfCheck",
            progress_message = "CcAutoconfCheck %{label} - " + check_name,
            env = env | ctx.configuration.default_shell_env,
            tools = toolchain_info.cc_toolchain.all_files,
            # TODO: https://github.com/periareon/rules_cc_autoconf/issues/148
            execution_requirements = {"supports-path-mapping": ""} if bazel_features.rules.write_action_has_execution_requirements else {},
        )

    # The provider: this target's own results, and the defaults it read
    # (its dependencies' and its `default_deps`'), for dependents to read beneath theirs.
    own_files = cache_results.values() + define_results.values() + subst_results.values()
    info = CcAutoconfInfo(
        owner = ctx.label,
        deps = deps,
        cache_results = cache_results,
        content_cache = content_cache,
        define_results = define_results,
        subst_results = subst_results,
        unquoted_defines = unquoted_defines,
        defaults = default_infos,
    )
    return [
        info,
        template_variables,
        OutputGroupInfo(
            _validation = depset(validation_outputs),
            autoconf_checks = depset(check_specs),
            autoconf_results = depset(own_files),
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
              "to JSON metadata produced by `checks.AC_BUILD_SETTING`. Each entry is also a make " +
              "variable under its define and subst names: a `cc_*` target listing this target in " +
              "`toolchains` reads the setting's value as `$(VAR)` in `local_defines` or `copts`, as " +
              "`autoconf_hdr` reads `@VAR@`.",
        providers = [BuildSettingInfo],
        default = {},
    ),
    "cc_deps": attr.label_list(
        doc = "Libraries the checks compile against and link, as configure's CPPFLAGS and LIBS carry a " +
              "project's dependencies: every check sees their include directories and defines, and every " +
              "link probe links their archives after its own object.  Their flags are part of each check's " +
              "identity, so a check never shares a result between targets with different `cc_deps`.  Prefer " +
              "`cc_deps = [\"@gmp//:gmp\"]` with a real `AC_CHECK_LIB` over a static `AC_DEFINE`.",
        providers = [CcInfo],
    ),
    "checks": attr.string_list(
        doc = "List of JSON-encoded checks from checks (e.g., `checks.AC_CHECK_HEADER('stdio.h')`).",
        default = [],
    ),
    "conlyopts": attr.string_list(
        doc = "Flags for the C compiles of this target's checks only, after `copts` (as on a cc_library).  " +
              "Not transitive: flags for every check of a build, the gnulib ports included, are `--copt`.",
    ),
    "copts": attr.string_list(
        doc = "Flags for every compile of this target's checks, after the toolchain's and `--copt` / " +
              "`--cxxopt` and before a check's own `copts` (the order of a cc_library's), so they win " +
              "over the command line where the last flag counts.  Not transitive.",
    ),
    "cxxopts": attr.string_list(
        doc = "Flags for the C++ compiles of this target's checks only, after `copts` (as on a cc_library).  " +
              "Not transitive.",
    ),
    "default_deps": attr.label_list(
        doc = "`autoconf_defaults` targets.  Their values stand where no result of the name comes through " +
              "`deps`; a result overrides a default silently, and two defaults of one name must agree.  " +
              "The defaults a `deps` entry carries are read as well, and dependents of this target read all " +
              "of them in turn.  This is gnulib's `*_H_DEFAULTS` pattern (`HAVE_X=1`, `REPLACE_X=0` before the " +
              "probes may reassign them): list a header's `:defaults` here where the m4 `AC_REQUIRE`s " +
              "`gl_<HEADER>_H_DEFAULTS`.",
        providers = [CcAutoconfDefaultsInfo],
    ),
    "deps": attr.label_list(
        doc = "Additional `autoconf`, `autoconf_library`, or `autoconf_package_info` dependencies.",
        providers = [CcAutoconfInfo],
    ),
    "template_vars": attr.string_dict(
        doc = "Further make variables: variable -> the `name` of the `build_settings` entry whose value " +
              "it carries (`{\"PLUGIN_DIRECTORY\": \"ac_cv_subst_PLUGIN_DIR\"}`), beyond the define and " +
              "subst names every entry publishes. Only a build setting can be one; a check's result is " +
              "an action output and naming it fails.",
        default = {},
    ),
    "_checker": attr.label(
        cfg = config.exec(AUTOCONF_EXEC_GROUP),
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
    exec_groups = {AUTOCONF_EXEC_GROUP: exec_group(toolchains = use_cc_toolchain())},
    toolchains = [
        config_common.toolchain_type("@rules_cc_autoconf//autoconf:toolchain_type", mandatory = False),
    ],
    provides = [CcAutoconfInfo, platform_common.TemplateVariableInfo],
)

def _autoconf_cache_impl(ctx):
    return autoconf_impl_common(ctx, resolve_toolchain = False)

autoconf_cache = rule(
    implementation = _autoconf_cache_impl,
    doc = """\
Run autoconf-like checks without resolving the autoconf toolchain.

Identical to ``autoconf`` except that it does **not** resolve the
``autoconf_toolchain``.  Use this rule for targets that are listed as
``cache_deps`` of an ``autoconf_toolchain`` -- using the regular ``autoconf``
rule in that position would create a dependency cycle.

Dep-level caching still applies: if a check's cache variable name already
has a result in transitive ``deps``, the action is skipped.
""",
    attrs = COMMON_ATTRS,
    fragments = ["cpp"],
    exec_groups = {AUTOCONF_EXEC_GROUP: exec_group(toolchains = use_cc_toolchain())},
    provides = [CcAutoconfInfo, platform_common.TemplateVariableInfo],
)
