"""# checks

This module provides Bazel macros that mirror GNU Autoconf's behavior.

## Cache Variables, Defines, and Subst Values

Checks (AC_CHECK_FUNC, AC_CHECK_HEADER, etc.) create cache variables by default, following
autoconf's naming convention:
- AC_CHECK_HEADER("foo.h") → cache variable "ac_cv_header_foo_h"
- AC_CHECK_FUNC("foo") → cache variable "ac_cv_func_foo"
- AC_CHECK_DECL("foo") → cache variable "ac_cv_have_decl_foo"

Defines and subst values are created explicitly using AC_DEFINE and AC_SUBST.

## Condition Resolution

When AC_DEFINE or AC_SUBST use a `condition` parameter that references a cache variable
(e.g., `condition="ac_cv_header_foo_h"`), the condition is evaluated as a **truthy check**:

- Condition is `true` if:
  - The cache variable exists (check was run)
  - The check succeeded (success = true)
  - The value is truthy (non-empty string, not "0")

- Condition is `false` if:
  - The cache variable doesn't exist (check wasn't run)
  - The check failed (success = false)
  - The value is falsy (empty string or "0")

If the condition includes a comparison operator (e.g., `condition="ac_cv_header_foo_h==1"`),
it performs value comparison instead of truthy check.

Example:
```python
# Check creates cache variable
macros.AC_CHECK_HEADER("alloca.h")
# → Creates cache variable: "ac_cv_header_alloca_h" with value "1" (if header exists)

# Define with truthy condition
macros.AC_DEFINE(
    "HAVE_ALLOCA_H",
    condition="ac_cv_header_alloca_h",  # Truthy: true if check succeeded and value is truthy
    if_true="1",
    if_false=None,  # Don't define if condition is false
)

# Subst with truthy condition
macros.AC_SUBST(
    "HAVE_ALLOCA_H",
    condition="ac_cv_header_alloca_h",  # Truthy check
    if_true="1",
    if_false="0",  # Always set subst value
)
```

## Default Includes

Default includes used by AC_CHECK_DECL, AC_CHECK_TYPE, etc. when no includes are specified.

GNU Autoconf's AC_INCLUDES_DEFAULT uses #ifdef HAVE_* guards, but that relies on
AC_CHECK_HEADERS_ONCE being called earlier to define those macros. In Bazel, each
compile test runs independently without access to results from other checks, so we
use unconditional includes instead. This matches what would happen on a modern
POSIX system where all the standard headers exist.

See: https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#Default-Includes

## Header / includes parameter

Checks that need to include headers (AC_CHECK_DECL, AC_CHECK_TYPE, AC_CHECK_SIZEOF,
AC_CHECK_MEMBER, etc.) take an **`includes`** parameter. **AC_CHECK_FUNC** does not
take includes (standard Autoconf link test only); use **GL_CHECK_FUNCS_ANDROID** (or
other GL_CHECK_FUNCS* in gnulib/macros.bzl) when includes are needed. Both of these forms are
supported:

Use **include directives** as strings, e.g. `"#include <stdlib.h>"` or
`"#include <sys/stat.h>"`, matching Autoconf/gnulib (e.g.
`gl_CHECK_FUNCS_ANDROID([faccessat], [[#include <unistd.h>]])`). The list is
joined with newlines to form the prologue of the test program.

The parameter `headers` is a deprecated alias for `includes`; prefer `includes`.
"""

# Used by AC_CHECK_HEADER (single header name → #include line)
_AC_INCLUDE_FORMAT_WITH_NEWLINE = "#include <{}>\n"

# Default includes for AC_CHECK_DECL, AC_CHECK_TYPE, etc. (AC_INCLUDES_DEFAULT).
# Exposed as utils.AC_INCLUDES_DEFAULT. All includes use the form #include <foo>.
# See: https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#Default-Includes
_AC_INCLUDES_DEFAULT = """\
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include <inttypes.h>
#include <stdint.h>
#ifdef _WIN32
/* Windows doesn't have POSIX headers */
#else
#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
"""

# Template used by AC_LANG_PROGRAM and by AC_TRY_COMPILE/AC_TRY_LINK for
# the includes+code path. Exported for use in checks.bzl.
_AC_LANG_PROGRAM_TEMPLATE = """\
{}

int main(void) {{
    {}
    return 0;
}}
"""

def _header_code_from_includes(includes_list):
    """Build C header block from list of include directives (e.g. ['#include <stdlib.h>'])."""
    if not includes_list:
        return ""
    return "\n".join([h.strip() for h in includes_list])

# AC_CHECK_FUNC default code template (GNU Autoconf extern declaration pattern)
_AC_CHECK_FUNC_DEFAULT_TEMPLATE = """\
/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.
   MSVC does not have GCC builtins, so we can safely use int. */
#ifdef __cplusplus
extern "C"
#endif
#if defined _MSC_VER
/* Since MSVC 2015, many CRT functions (printf, scanf, etc.) are inline
   in UCRT headers and not exported as linker symbols. Link against
   legacy_stdio_definitions.lib to make them available for link tests. */
#pragma comment(lib, "legacy_stdio_definitions.lib")
int {function} ();
#else
char {function} ();
#endif

int main(void) {{
    return {function}();
}}
"""

# Type check code template with includes
_AC_CHECK_TYPE_WITH_INCLUDES_TEMPLATE = """\
{header_code}
int main(void) {{
    if (sizeof({type_name}))
        return 0;
    return 1;
}}
"""

def _add_conditionals(
        check,
        if_true = None,
        if_false = None):
    """Add conditional values to a check dictionary.

    Args:
        check: The check dictionary to add conditionals to.
        if_true: Value to use when check succeeds (string or None).
        if_false: Value to use when check fails (string or None).

    Returns:
        The modified check dictionary.
    """
    if if_true != None:
        check["if_true"] = if_true
    if if_false != None:
        check["if_false"] = if_false
    return check

def _normalize_for_cache_name(s):
    """Normalize a string for use in cache variable names.

    Replaces special characters with underscores, following autoconf conventions.
    """
    return s.replace("/", "_").replace(".", "_").replace("-", "_").replace(" ", "_").replace("*", "P").upper()

def _get_cache_name_for_header(header):
    """Get autoconf cache variable name for AC_CHECK_HEADER.

    Args:
        header: Header name (e.g., "stdio.h", "sys/stat.h")

    Returns:
        Cache variable name (e.g., "ac_cv_header_stdio_h", "ac_cv_header_sys_stat_h")
    """
    normalized = _normalize_for_cache_name(header)
    return "ac_cv_header_" + normalized.lower()

def _get_cache_name_for_func(function):
    """Get autoconf cache variable name for AC_CHECK_FUNC.

    Args:
        function: Function name (e.g., "printf", "malloc")

    Returns:
        Cache variable name (e.g., "ac_cv_func_printf", "ac_cv_func_malloc")
    """
    normalized = _normalize_for_cache_name(function)
    return "ac_cv_func_" + normalized.lower()

def _get_cache_name_for_decl(symbol):
    """Get autoconf cache variable name for AC_CHECK_DECL.

    Args:
        symbol: Symbol name (e.g., "NULL", "stdout")

    Returns:
        Cache variable name (e.g., "ac_cv_have_decl_NULL", "ac_cv_have_decl_stdout")
    """
    normalized = _normalize_for_cache_name(symbol)
    return "ac_cv_have_decl_" + normalized.lower()

def _get_cache_name_for_type(type_name):
    """Get autoconf cache variable name for AC_CHECK_TYPE.

    Args:
        type_name: Type name (e.g., "size_t", "pthread_t")

    Returns:
        Cache variable name (e.g., "ac_cv_type_size_t", "ac_cv_type_pthread_t")
    """
    normalized = _normalize_for_cache_name(type_name)
    return "ac_cv_type_" + normalized.lower()

def _get_cache_name_for_sizeof(type_name):
    """Get autoconf cache variable name for AC_CHECK_SIZEOF.

    Args:
        type_name: Type name (e.g., "int", "long")

    Returns:
        Cache variable name (e.g., "ac_cv_sizeof_int", "ac_cv_sizeof_long")
    """
    normalized = _normalize_for_cache_name(type_name)
    return "ac_cv_sizeof_" + normalized.lower()

def _validate_not_select(value, param_name, macro_name):
    """Validate that a value is not a select().

    Bazel's select() cannot be used with AC_DEFINE, AC_SUBST, or M4_VARIABLE
    because these macros are evaluated at loading time to produce JSON,
    but select() is only resolved at analysis time.

    For platform-specific values, use the `condition` parameter with
    platform detection checks instead.

    Args:
        value: The value to check.
        param_name: Name of the parameter for error message.
        macro_name: Name of the macro for error message.
    """
    if type(value) == "select":
        fail((
            "{}() does not support select() for the '{}' parameter. " +
            "select() is resolved at analysis time, but macro checks are " +
            "evaluated at loading time. For platform-specific values, use " +
            "the 'condition' parameter with a platform detection check, or " +
            "create separate targets for different platforms."
        ).format(macro_name, param_name))

