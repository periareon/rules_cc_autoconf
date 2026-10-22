"""Unit tests for the port index."""

from __future__ import annotations

from tools.convert.index import PortIndex, parse_build
from tools.convert.tests.helpers import must

FSTAT = '''"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/fstat.m4"""

autoconf(
    name = "gl_FUNC_FSTAT",
)

autoconf(
    name = "fstat",
)
'''

SYS_STAT_H = '''"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/sys_stat_h.m4"""
autoconf(name = "gl_SYS_STAT_H")
autoconf(name = "gl_SYS_STAT_H_DEFAULTS")
autoconf(name = "sys_stat_h")
'''

EXTENSIONS = '''"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/extensions.m4"""
autoconf(name = "extensions")
'''

CANONICAL_HOST = '''"""AC_CANONICAL_HOST"""
autoconf(name = "AC_CANONICAL_HOST")
'''

STRERROR_R = '''"""docs"""
autoconf(name = "ac_cv_func_strerror_r")
autoconf(name = "HAVE_STRERROR_R")
autoconf(name = "AC_FUNC_STRERROR_R")
'''


def make_index() -> PortIndex:
    return PortIndex(
        [
            parse_build("gnulib/m4/fstat", FSTAT),
            parse_build("gnulib/m4/sys_stat_h", SYS_STAT_H),
            parse_build("gnulib/m4/extensions", EXTENSIONS),
            parse_build("autoconf/macros/AC_CANONICAL_HOST", CANONICAL_HOST),
            parse_build("autoconf/macros/AC_FUNC_STRERROR_R", STRERROR_R),
        ]
    )


# ---- Parsing BUILD files and resolving macros


def test_parse_build() -> None:
    port = parse_build("gnulib/m4/fstat", FSTAT)
    assert port.m4_file == "fstat.m4"
    assert port.targets == {"gl_FUNC_FSTAT", "fstat"}
    assert port.aggregator == "fstat"
    assert port.label("fstat") == "//gnulib/m4/fstat"
    assert port.label("gl_FUNC_FSTAT") == "//gnulib/m4/fstat:gl_FUNC_FSTAT"
    assert must(port.url).endswith("/m4/fstat.m4")


def test_resolve_by_definition_file() -> None:
    index = make_index()
    exact = must(index.resolve("gl_FUNC_FSTAT", "m4/fstat.m4"))
    assert (exact.label, exact.reason, exact.exact) == ("//gnulib/m4/fstat:gl_FUNC_FSTAT", "target", True)
    # Windows spelling of the location.
    assert must(index.resolve("gl_FUNC_FSTAT", "m4\\fstat.m4")).label == "//gnulib/m4/fstat:gl_FUNC_FSTAT"
    # A macro of a ported file without its own target: the package.
    aggregate = must(index.resolve("gl_PREREQ_FSTAT", "m4/fstat.m4"))
    assert (aggregate.label, aggregate.reason, aggregate.exact) == ("//gnulib/m4/fstat", "file", False)
    # Macros whose name differs from the file: the file wins.
    assert must(index.resolve("gl_USE_SYSTEM_EXTENSIONS", "m4/extensions.m4")).label == "//gnulib/m4/extensions"


def test_resolve_by_name() -> None:
    index = make_index()
    assert must(index.resolve("AC_CANONICAL_HOST")).label == "//autoconf/macros/AC_CANONICAL_HOST"
    assert must(index.resolve("AC_CANONICAL_HOST")).reason == "macro-package"
    assert must(index.resolve("HAVE_STRERROR_R")).label == "//autoconf/macros/AC_FUNC_STRERROR_R:HAVE_STRERROR_R"
    assert must(index.resolve("gl_SYS_STAT_H")).label == "//gnulib/m4/sys_stat_h:gl_SYS_STAT_H"
    assert index.resolve("gl_UNKNOWN") is None
    assert index.resolve("gl_UNKNOWN", "m4/unknown.m4") is None


# ---- Results a port produces through macros


