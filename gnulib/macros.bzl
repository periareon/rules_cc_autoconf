"""# gnulib macros

Common gnulib macro patterns as Bazel wrappers.

These macros encapsulate common patterns from gnulib's m4 files, making it
easier to port modules while maintaining consistency with gnulib behavior.
"""

# json is a built-in Starlark module in Bazel
# We can use it directly without importing

# Import the check functions we need
load("//autoconf:checks.bzl", autoconf_checks = "checks")

def _normalize_for_cache_name(s):
    """Normalize a string for use in cache variable names.

    Replaces special characters with underscores, following autoconf conventions.
    """
    return s.replace("-", "_").replace(".", "_").replace(" ", "_").replace("*", "P")

def _get_cache_name_for_func(function):
    """Get autoconf cache variable name for AC_CHECK_FUNC.

    Args:
        function: Function name (e.g., "printf", "malloc")

    Returns:
        Cache variable name (e.g., "ac_cv_func_printf", "ac_cv_func_malloc")
    """
    normalized = _normalize_for_cache_name(function)
    return "ac_cv_func_" + normalized.lower()

def _check_funcs_android(
        functions,
        *,
        # buildifier: disable=unused-variable
        includes = None,
        code = None,
        requires = None):
    """Check multiple functions with Android/macOS compatibility (gl_CHECK_FUNCS_ANDROID).

    Gnulib's gl_CHECK_FUNCS_ANDROID checks whether each function is available (via
    AC_CHECK_FUNC), then AC_DEFINEs HAVE_<FUNCTION> in config.h only when the function
    exists, and AC_SUBSTs HAVE_<FUNCTION> in subst.h always (0 or 1). This matches the
    pattern used by many gnulib modules.

    Upstream:
    <https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/gnulib-common.m4#L1630-L1665>

    Original m4 example:
    ```m4
    gl_CHECK_FUNCS_ANDROID([faccessat], [[#include <unistd.h>]])
    gl_CHECK_FUNCS_ANDROID([dup3])
    ```

    Example (use `#include <foo>` in the includes list)::
    ```python
    load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")
    checks = gl_macros.GL_CHECK_FUNCS_ANDROID(
        ["faccessat", "dup3"],
        includes = ["#include <unistd.h>"],
    )
    ```

    Implementation:
    For each function, this creates: AC_CHECK_FUNC; AC_DEFINE(HAVE_<FUNCTION>) when
    present; AC_SUBST(HAVE_<FUNCTION>, "1"/"0").

    Args:
        functions: List of function names to check (e.g., ["dup3", "printf"]) or single function name.
        includes: Ignored for now (only used in Android case; accepted for API compatibility).
        code: Optional custom code to compile (applies to all function checks).
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).

    Returns:
        List of JSON-encoded check strings that can be added to an autoconf target's checks.
    """
    if not functions:
        return []

    result = []

    for function in functions:
        function_upper = function.upper().replace("-", "_")
        have_var = "HAVE_{}".format(function_upper)

        # Get cache variable name for the function check
        cache_var = _get_cache_name_for_func(function)

        # 1. AC_CHECK_FUNC - creates the cache variable (includes ignored for now)
        func_check = autoconf_checks.AC_CHECK_FUNC(
            function,
            code = code,
            requires = requires,
        )

        result.append(func_check)

        # 2. AC_DEFINE - only when function exists
        result.append(autoconf_checks.AC_DEFINE(
            have_var,
            condition = cache_var,
            requires = [cache_var],
        ))

    return result

def _check_funcs_macos(
        functions,
        *,
        includes = None,
        code = None,
        requires = None):
    """Check functions with macOS portability (gl_CHECK_FUNCS_MACOS).

    Gnulib's gl_CHECK_FUNCS_MACOS checks function availability with macOS version
    handling (yes/no/future). In Bazel we use the same link-check pattern as
    GL_CHECK_FUNCS_ANDROID; platform overrides can be done via select() in BUILD files.

    Upstream:
    https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/gnulib-common.m4#L1680-L1718

    Original m4 example:
    ```
    gl_CHECK_FUNCS_MACOS([faccessat], [[#include <unistd.h>]])
    ```

    Example:
    ```python
    load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")
    checks = gl_macros.GL_CHECK_FUNCS_MACOS(
        ["faccessat"],
        includes = ["#include <unistd.h>"],
    )
    ```

    Same signature and return as GL_CHECK_FUNCS_ANDROID.
    """
    return _check_funcs_android(
        functions = functions,
        includes = includes,
        code = code,
        requires = requires,
    )