def _ac_check_header(
        header,
        name = None,
        define = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        subst = None):
    """Check for a header file.

    Original m4 example:
    ```m4
    AC_CHECK_HEADER([stdio.h])
    AC_CHECK_HEADER([pthread.h], [AC_CHECK_FUNC([pthread_create])])
    ```

    Example:
    ```python
    # Cache variable only (no define, no subst)
    macros.AC_CHECK_HEADER("stdio.h")
    # → Creates cache variable: "ac_cv_header_stdio_h"

    # Cache variable + define (explicit name)
    macros.AC_CHECK_HEADER("pthread.h", define = "HAVE_PTHREAD_H")
    # → Creates cache variable: "ac_cv_header_pthread_h"
    # → Creates define: "HAVE_PTHREAD_H" in config.h

    # Cache variable + define (using cache var name)
    macros.AC_CHECK_HEADER("pthread.h", define = True)
    # → Creates cache variable: "ac_cv_header_pthread_h"
    # → Creates define: "ac_cv_header_pthread_h" in config.h
    ```

    Args:
        header: Name of the header file (e.g., `"stdio.h"`)
        name: Cache variable name (defaults to `ac_cv_header_<HEADER>` following autoconf convention)
        define: Define behavior:
            - `None` (default): No define is created, only cache variable
            - `True`: Create define using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create define with explicit name `HAVE_FOO` (implies `define=True`)
        includes: Optional list of headers to include.
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        subst: Subst behavior:
            - `None` (default): No subst is created, only cache variable
            - `True`: Create subst using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create subst with explicit name `HAVE_FOO` (implies `subst=True`)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Generate cache variable name if not provided
    if not name:
        name = _get_cache_name_for_header(header)

    # Generate code to include the header
    header_code = _AC_INCLUDE_FORMAT_WITH_NEWLINE.format(header)
    if includes:
        header_code = _header_code_from_includes(includes) + "\n" + header_code

    check = {
        "code": header_code,
        "language": language,
        "name": name,  # Cache variable name (e.g., "ac_cv_header_stdio_h")
        "type": "compile",  # Header checks are just compile checks with #include code
    }

    if define:
        check["define"] = define
    if subst:
        check["subst"] = subst
    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    return json.encode(check)

def _ac_check_func(
        function,
        name = None,
        *,
        define = None,
        code = None,
        language = "c",
        compile_defines = None,
        requires = None,
        subst = None):
    """Check for a function.

    Original m4 example:
    ```m4
    AC_CHECK_FUNC([malloc])
    AC_CHECK_FUNC([mmap], [AC_DEFINE([HAVE_MMAP_FEATURE], [1])])
    ```

    Example:
    ```python
    # Cache variable only (no define, no subst)
    macros.AC_CHECK_FUNC("malloc")
    # → Creates cache variable: "ac_cv_func_malloc"

    # Cache variable + define (explicit name)
    macros.AC_CHECK_FUNC("mmap", define = "HAVE_MMAP")
    # → Creates cache variable: "ac_cv_func_mmap"
    # → Creates define: "HAVE_MMAP" in config.h

    # Cache variable + define (using cache var name)
    macros.AC_CHECK_FUNC("mmap", define = True)
    # → Creates cache variable: "ac_cv_func_mmap"
    # → Creates define: "ac_cv_func_mmap" in config.h

    # Cache variable + subst
    macros.AC_CHECK_FUNC("_Exit", subst = True)
    # → Creates cache variable: "ac_cv_func__Exit"
    # → Creates subst: "ac_cv_func__Exit" in subst.h
    ```

    Args:
        function: Name of the function (e.g., `"printf"`)
        name: Cache variable name (defaults to `ac_cv_func_<FUNCTION>` following autoconf convention)
        define: Define behavior:
            - `None` (default): No define is created, only cache variable
            - `True`: Create define using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create define with explicit name `HAVE_FOO` (implies `define=True`)
        code: Custom code to compile (optional).
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        subst: Subst behavior:
            - `None` (default): No subst is created, only cache variable
            - `True`: Create subst using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create subst with explicit name `HAVE_FOO` (implies `subst=True`)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Generate cache variable name if not provided
    if not name:
        name = _get_cache_name_for_func(function)

    if not code:
        code = _AC_CHECK_FUNC_DEFAULT_TEMPLATE.format(function = function)

    check = {
        "language": language,
        "name": name,  # Cache variable name
        "type": "function",
    }

    check["code"] = code
    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if define:
        check["define"] = define
    if subst:
        check["subst"] = subst

    return json.encode(check)

_AC_TEST_TYPE_CODE_TEMPLATE = _AC_INCLUDES_DEFAULT + """
int main(void) {{
    if (sizeof({type_name}))
        return 0;
    return 1;
}}
"""

