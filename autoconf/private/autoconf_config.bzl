"""# autoconf_config

Common utilities for autoconf rules.
"""

load("@rules_cc//cc:action_names.bzl", "ACTION_NAMES")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")
load("//autoconf/private:ctx_actions_write.bzl", "write")
load("//autoconf/private:providers.bzl", "CcAutoconfDefaultsInfo", "CcAutoconfInfo")

_TOOLCHAIN_TYPE = "//autoconf:toolchain_type"

# Compiler probes use the checker and C++ tools on one execution platform.
AUTOCONF_EXEC_GROUP = "autoconf"

_COPTS_MARKER = "{rules_cc_autoconf:copts}"
_LINKOPTS_MARKER = "{rules_cc_autoconf:linkopts}"

def encode_result(value, success = True):
    """Encode a value as a flat result JSON string.

    Returns the content for a result file that can be referenced from
    ``CcAutoconfInfo``.  Using this function insulates callers from the
    internal result-file schema.

    Args:
        value: The result value.  Strings are JSON-encoded (quoted in the
            output); numbers and booleans are stored as their JSON
            representation.  Use ``None`` for a valueless define
            (``#define FOO`` with no value).
        success: Whether the check succeeded.  Defaults to ``True``.
            A result with ``success=False`` is rendered as
            ``/* #undef FOO */`` in the generated header.

    Returns:
        A JSON string suitable for ``ctx.actions.write``.
    """
    return json.encode_indent({
        "success": success,
        "value": json.encode(value) if value != None else None,
    }, indent = " " * 4) + "\n"

# Every character the constant fast path accepts inside a string value.  The
# checker (nlohmann::json) and Starlark's `json.encode` agree on how to escape
# these; anything outside this set falls back to the checker so the result
# file stays byte-identical regardless of which path produced it.
_ASCII_PRINTABLE = " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"

_CONSTANT_CHECK_TYPES = ("define", "m4_variable")

# Fields that make the checker consult other results at execution time.  A
# check using any of them must still run through the checker.
_RUNTIME_INPUT_FIELDS = ("condition", "requires", "compile_defines", "input_deps")

def constant_check_result(check):
    """Return the result-file content for a check that needs no execution.

    `AC_DEFINE` / `AC_SUBST` / `M4_VARIABLE` style checks with a literal value
    and no `condition`/`requires` never touch a compiler: `check_define`
    simply echoes the value back.  (It also ignores `code`, so the placeholder
    program `M4_VARIABLE` carries does not disqualify it.)  For those the
    result can be written at analysis time instead of spawning a
    `CcAutoconfCheck` action.  The content produced here
    mirrors `checker.cc` exactly (`{success, type, value}` with 4-space
    indent, sorted keys, trailing newline) so downstream consumers cannot
    tell the two apart.

    Args:
        check: Decoded check dict (as passed in the `checks` attribute).

    Returns:
        The file content as a string, or `None` when the check must run
        through the checker.
    """
    check_type = check.get("type")
    if check_type not in _CONSTANT_CHECK_TYPES:
        return None
    for field in _RUNTIME_INPUT_FIELDS:
        if check.get(field):
            return None

    # Missing or `None` both reach `check_define` as "no value" -> "".
    value = check.get("define_value")
    if value == None:
        value = ""
    value_type = type(value)
    if value_type == "string":
        for char in value.elems():
            if char not in _ASCII_PRINTABLE:
                return None
    elif value_type == "int":
        # nlohmann parses the dumped integer back into a 64-bit number.
        if value >= 9223372036854775808 or value < -9223372036854775808:
            return None
    elif value_type != "bool":
        return None

    return json.encode_indent({
        "success": True,
        "type": check_type,
        "value": value,
    }, indent = " " * 4) + "\n"

def get_autoconf_toolchain_cache(ctx):
    """Get the content-based cache from the autoconf toolchain.

    Returns the content cache of the toolchain's ``cache_deps``.  Used by the
    ``autoconf`` rule for content-based action deduplication.

    Args:
        ctx (ctx): The rule context (must declare the autoconf toolchain type).

    Returns:
        dict[str, File]: Mapping of content keys to result files.
                         Empty dict if no toolchain is configured.
    """
    toolchain = ctx.toolchains[_TOOLCHAIN_TYPE]
    if not toolchain:
        return {}
    cache = getattr(toolchain, "autoconf_cache", None)
    return cache if cache else {}