def _check_funcs_android_macos(
        functions,
        *,
        includes = None,
        code = None,
        requires = None):
    """Check functions with Android and macOS portability (gl_CHECK_FUNCS_ANDROID_MACOS).

    Gnulib's gl_CHECK_FUNCS_ANDROID_MACOS combines Android and macOS availability
    handling. In Bazel we use the same link-check pattern as GL_CHECK_FUNCS_ANDROID;
    platform overrides can be done via select() in BUILD files.

    Upstream:
    https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/gnulib-common.m4#L1734-L1738

    Original m4 example:
    ```m4
    gl_CHECK_FUNCS_ANDROID_MACOS([faccessat], [[#include <unistd.h>]])
    ```

    Example:
    load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")
    checks = gl_macros.GL_CHECK_FUNCS_ANDROID_MACOS(
        ["faccessat"],
        includes = ["#include <unistd.h>"],
    )
    ```

    Same signature and return as GL_CHECK_FUNCS_ANDROID.
    """
    return _check_funcs_android(
        functions = functions,
        includes = includes,
        code = code,
        requires = requires,
    )

def _next_headers_internal(headers, value = None, requires = None):
    """Shared logic for NEXT_* variables (gl_NEXT_HEADERS_INTERNAL with include_next=yes).

    For each header, creates two AC_SUBST checks:
    1. NEXT_<HEADER_UPPER>
    2. NEXT_AS_FIRST_DIRECTIVE_<HEADER_UPPER>

    By default (value=None), uses "<header>" which matches macOS autoconf behavior.
    Pass value="" for Linux behavior (empty string).

    Args:
        headers: List of header names.
        value: Override value for all headers. None means use "<header>".
        requires: Requirements that must be met for this check to run.
            Can be define names (e.g., `"HAVE_FOO"`), negated (e.g., `"!HAVE_FOO"`),
            or value-based (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
    """
    if not headers:
        return []

    result = []
    for header in headers:
        header_upper = header.upper().replace("/", "_").replace(".", "_").replace("-", "_")
        next_var = "NEXT_{}".format(header_upper)
        next_as_first_var = "NEXT_AS_FIRST_DIRECTIVE_{}".format(header_upper)
        header_value = value if value != None else "<{}>".format(header)
        result.append(autoconf_checks.AC_SUBST(next_var, header_value, requires = requires))
        result.append(autoconf_checks.AC_SUBST(next_as_first_var, header_value, requires = requires))
    return result

def _check_next_headers(headers, value = None, requires = None):
    """Set NEXT_* variables for headers (gl_CHECK_NEXT_HEADERS).

    Gnulib's gl_CHECK_NEXT_HEADERS runs AC_CHECK_HEADERS_ONCE for the headers, then
    for each header sets NEXT_<HEADER> and NEXT_AS_FIRST_DIRECTIVE_<HEADER>.

    The behavior matches autoconf:
    - Checks if header exists via AC_CHECK_HEADER (creates cache variable)
    - ALWAYS sets NEXT_<HEADER> = "<header>" (regardless of whether header exists)

    The key insight is that autoconf's gl_CHECK_NEXT_HEADERS ALWAYS sets NEXT_*
    to "<header>". To get conditional behavior (like mntent_h where NEXT_* should
    be empty when header doesn't exist), don't call this macro at all - instead
    use the requires parameter to make the entire check conditional.

    Upstream:
    <https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/include_next.m4#L133-L158>

    Args:
        headers: Header name (e.g., "assert.h" or "sys/stat.h") or list of headers.
        value: Override value for NEXT_* vars. None means "<header>".
        requires: Requirements that must be met for this check to run. Use this
            to make the check conditional (e.g., requires=["ac_cv_header_foo_h"]
            to only set NEXT_* when the header exists).

    Returns:
        List of JSON-encoded check strings (header check + two AC_SUBST per header).
    """
    return _next_headers_internal(headers, value, requires = requires)

def _next_headers(headers, value = None):
    """Set NEXT_* variables for headers without existence check (gl_NEXT_HEADERS).

    Gnulib's gl_NEXT_HEADERS sets NEXT_* and NEXT_AS_FIRST_DIRECTIVE_* for each
    header without running AC_CHECK_HEADERS_ONCE. Use for standard headers (e.g.
    C89) that can be assumed to exist.

    The value is platform-specific in autoconf:
    - macOS: "<header>" (default when value=None)
    - Linux: "" (pass value="" for Linux-specific rules)

    For platform-specific behavior, use select at the rule level.

    Upstream:
    <https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/include_next.m4#L163-L168>

    Args:
        headers: Header name (e.g., "stddef.h" or "sys/stat.h") or list of headers.
        value: Override value for NEXT_* vars. None means "<header>", "" means empty.

    Returns:
        List of JSON-encoded check strings (two AC_SUBST checks per header).
    """
    return _next_headers_internal(headers, value)

