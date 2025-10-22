"""# autoconf_config

Common utilities for autoconf rules.
"""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc:find_cc_toolchain.bzl", "find_cpp_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

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
        total = len(transitive_checks)
        new = len(dep_info.results)
        updated = transitive_checks | dep_info.results
        if total + new != len(updated):
            providers = {}
            duplicates = [define_name for define_name in dep_info.results.keys() if define_name in transitive_checks]
            for duplicate in duplicates:
                providers[duplicate] = []
                for info in dep_infos:
                    if duplicate in info.results:
                        providers[duplicate].append(str(info.owner))

            fail("A duplicate check was detected in dependencies of `{}`: {}".format(
                label,
                json.encode_indent(providers, indent = " " * 4),
            ))
        transitive_checks = updated

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
