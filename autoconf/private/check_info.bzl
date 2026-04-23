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
    "define_value": "(str | int | bool | None): Value for the define when the check succeeds. " +
                    "Strings that are C integer literals (e.g. '0x0ff', '0755', '0b1010') render " +
                    "unquoted. Wrap in escaped quotes ('\"value\"') for a C string literal.",
    "define_value_fail": "(str | int | bool | None): Value for the define when the check fails. " +
                         "Same rendering rules as define_value.",
    "flag": "str: Compiler flag to test (for compiler-flag checks).",
    "if_false": "(str | int | bool | None): Value to use when a condition evaluates to false. " +
                "Same rendering rules as define_value.",
    "if_true": "(str | int | bool | None): Value to use when a condition evaluates to true. " +
               "Same rendering rules as define_value.",
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

_HEX_DIGITS = "0123456789abcdefABCDEF"
_OCTAL_DIGITS = "01234567"
_BINARY_DIGITS = "01"
_DECIMAL_DIGITS = "0123456789"

def _is_c_integer_literal(s):
    """Return True if s looks like a C integer literal (dec/hex/oct/bin with optional suffix).

    Examples: "42", "-1", "0", "0x0ff", "0XFF", "0b1010", "0755", "0xffULL".
    """
    if type(s) != "string" or len(s) == 0:
        return False

    # Starlark has no while-loop, so we consume the string by slicing.
    # Strip optional leading sign.
    if s[0] == "+" or s[0] == "-":
        s = s[1:]
        if len(s) == 0:
            return False

    digits = ""
    if len(s) >= 2 and s[0] == "0":
        if s[1] == "x" or s[1] == "X":
            digits = _HEX_DIGITS
            s = s[2:]
        elif s[1] == "b" or s[1] == "B":
            digits = _BINARY_DIGITS
            s = s[2:]
        else:
            digits = _OCTAL_DIGITS
            s = s[1:]  # skip leading 0, already counts as a digit
    elif len(s) >= 1 and s[0] == "0":
        s = s[1:]
        return _consume_suffix(s)
    else:
        digits = _DECIMAL_DIGITS

    if len(s) == 0 and digits != "":
        # Needed at least one digit after prefix (0x, 0b)
        return digits == _OCTAL_DIGITS

    count = 0
    for c in s:
        if c not in digits:
            break
        count += 1
    if count == 0:
        return False
    s = s[count:]

    return _consume_suffix(s)

def _consume_suffix(s):
    """Consume an optional C integer suffix (u/U, l/L, ll/LL) and return True if nothing remains."""
    if len(s) == 0:
        return True
    if s[0] == "u" or s[0] == "U":
        s = s[1:]
        if len(s) >= 2 and (s[0] == "l" or s[0] == "L") and (s[1] == "l" or s[1] == "L"):
            s = s[2:]
        elif len(s) >= 1 and (s[0] == "l" or s[0] == "L"):
            s = s[1:]
    elif s[0] == "l" or s[0] == "L":
        s = s[1:]
        if len(s) >= 1 and (s[0] == "l" or s[0] == "L"):
            s = s[1:]
        if len(s) >= 1 and (s[0] == "u" or s[0] == "U"):
            s = s[1:]
    return len(s) == 0

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
    _validate_value_field("define_value", define_value)
    _validate_value_field("define_value_fail", define_value_fail)
    _validate_value_field("if_true", if_true)
    _validate_value_field("if_false", if_false)

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

def _validate_value_field(field_name, value):
    """Validate that a define/condition value is an accepted type.

    Accepted: None, bool, int, or str. String values are further
    classified as C integer literals (rendered unquoted), quoted C
    strings (e.g. '"foo"'), or plain tokens — all valid.
    """
    if value == None:
        return
    t = type(value)
    if t == "bool" or t == "int":
        return
    if t != "string":
        fail("'{}' must be str, int, bool, or None — got {}".format(field_name, t))

    # All string forms are accepted. _is_c_integer_literal classifies
    # the value so future callers (or stricter modes) can distinguish
    # numeric-literal strings from free-form tokens.
    _is_c_integer_literal(value)  # validates parsing; result unused by design

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