def _ac_lib_have_linkflags(
        *,
        lib_name,
        includes,
        test_code,
        lib_value = "",
        ltlib_value = "",
        lib_prefix_value = ""):
    """Emulate AC_LIB_HAVE_LINKFLAGS from lib-link.m4 (link test + HAVE_LIB_* / LIB_* / LTLIB_*).

    Gnulib's AC_LIB_HAVE_LINKFLAGS(name, dependencies, includes, testcode) finds library
    flags via AC_LIB_LINKFLAGS_BODY, runs AC_LINK_IFELSE, and on success sets HAVE_LIB<NAME>=yes,
    AC_SUBSTs LIB<NAME>, LTLIB<NAME>, LIB<NAME>_PREFIX, and #defines HAVE_LIB<NAME>. In Bazel we
    run a single AC_TRY_LINK with the given includes and test_code; the target's deps must
    provide the library. On success we AC_DEFINE HAVE_LIB_<NAME> and AC_SUBST the variables
    with the values you pass.

    Upstream:
    <https://github.com/coreutils/gnulib/blob/a8482ceecf8f51571e773475a0efa9af7bb616e2/m4/lib-link.m4#L13-L19>

    Original m4 example:
    ```m4
    AC_LIB_HAVE_LINKFLAGS([intl], [], [[#include <libintl.h>]], [gettext(""); return 0;])
    ```

    Example:
    ```python
    load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")
    checks = gl_macros.AC_LIB_HAVE_LINKFLAGS(
        lib_name = "intl",
        includes = ["#include <libintl.h>"],
        test_code = "gettext(\"\"); return 0;",
        lib_value = "-lintl",
    )
    ```

    For a single symbol, prefer `checks.AC_CHECK_LIB`.

    Args:
        lib_name: Library name (e.g. "intl", "iconv").
        includes: List of include directives for the link test (e.g. ["#include <libintl.h>"]).
        test_code: Body of main() for the link test (e.g. "gettext(\"\"); return 0;").
        lib_value: Value for LIB_<NAME> when link succeeds (e.g. "-lintl").
        ltlib_value: Value for LTLIB_<NAME> when link succeeds.
        lib_prefix_value: Value for LIB_<NAME>_PREFIX when link succeeds.

    Returns:
        List of JSON-encoded check strings (one link check + four subst).
    """
    name_normalized = _normalize_for_cache_name(lib_name)

    # Match m4: NAME = translit(name, [a-z./+-], [A-Z____]); HAVE_LIB[]NAME => HAVE_LIBSIGSEGV (no underscore)
    name_upper = lib_name.upper().replace(".", "_").replace("/", "_").replace("+", "_").replace("-", "_")
    cache_var = "ac_cv_lib_" + name_normalized.lower()
    have_var = "HAVE_LIB" + name_upper
    lib_var = "LIB" + name_upper
    ltlib_var = "LTLIB" + name_upper
    lib_prefix_var = "LIB" + name_upper + "_PREFIX"

    result = []

    # 1. Link check (library must come from target deps; no per-check link_flags in checker)
    if includes:
        prologue = "\n".join(includes) if type(includes) == type([]) else includes
        full_code = prologue + "\n\nint main(void) {\n  " + test_code.replace("\n", "\n  ") + "\n  return 0;\n}"
    else:
        full_code = "int main(void) {\n  " + test_code.replace("\n", "\n  ") + "\n  return 0;\n}"

    result.append(autoconf_checks.AC_TRY_LINK(
        code = full_code,
        name = cache_var,
        define = have_var,
    ))

    # 2–5. AC_SUBST(HAVE_LIB_<NAME>, LIB_<NAME>, LTLIB_<NAME>, LIB_<NAME>_PREFIX)
    for (var, val_true, val_false) in [
        (have_var, "yes", "no"),
        (lib_var, lib_value, ""),
        (ltlib_var, ltlib_value, ""),
        (lib_prefix_var, lib_prefix_value, ""),
    ]:
        result.append(autoconf_checks.AC_SUBST(
            var,
            condition = cache_var,
            if_true = val_true,
            if_false = val_false,
        ))

    return result

macros = struct(
    GL_CHECK_FUNCS_ANDROID = _check_funcs_android,
    GL_CHECK_FUNCS_MACOS = _check_funcs_macos,
    GL_CHECK_FUNCS_ANDROID_MACOS = _check_funcs_android_macos,
    GL_CHECK_NEXT_HEADERS = _check_next_headers,
    GL_NEXT_HEADERS = _next_headers,
    AC_LIB_HAVE_LINKFLAGS = _ac_lib_have_linkflags,
)
