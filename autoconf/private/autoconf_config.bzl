"""# autoconf_config

Common utilities for autoconf rules.
"""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

_TOOLCHAIN_TYPE = "//autoconf:toolchain_type"

def get_autoconf_toolchain_defaults(ctx):
    """Get default checks from the autoconf toolchain if available.

    Args:
        ctx (ctx): The rule context.

    Returns:
        struct: A struct with `cache`, `define`, and `subst` fields, each containing
                a dict[str, File] mapping variable names to result files.
                Returns struct(cache={}, define={}, subst={}) if no toolchain is configured.
    """

    # Access toolchain - returns None if not registered or mandatory=False
    toolchain = ctx.toolchains[_TOOLCHAIN_TYPE]
    if not toolchain:
        return struct(cache = {}, define = {}, subst = {})

    autoconf_defaults = getattr(toolchain, "autoconf_defaults", None)
    if not autoconf_defaults:
        return struct(cache = {}, define = {}, subst = {})

    return struct(
        cache = getattr(autoconf_defaults, "cache", {}),
        define = getattr(autoconf_defaults, "define", {}),
        subst = getattr(autoconf_defaults, "subst", {}),
    )

def get_autoconf_toolchain_defaults_by_label(ctx):
    """Get default checks from the autoconf toolchain, organized by source label.

    Args:
        ctx (ctx): The rule context.

    Returns:
        dict: A mapping of source labels to structs with `cache`, `define`, and `subst` fields,
              each containing a dict[str, File]. Returns empty dict if no toolchain is configured.
    """

    # Access toolchain - returns None if not registered or mandatory=False
    toolchain = ctx.toolchains[_TOOLCHAIN_TYPE]
    if not toolchain:
        return {}

    autoconf_defaults = getattr(toolchain, "autoconf_defaults", None)
    if not autoconf_defaults:
        return {}

    return getattr(autoconf_defaults, "defaults_by_label", {})

def filter_defaults(defaults_by_label, include_labels, exclude_labels):
    """Filter toolchain defaults based on include/exclude lists.

    Args:
        defaults_by_label (dict): Mapping of Label -> struct(cache=..., define=..., subst=...) from toolchain.
        include_labels (list[Label]): If non-empty, only include defaults from these labels.
            An error is raised if a label is specified but not found in the toolchain.
        exclude_labels (list[Label]): If non-empty, exclude defaults from these labels.
            Labels not found in the toolchain are silently ignored.

    Returns:
        struct: A struct with `cache`, `define`, and `subst` fields, each containing
                a merged dict[str, File] after filtering.
    """
    if include_labels and exclude_labels:
        fail("defaults_include and defaults_exclude are mutually exclusive")

    result_cache = {}
    result_define = {}
    result_subst = {}

    if include_labels:
        # Only include specified labels
        for label in include_labels:
            if label not in defaults_by_label:
                fail("defaults_include specifies label '{}' but it is not provided by the autoconf toolchain. Available labels: {}".format(
                    label,
                    ", ".join([str(label_key) for label_key in defaults_by_label.keys()]),
                ))
            label_defaults = defaults_by_label[label]
            result_cache = result_cache | getattr(label_defaults, "cache", {})
            result_define = result_define | getattr(label_defaults, "define", {})
            result_subst = result_subst | getattr(label_defaults, "subst", {})
    elif exclude_labels:
        # Include all except specified labels
        exclude_set = {label: True for label in exclude_labels}
        for label, label_defaults in defaults_by_label.items():
            if label not in exclude_set:
                result_cache = result_cache | getattr(label_defaults, "cache", {})
                result_define = result_define | getattr(label_defaults, "define", {})
                result_subst = result_subst | getattr(label_defaults, "subst", {})
    else:
        # Include all
        for label_defaults in defaults_by_label.values():
            result_cache = result_cache | getattr(label_defaults, "cache", {})
            result_define = result_define | getattr(label_defaults, "define", {})
            result_subst = result_subst | getattr(label_defaults, "subst", {})

    return struct(
        cache = result_cache,
        define = result_define,
        subst = result_subst,
    )

def merge_with_defaults(defaults, results):
    """Merge check results with toolchain defaults.

    Results from actual targets take precedence over defaults - any variable
    present in both will use the result value, not the default.

    Args:
        defaults (dict): Default checks from toolchain (variable name -> File).
        results (dict): Check results from targets (variable name -> File).

    Returns:
        dict: Merged results with targets overriding defaults.
    """

    # Defaults first, then results override
    return defaults | results

