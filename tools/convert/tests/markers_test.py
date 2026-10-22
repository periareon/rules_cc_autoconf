"""Unit tests for the marker parser (no autotools needed)."""

from __future__ import annotations

from tools.convert import markers

T = "\t"


def marker(kind: str, *fields: str) -> str:
    return "#RCCAC" + T + kind + T + T.join(fields)


def test_defun_with_nested_call_and_text() -> None:
    text = "\n".join(
        [
            "#! /bin/sh",
            marker("BEGIN", "id=1", "name=gl_FOO", "loc=m4\\foo.m4:9"),
            marker("ARG", "id=1", "n=1", "first"),
            "REPLACE_FOO=0",
            marker("CALL", "id=2", "macro=AC_DEFINE"),
            marker("ARG", "id=2", "n=1", "HAVE_FOO"),
            marker("ARG", "id=2", "n=2", "1"),
            "printf '%s\\n' '#define HAVE_FOO 1' >>confdefs.h",
            marker("ENDCALL", "id=2", "macro=AC_DEFINE"),
            "fi",
            marker("END", "id=1", "name=gl_FOO"),
            "exit 0",
        ]
    )
    tree = markers.parse(text)
    assert tree.warnings == []
    assert [type(i).__name__ for i in tree.items] == ["Text", "Node", "Text"]
    foo = tree.nodes[0]
    assert foo.kind == "defun"
    assert foo.name == "gl_FOO"
    assert foo.loc_file == "m4/foo.m4"
    assert foo.loc_line == 9
    assert foo.args == ["first"]
    assert foo.line == 2
    assert foo.end_line == 11
    assert foo.shell == "REPLACE_FOO=0\nfi\n"
    (define,) = foo.children
    assert define.kind == "call"
    assert define.name == "AC_DEFINE"
    assert define.args == ["HAVE_FOO", "1"]
    assert define.shell == "printf '%s\\n' '#define HAVE_FOO 1' >>confdefs.h\n"
    assert foo.calls("AC_DEFINE") == [define]
    assert tree.find("AC_DEFINE") == [define]


def test_continuation_lines_and_tabs_in_free_text() -> None:
    text = "\n".join(
        [
            marker("CALL", "id=7", "macro=AC_RUN_IFELSE"),
            marker("ARG", "id=7", "n=1", "AC_LANG_PROGRAM([[#include <x.h>]],"),
            "#RCCAC|  [[return a[0] = b;]])",
            marker("ARG", "id=7", "n=4", "case $host_os in\tmingw*) x=yes ;; esac"),
            marker("PROG", "id=7", "#include <x.h>"),
            "#RCCAC|int",
            "#RCCAC|main (void)",
            "#RCCAC|{",
            "#RCCAC|return a[0] = b;",
            "#RCCAC|  ;",
            "#RCCAC|  return 0;",
            "#RCCAC|}",
            'if test "$cross_compiling" = yes',
            marker("ENDCALL", "id=7", "macro=AC_RUN_IFELSE"),
        ]
    )
    tree = markers.parse(text)
    assert tree.warnings == []
    (call,) = tree.nodes
    assert len(call.args) == 4
    assert call.args[0] == "AC_LANG_PROGRAM([[#include <x.h>]],\n  [[return a[0] = b;]])"
    assert call.args[1] == ""
    assert call.args[3] == "case $host_os in\tmingw*) x=yes ;; esac"
    assert call.prog == "#include <x.h>\nint\nmain (void)\n{\nreturn a[0] = b;\n  ;\n  return 0;\n}"


def test_hoisted_defun_inside_top_level_call_is_a_child() -> None:
    text = "\n".join(
        [
            marker("CALL", "id=1", "macro=AC_CHECK_HEADERS"),
            marker("BEGIN", "id=2", "name=gl_X", "loc=m4/x.m4:1"),
            marker("END", "id=2", "name=gl_X"),
            "ac_fn_c_check_header_compile",
            marker("ENDCALL", "id=1", "macro=AC_CHECK_HEADERS"),
        ]
    )
    tree = markers.parse(text)
    assert tree.warnings == []
    (call,) = tree.nodes
    assert [c.name for c in call.children] == ["gl_X"]
    assert [n.name for n in tree.defuns()] == ["gl_X"]


def test_internal_defuns_are_filtered() -> None:
    text = "\n".join(
        [
            marker("BEGIN", "id=1", "name=_AC_Header_stdio_h", "loc=configure.ac:5"),
            marker("END", "id=1", "name=_AC_Header_stdio_h"),
            marker("BEGIN", "id=2", "name=gl_Y", "loc=-"),
            marker("END", "id=2", "name=gl_Y"),
        ]
    )
    tree = markers.parse(text)
    assert [n.name for n in tree.defuns()] == ["gl_Y"]
    assert [n.name for n in tree.defuns(include_internal=True)] == ["_AC_Header_stdio_h", "gl_Y"]
    assert tree.find("gl_Y")[0].loc is None


def test_mismatched_markers_are_reported_not_fatal() -> None:
    text = "\n".join(
        [
            marker("BEGIN", "id=1", "name=A", "loc=a.m4:1"),
            marker("CALL", "id=2", "macro=AC_SUBST"),
            marker("END", "id=1", "name=A"),
            marker("ENDCALL", "id=9", "macro=AC_SUBST"),
            marker("ARG", "id=8", "n=1", "x"),
        ]
    )
    tree = markers.parse(text)
    assert len(tree.warnings) == 3, tree.warnings
    assert tree.nodes[0].end_line == 3


def test_strip_embedded_markers() -> None:
    text = "int\n#RCCAC\tCALL\tid=3\tmacro=AC_REQUIRE\n#RCCAC\tARG\tid=3\tn=1\tX\n#RCCAC|y\nmain (void)"
    assert markers.strip_embedded_markers(text) == "int\nmain (void)"