def collect_defaults(default_deps, dep_infos):
    """The defaults a target reads: its `default_deps` and the defaults its `deps` carry.

    Args:
        default_deps (list[Target]): The `default_deps` attribute (`autoconf_defaults` targets).
        dep_infos (list[CcAutoconfInfo]): The transitive `deps`.

    Returns:
        depset[CcAutoconfDefaultsInfo]: What this target reads and, as an
            `autoconf` target, passes on to its dependents.
    """
    return depset(
        [dep[CcAutoconfDefaultsInfo] for dep in default_deps],
        transitive = [info.defaults for info in dep_infos],
    )

def merge_defaults(default_infos):
    """Flatten defaults into one set of results.

    Two defaults of one name must agree: the gnulib pattern has exactly one
    `_H_DEFAULTS` macro assign each variable, so a disagreement is a port bug,
    not something to resolve by order.

    Args:
        default_infos (list[CcAutoconfDefaultsInfo]): Usually `collect_defaults(...).to_list()`.

    Returns:
        struct: `cache`, `define`, `subst` dicts (name to result file) and the
            `unquoted_defines` list.
    """
    content = {}
    owner = {}
    for info in default_infos:
        for path, text in info.content.items():
            content[path] = text
            owner[path] = info.owner
    out = {"cache": {}, "define": {}, "subst": {}}
    unquoted = {}
    for info in default_infos:
        for kind, results in (("cache", info.cache_results), ("define", info.define_results), ("subst", info.subst_results)):
            for name, file in results.items():
                existing = out[kind].get(name)
                if existing != None and existing.path != file.path and content.get(existing.path) != content.get(file.path):
                    fail("Default {} '{}' is given two values:\n  {}: {}\n  {}: {}".format(
                        kind,
                        name,
                        owner.get(existing.path),
                        json.decode(content.get(existing.path, "{}")).get("value"),
                        owner.get(file.path),
                        json.decode(content.get(file.path, "{}")).get("value"),
                    ))
                out[kind][name] = file
        for uq in info.unquoted_defines:
            unquoted[uq] = True
    return struct(cache = out["cache"], define = out["define"], subst = out["subst"], unquoted_defines = unquoted.keys())

def merge_results(dep_results, defaults):
    """What a consumer reads: the defaults beneath the ordinary results.

    Args:
        dep_results (dict): `collect_transitive_results` of `deps`.
        defaults (struct): `merge_defaults` of the defaults read.

    Returns:
        struct: `cache`, `define`, `subst` dicts and the `unquoted_defines` list.
    """
    unquoted = {uq: True for uq in defaults.unquoted_defines}
    for uq in dep_results.get("unquoted_defines", []):
        unquoted[uq] = True
    return struct(
        cache = defaults.cache | dep_results["cache"],
        define = defaults.define | dep_results["define"],
        subst = defaults.subst | dep_results["subst"],
        unquoted_defines = unquoted.keys(),
    )

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

def collect_transitive_results(dep_infos):
    """Collect transitive cache variable results.

    Args:
        dep_infos (list): A list of `CcAutoconfInfo`.

    Returns:
        dict: A mapping with keys "cache", "content_cache", "define", "subst"
              (each dict[str, File]) and "unquoted_defines" (list[str]).
              Defaults are not results: `collect_defaults` gathers them.
    """
    cache_results = {}
    content_cache = {}
    define_results = {}
    subst_results = {}
    unquoted_defines_set = {}
    path_to_content_key = {}
    for dep_info in dep_infos:
        # Cache variable names — no conflict detection needed since content-based
        # dedup handles identity; the same name may appear from different actions
        # that are semantically identical.
        for cache_name, cache_file in dep_info.cache_results.items():
            cache_results[cache_name] = cache_file

        # Content cache — same content key always means the same check, so
        # merging is safe without conflict detection.  Also build a reverse
        # map (file path -> content key) so that downstream conflict checks
        # can distinguish "same implementation, different File object" from
        # genuinely conflicting implementations.
        for ckey, cfile in dep_info.content_cache.items():
            content_cache[ckey] = cfile
            path_to_content_key[cfile.path] = ckey

        for define_name, define_file in dep_info.define_results.items():
            if define_name in define_results:
                existing_file = define_results[define_name]
                if existing_file.path != define_file.path:
                    if not _same_content_key(existing_file, define_file, path_to_content_key):
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
                    if not _same_content_key(existing_file, subst_file, path_to_content_key):
                        fail("Subst '{}' is defined in multiple dependencies with different result files:\n  First:  {}\n  Second: {}\nThis indicates duplicate subst across different autoconf targets.".format(
                            subst_name,
                            existing_file.path,
                            subst_file.path,
                        ))
            subst_results[subst_name] = subst_file

        for uq in dep_info.unquoted_defines:
            unquoted_defines_set[uq] = True

    return {
        "cache": cache_results,
        "content_cache": content_cache,
        "define": define_results,
        "subst": subst_results,
        "unquoted_defines": sorted(unquoted_defines_set.keys()),
    }

