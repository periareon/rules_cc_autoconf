"""Autoconf-style macro helpers.

This module provides convenience functions that mimic common autoconf macros.
"""

def _into_label(value):
    if type(value) == "Label":
        return str(value)

    if value.startswith("@"):
        return value

    return str(Label("@//{}:{}".format(native.package_name(), value.lstrip(":"))))

def ac_check_header(header, define = None, language = "c"):
    """Check for a header file.

    Args:
        header: Name of the header file (e.g., "stdio.h")
        define: Custom define name (defaults to HAVE_<HEADER>)
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_" + header.upper().replace("/", "_").replace(".", "_").replace("-", "_")

    return json.encode({
        "define": define,
        "language": language,
        "name": header,
        "type": "header",
    })

def ac_check_func(function, define = None, code = None, file = None, language = "c"):
    """Check for a function.

    Args:
        function: Name of the function (e.g., "printf")
        define: Custom define name (defaults to HAVE_<FUNCTION>)
        code: Custom code to compile (optional)
        file: Label to a file containing custom code (optional)
        language: Language to use for check ("c" or "cpp")

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

def ac_check_type(type_name, *, define = None, code = None, file = None, language = "c"):
    """Check for a type.

    Args:
        type_name: Name of the type (e.g., "size_t")
        define: Custom define name (defaults to HAVE_<TYPE>)
        code: Custom code that includes necessary headers (optional, defaults to standard headers)
        file: Label to a file containing custom code (optional)
        language: Language to use for check ("c" or "cpp")

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

    return json.encode(check)

def ac_check_symbol(symbol, *, define = None, code = None, file = None, language = "c"):
    """Check if a symbol is defined.

    Args:
        symbol: Name of the symbol (e.g., "NULL")
        define: Custom define name (defaults to HAVE_<SYMBOL>)
        code: Custom code that includes necessary headers (optional)
        file: Label to a file containing custom code (optional)
        language: Language to use for check ("c" or "cpp")

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

    return json.encode(check)

def ac_try_compile(*, code = None, file = None, define = None, language = "c"):
    """Try to compile custom code.

    Note:
        This is a rules_cc_autoconf extension. While GNU Autoconf has an
        obsolete AC_TRY_COMPILE macro (replaced by AC_COMPILE_IFELSE), this
        version adds support for file-based checks which is useful in Bazel.

    Args:
        code: Code to compile (optional if file is provided)
        define: Define name to set if compilation succeeds
        file: Label to a file containing code to compile (optional if code is provided)
        language: Language to use for check ("c" or "cpp")

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

    return json.encode(check)

_AC_SIMPLE_MAIN_TEMPLATE = """\
int main(void) { return 0; }
"""

def ac_prog_cc():
    """Check that a C compiler is available.

    This is mostly a no-op in Bazel since the toolchain must be configured,
    but returns a check that will verify the compiler works.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return ac_try_compile(
        code = _AC_SIMPLE_MAIN_TEMPLATE,
        define = "HAVE_C_COMPILER",
        language = "c",
    )

def ac_prog_cxx():
    """Check that a C++ compiler is available.

    This is mostly a no-op in Bazel since the toolchain must be configured,
    but returns a check that will verify the compiler works.

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return ac_try_compile(
        code = _AC_SIMPLE_MAIN_TEMPLATE,
        define = "HAVE_CXX_COMPILER",
        language = "cpp",
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

def ac_check_sizeof(type_name, *, define = None, headers = None, language = "c"):
    """Check the size of a type.

    WARNING: This macro is NOT cross-compile friendly. It requires compiling and
    running code to determine the size, which doesn't work when cross-compiling.

    Example:

    ```python
    AC_CHECK_SIZEOF("int")  # Sets SIZEOF_INT to the size
    AC_CHECK_SIZEOF("size_t", headers = ["stddef.h"])
    ```

    Args:
        type_name: Name of the type (e.g., "int", "size_t", "void*")
        define: Custom define name (defaults to SIZEOF_<TYPE>)
        headers: Optional list of headers to include
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "SIZEOF_" + type_name.upper().replace(" ", "_").replace("*", "P")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_SIZEOF_TEMPLATE.format(header_code, type_name, type_name)

    return json.encode({
        "code": code,
        "define": define,
        "language": language,
        "name": type_name,
        "type": "sizeof",
    })

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

def ac_check_alignof(type_name, *, define = None, headers = None, language = "c"):
    """Check the alignment of a type.

    WARNING: This macro is NOT cross-compile friendly. It requires compiling and
    running code to determine the alignment, which doesn't work when cross-compiling.

    Args:
        type_name: Name of the type
        define: Custom define name (defaults to ALIGNOF_<TYPE>)
        headers: Optional list of headers to include
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "ALIGNOF_" + type_name.upper().replace(" ", "_").replace("*", "P")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_ALIGNOF_TEMPLATE.format(header_code, type_name)

    return json.encode({
        "code": code,
        "define": define,
        "language": language,
        "name": type_name,
        "type": "alignof",
    })

_AC_CHECK_DECL_TEMPLATE = """\
{}

