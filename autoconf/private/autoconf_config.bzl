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
        dict: A mapping of define names to check result files from toolchain defaults,
              or an empty dict if no toolchain is configured.
    """

    # Access toolchain - returns None if not registered or mandatory=False
    toolchain = ctx.toolchains[_TOOLCHAIN_TYPE]
    if not toolchain:
        return {}

    autoconf_defaults = getattr(toolchain, "autoconf_defaults", None)
    if not autoconf_defaults:
        return {}

    return autoconf_defaults.defaults

def get_autoconf_toolchain_defaults_by_label(ctx):
    """Get default checks from the autoconf toolchain, organized by source label.

    Args:
        ctx (ctx): The rule context.

    Returns:
        dict: A mapping of source labels to dicts of (define name -> check result file),
              or an empty dict if no toolchain is configured.
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
        defaults_by_label (dict): Mapping of Label -> dict[str, File] from toolchain.
        include_labels (list[Label]): If non-empty, only include defaults from these labels.
            An error is raised if a label is specified but not found in the toolchain.
        exclude_labels (list[Label]): If non-empty, exclude defaults from these labels.
            Labels not found in the toolchain are silently ignored.

    Returns:
        dict: A merged dict of define name -> File after filtering.
    """
    if include_labels and exclude_labels:
        fail("defaults_include and defaults_exclude are mutually exclusive")

    if include_labels:
        # Only include specified labels
        result = {}
        for label in include_labels:
            if label not in defaults_by_label:
                fail("defaults_include specifies label '{}' but it is not provided by the autoconf toolchain. Available labels: {}".format(
                    label,
                    ", ".join([str(l) for l in defaults_by_label.keys()]),
                ))
            result = result | defaults_by_label[label]
        return result
    elif exclude_labels:
        # Include all except specified labels
        result = {}
        exclude_set = {label: True for label in exclude_labels}
        for label, defines in defaults_by_label.items():
            if label not in exclude_set:
                result = result | defines
        return result
    else:
        # Include all
        result = {}
        for defines in defaults_by_label.values():
            result = result | defines
        return result

def merge_with_defaults(defaults, results):
    """Merge check results with toolchain defaults.

    Results from actual targets take precedence over defaults - any define
    present in both will use the result value, not the default.

    Args:
        defaults (dict): Default checks from toolchain (define name -> File).
        results (dict): Check results from targets (define name -> File).

    Returns:
        dict: Merged results with targets overriding defaults.
    """

    # Defaults first, then results override
    return defaults | results

def collect_transitive_results(label, dep_infos):
    """Collect transitive results while failing on duplicates.

    Args:
        label (Label): The target running this macro.
        dep_infos (list): A list of `CcAutoconfInfo`.

    Returns:
        dict: A mapping of define name to check result files.
    """
    transitive_checks = {}
    for dep_info in dep_infos:
        # Merge results, allowing duplicates to pass through
        # The conflict detection in autoconf.bzl will handle define/subst coexistence
        transitive_checks = transitive_checks | dep_info.results

    return transitive_checks

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

def create_config_dict(toolchain_info, checks):
    """Create a config dictionary for the autoconf runner.

    Args:
        toolchain_info (cc_toolchain): Struct from get_cc_toolchain_info().
        checks (list[str]): List of processed checks.

    Returns:
        A dictionary representing the autoconf config.
    """
    return {
        "c_compiler": toolchain_info.c_compiler_path,
        "c_flags": toolchain_info.c_flags,
        "c_link_flags": toolchain_info.c_link_flags,
        "checks": checks,
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