def test_next_headers_and_subst_true_are_substs() -> None:
    """What a port provides through macros and `subst = True` counts: errno_h's GL_NEXT_HEADERS
    gives NEXT_ERRNO_H, uchar_h's AC_COMPUTE_INT(..., subst = True) gives BITSIZEOF_WCHAR_T; the
    converter must not supply either itself."""
    text = """autoconf(
    name = "x",
    checks = gl_macros.GL_NEXT_HEADERS(
        ["errno.h", "sys/stat.h"],
        condition = "!gl_cv_header_errno_h_complete",
    ) + [
        checks.AC_COMPUTE_INT(
            "BITSIZEOF_WCHAR_T",
            "(int)(sizeof(wchar_t) * CHAR_BIT)",
            includes = ["#include <stddef.h>"],
            subst = True,
        ),
        checks.AC_COMPUTE_INT("SIZEOF_INT", "(int)sizeof(int)"),
        gl_checks.GL_NEXT_HEADER(
            "stdarg.h",
            name = "NEXT_STDARG_H",
            requires = ["!gl_cv_header_stdarg_h"],
        ),
        gl_checks.GL_NEXT_HEADER("limits.h"),
        checks.AC_CHECK_FUNC(
            "openat",
            define = "HAVE_OPENAT",
            subst = True,
        ),
        checks.AC_CHECK_DECL("strerror_r", subst = True),
        checks.AC_CHECK_FUNC("fchownat"),
        checks.AC_CHECK_FUNC(
            "getdelim",
            name = "_gl_cv_func_getdelim",
        ),
        checks.AC_CHECK_FUNC(
            "getrusage",
            define = "HAVE_GETRUSAGE",
            subst = "HAVE_GETRUSAGE",
        ),
    ],
)
"""
    port = parse_build("gnulib/m4/x", text)
    assert "NEXT_ERRNO_H" in port.substs
    assert "NEXT_AS_FIRST_DIRECTIVE_SYS_STAT_H" in port.substs
    assert "BITSIZEOF_WCHAR_T" in port.substs
    assert "SIZEOF_INT" not in port.substs
    assert "NEXT_STDARG_H" in port.substs
    assert "NEXT_LIMITS_H" in port.substs
    # One check, both buckets: not an ambiguity a condition has to spell out.
    assert port.dual == {"BITSIZEOF_WCHAR_T", "HAVE_OPENAT", "HAVE_DECL_STRERROR_R", "HAVE_GETRUSAGE"}
    assert "HAVE_OPENAT" in port.substs
    assert "HAVE_FCHOWNAT" not in port.substs
    # The probes behind the HAVE_* names: a condition over the name is the probe's success.
    assert port.probes == {
        "HAVE_OPENAT": "ac_cv_func_openat",
        "HAVE_DECL_STRERROR_R": "ac_cv_have_decl_strerror_r",
        "HAVE_FCHOWNAT": "ac_cv_func_fchownat",
        "HAVE_GETRUSAGE": "ac_cv_func_getrusage",
        "HAVE_GETDELIM": "_gl_cv_func_getdelim",
    }
    assert "_gl_cv_func_getdelim" in port.cache
    assert "ac_cv_func_getdelim" not in port.cache
    index = PortIndex(
        [
            port,
            parse_build(
                "gnulib/m4/y",
                'autoconf(\n    name = "y",\n    checks = [\n'
                '        checks.AC_DEFINE("BITSIZEOF_WCHAR_T", 32),\n    ],\n)\n',
            ),
        ]
    )
    assert "BITSIZEOF_WCHAR_T" not in index.ambiguous
    assert index.probes["HAVE_OPENAT"] == "ac_cv_func_openat"
    assert "HAVE_OPENAT" not in index.plain_substs
    assert "HAVE_OPENAT" in index.result_substs  # the probe substitutes it: a result on every platform
    assert "HAVE_FCHOWNAT" not in index.result_substs


def test_resolve_by_name_in_gnulib_file() -> None:
    """A port target's name defined in a file the index does not know is the port only when gnulib-tool
    wrote the file (bison's m4/printf-posix-rpl.m4); a project's macro of that name is not."""
    port = parse_build(
        "gnulib/m4/printf-posix",
        '"""https://github.com/coreutils/gnulib/blob/abc/m4/printf-posix.m4"""\n'
        'autoconf(name = "gl_FUNC_PRINTF_POSIX")\nautoconf(name = "printf-posix")',
    )
    index = PortIndex([port])
    plain = must(index.resolve("gl_FUNC_PRINTF_POSIX", "m4/printf-posix-rpl.m4"))
    assert (plain.reason, plain.exact) == ("name", False)
    library = must(index.resolve("gl_FUNC_PRINTF_POSIX", "m4/printf-posix-rpl.m4", library=True))
    assert (library.label, library.reason, library.exact) == (
        "//gnulib/m4/printf-posix:gl_FUNC_PRINTF_POSIX",
        "target",
        True,
    )
    assert index.resolve("gl_FUNC_NOTHING", "m4/x.m4", library=True) is None
