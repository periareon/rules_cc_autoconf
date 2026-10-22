"""Unit tests for the m4 argument helpers."""

from __future__ import annotations

import pytest

from tools.convert import m4args

# Argument splitting, quoting and call parsing.


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[a, b], [c]", ["[a, b]", "[c]"]),
        ("x, m4_if(a, b), [[d, e]]", ["x", "m4_if(a, b)", "[[d, e]]"]),
        ("", []),
        ("a,,b", ["a", "", "b"]),
    ],
    ids=["quoted", "nested-call", "empty", "empty-middle"],
)
def test_split_args_respects_quotes_and_parens(text: str, expected: list[str]) -> None:
    assert m4args.split_args(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[stdio.h]", "stdio.h"),
        ("[[#include <x.h>]]", "[#include <x.h>]"),
        ("[a] b [c]", "[a] b [c]"),
        ("  plain ", "plain"),
    ],
    ids=["one-level", "two-levels", "not-enclosing", "whitespace"],
)
def test_unquote(text: str, expected: str) -> None:
    assert m4args.unquote(text) == expected


def test_parse_call() -> None:
    call = m4args.parse_call("AC_DEFINE([HAVE_X], [1], [Have X.])")
    assert call is not None
    assert call.name == "AC_DEFINE"
    assert call.args == ["HAVE_X", "1", "Have X."]
    bare = m4args.parse_call("AC_PROG_CC")
    assert bare is not None
    assert bare.args == []
    assert m4args.parse_call("x=yes") is None
    assert m4args.parse_call("AC_DEFINE([A]) AC_DEFINE([B])") is None
    assert m4args.parse_call("AC_DEFINE([A])dnl") is not None


def test_lang_program() -> None:
    program = m4args.lang_program("AC_LANG_PROGRAM([[#include <stdlib.h>\n  int x;]], [[return x;]])")
    assert program is not None
    assert program.prologue == "#include <stdlib.h>\n  int x;"
    assert program.body == "return x;"
    assert m4args.lang_program("AC_LANG_SOURCE([[int main(void) {}]])") is None
    assert m4args.lang_source("AC_LANG_SOURCE([[int main(void) {}]])") == "int main(void) {}"


def test_lists_and_includes() -> None:
    assert m4args.words(" a.h\n b.h ") == ["a.h", "b.h"]
    assert m4args.is_literal_list("a b")
    assert not m4args.is_literal_list("$gl_headers")
    assert m4args.includes_lines("#include <a.h>\n  #include <b.h>\n") == ["#include <a.h>", "#include <b.h>"]


def test_quadrigraphs() -> None:
    assert m4args.quadrigraphs("a@<:@2@:>@ @S|@x @%:@") == "a[2] $x #"


def test_words_drop_line_continuations() -> None:
    # `AC_CHECK_FUNCS(fnmatch memmove \\<newline> strpbrk)`: the backslash
    # reaches the macro as a word of its own.
    assert m4args.words("fnmatch memmove \\\n\t\tstrpbrk") == ["fnmatch", "memmove", "strpbrk"]
    assert m4args.words("a \\ b") == ["a", "b"]


def test_neutralize_unexpanded() -> None:
    shell = (
        "AX_COMPILE_CHECK_RETTYPE(major, 0,\n[#include <x.h>])\nPA_CROSS_COMPILE\n"
        "PKG_CHECK_MODULES([CATCH2], [catch2],, [AC_LANG_PUSH([C++])])\nFOO_BAR=1\n__RCCAC_NODE_5__\n"
    )
    text, names, _builtins = m4args.neutralize_unexpanded(shell)
    assert text == (
        ": AX_COMPILE_CHECK_RETTYPE\n: PA_CROSS_COMPILE\n: PKG_CHECK_MODULES\nFOO_BAR=1\n__RCCAC_NODE_5__\n"
    )
    assert names == ["AX_COMPILE_CHECK_RETTYPE", "PKG_CHECK_MODULES", "PA_CROSS_COMPILE"]
    assert m4args.neutralize_unexpanded("x=$(expr 1)\n") == ("x=$(expr 1)\n", [], [])


# Unexpanded macros left in the shell.


def test_undefined_builtins_and_comments() -> None:
    """A macro nothing defines is reported; an autoconf builtin left in the shell is not a
    missing macro; a call in a shell comment stays; a mixed-case C call is not a macro."""
    text, names, builtins = m4args.neutralize_unexpanded(
        "#  AC_DEFINE(DEBUG)\nPKG_CHECK_MODULES([X], [y])\ncase $x in\n  yes) AC_CHECK_FUNCS(foo) ;;\nesac\n"
        "echo 'EVP_DecryptInit(ctx)'\nif test x = x; then # AX_FOO(bar)\n  :\nfi\n"
    )
    assert names == ["PKG_CHECK_MODULES"]
    assert builtins == ["AC_CHECK_FUNCS"]
    assert "#  AC_DEFINE(DEBUG)" in text
    assert "# AX_FOO(bar)" in text
    assert ": PKG_CHECK_MODULES" in text
    assert "EVP_DecryptInit(ctx)" in text


# M4sh variable forms.


def test_as_var_set_and_as_var_if() -> None:
    """nasm's PA_HAVE_FUNC: `AS_VAR_SET([cache],[yes])` is `cache=yes`; `AS_VAR_IF([cache],[yes],[a],[b])`
    tests the variable."""
    assert m4args.m4sh_to_shell("AS_VAR_SET([cache],[yes])") == "cache=yes"
    assert m4args.m4sh_to_shell("AS_VAR_SET([x],[a b])") == 'x="a b"'
    shell = m4args.m4sh_to_shell("AS_VAR_IF([cache],[yes],[AC_DEFINE([HAVE_X], 1)],[:])")
    assert 'if test "x$cache" = xyes; then' in shell
    assert "AC_DEFINE([HAVE_X], 1)" in shell
    assert "else" in shell
