"""Unit tests for the sed-to-substitutions translation of header recipes."""

from __future__ import annotations

import re
import textwrap

from tools.convert import sedtext

# The shape of gnulib's verify.h, cut down: a guard, a block that is live only once copied
# into assert.h, and the `verify` / `assume` half between the omit markers.
VERIFY_H = textwrap.dedent("""\
    #ifndef _GL_VERIFY_H
    #define _GL_VERIFY_H

    #define _GL_VERIFY(R, DIAGNOSTIC, ...) _gl_verify_type (R)
    #ifdef _GL_STATIC_ASSERT_H
    # define _Static_assert(...) _GL_VERIFY (__VA_ARGS__, "static assertion failed", -)
    #endif

    /* @assert.h omit start@  */
    #define verify(R) _GL_VERIFY (R, "verify (" #R ")", -)
    /* @assert.h omit end@  */

    #endif
    """)
PROGRAM = (
    "sed -e '/@assert.h omit start@/,/@assert.h omit end@/d' -e 's|_gl_verify|_gl_static_assert|g' "
    "-e 's|_GL_VERIFY|_GL_STATIC_ASSERT|g' -e 's|_GL\\(_STATIC_ASSERT_H\\)|_GL_M4\\1|g'"
)


def apply(substitutions: dict[str, str], text: str) -> str:
    """As the resolver does: in key order, each over the whole text."""
    for key in sorted(substitutions):
        text = text.replace(key, substitutions[key])
    return text


def test_parse() -> None:
    commands = sedtext.parse(PROGRAM)
    assert commands is not None
    assert [type(c).__name__ for c in commands] == ["RangeDelete", "Subst", "Subst", "Subst"]
    last = commands[-1]
    assert isinstance(last, sedtext.Subst)
    assert last.pattern.pattern == "_GL(_STATIC_ASSERT_H)"
    assert last.replacement == "_GL_M4\\g<1>"
    assert last.everywhere
    # A command this module does not know (a single-line delete, `y`, `p`) refuses the program.
    assert sedtext.parse("sed -e '/x/d'") is None
    assert sedtext.parse("sed -e 'y/abc/xyz/'") is None
    assert sedtext.parse("sed -n -e 's|a|b|'") == [sedtext.Subst(re.compile("a"), "b", False)]


def test_basic_regex_and_replacement() -> None:
    assert sedtext._basic_regex("a(b)").pattern == "a\\(b\\)"
    assert sedtext._basic_regex("a\\(b\\)+").pattern == "a(b)\\+"
    assert sedtext._basic_regex("x\\{2\\}").pattern == "x{2}"
    assert sedtext._replacement("\\1&\\&") == "\\g<1>\\g<0>&"


def test_run_matches_sed() -> None:
    """The emulation deletes the range inclusive of both marker lines and applies the
    renames in order, so the renamed guard is renamed again by the later command."""
    commands = sedtext.parse(PROGRAM)
    assert commands is not None
    out = sedtext.run(commands, VERIFY_H)
    assert out[8:11] == [None, None, None]
    assert "\n".join(line for line in out if line is not None) == textwrap.dedent("""\
        #ifndef _GL_M4_STATIC_ASSERT_H
        #define _GL_M4_STATIC_ASSERT_H

        #define _GL_STATIC_ASSERT(R, DIAGNOSTIC, ...) _gl_static_assert_type (R)
        #ifdef _GL_M4_STATIC_ASSERT_H
        # define _Static_assert(...) _GL_STATIC_ASSERT (__VA_ARGS__, "static assertion failed", -)
        #endif


        #endif
        """)


def test_substitutions_reproduce_sed() -> None:
    """The matched texts become substitutions; the guard lines, whose rename the later
    command renames again (something key-ordered exact replacement cannot do), are keys of
    their own; the deleted range is fenced with `#if 0`."""
    commands = sedtext.parse(PROGRAM)
    assert commands is not None
    substitutions = sedtext.substitutions(commands, VERIFY_H)
    assert substitutions == {
        "#define _GL_VERIFY_H": "#define _GL_M4_STATIC_ASSERT_H",
        "#ifndef _GL_VERIFY_H": "#ifndef _GL_M4_STATIC_ASSERT_H",
        "/* @assert.h omit end@  */": "#endif /* @assert.h omit end@  */",
        "/* @assert.h omit start@  */": "#if 0 /* @assert.h omit start@  */",
        "_GL_STATIC_ASSERT_H": "_GL_M4_STATIC_ASSERT_H",
        "_GL_VERIFY": "_GL_STATIC_ASSERT",
        "_gl_verify": "_gl_static_assert",
    }
    rendered = apply(substitutions, VERIFY_H)
    # Everything outside the fence is sed's output; the fence holds the deleted half.
    expected = [line for line in sedtext.run(commands, VERIFY_H) if line is not None]
    got = rendered.split("\n")
    assert got[8] == "#if 0 /* @assert.h omit start@  */"
    assert got[10] == "#endif /* @assert.h omit end@  */"
    assert got[:8] + got[11:] == expected
    # Older gnulib: the renames alone, no range and no second rename of the guard.
    simple = sedtext.parse("sed -e 's|_gl_verify|_gl_static_assert|g' -e 's|_GL_VERIFY|_GL_STATIC_ASSERT|g'")
    assert simple is not None
    assert sedtext.substitutions(simple, VERIFY_H) == {
        "_GL_VERIFY": "_GL_STATIC_ASSERT",
        "_gl_verify": "_gl_static_assert",
    }


def test_substitutions_refused() -> None:
    """A range that opens on the last line (sed looks for its end from the next line on, so it
    is one line) has no fence; renames whose results overlap (so no whole-line key settles
    them) are given up."""
    at_end = sedtext.parse("sed -e '/omit end/,/never/d'")
    assert at_end is not None
    assert sedtext.substitutions(at_end, VERIFY_H.replace("\n#endif\n", "\n")) is None
    # From the start marker to the end of the file when the end pattern never matches.
    assert sedtext.run(at_end, "a\nomit end\nb\nc\n") == ["a", None, None, None, None]
    # The second rename matches the first's result, so key order (`X_H` before `_GL_VERIFY`)
    # misses it; the whole-line key `_GL_VERIFY_H` sorts after `_GL_VERIFY`, which has
    # consumed it by then.  (In verify.h the guard lines start with `#` and sort first.)
    chained = sedtext.parse("sed -e 's|_GL_VERIFY|X|g' -e 's|X_H|Y|g'")
    assert chained is not None
    assert sedtext.substitutions(chained, "_GL_VERIFY_H\n") is None
    assert sedtext.substitutions(chained, "#ifndef _GL_VERIFY_H\n") == {
        "#ifndef _GL_VERIFY_H": "#ifndef Y",
        "X_H": "Y",
        "_GL_VERIFY": "X",
    }
    assert sedtext.substitutions([], "x\n") == {}