def collect_transitive_content_cache(dep_infos):
    """Collect transitive `content_cache` without conflict detection.

    For action deduplication only. Unlike `collect_transitive_results`,
    this does NOT validate define/subst uniqueness — the sole caller is
    `autoconf_toolchain`'s `cache_deps` path, which intentionally tolerates
    overlap so a broad sweep of cache providers (e.g., every gnulib m4
    module) can be passed without analysis-time conflicts. Last-write-wins
    is safe because the content key already encodes the full check
    implementation, so any two files mapped to the same key are
    interchangeable.

    Args:
        dep_infos (list): A list of `CcAutoconfInfo`.

    Returns:
        dict[str, File]: Mapping of content keys to result files.
    """
    content_cache = {}
    for dep_info in dep_infos:
        for ckey, cfile in dep_info.content_cache.items():
            content_cache[ckey] = cfile
    return content_cache

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

# Some features are incompatible with the probing and result in errors
# across various toolchain implementations and rules_cc versions.
_UNSUPPORTED_RULES_CC_FEATURES = [
    # Enforces declared-only header use; needs `%{module_name}` + cc_library module map.
    "layering_check",
    # Adds `-fmodule-map-file` / `-fmodule-name` to compiles; needs module map variables.
    "use_module_maps",
    # Toolchain capability to emit `.cppmap` files for cc_library hdrs; no hdrs here.
    "module_maps",
    # Synthesizes per-header `-fsyntax-only` actions for cc_library hdrs; nothing to parse.
    "parse_headers",
    # Compiles cc_library hdrs into clang PCMs; needs `%{module_name}` + `.pcm` graph.
    "header_modules",
    # C++20 named modules orchestration; needs module interface unit context.
    "cpp_modules",
]

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
    toolchain = ctx.exec_groups[AUTOCONF_EXEC_GROUP].toolchains[Label("@bazel_tools//tools/cpp:toolchain_type")]
    cc_toolchain = toolchain.cc if hasattr(toolchain, "cc_provider_in_toolchain") and hasattr(toolchain, "cc") else toolchain
    feature_configuration = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features + _UNSUPPORTED_RULES_CC_FEATURES,
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

    # The target's own copts / conlyopts / cxxopts, as on a cc_library: for
    # its checks only, after --copt / --cxxopt and before a check's own copts.
    copts = getattr(ctx.attr, "copts", [])
    conlyopts = getattr(ctx.attr, "conlyopts", [])
    cxxopts = getattr(ctx.attr, "cxxopts", [])
    c_compile_variables = cc_common.create_compile_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + copts + conlyopts + [_COPTS_MARKER],
    )

    c_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.c_compile,
        variables = c_compile_variables,
    )

    cpp_compile_variables = cc_common.create_compile_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        user_compile_flags = ctx.fragments.cpp.copts + ctx.fragments.cpp.cxxopts + copts + cxxopts + [_COPTS_MARKER],
    )

    cpp_flags = cc_common.get_memory_inefficient_command_line(
        feature_configuration = feature_configuration,
        action_name = ACTION_NAMES.cpp_compile,
        variables = cpp_compile_variables,
    )

    link_variables = cc_common.create_link_variables(
        feature_configuration = feature_configuration,
        cc_toolchain = cc_toolchain,
        is_linking_dynamic_library = False,
        is_static_linking_mode = True,
        user_link_flags = ctx.fragments.cpp.linkopts + [_LINKOPTS_MARKER],
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
    write(
        actions = ctx.actions,
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
        user_compile_flags = ctx.fragments.cpp.copts,
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
