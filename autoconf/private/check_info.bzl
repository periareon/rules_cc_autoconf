"""Schema for autoconf check definitions.

Provides `make_check()` for JSON encoding (used by factory functions at
loading time) and `AutoconfCheck` provider for structured analysis-time
validation (used only by the validation aspect).
"""

KNOWN_CHECK_FIELDS = {
    "code": "str: C/C++ source code to compile or link for this check.",
    "compile_defines": "list[str]: Preprocessor define names from previous checks to add before includes.",
    "condition": "str: Boolean expression that selects between if_true/if_false values.",
    "define": "(str | bool): Define name to set in config.h, or True to use the cache variable name.",
    "define_value": "(str | int | None): Value for the define when the check succeeds.",
    "define_value_fail": "(str | int | None): Value for the define when the check fails.",
    "flag": "str: Compiler flag to test (for compiler-flag checks).",
    "if_false": "(str | int | None): Value to use when a condition evaluates to false.",
    "if_true": "(str | int | None): Value to use when a condition evaluates to true.",
    "input_deps": "(list[str]) Dependency variable names (wiring only, not run conditions).",
    "language": "(str) Language for the check ('c' or 'cpp').",
    "libraries": "list[str]: Library names to search in order (for search_libs).",
    "library": "str: Single library name to link against (for AC_CHECK_LIB).",
    "name": "str: Cache variable name (e.g. 'ac_cv_header_stdio_h').",
    "requires": "(list[str]): Requirements that must be truthy for the check to run.",
    "subst": "(str | bool | None): Substitution variable name for `@VAR@` replacement, or True to use the cache variable name.",
    "type": "str: Check type (compile, link, function, type, sizeof, alignof, etc.).",
    "unquote": "bool: If true, emit the define with unquoted (AC_DEFINE_UNQUOTED) style.",
}

KNOWN_CHECK_TYPES = {
    "GL_NEXT_HEADER": True,
    "alignof": True,
    "compile": True,
    "compute_int": True,
    "decl": True,
    "define": True,
    "fail": True,
    "function": True,
    "lib": True,
    "link": True,
    "m4_variable": True,
    "member": True,
    "search_libs": True,
    "sizeof": True,
    "subst": True,
    "type": True,
}

TYPES_REQUIRING_CODE = {
    "GL_NEXT_HEADER": True,
    "alignof": True,
    "compile": True,
    "compute_int": True,
    "decl": True,
    "link": True,
    "member": True,
    "search_libs": True,
    "sizeof": True,
}

def make_check(check):
    """Encode a check dict as JSON.

    Single entry point for all factory functions. Currently a thin wrapper
    around json.encode(); exists so that future global transformations or
    legacy conversions can be applied in one place.

    Args:
        check: Dict with check fields (e.g. {"type": "compile", "name": ...}).

    Returns:
        A JSON-encoded string suitable for the autoconf rule's checks attr.
    """
    return json.encode(check)

def _autoconf_check_init(
        type,
        name,
        code = None,
        compile_defines = None,
        condition = None,
        define = None,
        define_value = None,
        define_value_fail = None,
        flag = None,
        if_true = None,
        if_false = None,
        input_deps = None,
        language = None,
        libraries = None,
        library = None,
        requires = None,
        subst = None,
        unquote = None):
    """Validate and construct an AutoconfCheck provider instance.

    Called by the validation aspect after json.decode() to verify that a
    serialized check round-trips into a well-typed provider. Factory
    functions never call this directly.
    """
    if type not in KNOWN_CHECK_TYPES:
        fail("Unknown check type '{}'. Known types: {}".format(
            type,
            ", ".join(sorted(KNOWN_CHECK_TYPES.keys())),
        ))

    if type in TYPES_REQUIRING_CODE and code == None:
        fail("Check '{}' (type '{}') requires a 'code' field.".format(name, type))

    _validate_list_field("compile_defines", compile_defines)
    _validate_list_field("input_deps", input_deps)
    _validate_list_field("requires", requires)
    _validate_list_field("libraries", libraries)

    return {
        "code": code,
        "compile_defines": compile_defines,
        "condition": condition,
        "define": define,
        "define_value": define_value,
        "define_value_fail": define_value_fail,
        "flag": flag,
        "if_false": if_false,
        "if_true": if_true,
        "input_deps": input_deps,
        "language": language,
        "libraries": libraries,
        "library": library,
        "name": name,
        "requires": requires,
        "subst": subst,
        "type": type,
        "unquote": unquote,
    }

def _validate_list_field(field_name, value):
    """Fail if value is not None and not a list."""
    if value != None and type(value) != "list":
        fail("'{}' must be a list, got {}".format(field_name, type(value)))

AutoconfCheckInfo, _new_autoconf_check = provider(
    doc = "Structured autoconf check definition with typed, documented fields. " +
          "Used by the validation aspect to parse check JSON at analysis time.",
    fields = KNOWN_CHECK_FIELDS,
    init = _autoconf_check_init,
)