int main(void) {{
    (void) {};
    return 0;
}}
"""

def ac_check_decl(symbol, *, define = None, headers = None, language = "c"):
    """Check if a symbol is declared.

    This is different from AC_CHECK_SYMBOL - it checks if something is
    declared (not just #defined).

    Example:

    ```python
    AC_CHECK_DECL("NULL", headers = ["stddef.h"])
    AC_CHECK_DECL("stdout", headers = ["stdio.h"])
    ```

    Args:
        symbol: Name of the symbol to check
        define: Custom define name (defaults to HAVE_DECL_<SYMBOL>)
        headers: Optional list of headers to include
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        define = "HAVE_DECL_" + symbol.upper().replace("-", "_")

    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_CHECK_DECL_TEMPLATE.format(header_code, symbol)

    return json.encode({
        "code": code,
        "define": define,
        "language": language,
        "name": symbol,
        "type": "decl",
    })

_AC_CHECK_MEMBER_TEMPLATE = """
{}
#include <stddef.h>

int main(void) {{
    {} s;
    return offsetof({}, {});
}}
"""

def ac_check_member(aggregate, member, *, define = None, headers = None, language = "c"):
    """Check if a struct or union has a member.

    Example:

    ```python
    AC_CHECK_MEMBER("struct stat", "st_rdev", headers = ["sys/stat.h"])
    AC_CHECK_MEMBER("struct tm", "tm_zone", headers = ["time.h"])
    ```

    Args:
        aggregate: Struct or union name (e.g., "struct stat")
        member: Member name (e.g., "st_rdev")
        define: Custom define name (defaults to HAVE_<AGGREGATE>_<MEMBER>)
        headers: Optional list of headers to include
        language: Language to use for check ("c" or "cpp")

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

    return json.encode({
        "code": code,
        "define": define,
        "language": language,
        "name": "{}.{}".format(aggregate, member),
        "type": "member",
    })

_AC_COMPUTE_INT_TEMPLATE = """
{}

int main(void) {{
    return ({});
}}
"""

def ac_compute_int(define, expression, *, headers = None, language = "c"):
    """Compute an integer value at compile time.

    WARNING: This macro is NOT cross-compile friendly. It requires compiling and
    running code to compute the value, which doesn't work when cross-compiling.

    This matches GNU autoconf's AC_COMPUTE_INT signature:
    AC_COMPUTE_INT([VARIABLE], [EXPRESSION])

    This is useful for computing values like sizeof(), alignof(), or
    other compile-time constants.

    Example:
    ```python
    AC_COMPUTE_INT("SIZEOF_INT", "sizeof(int)")
    AC_COMPUTE_INT("MAX_VALUE", "1 << 16")
    AC_COMPUTE_INT("TWO", "1 + 1")
    ```

    Args:
        define: Define name for the result (first arg to match autoconf)
        expression: C expression that evaluates to an integer (second arg)
        headers: Optional list of headers to include
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    header_code = ""
    if headers:
        header_code = "\n".join(["#include <{}>".format(h) for h in headers])

    code = _AC_COMPUTE_INT_TEMPLATE.format(header_code, expression)

    return json.encode({
        "code": code,
        "define": define,
        "language": language,
        "name": expression,
        "type": "compute_int",
    })

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

def ac_c_bigendian(define = "WORDS_BIGENDIAN", language = "c"):
    """Check byte order (endianness) of the system.

    WARNING: This macro is NOT cross-compile friendly. It requires compiling and
    running code to determine endianness, which doesn't work when cross-compiling.

    Note:
        The define is set to 1 for big-endian, 0 for little-endian.

    Args:
        define: Define name (defaults to WORDS_BIGENDIAN)
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    return json.encode({
        "code": _AC_C_BIGENDIAN_TEMPLATE,
        "define": define,
        "language": language,
        "name": "byte_order",
        "type": "endian",
    })

_AC_C_INLINE_TEMPLATE = """\
static inline int test_func(int x) {
    return x * 2;
}

int main(void) {
    return test_func(21);
}
"""

def ac_c_inline(define = "inline", language = "c"):
    """Check what inline keyword the compiler supports.

    Tests inline keyword and defines it to the appropriate value.

    Args:
        define: Define name (defaults to "inline")
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return json.encode({
        "code": _AC_C_INLINE_TEMPLATE,
        "define": define,
        "define_value": "inline",
        "define_value_fail": "",
        "language": language,
        "name": "inline",
        "type": "compile",
    })

_AC_C_RESTRICT_TEMPLATE = """\
int main(void) {
    int *restrict ptr = (int*)0x1000;
    return (int)ptr;
}
"""

def ac_c_restrict(define = "restrict", language = "c"):
    """Check if the compiler supports restrict keyword.

    Note:
        If restrict is not supported, the define is set to empty string.

    Args:
        define: Define name (defaults to "restrict")
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    return json.encode({
        "code": _AC_C_RESTRICT_TEMPLATE,
        "define": define,
        "define_value": "restrict",  # Value to use if check succeeds
        "define_value_fail": "",  # Value to use if check fails
        "language": language,
        "name": "restrict",
        "type": "compile",
    })

def ac_prog_cc_c_o(define = "NO_MINUS_C_MINUS_O", language = "c"):
    """Check if the compiler supports -c and -o flags simultaneously.

    Note:
        If the compiler does NOT support both flags together, the define is set.

    Args:
        define: Define name (defaults to "NO_MINUS_C_MINUS_O")
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # This is a bit tricky to test in Bazel since we don't directly control
    # the compiler invocation. We'll test by trying to compile a simple program
    # and assume that if compilation succeeds, the flags work.

    return json.encode({
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "define_value": "",  # Value if flags work (no define)
        "define_value_fail": "1",  # Value if flags don't work
        "language": language,
        "name": "cc_c_o",
        "type": "compile",
    })

def ac_check_c_compiler_flag(flag, define = None, language = "c"):
    """Check if the C compiler supports a specific flag.

    Args:
        flag: Compiler flag to test (e.g., "-Wall", "-std=c99")
        define: Custom define name (defaults to HAVE_FLAG_<FLAG>)
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")
        define = "HAVE_FLAG_" + clean_flag.upper()

    return json.encode({
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + flag.replace("-", "_"),
        "type": "compile",
    })

def ac_check_cxx_compiler_flag(flag, define = None, language = "cpp"):
    """Check if the C++ compiler supports a specific flag.

    Args:
        flag: Compiler flag to test (e.g., "-Wall", "-std=c++17")
        define: Custom define name (defaults to HAVE_FLAG_<FLAG>)
        language: Language to use for check ("c" or "cpp")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """
    if not define:
        # Clean up the flag name for the define
        # Replace special characters with underscores for valid C macro names
        clean_flag = flag.replace("-", "_").replace("=", "_").replace("+", "_").replace("/", "_").replace(":", "_")
        define = "HAVE_FLAG_" + clean_flag.upper()

    return json.encode({
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "flag": flag,  # Special field to indicate this needs flag testing
        "language": language,
        "name": "flag_" + flag.replace("-", "_"),
        "type": "compile",
    })

def ac_define(define, value = "1"):
    """Define a configuration macro unconditionally.

    This is equivalent to GNU Autoconf's AC_DEFINE macro. It creates
    a define that will always be set in the generated config.h file.

    Example:

    ```python
    AC_DEFINE("CUSTOM_VALUE", "42")
    AC_DEFINE("ENABLE_FEATURE", "1")
    AC_DEFINE("PROJECT_NAME", '"MyProject"')
    ```

    Args:
        define: Define name (e.g., "CUSTOM_VALUE")
        value: Value to assign (defaults to "1")

    Returns:
        A JSON-encoded check string for use with the autoconf rule.
    """

    # Create a compile check that always succeeds
    return json.encode({
        "code": _AC_SIMPLE_MAIN_TEMPLATE,
        "define": define,
        "define_value": str(value),
        "define_value_fail": str(value),
        "language": "c",
        "name": define,
        "type": "compile",
    })

macros = struct(
    AC_CHECK_HEADER = ac_check_header,
    AC_CHECK_FUNC = ac_check_func,
    AC_CHECK_TYPE = ac_check_type,
    AC_CHECK_SYMBOL = ac_check_symbol,
    AC_TRY_COMPILE = ac_try_compile,
    AC_PROG_CC = ac_prog_cc,
    AC_PROG_CXX = ac_prog_cxx,
    AC_CHECK_SIZEOF = ac_check_sizeof,
    AC_CHECK_ALIGNOF = ac_check_alignof,
    AC_CHECK_DECL = ac_check_decl,
    AC_CHECK_MEMBER = ac_check_member,
    AC_COMPUTE_INT = ac_compute_int,
    AC_C_BIGENDIAN = ac_c_bigendian,
    AC_C_INLINE = ac_c_inline,
    AC_C_RESTRICT = ac_c_restrict,
    AC_PROG_CC_C_O = ac_prog_cc_c_o,
    AC_CHECK_C_COMPILER_FLAG = ac_check_c_compiler_flag,
    AC_CHECK_CXX_COMPILER_FLAG = ac_check_cxx_compiler_flag,
    AC_DEFINE = ac_define,
)
