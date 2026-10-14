"""Unit tests for the marker parser (no autotools needed)."""

from __future__ import annotations

import unittest

from tools.convert import markers

T = "\t"


def marker(kind: str, *fields: str) -> str:
    return "#RCCAC" + T + kind + T + T.join(fields)


class MarkersTest(unittest.TestCase):

    def test_defun_with_nested_call_and_text(self) -> None:
        text = "\n".join([
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
        ])
        tree = markers.parse(text)
        self.assertEqual(tree.warnings, [])
        self.assertEqual([type(i).__name__ for i in tree.items], ["Text", "Node", "Text"])
        foo = tree.nodes[0]
        self.assertEqual(foo.kind, "defun")
        self.assertEqual(foo.name, "gl_FOO")
        self.assertEqual(foo.loc_file, "m4/foo.m4")
        self.assertEqual(foo.loc_line, 9)
        self.assertEqual(foo.args, ["first"])
        self.assertEqual(foo.line, 2)
        self.assertEqual(foo.end_line, 11)
        self.assertEqual(foo.shell, "REPLACE_FOO=0\nfi\n")
        (define,) = foo.children
        self.assertEqual(define.kind, "call")
        self.assertEqual(define.name, "AC_DEFINE")
        self.assertEqual(define.args, ["HAVE_FOO", "1"])
        self.assertEqual(define.shell, "printf '%s\\n' '#define HAVE_FOO 1' >>confdefs.h\n")
        self.assertEqual(foo.calls("AC_DEFINE"), [define])
        self.assertEqual(tree.find("AC_DEFINE"), [define])

    def test_continuation_lines_and_tabs_in_free_text(self) -> None:
        text = "\n".join([
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
            "if test \"$cross_compiling\" = yes",
            marker("ENDCALL", "id=7", "macro=AC_RUN_IFELSE"),
        ])
        tree = markers.parse(text)
        self.assertEqual(tree.warnings, [])
        (call,) = tree.nodes
        self.assertEqual(len(call.args), 4)
        self.assertEqual(call.args[0], "AC_LANG_PROGRAM([[#include <x.h>]],\n  [[return a[0] = b;]])")
        self.assertEqual(call.args[1], "")
        self.assertEqual(call.args[3], "case $host_os in\tmingw*) x=yes ;; esac")
        self.assertEqual(
            call.prog,
            "#include <x.h>\nint\nmain (void)\n{\nreturn a[0] = b;\n  ;\n  return 0;\n}",
        )

    def test_hoisted_defun_inside_top_level_call_is_a_child(self) -> None:
        text = "\n".join([
            marker("CALL", "id=1", "macro=AC_CHECK_HEADERS"),
            marker("BEGIN", "id=2", "name=gl_X", "loc=m4/x.m4:1"),
            marker("END", "id=2", "name=gl_X"),
            "ac_fn_c_check_header_compile",
            marker("ENDCALL", "id=1", "macro=AC_CHECK_HEADERS"),
        ])
        tree = markers.parse(text)
        self.assertEqual(tree.warnings, [])
        (call,) = tree.nodes
        self.assertEqual([c.name for c in call.children], ["gl_X"])
        self.assertEqual([n.name for n in tree.defuns()], ["gl_X"])

    def test_internal_defuns_are_filtered(self) -> None:
        text = "\n".join([
            marker("BEGIN", "id=1", "name=_AC_Header_stdio_h", "loc=configure.ac:5"),
            marker("END", "id=1", "name=_AC_Header_stdio_h"),
            marker("BEGIN", "id=2", "name=gl_Y", "loc=-"),
            marker("END", "id=2", "name=gl_Y"),
        ])
        tree = markers.parse(text)
        self.assertEqual([n.name for n in tree.defuns()], ["gl_Y"])
        self.assertEqual(
            [n.name for n in tree.defuns(include_internal=True)],
            ["_AC_Header_stdio_h", "gl_Y"],
        )
        self.assertIsNone(tree.find("gl_Y")[0].loc)

    def test_mismatched_markers_are_reported_not_fatal(self) -> None:
        text = "\n".join([
            marker("BEGIN", "id=1", "name=A", "loc=a.m4:1"),
            marker("CALL", "id=2", "macro=AC_SUBST"),
            marker("END", "id=1", "name=A"),
            marker("ENDCALL", "id=9", "macro=AC_SUBST"),
            marker("ARG", "id=8", "n=1", "x"),
        ])
        tree = markers.parse(text)
        self.assertEqual(len(tree.warnings), 3, tree.warnings)
        self.assertEqual(tree.nodes[0].end_line, 3)

    def test_strip_embedded_markers(self) -> None:
        text = "int\n#RCCAC\tCALL\tid=3\tmacro=AC_REQUIRE\n#RCCAC\tARG\tid=3\tn=1\tX\n#RCCAC|y\nmain (void)"
        self.assertEqual(markers.strip_embedded_markers(text), "int\nmain (void)")


if __name__ == "__main__":
    unittest.main()