def collect_transitive_results(dep_infos):
    """Collect transitive cache variable results.

    Args:
        dep_infos (list): A list of `CcAutoconfInfo`.

    Returns:
        dict: A mapping of cache variable name to check result files.
    """
    cache_results = {}
    define_results = {}
    subst_results = {}
    for dep_info in dep_infos:
        # Check for conflicts before merging - same cache variable from different targets
        # should point to the same file (they're the same check result)
        # Compare file paths, not File objects, since the same file from different
        # dependencies might be different File objects
        for cache_name, cache_file in dep_info.cache_results.items():
            if cache_name in cache_results:
                existing_file = cache_results[cache_name]

                # Compare file paths to see if they're the same file
                if existing_file.path != cache_file.path:
                    # Different files for the same cache variable - this is a conflict
                    # This should not happen if Starlark properly prevents duplicates
                    fail("Cache variable '{}' is defined in multiple dependencies with different result files:\n  First:  {}\n  Second: {}\nThis indicates duplicate checks across different autoconf targets.".format(
                        cache_name,
                        existing_file.path,
                        cache_file.path,
                    ))
            cache_results[cache_name] = cache_file

        for define_name, define_file in dep_info.define_results.items():
            if define_name in define_results:
                existing_file = define_results[define_name]
                if existing_file.path != define_file.path:
                    fail("Define '{}' is defined in multiple dependencies with different result files:\n  First:  {}\n  Second: {}\nThis indicates duplicate defines across different autoconf targets.".format(
                        define_name,
                        existing_file.path,
                        define_file.path,
                    ))
            define_results[define_name] = define_file

        for subst_name, subst_file in dep_info.subst_results.items():
            if subst_name in subst_results:
                existing_file = subst_results[subst_name]
                if existing_file.path != subst_file.path:
                    fail("Subst '{}' is defined in multiple dependencies with different result files:\n  First:  {}\n  Second: {}\nThis indicates duplicate subst across different autoconf targets.".format(
                        subst_name,
                        existing_file.path,
                        subst_file.path,
                    ))
            subst_results[subst_name] = subst_file

    return {
        "cache": cache_results,
        "define": define_results,
        "subst": subst_results,
    }

def collect_deps(deps):
    """Collect `CcAutoconfInfo` from dependencies.

    Args:
        deps (list): A list of `Target`

    Returns:
        depset: A depset of `CcAutoconfInfo`.
    """
    direct = []
    transitive = []
    for dep in deps:
        info = dep[CcAutoconfInfo]
        direct.append(info)
        transitive.append(info.deps)

    return depset(direct, transitive = transitive)

def get_cc_toolchain_info(ctx):
    """Get cc_toolchain information for autoconf configuration.

    Args:
        ctx (ctx): The rule context.

    Returns:
        A struct containing:
            - cc_toolchain: The cc_toolchain
            - feature_configuration: The feature configuration
            - c_compiler_path: Path to C compiler
            - cpp_compiler_path: Path to C++ compiler
            - linker_path: Path to linker
            - c_flags: List of C compiler flags
            - cpp_flags: List of C++ compiler flags
            - c_link_flags: List of C linker flags
            - cpp_link_flags: List of C++ linker flags
            - compiler_type: The compiler type
    """
    cc_toolchain = find_cpp_toolchain(ctx)
    feature_configuration = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features,
    )

    c_compiler_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.c_compile,
    )

    cpp_compiler_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_compile,
    )

    linker_path = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
    )

    compile_variables = cc_common.create_compile_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts,
    )

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

    return struct(
        cc_toolchain = cc_toolchain,
        feature_configuration = feature_configuration,
        c_compiler_path = c_compiler_path,
        cpp_compiler_path = cpp_compiler_path,
        linker_path = linker_path,
        c_flags = c_flags,
        cpp_flags = cpp_flags,
        c_link_flags = c_link_flags,
        cpp_link_flags = cpp_link_flags,
        compiler_type = cc_toolchain.compiler,
    )

def create_config_dict(toolchain_info):
    """Create a config dictionary for the autoconf runner.

    Args:
        toolchain_info (cc_toolchain): Struct from get_cc_toolchain_info().

    Returns:
        A dictionary representing the autoconf config.
    """
    return {
        "c_compiler": toolchain_info.c_compiler_path,
        "c_flags": toolchain_info.c_flags,
        "c_link_flags": toolchain_info.c_link_flags,
        "compiler_type": toolchain_info.compiler_type,
        "cpp_compiler": toolchain_info.cpp_compiler_path,
        "cpp_flags": toolchain_info.cpp_flags,
        "cpp_link_flags": toolchain_info.cpp_link_flags,
        "linker": toolchain_info.linker_path,
    }

def write_config_json(ctx, config_dict):
    """Write a config dictionary to a JSON file.

    Args:
        ctx (ctx): The rule context.
        config_dict (dict): The config dictionary from create_config_dict().

    Returns:
        A File representing the config JSON file.
    """
    config_json = ctx.actions.declare_file("{}.ac.json".format(ctx.label.name))
    ctx.actions.write(
        output = config_json,
        content = json.encode_indent(config_dict, indent = " " * 4) + "\n",
    )
    return config_json

def get_environment_variables(ctx, toolchain_info):
    """Get environment variables for running autoconf checks.

    Args:
        ctx (ctx): The rule context.
        toolchain_info (cc_toolchain): Struct from get_cc_toolchain_info().

    Returns:
        A dictionary of environment variables.
    """
    compile_variables_for_env = cc_common.create_compile_variables(
        feature_configuration = toolchain_info.feature_configuration,
        cc_toolchain = toolchain_info.cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts,
    )
    compile_env = cc_common.get_environment_variables(
        feature_configuration = toolchain_info.feature_configuration,
        action_name = ACTION_NAMES.c_compile,
        variables = compile_variables_for_env,
    )
    link_variables_for_env = cc_common.create_link_variables(
        feature_configuration = toolchain_info.feature_configuration,
        cc_toolchain = toolchain_info.cc_toolchain,
        is_linking_dynamic_library = False,
        is_static_linking_mode = True,
    )
    link_env = cc_common.get_environment_variables(
        feature_configuration = toolchain_info.feature_configuration,
        action_name = ACTION_NAMES.cpp_link_executable,
        variables = link_variables_for_env,
    )

    # Merge compile and link environment variables, with compile taking precedence
    # since it has the critical INCLUDE paths for MSVC
    return link_env | compile_env
