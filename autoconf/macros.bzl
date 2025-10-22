"""# macros"""

def _into_label(value):
    if type(value) == "Label":
        return str(value)

    if value.startswith("@"):
        return value

    return str(Label("@//{}:{}".format(
        native.package_name(),
        value.lstrip(":"),
    )))

def _add_conditionals(
        check,
        if_true = None,
        if_false = None):
    """Add conditional checks to a check dictionary.

    Args:
        check: The check dictionary to add conditionals to.
        if_true: List of JSON-encoded checks to run if this check succeeds.
        if_false: List of JSON-encoded checks to run if this check fails.

    Returns:
        The modified check dictionary.
    """
    if if_true:
        check["if_true"] = [json.decode(c) for c in if_true]
    if if_false:
        check["if_false"] = [json.decode(c) for c in if_false]
    return check

def _ac_check_header(
        header,
        define = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check for a header file.

    Original m4 example:
    ```m4
    AC_CHECK_HEADER([stdio.h])
    AC_CHECK_HEADER([pthread.h], [AC_CHECK_FUNC([pthread_create])])
    ```

    Example:
    ```python
    macros.AC_CHECK_HEADER("stdio.h")
    macros.AC_CHECK_HEADER(
        "pthread.h",
        if_true = [macros.AC_CHECK_FUNC("pthread_create")],
    )
    ```

    Args:
        header: Name of the header file (e.g., `"stdio.h"`)
        define: Custom define name (defaults to `HAVE_<HEADER>`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds. These checks
            will automatically have this check's define added to their requires.
        if_false: List of checks to run if this check fails. These checks
            will automatically have a negated require added (e.g., `"!HAVE_FOO"`).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_" + header.upper().replace("/", "_").replace(".", "_").replace("-", "_")

    check = {
        "define": define,
        "language": language,
        "name": header,
        "type": "header",
    }
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_check_func(
        function,
        define = None,
        code = None,
        file = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check for a function.

    Original m4 example:
    ```m4
    AC_CHECK_FUNC([malloc])
    AC_CHECK_FUNC([mmap], [AC_DEFINE([HAVE_MMAP_FEATURE], [1])])
    ```

    Example:
    ```python
    macros.AC_CHECK_FUNC("malloc")
    macros.AC_CHECK_FUNC(
        "mmap",
        if_true = [macros.AC_DEFINE("HAVE_MMAP_FEATURE", "1")],
    )
    ```

    Args:
        function: Name of the function (e.g., `"printf"`)
        define: Custom define name (defaults to `HAVE_<FUNCTION>`)
        code: Custom code to compile (optional)
        file: Label to a file containing custom code (optional)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds. These checks
            will automatically have this check's define added to their requires.
        if_false: List of checks to run if this check fails. These checks
            will automatically have a negated require added (e.g., `"!HAVE_FOO"`).

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_" + function.upper().replace("-", "_")

    check = {
        "define": define,
        "language": language,
        "name": function,
        "type": "function",
    }

    if code:
        check["code"] = code
    if file:
        check["file"] = _into_label(file)
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_TEST_TYPE_CODE_TEMPLATE = """\
#include <stdio.h>
#ifdef _WIN32
/* Windows doesn't have POSIX headers */
#else
#include <sys/types.h>
#include <sys/stat.h>
#endif
#include <stdlib.h>
#include <stddef.h>
#include <string.h>
#include <inttypes.h>
#include <stdint.h>
#ifndef _WIN32
#include <unistd.h>
#endif

int main(void) {{
    if (sizeof({type_name}))
        return 0;
    return 1;
}}
"""

def _ac_check_type(
        type_name,
        *,
        define = None,
        code = None,
        file = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check for a type.

    Original m4 example:
    ```m4
    AC_CHECK_TYPE([size_t])
    ```

    Example:
    ```python
    macros.AC_CHECK_TYPE("size_t")
    macros.AC_CHECK_TYPE("int64_t", code = "#include <stdint.h>")
    ```

    Args:
        type_name: Name of the type (e.g., `size_t`)
        define: Custom define name (defaults to `HAVE_<TYPE>`)
        code: Custom code that includes necessary headers (optional, defaults to standard headers)
        file: Label to a file containing custom code (optional)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_" + type_name.upper().replace(" ", "_").replace("*", "P")

    check = {
        "define": define,
        "language": language,
        "name": type_name,
        "type": "type",
    }

    # If no code or file provided, use default headers like GNU Autoconf's AC_INCLUDES_DEFAULT
    if not code and not file:
        check["code"] = _AC_TEST_TYPE_CODE_TEMPLATE.format(type_name = type_name)
    elif code:
        check["code"] = code
    elif file:
        check["file"] = _into_label(file)
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_check_symbol(
        symbol,
        *,
        define = None,
        code = None,
        file = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check if a symbol is defined.

    Original m4 example:
    ```m4
    AC_CHECK_SYMBOL([NULL], [AC_DEFINE([HAVE_NULL], [1])], [], [[#include <stddef.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_SYMBOL("NULL", code = "#include <stddef.h>")
    ```

    Args:
        symbol: Name of the symbol (e.g., `NULL`)
        define: Custom define name (defaults to `HAVE_<SYMBOL>`)
        code: Custom code that includes necessary headers (optional)
        file: Label to a file containing custom code (optional)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_" + symbol.upper().replace("-", "_")

    check = {
        "define": define,
        "language": language,
        "name": symbol,
        "type": "symbol",
    }

    if code:
        check["code"] = code
    if file:
        check["file"] = _into_label(file)
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_try_compile(
        *,
        code = None,
        file = None,
        define = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Try to compile custom code.

    Original m4 example:
    ```m4
    AC_TRY_COMPILE([#include <stdio.h>], [printf("test");], [AC_DEFINE([HAVE_PRINTF], [1])])
    ```

    Example:
    ```python
    macros.AC_TRY_COMPILE(
        code = "#include <stdio.h>\\nint main() { printf(\\"test\\"); return 0; }",
        define = "HAVE_PRINTF",
    )
    macros.AC_TRY_COMPILE(file = ":test.c", define = "CUSTOM_CHECK")
    ```

    Note:
        This is a rules_cc_autoconf extension. While GNU Autoconf has an
        obsolete AC_TRY_COMPILE macro (replaced by AC_COMPILE_IFELSE), this
        version adds support for file-based checks which is useful in Bazel.

    Args:
        code: Code to compile (optional if file is provided)
        define: Define name to set if compilation succeeds
        file: Label to a file containing code to compile (optional if code is provided)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not code and not file:
        fail("Either 'code' or 'file' must be provided")
    if code and file:
        fail("Cannot provide both 'code' and 'file'")

    check = {
        "define": define,
        "language": language,
        "name": define,
        "type": "compile",
    }

    if code:
        check["code"] = code
    if file:
        check["file"] = _into_label(file)
    if requires:
        check["requires"] = requires

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

// Use array trick to get sizeof at compile time
char size_check[sizeof({})];

int main(void) {{
    return sizeof({});
}}
"""

def _ac_check_sizeof(
        type_name,
        *,
        define = None,
        headers = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check the size of a type.

    Original m4 example:
    ```m4
    AC_CHECK_SIZEOF([int])
    AC_CHECK_SIZEOF([size_t], [], [[#include <stddef.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_SIZEOF("int")
    macros.AC_CHECK_SIZEOF("size_t", headers = ["stddef.h"])
    ```

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to determine the size, which doesn't work when cross-compiling.

    Args:
        type_name: Name of the type (e.g., `int`, `size_t`, `void*`)
        define: Custom define name (defaults to `SIZEOF_<TYPE>`)
        headers: Optional list of headers to include
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "SIZEOF_" + type_name.upper().replace(" ", "_").replace("*", "P")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_SIZEOF_TEMPLATE.format(header_code, type_name, type_name)

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": type_name,
        "type": "sizeof",
    }
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

_AC_CHECK_ALIGNOF_TEMPLATE = """\
{}
#include <stddef.h>

struct align_check {{
    char c;
    {} x;
}};

int main(void) {{
    return offsetof(struct align_check, x);
}}
"""

def _ac_check_alignof(
        type_name,
        *,
        define = None,
        headers = None,
        language = "c",
        requires = None):
    """Check the alignment of a type.

    Original m4 example:
    ```m4
    AC_CHECK_ALIGNOF([int])
    AC_CHECK_ALIGNOF([double], [[#include <stddef.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_ALIGNOF("int")
    macros.AC_CHECK_ALIGNOF("double", headers = ["stddef.h"])
    ```

    Cross-Compile Warning:
        This macro is NOT cross-compile friendly. It requires compiling and
        running code to determine the alignment, which doesn't work when cross-compiling.

    Args:
        type_name: Name of the type
        define: Custom define name (defaults to `ALIGNOF_<TYPE>`)
        headers: Optional list of headers to include
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "ALIGNOF_" + type_name.upper().replace(" ", "_").replace("*", "P")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_ALIGNOF_TEMPLATE.format(header_code, type_name)

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": type_name,
        "type": "alignof",
    }
    if requires:
        check["requires"] = requires

    return json.encode(check)

_AC_CHECK_DECL_TEMPLATE = """\
{}

int main(void) {{
    (void) {};
    return 0;
}}
"""

def _ac_check_decl(
        symbol,
        *,
        define = None,
        headers = None,
        value = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check if a symbol is declared.

    Original m4 example:
    ```m4
    AC_CHECK_DECL([NULL], [AC_DEFINE([HAVE_DECL_NULL], [1])], [], [[#include <stddef.h>]])
    AC_CHECK_DECL([stdout], [AC_DEFINE([HAVE_DECL_STDOUT], [1])], [], [[#include <stdio.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_DECL("NULL", headers = ["stddef.h"])
    macros.AC_CHECK_DECL("stdout", headers = ["stdio.h"])
    ```

    Note:
        This is different from `AC_CHECK_SYMBOL` - it checks if something is
        declared (not just `#defined`).

    Args:
        symbol: Name of the symbol to check
        define: Custom define name (defaults to `HAVE_DECL_<SYMBOL>`)
        headers: Optional list of headers to include
        value: Optional value to assign when the check is True.
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_DECL_" + symbol.upper().replace("-", "_")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_DECL_TEMPLATE.format(header_code, symbol)

    check = {
        "code": code,
        "define": define,
        "define_value": "1",
        "define_value_fail": "",
        "language": language,
        "name": symbol,
        "type": "decl",
    }
    if value != None:
        check["define_value"] = value
    if requires:
        check["requires"] = requires

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
        aggregate,
        member,
        *,
        define = None,
        headers = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
    """Check if a struct or union has a member.

    Original m4 example:
    ```m4
    AC_CHECK_MEMBER([struct stat.st_rdev], [AC_DEFINE([HAVE_STRUCT_STAT_ST_RDEV], [1])], [], [[#include <sys/stat.h>]])
    AC_CHECK_MEMBER([struct tm.tm_zone], [AC_DEFINE([HAVE_STRUCT_TM_TM_ZONE], [1])], [], [[#include <time.h>]])
    ```

    Example:
    ```python
    macros.AC_CHECK_MEMBER("struct stat", "st_rdev", headers = ["sys/stat.h"])
    macros.AC_CHECK_MEMBER("struct tm", "tm_zone", headers = ["time.h"])
    ```

    Args:
        aggregate: Struct or union name (e.g., `struct stat`)
        member: Member name (e.g., `st_rdev`)
        define: Custom define name (defaults to `HAVE_<AGGREGATE>_<MEMBER>`)
        headers: Optional list of headers to include
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        agg_clean = aggregate.upper().replace(" ", "_").replace(".", "_")
        mem_clean = member.upper().replace(".", "_")
        define = "HAVE_{}_{}".format(agg_clean, mem_clean)

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_MEMBER_TEMPLATE.format(header_code, aggregate, aggregate, member)

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": "{}.{}".format(aggregate, member),
        "type": "member",
    }
    if requires:
        check["requires"] = requires

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
        headers = None,
        language = "c",
        requires = None):
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
        headers: Optional list of headers to include
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_COMPUTE_INT_TEMPLATE.format(header_code, expression)

    check = {
        "code": code,
        "define": define,
        "language": language,
        "name": expression,
        "type": "compute_int",
    }
    if requires:
        check["requires"] = requires

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
        requires = None):
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
        requires = None):
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
    }
    if requires:
        check["requires"] = requires

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
        requires = None):
    """Check if the compiler supports restrict keyword.

    Original m4 example:
    ```m4
    AC_C_RESTRICT
    ```

    Example:
    ```python
    macros.AC_C_RESTRICT()
    ```

    Note:
        If restrict is not supported, the define is set to empty string.

    Args:
        define: Define name (defaults to `restrict`)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

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

    return json.encode(check)

def _ac_prog_cc_c_o(
        define = "NO_MINUS_C_MINUS_O",
        language = "c",
        requires = None):
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

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # This is a bit tricky to test in Bazel since we don't directly control
    # the compiler invocation. We'll test by trying to compile a simple program
    # and assume that if compilation succeeds, the flags work.

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "define_value": "",  # Value if flags work (no define)
        "define_value_fail": "1",  # Value if flags don't work
        "language": language,
        "name": "cc_c_o",
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    return json.encode(check)

def _ac_check_c_compiler_flag(
        flag,
        define = None,
        language = "c",
        requires = None):
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

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")
        define = "HAVE_FLAG_" + clean_flag.upper()

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + flag.replace("-", "_"),
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    return json.encode(check)

def _ac_check_cxx_compiler_flag(
        flag,
        define = None,
        language = "cpp",
        requires = None):
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

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")
        define = "HAVE_FLAG_" + clean_flag.upper()

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + flag.replace("-", "_"),
        "type": "compile",
    }
    if requires:
        check["requires"] = requires

    return json.encode(check)

_AC_CHECK_LIB_TEMPLATE = """\
/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.  */
#ifdef __cplusplus
extern "C"
#endif
char {function} ();

int main(void) {{
    return {function}();
}}
"""

def _ac_check_lib(
        library,
        function,
        *,
        define = None,
        code = None,
        file = None,
        language = "c",
        requires = None,
        if_true = None,
        if_false = None):
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
        define: Custom define name (defaults to `HAVE_LIB<LIBRARY>`)
        code: Custom code to compile and link (optional)
        file: Label to a file containing custom code (optional)
        language: Language to use for check (`"c"` or `"cpp"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_LIB" + library.upper().replace("-", "_")

    check = {
        "define": define,
        "language": language,
        "library": library,
        "name": function,
        "type": "lib",
    }

    # If custom code is provided, use it; otherwise generate default function check code
    if code:
        check["code"] = code
    elif file:
        check["file"] = _into_label(file)
    else:
        # Generate default code similar to AC_CHECK_FUNC
        check["code"] = _AC_CHECK_LIB_TEMPLATE.format(
            function = function,
        )
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_define(
        define,
        value = "1",
        requires = None,
        if_true = None,
        if_false = None):
    """Define a configuration macro unconditionally.

    Original m4 example:
    ```m4
    AC_DEFINE([CUSTOM_VALUE], [42])
    AC_DEFINE([ENABLE_FEATURE], [1])
    AC_DEFINE([PROJECT_NAME], ["MyProject"])
    ```

    Example:
    ```python
    macros.AC_DEFINE("CUSTOM_VALUE", "42")
    macros.AC_DEFINE("ENABLE_FEATURE", "1")
    macros.AC_DEFINE("PROJECT_NAME", '"MyProject"')
    ```

    Note:
        This is equivalent to GNU Autoconf's AC_DEFINE macro. It creates
        a define that will always be set in the generated config.h file.

    Args:
        define: Define name (e.g., `"CUSTOM_VALUE"`)
        value: Value to assign (defaults to `"1"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Create a compile check that always succeeds
    check = {
        "code": "",
        "define": define,
        "define_value": str(value),
        "define_value_fail": str(value),
        "language": "c",
        "name": define,
        "type": "define",
    }
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _ac_subst(
        variable,
        value = "1",
        requires = None,
        if_true = None,
        if_false = None):
    """Substitute a variable value (equivalent to AC_SUBST in GNU Autoconf).

    Original m4 example:
    ```m4
    HAVE_DECL_WCSDUP=1;   AC_SUBST([HAVE_DECL_WCSDUP])
    HAVE_DECL_WCWIDTH=1;  AC_SUBST([HAVE_DECL_WCWIDTH])
    ```

    Example:
    ```python
    macros.AC_SUBST("HAVE_DECL_WCSDUP", 1)
    macros.AC_SUBST("HAVE_DECL_WCWIDTH", 1)
    ```

    Note:
        In GNU Autoconf, `AC_SUBST` is used to substitute shell variables into
        Makefiles. In `rules_cc_autoconf`, all variables are automatically
        available for substitution in `requires` clauses and other contexts.
        This macro is functionally equivalent to `AC_DEFINE` but serves as
        documentation that the variable is intended for substitution rather than
        as a C preprocessor define.

    Args:
        variable: Variable name (e.g., `"LIBRARY_PATH"`)
        value: Value to assign (defaults to `"1"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`), negated checks
            (e.g., `"!HAVE_FOO"`), or value-based requirements
            (e.g., `"REPLACE_FSTAT==1"`, `"REPLACE_FSTAT!=0"`).
        if_true: List of checks to run if this check succeeds.
        if_false: List of checks to run if this check fails.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Create a compile check that always succeeds
    # Functionally equivalent to AC_DEFINE, but documents that this is
    # intended for substitution rather than as a C preprocessor define
    check = {
        "code": "",
        "define": variable,
        "define_value": str(value),
        "define_value_fail": str(value),
        "language": "c",
        "name": variable,
        "type": "define",
    }
    if requires:
        check["requires"] = requires

    _add_conditionals(check, if_true, if_false)
    return json.encode(check)

def _m4_define(
        define,
        value = "1",
        requires = None):
    """Define a configuration M4 variable unconditionally.

    Original m4 example:
    ```m4
    REPLACE_FOO=1
    ```

    Example:
    ```python
    macros.M4_DEFINE("REPLACE_FOO", "1")
    ```

    Note:
        Currently this is not functionally different than `AC_DEFINE` but is useful in
        tracking the difference in actual `AC_DEFINE` values and variables used later
        in the M4 macro.

    Args:
        define: Define name (e.g., `"CUSTOM_VALUE"`)
        value: Value to assign (defaults to `"1"`)
        requires: List of requirements that must be met before this check runs.
            Can be simple define names (e.g., `"HAVE_FOO"`) or value-based
            requirements (e.g., `"REPLACE_FSTAT=1"` to require specific value)

    Returns:
        A JSON-encoded check string for use with the m4 rule.
    """

    check = {
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "define_value": str(value),
        "define_value_fail": str(value),
        "language": "c",
        "name": define,
        "type": "define",
    }
    if requires:
        check["requires"] = requires

    return json.encode(check)

macros = struct(
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
    AC_CHECK_SYMBOL = _ac_check_symbol,
    AC_CHECK_TYPE = _ac_check_type,
    AC_COMPUTE_INT = _ac_compute_int,
    AC_DEFINE = _ac_define,
    AC_PROG_CC = _ac_prog_cc,
    AC_PROG_CC_C_O = _ac_prog_cc_c_o,
    AC_PROG_CXX = _ac_prog_cxx,
    AC_SUBST = _ac_subst,
    AC_TRY_COMPILE = _ac_try_compile,
    M4_DEFINE = _m4_define,
)