def _ac_check_type(
        type_name,
        *,
        name = None,
        define = None,
        code = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check for a type.

    Original m4 example:
    ```m4
    AC_CHECK_TYPE([size_t])
    AC_CHECK_TYPE([pthread_t], [], [], [[#include <pthread.h>]])
    ```

    Example:
    ```python
    # Cache variable only (no define, no subst)
    macros.AC_CHECK_TYPE("size_t")
    # → Creates cache variable: "ac_cv_type_size_t"

    # Cache variable + define (explicit name)
    macros.AC_CHECK_TYPE("pthread_t", includes = ["pthread.h"], define = "HAVE_PTHREAD_T")
    # → Creates cache variable: "ac_cv_type_pthread_t"
    # → Creates define: "HAVE_PTHREAD_T" in config.h
    ```

    Args:
        type_name: Name of the type (e.g., `size_t`)
        name: Cache variable name (defaults to `ac_cv_type_<TYPE>` following autoconf convention)
        define: Define behavior:
            - `None` (default): No define is created, only cache variable
            - `True`: Create define using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create define with explicit name `HAVE_FOO` (implies `define=True`)
        code: Custom code that includes necessary headers (optional, defaults to standard headers)
        includes: Optional list of headers to include. If not specified and
            `code` is not specified, uses AC_INCLUDES_DEFAULT.
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: Subst behavior:
            - `None` (default): No subst is created, only cache variable
            - `True`: Create subst using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create subst with explicit name `HAVE_FOO` (implies `subst=True`)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Generate cache variable name if not provided
    if not name:
        name = _get_cache_name_for_type(type_name)

    # Handle define parameter
    define_name = None
    if define:
        if define == True:
            define_name = name
        else:
            define_name = define

    # Handle subst parameter
    subst_name = None
    if subst:
        if subst == True:
            subst_name = name
        else:
            subst_name = subst

    check = {
        # Always set define field (required by Check class)
        # Use define_name if provided, otherwise use cache variable name
        "define": define_name if define_name else name,
        "language": language,
        "name": name,  # Cache variable name
        "type": "type",
    }

    # Priority: code > file > includes > default
    if code:
        check["code"] = code
    elif includes:
        header_code = _header_code_from_includes(includes)
        check["code"] = _AC_CHECK_TYPE_WITH_INCLUDES_TEMPLATE.format(
            header_code = header_code,
            type_name = type_name,
        )
    else:
        # Use default headers like GNU Autoconf's AC_INCLUDES_DEFAULT
        check["code"] = _AC_TEST_TYPE_CODE_TEMPLATE.format(type_name = type_name)

    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if subst_name:
        check["subst"] = subst_name

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_try_compile(
        *,
        code = None,
        name = None,
        define = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Try to compile custom code.

    Original m4 example:
    ```m4
    AC_TRY_COMPILE([#include <stdio.h>], [printf("test");], [AC_DEFINE([HAVE_PRINTF], [1])])
    ```

    Example:
    ```python
    load("//autoconf:checks.bzl", "utils")
    macros.AC_TRY_COMPILE(
        code = "#include <stdio.h>\\nint main() { printf(\\"test\\"); return 0; }",
        define = "HAVE_PRINTF",
    )
    macros.AC_TRY_COMPILE(file = ":test.c", define = "CUSTOM_CHECK")
    macros.AC_TRY_COMPILE(
        includes = ["pthread.h"],
        code = "int x = PTHREAD_CREATE_DETACHED; (void)x;",
        define = "HAVE_PTHREAD_CREATE_DETACHED",
    )
    macros.AC_TRY_COMPILE(
        code = utils.AC_LANG_PROGRAM(["#include <stdio.h>"], "printf(\"test\");"),
        define = "HAVE_PRINTF",
    )
    ```

    Note:
        This is a rules_cc_autoconf extension. While GNU Autoconf has an
        obsolete AC_TRY_COMPILE macro (replaced by AC_COMPILE_IFELSE), this
        version adds support for file-based checks which is useful in Bazel.

        When `includes` is provided along with `code`, the code is treated as
        the body of main() and the includes are prepended. For the AC_LANG_PROGRAM
        pattern (prologue + body), use `utils.AC_LANG_PROGRAM(prologue, body)` and
        pass the result as `code`.

    Args:
        code: Code to compile. If `includes` is also provided, this is treated as
            the body of main(). Otherwise, it should be complete C code. For
            AC_LANG_PROGRAM-style tests, use code = utils.AC_LANG_PROGRAM(prologue, body).
        name: Cache variable name.
        define: Define name to set if compilation succeeds
        includes: Optional list of headers to include. When provided, `code` is
            treated as the body of main() function.
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: If True, automatically create a substitution variable with the same name
            that will be set to "1" if the check succeeds, "0" if it fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not code and not includes:
        fail("AC_TRY_COMPILE: Either 'code' or 'includes' with 'code' must be provided. For AC_LANG_PROGRAM pattern use code = utils.AC_LANG_PROGRAM(prologue, body).")
    if not name and not define:
        fail("AC_TRY_COMPILE requires `name` or `define`.")

    if name == None:
        name = "ac_cv_try_compile_{}".format(define)

    check = {
        "language": language,
        "name": name,
        "type": "compile",
    }

    if define == True:
        define = name

    if define:
        check["define"] = define

    if includes:
        # When includes is provided, code is the body of main()
        header_code = _header_code_from_includes(includes)
        body_code = code if code else ""
        check["code"] = _AC_LANG_PROGRAM_TEMPLATE.format(header_code, body_code)
    elif code:
        check["code"] = code

    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_try_link(
        *,
        code = None,
        name = None,
        define = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = 1,
        if_false = 0,
        subst = None):
    """Try to compile and link custom code.

    This function mirrors GNU Autoconf's `AC_LINK_IFELSE` macro, which uses
    `AC_LANG_PROGRAM` to construct test programs. In Bazel, use `code` (optionally
    with `includes`). For the AC_LANG_PROGRAM pattern, pass
    code = utils.AC_LANG_PROGRAM(prologue, body).

    Comparison with GNU Autoconf:

    **GNU Autoconf:**
    ```m4
    AC_LINK_IFELSE(
        [AC_LANG_PROGRAM(
            [[#include <stdio.h>]],     # Prologue (includes/declarations)
            [[printf("test");]]         # Body (code inside main())
        )],
        [AC_DEFINE([HAVE_PRINTF], [1])],
        []
    )
    ```

    **Bazel (AC_LANG_PROGRAM pattern):**
    ```python
    load("//autoconf:checks.bzl", "utils")
    checks.AC_TRY_LINK(
        code = utils.AC_LANG_PROGRAM(["#include <stdio.h>"], "printf(\"test\");"),
        define = "HAVE_PRINTF",
    )
    ```

    **Bazel (includes pattern - backward compat):**
    ```python
    checks.AC_TRY_LINK(
        includes = ["stdio.h"],
        code = "printf(\"test\");",     # Treated as body of main()
        define = "HAVE_PRINTF",
    )
    ```

    **Bazel (raw code pattern):**
    ```python
    checks.AC_TRY_LINK(
        code = "#include <stdio.h>\\nint main() { printf(\\"test\\"); return 0; }",
        define = "HAVE_PRINTF",
    )
    ```

    Note:
        This is similar to AC_TRY_COMPILE but also links the code, which is necessary
        to verify that functions are available at link time (not just declared).

    Args:
        code: Code to compile and link. If `includes` is also provided, this is
            treated as the body of main(). Otherwise, it should be complete C code.
            For AC_LANG_PROGRAM-style tests use code = utils.AC_LANG_PROGRAM(prologue, body).
        name: Cache variable name.
        define: Define name to set if compilation and linking succeeds
        includes: Optional list of headers to include (backward compatibility).
            When provided, `code` is treated as the body of main() function.
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not code and not includes:
        fail("AC_TRY_LINK: Either 'code' or 'includes' with 'code' must be provided. For AC_LANG_PROGRAM pattern use code = utils.AC_LANG_PROGRAM(prologue, body).")
    if not name and not define:
        fail("AC_TRY_LINK requires `name` or `define`.")

    if name == None:
        name = "ac_cv_try_link_{}".format(define)

    check = {
        "language": language,
        "name": name,
        "type": "link",
    }

    if define == True:
        define = name

    if define:
        check["define"] = define

    if includes:
        # Backward compatibility: includes + code pattern
        header_code = _header_code_from_includes(includes)
        body_code = code if code else ""
        check["code"] = _AC_LANG_PROGRAM_TEMPLATE.format(header_code, body_code)
    elif code:
        check["code"] = code

    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_SIMPLE_MAIN_TEMPLATE = """\
int main(void) { return 0; }
"""

def _ac_prog_cc(requires = None):
    """Check that a C compiler is available.

    Original m4 example:
    ```m4
    AC_PROG_CC
    AC_PROG_CC([gcc clang])
    ```

    Example:
    ```python
    macros.AC_PROG_CC()
    ```

    Note:
        This is mostly a no-op in Bazel since the toolchain must be configured,
        but returns a check that will verify the compiler works.

    Args:
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return _ac_try_compile(
        code = _AC_SIMPLE_MAIN_TEMPLATE,
        define = "HAVE_C_COMPILER",
        language = "c",
        requires = requires,
    )

def _ac_prog_cxx(requires = None):
    """Check that a C++ compiler is available.

    Original m4 example:
    ```m4
    AC_PROG_CXX
    AC_PROG_CXX([g++ clang++])
    ```

    Example:
    ```python
    macros.AC_PROG_CXX()
    ```

    Note:
        This is mostly a no-op in Bazel since the toolchain must be configured,
        but returns a check that will verify the compiler works.

    Args:
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return _ac_try_compile(
        code = _AC_SIMPLE_MAIN_TEMPLATE,
        define = "HAVE_CXX_COMPILER",
        language = "cpp",
        requires = requires,
    )

_AC_CHECK_SIZEOF_TEMPLATE = """\
{}
#include <stddef.h>

/* Use negative array size to verify sizeof at compile time.
   The value {{value}} will be tested - if sizeof matches, compilation succeeds.
   This pattern is portable across all C compilers including MSVC. */
typedef int _sizeof_check_type[sizeof({}) == {{value}} ? 1 : -1];

int main(void) {{
    return 0;
}}
"""

def _ac_check_sizeof(
        type_name,
        *,
        name = None,
        define = None,
        includes = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check the size of a type.

    Original m4 example:
    ```m4
    AC_CHECK_SIZEOF([int])
    AC_CHECK_SIZEOF([size_t], [], [[#include <stddef.h>]])
    ```

    Example:
    ```python
    # Cache variable only (no define, no subst)
    macros.AC_CHECK_SIZEOF("int")
    # → Creates cache variable: "ac_cv_sizeof_int"

    # Cache variable + define (explicit name)
    macros.AC_CHECK_SIZEOF("size_t", includes = ["stddef.h"], define = "SIZEOF_SIZE_T")
    # → Creates cache variable: "ac_cv_sizeof_size_t"
    # → Creates define: "SIZEOF_SIZE_T" in config.h
    ```

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to determine the size, which doesn't work when cross-compiling.

    Args:
        type_name: Name of the type (e.g., `int`, `size_t`, `void*`)
        name: Cache variable name (defaults to `ac_cv_sizeof_<TYPE>` following autoconf convention)
        define: Define behavior:
            - `None` (default): No define is created, only cache variable
            - `True`: Create define using the cache variable name (`name`)
            - `"SIZEOF_FOO"`: Create define with explicit name `SIZEOF_FOO` (implies `define=True`)
        includes: Optional list of header names to include (e.g. `["stddef.h"]`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: Subst behavior:
            - `None` (default): No subst is created, only cache variable
            - `True`: Create subst using the cache variable name (`name`)
            - `"SIZEOF_FOO"`: Create subst with explicit name `SIZEOF_FOO` (implies `subst=True`)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Generate cache variable name if not provided
    if not name:
        name = _get_cache_name_for_sizeof(type_name)

    # Handle define parameter
    define_name = None
    if define:
        if define == True:
            define_name = name
        else:
            define_name = define

    # Handle subst parameter
    subst_name = None
    if subst:
        if subst == True:
            subst_name = name
        else:
            subst_name = subst

    header_code = _header_code_from_includes(includes) if includes else ""

    code = _AC_CHECK_SIZEOF_TEMPLATE.format(header_code, type_name)

    check = {
        "code": code,
        # Always set define field (required by Check class)
        # Use define_name if provided, otherwise use cache variable name
        "define": define_name if define_name else name,
        "language": language,
        "name": name,  # Cache variable name
        "type": "sizeof",
    }

    if requires:
        check["requires"] = requires

    if subst_name:
        check["subst"] = subst_name

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_CHECK_ALIGNOF_TEMPLATE = """\
{}
#include <stddef.h>

struct align_check {{
    char c;
    {} x;
}};

/* Use negative array size to verify alignment at compile time.
   The value {{value}} will be tested - if alignment matches, compilation succeeds.
   This pattern is portable across all C compilers including MSVC. */
typedef int _alignof_check_type[offsetof(struct align_check, x) == {{value}} ? 1 : -1];

int main(void) {{
    return 0;
}}
"""

def _ac_check_alignof(
        type_name,
        *,
        define = None,
        includes = None,
        language = "c",
        requires = None,
        subst = None):
    """Check the alignment of a type.

    Original m4 example:
    ```m4
    AC_CHECK_ALIGNOF([int])
    AC_CHECK_ALIGNOF([double], [[#include <stddef.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_ALIGNOF("int")
    macros.AC_CHECK_ALIGNOF("double", includes = ["stddef.h"])
    ```

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to determine the alignment, which doesn't work when cross-compiling.

    Args:
        type_name: Name of the type
        define: Custom define name (defaults to `ALIGNOF_<TYPE>`)
        includes: Optional list of header names to include (e.g. `["stddef.h"]`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "ALIGNOF_" + type_name.upper().replace(" ", "_").replace("*", "P")

    header_code = _header_code_from_includes(includes) if includes else ""

    code = _AC_CHECK_ALIGNOF_TEMPLATE.format(header_code, type_name)

    # Generate cache variable name for alignof checks
    # Use define name as cache variable name (since there's no standard autoconf convention)
    cache_name = define

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": cache_name,  # Cache variable name (use define name)
        "type": "alignof",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

_AC_CHECK_DECL_TEMPLATE = """\
{0}

int main(void) {{
#ifndef {1}
#ifdef __cplusplus
  (void) {1};
#else
  (void) {1};
#endif
#endif

  ;
  return 0;
}}
"""

def _ac_check_decl(
        symbol,
        *,
        name = None,
        define = None,
        includes = None,
        compile_defines = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check if a symbol is declared.

    Original m4 example:
    ```m4
    AC_CHECK_DECL([NULL], [AC_DEFINE([HAVE_DECL_NULL], [1])], [], [[#include <stddef.h>]])
    AC_CHECK_DECL([stdout], [AC_DEFINE([HAVE_DECL_STDOUT], [1])], [], [[#include <stdio.h>]])
    ```

    Example:
    ```python
    # Cache variable only (no define, no subst)
    macros.AC_CHECK_DECL("NULL", includes = ["stddef.h"])
    # → Creates cache variable: "ac_cv_have_decl_NULL"

    # Cache variable + define (explicit name)
    macros.AC_CHECK_DECL("stdout", includes = ["stdio.h"], define = "HAVE_DECL_STDOUT")
    # → Creates cache variable: "ac_cv_have_decl_stdout"
    # → Creates define: "HAVE_DECL_STDOUT" in config.h
    ```

    Note:
        This is different from `AC_CHECK_SYMBOL` - it checks if something is
        declared (not just `#defined`).

        When no includes are specified, the standard default includes
        are used (AC_INCLUDES_DEFAULT from GNU Autoconf).

        Use header names like `"stdlib.h"` (not `"#include <stdlib.h>"`).

    Args:
        symbol: Name of the symbol to check
        name: Cache variable name (defaults to `ac_cv_have_decl_<SYMBOL>` following autoconf convention)
        define: Define behavior:
            - `None` (default): No define is created, only cache variable
            - `True`: Create define using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create define with explicit name `HAVE_FOO` (implies `define=True`)
        includes: Optional list of header names to include (e.g. `["stdlib.h"]`).
            If not specified and `headers` is not specified, uses AC_INCLUDES_DEFAULT.
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: Subst behavior:
            - `None` (default): No subst is created, only cache variable
            - `True`: Create subst using the cache variable name (`name`)
            - `"HAVE_FOO"`: Create subst with explicit name `HAVE_FOO` (implies `subst=True`)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Generate cache variable name if not provided
    if not name:
        name = _get_cache_name_for_decl(symbol)

    # Handle define parameter
    define_name = None
    if define:
        if define == True:
            define_name = name
        else:
            define_name = define

    # Handle subst parameter
    subst_name = None
    if subst:
        if subst == True:
            subst_name = name
        else:
            subst_name = subst

    if includes:
        header_code = _header_code_from_includes(includes)
    else:
        header_code = _AC_INCLUDES_DEFAULT

    code = _AC_CHECK_DECL_TEMPLATE.format(header_code, symbol)

    check = {
        "code": code,
        # Always set define field (required by Check class)
        # Use define_name if provided, otherwise use cache variable name
        "define": define_name if define_name else name,
        "define_value": 1,
        "language": language,
        "name": name,  # Cache variable name
        "type": "decl",
    }

    # Set define_value_fail: when if_false is None use default (0 when define_name set);
    # otherwise use the provided value.
    if if_false == None:
        if define_name:
            check["define_value_fail"] = 0
    else:
        check["define_value_fail"] = if_false

    if subst_name:
        check["subst"] = subst_name
    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_CHECK_MEMBER_TEMPLATE = """
{}
#include <stddef.h>

int main(void) {{
    {} s;
    return offsetof({}, {});
}}
"""

def _ac_check_member(
        aggregate_member,
        *,
        name = None,
        define = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check if a struct or union has a member.

    Original m4 example:
    ```m4
    AC_CHECK_MEMBER([struct stat.st_rdev], [AC_DEFINE([HAVE_STRUCT_STAT_ST_RDEV], [1])], [], [[#include <sys/stat.h>]])
    AC_CHECK_MEMBER([struct tm.tm_zone], [AC_DEFINE([HAVE_STRUCT_TM_TM_ZONE], [1])], [], [[#include <time.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_MEMBER("struct stat.st_rdev", includes = ["sys/stat.h"])
    macros.AC_CHECK_MEMBER("struct tm.tm_zone", includes = ["time.h"])
    ```

    Args:
        aggregate_member: Struct or union name with (e.g., `struct stat.st_rdev`)
        name: Cache variable name (defaults to `ac_cv_member_aggregate_member` following autoconf convention)
        define: Custom define name (defaults to `HAVE_<AGGREGATE>_<MEMBER>`)
        includes: Optional list of header names to include (e.g. `["sys/stat.h"]`)
        language: Language to use for check (`"c"` or `"cpp"`)
        compile_defines: Optional list of preprocessor define names from previous checks
            to add before includes (e.g., `["_GNU_SOURCE", "_DARWIN_C_SOURCE"]`).
            Each string must match the define name of a previous check. The values
            from those checks will be used automatically.
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: If True, automatically create a substitution variable with the same name
            that will be set to "1" if the check succeeds, "0" if it fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    aggregate, _, member = aggregate_member.partition(".")
    agg_clean = aggregate.replace(" ", "_").replace(".", "_")
    mem_clean = member.replace(".", "_")

    if not name:
        name = "ac_cv_member_{}_{}".format(agg_clean, mem_clean)

    header_code = _header_code_from_includes(includes) if includes else ""

    code = _AC_CHECK_MEMBER_TEMPLATE.format(header_code, aggregate, aggregate, member)

    check = {
        "code": code,
        "language": language,
        "name": name,
        "type": "member",
    }
    if compile_defines:
        check["compile_defines"] = compile_defines
    if requires:
        check["requires"] = requires

    if define != None:
        check["define"] = define
    if subst != None:
        check["subst"] = subst

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_COMPUTE_INT_TEMPLATE = """
{}

int main(void) {{
    return ({});
}}
"""

def _ac_compute_int(
        define,
        expression,
        *,
        includes = None,
        language = "c",
        requires = None,
        subst = None):
    """Compute an integer value at compile time.

    Original m4 example:
    ```m4
    AC_COMPUTE_INT([SIZEOF_INT], [sizeof(int)])
    AC_COMPUTE_INT([MAX_VALUE], [1 << 16])
    ```

    Example:
    ```python
    macros.AC_COMPUTE_INT("SIZEOF_INT", "sizeof(int)")
    macros.AC_COMPUTE_INT("MAX_VALUE", "1 << 16")
    ```

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to compute the value, which doesn't work when cross-compiling.

    Args:
        define: Define name for the result (first arg to match autoconf)
        expression: C expression that evaluates to an integer (second arg)
        includes: Optional list of header names to include (e.g. `["stdlib.h"]`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    header_code = _header_code_from_includes(includes) if includes else ""

    code = _AC_COMPUTE_INT_TEMPLATE.format(header_code, expression)

    # Generate cache variable name for compute_int checks
    # Use define name as cache variable name (since there's no standard autoconf convention)
    cache_name = define

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": cache_name,  # Cache variable name (use define name)
        "type": "compute_int",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

_AC_C_BIGENDIAN_TEMPLATE = """\
#include <stdint.h>

int main(void) {
    uint32_t x = 0x01020304;
    uint8_t *p = (uint8_t*)&x;

    // If first byte is 0x01, it's big-endian
    if (p[0] == 0x01) {
        return 1;  // big-endian
    } else {
        return 0;  // little-endian
    }
}
"""

def _ac_c_bigendian(
        define = "WORDS_BIGENDIAN",
        language = "c",
        requires = None,
        subst = None):
    """Check byte order (endianness) of the system.

    Original m4 example:
    ```m4
    AC_C_BIGENDIAN([AC_DEFINE([WORDS_BIGENDIAN], [1])])
    ```

    Example:
    ```python
    macros.AC_C_BIGENDIAN()
    ```

    Note:
        The define is set to 1 for big-endian, 0 for little-endian.

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to determine endianness, which doesn't work when cross-compiling.

    Args:
        define: Define name (defaults to `WORDS_BIGENDIAN`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    check = {
        "code": _AC_C_BIGENDIAN_TEMPLATE,
        "define": define,
        "language": language,
        "name": "byte_order",
        "type": "endian",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

_AC_C_INLINE_TEMPLATE = """\
static inline int test_func(int x) {
    return x * 2;
}

int main(void) {
    return test_func(21);
}
"""

def _ac_c_inline(
        define = "inline",
        language = "c",
        requires = None,
        subst = None):
    """Check what inline keyword the compiler supports.

    Original m4 example:
    ```m4
    AC_C_INLINE
    ```

    Example:
    ```python
    macros.AC_C_INLINE()
    ```

    Tests inline keyword and defines it to the appropriate value.

    Args:
        define: Define name (defaults to `inline`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    check = {
        "code": _AC_C_INLINE_TEMPLATE,
        "define": define,
        "define_value": "inline",
        "define_value_fail": "",
        "language": language,
        "name": "inline",
        "type": "compile",
        "unquote": True,
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

_AC_C_RESTRICT_TEMPLATE = """\
int main(void) {
    int *restrict ptr = (int*)0x1000;
    return (int)ptr;
}
"""

def _ac_c_restrict(
        define = "restrict",
        language = "c",
        requires = None,
        subst = None):
    """Check if the compiler supports restrict keyword.

    Original m4 example:
    ```m4
    AC_C_RESTRICT
    ```

    Example:
    ```python
    checks.AC_C_RESTRICT()
    ```

    Note:
        If restrict is not supported, the define is set to empty string.

    Args:
        define: Define name (defaults to `restrict`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    check = {
        "code": _AC_C_RESTRICT_TEMPLATE,
        "define": define,
        "define_value": "restrict",  # Value to use if check succeeds
        "define_value_fail": "",  # Value to use if check fails
        "language": language,
        "name": "restrict",
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

def _ac_prog_cc_c_o(
        define = "NO_MINUS_C_MINUS_O",
        language = "c",
        requires = None,
        subst = None):
    """Check if the compiler supports -c and -o flags simultaneously.

    Original m4 example:
    ```m4
    AC_PROG_CC_C_O
    ```

    Example:
    ```python
    macros.AC_PROG_CC_C_O()
    ```

    Note:
        If the compiler does NOT support both flags together, the define is set.

    Args:
        define: Define name (defaults to `"NO_MINUS_C_MINUS_O"`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # This is a bit tricky to test in Bazel since we don't directly control
    # the compiler invocation. We'll test by trying to compile a simple program
    # and assume that if compilation succeeds, the flags work.

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "define_value": None,  # If flags work, don't define NO_MINUS_C_MINUS_O
        "define_value_fail": "",  # If flags don't work, define NO_MINUS_C_MINUS_O with empty value
        "language": language,
        "name": "cc_c_o",
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

def _ac_check_c_compiler_flag(
        flag,
        define = None,
        language = "c",
        requires = None,
        subst = None):
    """Check if the C compiler supports a specific flag.

    Original m4 example:
    ```m4
    AC_CHECK_C_COMPILER_FLAG([-Wall], [CFLAGS="$CFLAGS -Wall"])
    ```

    Example:
    ```python
    macros.AC_CHECK_C_COMPILER_FLAG("-Wall")
    macros.AC_CHECK_C_COMPILER_FLAG("-std=c99")
    ```

    Args:
        flag: Compiler flag to test (e.g., `"-Wall"`, `"-std=c99"`)
        define: Custom define name (defaults to `HAVE_FLAG_<FLAG>`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")

    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        define = "HAVE_FLAG_" + clean_flag.upper()

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + clean_flag,
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

def _ac_check_cxx_compiler_flag(
        flag,
        define = None,
        language = "cpp",
        requires = None,
        subst = None):
    """Check if the C++ compiler supports a specific flag.

    Original m4 example:
    ```m4
    AC_CHECK_CXX_COMPILER_FLAG([-std=c++17], [CXXFLAGS="$CXXFLAGS -std=c++17"])
    ```

    Example:
    ```python
    macros.AC_CHECK_CXX_COMPILER_FLAG("-std=c++17")
    macros.AC_CHECK_CXX_COMPILER_FLAG("-Wall")
    ```

    Args:
        flag: Compiler flag to test (e.g., `"-Wall"`, `"-std=c++17"`)
        define: Custom define name (defaults to `HAVE_FLAG_<FLAG>`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")

    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        define = "HAVE_FLAG_" + clean_flag.upper()

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + clean_flag,
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    return json.encode(check)

_AC_CHECK_LIB_TEMPLATE = """\
/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.
   MSVC does not have GCC builtins, so we can safely use int. */
#ifdef __cplusplus
extern "C"
#endif
#if defined _MSC_VER
/* Since MSVC 2015, many CRT functions (printf, scanf, etc.) are inline
   in UCRT headers and not exported as linker symbols. Link against
   legacy_stdio_definitions.lib to make them available for link tests. */
#pragma comment(lib, "legacy_stdio_definitions.lib")
int {function} ();
#else
char {function} ();
#endif

int main(void) {{
    return {function}();
}}
"""

def _ac_check_lib(
        library,
        function,
        *,
        name = None,
        define = None,
        code = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check for a function in a library.

    Original m4 example:
    ```m4
    AC_CHECK_LIB([m], [cos])
    AC_CHECK_LIB([pthread], [pthread_create])
    ```

    Example:
    ```python
    macros.AC_CHECK_LIB("m", "cos")
    macros.AC_CHECK_LIB("pthread", "pthread_create")
    ```

    Note:
        This checks if the specified function is available in the given library.
        It attempts to link against `-l<library>` to verify the library provides
        the function.

    Args:
        library: Library name without the `-l` prefix (e.g., `"m"`, `"pthread"`)
        function: Function name to check for in the library (e.g., `"cos"`, `"pthread_create"`)
        name: The name of the cache variable. Defaults to `ac_cv_lib_library_function`
        define: Custom define name (defaults to `HAVE_LIB<LIBRARY>`)
        code: Custom code to compile and link (optional)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: Value to use when check succeeds (currently not used for this check type).
        if_false: Value to use when check fails (currently not used for this check type).
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    clean_func = function.replace("-", "_")
    clean_lib = library.replace("-", "_")
    if not name:
        name = "ac_cv_lib_{}_{}".format(clean_lib, clean_func)

    if not define:
        define = "HAVE_LIB" + clean_lib.upper()

    check = {
        "define": define,
        "language": language,
        "library": library,
        "name": name,
        "type": "lib",
    }

    # If custom code is provided, use it; otherwise generate default function check code
    if code:
        check["code"] = code
    else:
        # Generate default code similar to AC_CHECK_FUNC
        check["code"] = _AC_CHECK_LIB_TEMPLATE.format(
            function = function,
        )
    if requires:
        check["requires"] = requires

    if subst != None:
        check["subst"] = subst

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_define_common(
        define,
        value = 1,
        requires = None,
        condition = None,
        if_true = 1,
        if_false = 0,
        subst = None):
    """Define a configuration macro.

    Args:
        define: Define name (e.g., `"CUSTOM_VALUE"`)
        value: Value to assign when no condition is specified (defaults to `"1"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        condition: Selects which value to use: `if_true` when condition is true,
            `if_false` when false. Does not affect whether the check runs.
        if_true: Value when condition is true (requires `condition`).
        if_false: Value when condition is false (requires `condition`).
            Use `None` to not define the macro when condition is false.
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        The raw check data
    """

    # Validate that select() is not used
    _validate_not_select(value, "value", "AC_DEFINE")
    _validate_not_select(if_true, "if_true", "AC_DEFINE")
    _validate_not_select(if_false, "if_false", "AC_DEFINE")

    check = {
        "code": "",
        "define": define,
        "language": "c",
        "name": "ac_cv_define_{}".format(define),
        "type": "define",
    }

    if subst != None:
        check["subst"] = subst

    if requires:
        check["requires"] = requires

    if condition:
        check["condition"] = condition
        check["define_value"] = if_true
        check["define_value_fail"] = if_false

    else:
        check["define_value"] = value
        check["define_value_fail"] = value

    return check

def _ac_define(
        define,
        value = 1,
        requires = None,
        condition = None,
        if_true = 1,
        if_false = 0,
        subst = None):
    """Define a configuration macro.

    Original m4 example:
    ```m4
    AC_DEFINE([CUSTOM_VALUE], [42])
    AC_DEFINE([ENABLE_FEATURE], [1])
    AC_DEFINE([PROJECT_NAME], ["MyProject"])
    AC_DEFINE([EMPTY_VALUE], [])
    ```

    Conditional example (m4):
    ```m4
    if test $gl_cv_foo = yes; then
      AC_DEFINE([FOO_WORKS], [1])
    fi
    ```

    Example:
    ```python
    # Simple (always define)
    macros.AC_DEFINE("CUSTOM_VALUE", "42")

    # Conditional (define based on another check's result)
    macros.AC_DEFINE(
        "FOO_WORKS",
        condition = "HAVE_FOO",
        if_true = "1",      # Value when HAVE_FOO is true
        if_false = None,    # Don't define when HAVE_FOO is false
    )
    ```

    Note:
        This is equivalent to GNU Autoconf's AC_DEFINE macro. When used without
        `condition`, it creates a define that will always be set. When used with
        `condition`, the define is set based on the condition's result.

        **Condition Resolution**: If `condition` references a cache variable (e.g.,
        `condition="ac_cv_header_foo_h"`), it's evaluated as a truthy check:
        - Condition is `true` if the check succeeded AND the value is truthy (non-empty, non-zero)
        - Condition is `false` if the check failed OR the value is falsy (empty, "0")
        - If condition includes a comparison (e.g., `condition="ac_cv_header_foo_h==1"`),
          it performs value comparison instead.

    Args:
        define: Define name (e.g., `"CUSTOM_VALUE"`)
        value: Value to assign when no condition is specified (defaults to `"1"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        condition: Selects which value to use: `if_true` when condition is true,
            `if_false` when false. Does not affect whether the check runs.
        if_true: Value when condition is true (requires `condition`).
        if_false: Value when condition is false (requires `condition`).
            Use `None` to not define the macro when condition is false.
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return json.encode(_ac_define_common(
        define = define,
        value = value,
        requires = requires,
        condition = condition,
        if_true = if_true,
        if_false = if_false,
        subst = subst,
    ))

def _ac_define_unquoted(
        define,
        value = 1,
        requires = None,
        condition = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Define a configuration macro without quoting (AC_DEFINE_UNQUOTED).

    Original m4 example:
    ```m4
    AC_DEFINE_UNQUOTED([ICONV_CONST], [$iconv_arg1])
    AC_DEFINE_UNQUOTED([VERSION], ["$PACKAGE_VERSION"])
    ```

    This is similar to AC_DEFINE, but with one key difference:
    - AC_DEFINE with empty value generates: `#define NAME /**/`
    - AC_DEFINE_UNQUOTED with empty value generates: `#define NAME ` (trailing space)

    Example:
    ```python
    # Simple (always define)
    macros.AC_DEFINE_UNQUOTED("ICONV_CONST", "")

    # Conditional (define based on another check's result)
    macros.AC_DEFINE_UNQUOTED(
        "ICONV_CONST",
        condition = "_gl_cv_iconv_nonconst",
        if_true = "",      # Empty value - will generate "#define ICONV_CONST " (trailing space)
        if_false = "const",  # Non-empty value
    )
    ```

    Note:
        This is equivalent to GNU Autoconf's AC_DEFINE_UNQUOTED macro. The main
        difference from AC_DEFINE is how empty values are rendered in config.h:
        AC_DEFINE uses `/**/` while AC_DEFINE_UNQUOTED uses a trailing space.

    Args:
        define: Define name (e.g., `"ICONV_CONST"`)
        value: Value to assign when no condition is specified (defaults to `"1"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        condition: Selects which value to use: `if_true` when condition is true,
            `if_false` when false. Does not affect whether the check runs.
        if_true: Value when condition is true (requires `condition`).
        if_false: Value when condition is false (requires `condition`).
            Use `None` to not define the macro when condition is false.
        subst: If True, mark as a substitution variable (for @VAR@ replacement in subst.h).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    check = _ac_define_common(
        define = define,
        value = value,
        requires = requires,
        condition = condition,
        if_true = if_true,
        if_false = if_false,
        subst = subst,
    )

    check["unquote"] = True

    return json.encode(check)

def _ac_subst(
        variable,
        value = 1,
        requires = None,
        condition = None,
        if_true = 1,
        if_false = 0):
    """Substitute a variable value (equivalent to AC_SUBST in GNU Autoconf).

    Original m4 example:
    ```m4
    HAVE_DECL_WCSDUP=1;   AC_SUBST([HAVE_DECL_WCSDUP])
    HAVE_DECL_WCWIDTH=1;  AC_SUBST([HAVE_DECL_WCWIDTH])
    ```

    Conditional example (m4):
    ```m4
    if test $gl_cv_sys_struct_lconv_ok = no; then
      REPLACE_STRUCT_LCONV=1
    else
      REPLACE_STRUCT_LCONV=0
    fi
    AC_SUBST([REPLACE_STRUCT_LCONV])
    ```

    Example:
    ```python
    # Simple (always set)
    macros.AC_SUBST("HAVE_DECL_WCSDUP", "1")

    # Conditional (set based on another check's result)
    macros.AC_SUBST(
        "REPLACE_STRUCT_LCONV",
        condition = "HAVE_STRUCT_LCONV_OK",
        if_true = "0",    # Value when condition is true
        if_false = "1",   # Value when condition is false
    )
    ```

    Note:
        **Condition Resolution**: If `condition` references a cache variable (e.g.,
        `condition="ac_cv_header_foo_h"`), it's evaluated as a truthy check:
        - Condition is `true` if the check succeeded AND the value is truthy (non-empty, non-zero)
        - Condition is `false` if the check failed OR the value is falsy (empty, "0")
        - If condition includes a comparison (e.g., `condition="ac_cv_header_foo_h==1"`),
          it performs value comparison instead.

        In GNU Autoconf, `AC_SUBST` is used to substitute shell variables into
        Makefiles and template files (replacing `@VAR@` patterns). When used
        without `condition`, it always sets the variable. When used with
        `condition`, the value depends on the condition's result.

    Args:
        variable: Variable name (e.g., `"LIBRARY_PATH"`)
        value: Value to assign when no condition is specified (defaults to `"1"`)
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        condition: Selects which value to use: `if_true` when condition is true,
            `if_false` when false. Does not affect whether the check runs.
        if_true: Value when condition is true (requires `condition`).
        if_false: Value when condition is false (requires `condition`).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Validate that select() is not used
    _validate_not_select(value, "value", "AC_SUBST")
    _validate_not_select(if_true, "if_true", "AC_SUBST")
    _validate_not_select(if_false, "if_false", "AC_SUBST")

    check = {
        "code": "",
        "language": "c",
        "name": "ac_cv_subst_{}".format(variable),
        "subst": variable,
        "type": "m4_variable",
    }

    if requires:
        check["requires"] = requires

    if condition:
        check["condition"] = condition
        check["define_value"] = if_true
        check["define_value_fail"] = if_false
    else:
        check["define_value"] = value
        check["define_value_fail"] = value

    return json.encode(check)

def _m4_variable(
        define,
        value = 1,
        requires = None,
        condition = None,
        if_true = None,
        if_false = None):
    """Define a configuration M4 variable.

    Original m4 example:
    ```m4
    REPLACE_FOO=1
    ```

    Conditional example (m4):
    ```m4
    if test $HAVE_FOO = yes; then
      REPLACE_FOO=0
    else
      REPLACE_FOO=1
    fi
    ```

    Example:
    ```python
    # Simple (always set)
    macros.M4_VARIABLE("REPLACE_FOO", "1")

    # Conditional (set based on another check's result)
    macros.M4_VARIABLE(
        "REPLACE_FOO",
        condition = "HAVE_FOO",
        if_true = "0",    # Value when HAVE_FOO is true
        if_false = "1",   # Value when HAVE_FOO is false
    )
    ```

    Note:
        This is similar to `AC_SUBST` but is useful for tracking the difference
        between actual `AC_DEFINE` values and M4 shell variables used in macros.
        Unlike `AC_SUBST`, this does not set `subst=true` by default.

    Args:
        define: Define name (e.g., `"REPLACE_FOO"`)
        value: Value to assign when no condition is specified (defaults to `"1"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)
        condition: Selects which value to use: `if_true` when condition is true,
            `if_false` when false. Does not affect whether the check runs.
        if_true: Value when condition is true (requires `condition`).
        if_false: Value when condition is false (requires `condition`).

    Returns:
        A JSON-encoded check string for use with the m4 rule.
    """

    # Validate that select() is not used
    _validate_not_select(value, "value", "M4_VARIABLE")
    _validate_not_select(if_true, "if_true", "M4_VARIABLE")
    _validate_not_select(if_false, "if_false", "M4_VARIABLE")

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "language": "c",
        "name": define,
        "subst": None,  # M4_VARIABLE is never a substitution variable - use AC_SUBST for that
        "type": "m4_variable",  # Compute value for requires but don't generate output (config.h or subst.h)
    }

    if requires:
        check["requires"] = requires

    if condition:
        check["condition"] = condition
        check["define_value"] = if_true

        # Only set define_value_fail if if_false is explicitly provided (not None)
        # This allows conditional m4 variables to not be created when condition is false
        if if_false != None:
            check["define_value_fail"] = if_false
    else:
        check["define_value"] = value
        check["define_value_fail"] = value

    return json.encode(check)

# ============================================================================
# Plural check macros (AC_CHECK_DECLS, AC_CHECK_HEADERS, etc.)
# These return lists of checks with automatically generated define names
# ============================================================================

def _get_define_name_for_decl(symbol):
    """Generate define name for AC_CHECK_DECL following autoconf convention.

    Args:
        symbol: Symbol name (e.g., "NULL", "stdout")

    Returns:
        Define name (e.g., "HAVE_DECL_NULL", "HAVE_DECL_STDOUT")
    """
    normalized = _normalize_for_cache_name(symbol)
    return "HAVE_DECL_" + normalized.upper()

def _get_define_name_for_header(header):
    """Generate define name for AC_CHECK_HEADER following autoconf convention.

    Args:
        header: Header name (e.g., "stdio.h", "sys/stat.h")

    Returns:
        Define name (e.g., "HAVE_STDIO_H", "HAVE_SYS_STAT_H")
    """
    normalized = header.replace("/", "_").replace(".", "_").replace("-", "_").replace(" ", "_").replace("*", "P").upper()
    return "HAVE_" + normalized

def _get_define_name_for_func(function):
    """Generate define name for AC_CHECK_FUNC following autoconf convention.

    Args:
        function: Function name (e.g., "printf", "malloc")

    Returns:
        Define name (e.g., "HAVE_PRINTF", "HAVE_MALLOC")
    """
    normalized = _normalize_for_cache_name(function)
    return "HAVE_" + normalized.upper()

def _get_define_name_for_type(type_name):
    """Generate define name for AC_CHECK_TYPE following autoconf convention.

    Args:
        type_name: Type name (e.g., "size_t", "pthread_t")

    Returns:
        Define name (e.g., "HAVE_SIZE_T", "HAVE_PTHREAD_T")
    """
    normalized = _normalize_for_cache_name(type_name)
    return "HAVE_" + normalized.upper()

def _ac_check_decls(
        symbols,
        *,
        includes = None,
        compile_defines = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check multiple declarations, creating HAVE_DECL_<SYMBOL> defines (AC_CHECK_DECLS).

    GNU Autoconf's AC_CHECK_DECLS(symbol, ...) checks whether each symbol is
    declared (e.g. in a header). This macro runs AC_CHECK_DECL for each symbol
    and sets define names HAVE_DECL_<SYMBOL> following autoconf conventions.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fCHECK_002dDECL

    M4 example (configure.ac):
      AC_CHECK_DECLS([NULL, stdout, stderr], [], [], [[#include <stdio.h>]])
      AC_CHECK_DECL([NULL], [AC_DEFINE([HAVE_DECL_NULL], [1])], [], [[#include <stddef.h>]])

    Bazel example (use `#include <foo>` in includes):
      load("//autoconf:checks.bzl", "macros")
      macros.AC_CHECK_DECLS(["NULL", "stdout", "stderr"], includes = ["#include <stdio.h>"])
      # Defines: HAVE_DECL_NULL, HAVE_DECL_STDOUT, HAVE_DECL_STDERR

    Args:
        symbols: List of symbol names to check.
        includes: Optional list of include directives (e.g. ["#include <stdio.h>"]).
        compile_defines: Optional list of preprocessor define names (applies to all checks).
        language: Language to use for checks (`"c"` or `"cpp"`).
        requires: Requirements that must be met (applies to all checks).
        if_true: Value to use when check succeeds (applies to all checks).
        if_false: Value to use when check fails (applies to all checks).
        subst: Subst behavior (applies to all checks).

    Returns:
        List of JSON-encoded check strings.
    """
    if not symbols:
        return []

    checks = []
    for symbol in symbols:
        define_name = _get_define_name_for_decl(symbol)
        subst_name = subst
        if subst_name == True:
            subst_name = define_name
        check = _ac_check_decl(
            symbol,
            define = define_name,
            includes = includes,
            compile_defines = compile_defines,
            language = language,
            requires = requires,
            if_true = if_true,
            if_false = if_false,
            subst = subst_name,
        )
        checks.append(check)

    return checks

def _ac_check_headers(
        headers,
        *,
        language = "c",
        includes = None,
        compile_defines = None,
        requires = None):
    """Check multiple headers, creating HAVE_<HEADER> defines (AC_CHECK_HEADERS).

    GNU Autoconf's AC_CHECK_HEADERS(header, ...) checks whether each header can
    be compiled. This macro runs AC_CHECK_HEADER for each header and sets
    HAVE_<HEADER_UPPER> (e.g. HAVE_STDIO_H) following autoconf conventions.
    Header names are turned into include directives as #include <header>.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fCHECK_002dHEADERS

    Original m4 example:
    ```m4
    AC_CHECK_HEADERS([stdio.h, stdlib.h, string.h])
    ```

    Example:
    ```python
    load("//autoconf:checks.bzl", "macros")
    macros.AC_CHECK_HEADERS(["stdio.h", "stdlib.h", "string.h"])
    # Defines: HAVE_STDIO_H, HAVE_STDLIB_H, HAVE_STRING_H
    ```

    Args:
        headers: List of header names to check (e.g. "sys/stat.h").
        language: Language to use for checks (`"c"` or `"cpp"`).
        includes: Optional list of include directives (e.g. ["#include <stdio.h>"]).
        compile_defines: Optional list of preprocessor define names (applies to all checks).
        requires: Requirements that must be met (applies to all checks).

    Returns:
        List of JSON-encoded check strings.
    """
    if not headers:
        return []

    checks = []
    for header in headers:
        define_name = _get_define_name_for_header(header)
        check = _ac_check_header(
            header,
            define = define_name,
            language = language,
            includes = includes,
            compile_defines = compile_defines,
            requires = requires,
        )
        checks.append(check)

    return checks

def _ac_check_funcs(
        functions,
        *,
        code = None,
        language = "c",
        compile_defines = None,
        requires = None,
        subst = None):
    """Check multiple functions, creating HAVE_<FUNCTION> defines (AC_CHECK_FUNCS).

    GNU Autoconf's AC_CHECK_FUNCS(function, ...) checks whether each function
    is available (linkable). This macro runs AC_CHECK_FUNC for each function
    and sets HAVE_<FUNCTION> following autoconf conventions.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fCHECK_002dFUNC

    M4 example (configure.ac):
      AC_CHECK_FUNCS([malloc, free, printf])

    Bazel example:
      load("//autoconf:checks.bzl", "macros")
      macros.AC_CHECK_FUNCS(["malloc", "free", "printf"])
      # Defines: HAVE_MALLOC, HAVE_FREE, HAVE_PRINTF

    Args:
        functions: List of function names to check.
        code: Custom code to compile (applies to all checks).
        language: Language to use for checks (`"c"` or `"cpp"`).
        compile_defines: Optional list of preprocessor define names (applies to all checks).
        requires: Requirements that must be met (applies to all checks).
        subst: Subst behavior (applies to all checks).

    Returns:
        List of JSON-encoded check strings.
    """
    if not functions:
        return []

    checks = []
    for function in functions:
        define_name = _get_define_name_for_func(function)
        subst_name = subst
        if subst_name == True:
            subst_name = define_name
        check = _ac_check_func(
            function,
            define = define_name,
            code = code,
            language = language,
            compile_defines = compile_defines,
            requires = requires,
            subst = subst_name,
        )
        checks.append(check)

    return checks

def _ac_check_types(
        types,
        *,
        code = None,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check multiple types, creating HAVE_<TYPE> defines (AC_CHECK_TYPES).

    GNU Autoconf's AC_CHECK_TYPE(type, ...) checks whether each type is defined.
    This macro runs AC_CHECK_TYPE for each type and sets HAVE_<TYPE> (e.g.
    HAVE_SIZE_T) following autoconf conventions. Use includes with
    `#include <foo>` for the headers that declare the types.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fCHECK_002dTYPE

    M4 example (configure.ac):
      AC_CHECK_TYPES([size_t, pthread_t, pid_t], [], [], [[#include <stddef.h>]])
      AC_CHECK_TYPE([pthread_t], [], [], [[#include <pthread.h>]])

    Bazel example (use `#include <foo>` in includes):
      load("//autoconf:checks.bzl", "macros")
      macros.AC_CHECK_TYPES(["size_t", "pthread_t", "pid_t"], includes = ["#include <stddef.h>", "#include <pthread.h>"])
      # Defines: HAVE_SIZE_T, HAVE_PTHREAD_T, HAVE_PID_T

    Args:
        types: List of type names to check.
        code: Custom code that includes necessary headers (applies to all checks).
        includes: Optional list of include directives (e.g. ["#include <stddef.h>"]).
        language: Language to use for checks (`"c"` or `"cpp"`).
        compile_defines: Optional list of preprocessor define names (applies to all checks).
        requires: Requirements that must be met (applies to all checks).
        if_true: Value to use when check succeeds (applies to all checks).
        if_false: Value to use when check fails (applies to all checks).
        subst: Subst behavior (applies to all checks).

    Returns:
        List of JSON-encoded check strings.
    """
    if not types:
        return []

    checks = []
    for type_name in types:
        define_name = _get_define_name_for_type(type_name)
        subst_name = subst
        if subst_name == True:
            subst_name = define_name
        check = _ac_check_type(
            type_name,
            define = define_name,
            code = code,
            includes = includes,
            language = language,
            compile_defines = compile_defines,
            requires = requires,
            if_true = if_true,
            if_false = if_false,
            subst = subst_name,
        )
        checks.append(check)

    return checks

def _ac_check_members(
        aggregate_members,
        *,
        includes = None,
        language = "c",
        compile_defines = None,
        requires = None,
        if_true = None,
        if_false = None,
        subst = None):
    """Check multiple structure members, creating HAVE_<AGG>_<MEMBER> defines (AC_CHECK_MEMBERS).

    GNU Autoconf's AC_CHECK_MEMBER(aggregate.member, ...) checks whether a
    structure member exists. This macro runs AC_CHECK_MEMBER for each
    "aggregate.member" and sets HAVE_<AGGREGATE>_<MEMBER> (e.g.
    HAVE_STRUCT_STAT_ST_RDEV). Use includes with `#include <foo>` for headers
    that declare the aggregate.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fCHECK_002dMEMBER

    M4 example (configure.ac):
      AC_CHECK_MEMBERS([struct stat.st_rdev, struct tm.tm_zone], [], [], [[#include <sys/stat.h>]])
      AC_CHECK_MEMBER([struct stat.st_rdev], [], [], [[#include <sys/stat.h>]])

    Bazel example (use `#include <foo>` in includes):
      load("//autoconf:checks.bzl", "macros")
      macros.AC_CHECK_MEMBERS(
          ["struct stat.st_rdev", "struct tm.tm_zone"],
          includes = ["#include <sys/stat.h>", "#include <time.h>"],
      )

    Args:
        aggregate_members: List of member specs (e.g. ["struct stat.st_rdev"]).
        includes: Optional list of include directives (e.g. ["#include <sys/stat.h>"]).
        language: Language to use for checks (`"c"` or `"cpp"`).
        compile_defines: Optional list of preprocessor define names (applies to all checks).
        requires: Requirements that must be met (applies to all checks).
        if_true: Value to use when check succeeds (applies to all checks).
        if_false: Value to use when check fails (applies to all checks).
        subst: Subst behavior (applies to all checks).

    Returns:
        List of JSON-encoded check strings.
    """
    if not aggregate_members:
        return []

    checks = []
    for member_spec in aggregate_members:
        # Parse member specification (e.g., "struct stat.st_rdev")
        aggregate, _, member = member_spec.partition(".")

        agg_clean = aggregate.upper().replace(" ", "_").replace(".", "_")
        mem_clean = member.upper().replace(".", "_")
        define = "HAVE_{}_{}".format(agg_clean, mem_clean)

        # AC_CHECK_MEMBER already generates define names automatically, so we don't need to specify it
        check = _ac_check_member(
            member_spec,
            includes = includes,
            language = language,
            compile_defines = compile_defines,
            requires = requires,
            if_true = if_true,
            if_false = if_false,
            subst = subst,
            define = define,
        )
        checks.append(check)

    return checks

def _ac_lang_program(prologue, body):
    """Build program code from prologue and main body (AC_LANG_PROGRAM).

    GNU Autoconf's AC_LANG_PROGRAM(prologue, body) expands to a complete test
    program: prologue (includes/declarations) followed by main() containing body.
    Use the result as the `code` argument to AC_TRY_COMPILE or AC_TRY_LINK.

    Upstream:
    https://www.gnu.org/savannah-checkouts/gnu/autoconf/manual/autoconf-2.72/autoconf.html#index-AC_005fLANG_005fPROGRAM

    M4 example (configure.ac):
      AC_TRY_COMPILE([AC_LANG_PROGRAM([[#include <stdio.h>]], [printf("test");])], ...)
      AC_TRY_LINK([AC_LANG_PROGRAM([[#include <langinfo.h>]], [char* cs = nl_langinfo(CODESET); return !cs;])], ...)

    Bazel example (use `#include <foo>` in the prologue list):
      load("//autoconf:checks.bzl", "utils", "checks")
      checks.AC_TRY_COMPILE(
          code = utils.AC_LANG_PROGRAM(["#include <stdio.h>"], "printf(\"test\");"),
          define = "HAVE_PRINTF",
      )
      checks.AC_TRY_LINK(
          code = utils.AC_LANG_PROGRAM(
              ["#include <langinfo.h>"],
              "char* cs = nl_langinfo(CODESET); return !cs;",
          ),
          define = "HAVE_LANGINFO_CODESET",
      )

    Args:
        prologue: List of strings (include directives or declarations), joined with newlines.
        body: Code inside main() — a string, or a list of strings joined with newlines.

    Returns:
        A string suitable for the `code` parameter of AC_TRY_COMPILE or AC_TRY_LINK.
    """
    if type(prologue) == type([]):
        prologue_str = "\n".join(prologue)
    else:
        prologue_str = prologue
    if type(body) == type([]):
        body_str = "\n".join(body)
    else:
        body_str = body
    return _AC_LANG_PROGRAM_TEMPLATE.format(prologue_str, body_str)

checks = struct(
    AC_C_BIGENDIAN = _ac_c_bigendian,
    AC_C_INLINE = _ac_c_inline,
    AC_C_RESTRICT = _ac_c_restrict,
    AC_CHECK_ALIGNOF = _ac_check_alignof,
    AC_CHECK_C_COMPILER_FLAG = _ac_check_c_compiler_flag,
    AC_CHECK_CXX_COMPILER_FLAG = _ac_check_cxx_compiler_flag,
    AC_CHECK_DECL = _ac_check_decl,
    AC_CHECK_FUNC = _ac_check_func,
    AC_CHECK_HEADER = _ac_check_header,
    AC_CHECK_LIB = _ac_check_lib,
    AC_CHECK_MEMBER = _ac_check_member,
    AC_CHECK_SIZEOF = _ac_check_sizeof,
    AC_CHECK_TYPE = _ac_check_type,
    AC_COMPUTE_INT = _ac_compute_int,
    AC_DEFINE = _ac_define,
    AC_DEFINE_UNQUOTED = _ac_define_unquoted,
    AC_PROG_CC = _ac_prog_cc,
    AC_PROG_CC_C_O = _ac_prog_cc_c_o,
    AC_PROG_CXX = _ac_prog_cxx,
    AC_SUBST = _ac_subst,
    AC_TRY_COMPILE = _ac_try_compile,
    AC_TRY_LINK = _ac_try_link,
    M4_VARIABLE = _m4_variable,
)

macros = struct(
    AC_CHECK_DECLS = _ac_check_decls,
    AC_CHECK_HEADERS = _ac_check_headers,
    AC_CHECK_FUNCS = _ac_check_funcs,
    AC_CHECK_TYPES = _ac_check_types,
    AC_CHECK_MEMBERS = _ac_check_members,
)

utils = struct(
    AC_LANG_PROGRAM = _ac_lang_program,
    AC_INCLUDES_DEFAULT = _AC_INCLUDES_DEFAULT,
)
