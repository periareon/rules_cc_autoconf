"""Unit tests for the m4 argument helpers."""

from __future__ import annotations

import unittest

from tools.convert import m4args


class M4ArgsTest(unittest.TestCase):

    def test_split_args_respects_quotes_and_parens(self) -> None:
        self.assertEqual(m4args.split_args("[a, b], [c]"), ["[a, b]", "[c]"])
        self.assertEqual(m4args.split_args("x, m4_if(a, b), [[d, e]]"), ["x", "m4_if(a, b)", "[[d, e]]"])
        self.assertEqual(m4args.split_args(""), [])
        self.assertEqual(m4args.split_args("a,,b"), ["a", "", "b"])

    def test_unquote(self) -> None:
        self.assertEqual(m4args.unquote("[stdio.h]"), "stdio.h")
        self.assertEqual(m4args.unquote("[[#include <x.h>]]"), "[#include <x.h>]")
        self.assertEqual(m4args.unquote("[a] b [c]"), "[a] b [c]")
        self.assertEqual(m4args.unquote("  plain "), "plain")

    def test_parse_call(self) -> None:
        call = m4args.parse_call("AC_DEFINE([HAVE_X], [1], [Have X.])")
        self.assertIsNotNone(call)
        self.assertEqual(call.name, "AC_DEFINE")
        self.assertEqual(call.args, ["HAVE_X", "1", "Have X."])
        self.assertEqual(m4args.parse_call("AC_PROG_CC").args, [])
        self.assertIsNone(m4args.parse_call("x=yes"))
        self.assertIsNone(m4args.parse_call("AC_DEFINE([A]) AC_DEFINE([B])"))
        self.assertIsNotNone(m4args.parse_call("AC_DEFINE([A])dnl"))

    def test_lang_program(self) -> None:
        program = m4args.lang_program(
            "AC_LANG_PROGRAM([[#include <stdlib.h>\n  int x;]], [[return x;]])"
        )
        self.assertIsNotNone(program)
        self.assertEqual(program.prologue, "#include <stdlib.h>\n  int x;")
        self.assertEqual(program.body, "return x;")
        self.assertIsNone(m4args.lang_program("AC_LANG_SOURCE([[int main(void) {}]])"))
        self.assertEqual(m4args.lang_source("AC_LANG_SOURCE([[int main(void) {}]])"), "int main(void) {}")

    def test_lists_and_includes(self) -> None:
        self.assertEqual(m4args.words(" a.h\n b.h "), ["a.h", "b.h"])
        self.assertTrue(m4args.is_literal_list("a b"))
        self.assertFalse(m4args.is_literal_list("$gl_headers"))
        self.assertEqual(m4args.includes_lines("#include <a.h>\n  #include <b.h>\n"),
                         ["#include <a.h>", "#include <b.h>"])

    def test_quadrigraphs(self) -> None:
        self.assertEqual(m4args.quadrigraphs("a@<:@2@:>@ @S|@x @%:@"), "a[2] $x #")


    def test_words_drop_line_continuations(self) -> None:
        # `AC_CHECK_FUNCS(fnmatch memmove \\<newline> strpbrk)`: the backslash
        # reaches the macro as a word of its own.
        self.assertEqual(m4args.words("fnmatch memmove \\\n\t\tstrpbrk"), ["fnmatch", "memmove", "strpbrk"])
        self.assertEqual(m4args.words("a \\ b"), ["a", "b"])

    def test_neutralize_unexpanded(self) -> None:
        shell = ("AX_COMPILE_CHECK_RETTYPE(major, 0,\n[#include <x.h>])\nPA_CROSS_COMPILE\n"
                 "PKG_CHECK_MODULES([CATCH2], [catch2],, [AC_LANG_PUSH([C++])])\nFOO_BAR=1\n__RCCAC_NODE_5__\n")
        text, names = m4args.neutralize_unexpanded(shell)
        self.assertEqual(text, ": AX_COMPILE_CHECK_RETTYPE\n: PA_CROSS_COMPILE\n: PKG_CHECK_MODULES\nFOO_BAR=1\n"
                               "__RCCAC_NODE_5__\n")
        self.assertEqual(names, ["AX_COMPILE_CHECK_RETTYPE", "PKG_CHECK_MODULES", "PA_CROSS_COMPILE"])
        self.assertEqual(m4args.neutralize_unexpanded("x=$(expr 1)\n"), ("x=$(expr 1)\n", []))

if __name__ == "__main__":
    unittest.main()
