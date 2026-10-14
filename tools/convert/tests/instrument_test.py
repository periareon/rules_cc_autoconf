"""The instrumented expansion against the bundled autoconf.

Pins the claims the converter is built on: AC_DEFUN bodies are delimited and
located, AC_REQUIRE'd bodies are hoisted ahead of the requirer and never
nested in it, direct calls nest, argument and program payloads round-trip,
and autom4te accepts the markers.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

from autoconf.tests.gnu.autotools import Autotools, scratch_dir

from tools.convert import expand as expand_mod
from tools.convert import markers

RT_M4 = textwrap.dedent(
    """\
    # Fixture macros.
    AC_DEFUN([RT_A],
    [
      AC_REQUIRE([RT_B])
      AC_CHECK_HEADERS([stdio.h stdlib.h])
      AC_DEFINE([A_DEF], [1], [Defined by RT_A.])
      if test "$ac_cv_header_stdio_h" = yes; then
        AC_SUBST([A_VAR], [yes])
      fi
    ])

    AC_DEFUN_ONCE([RT_B],
    [
      AC_CHECK_FUNCS_ONCE([malloc])
      AC_SUBST([B_VAR], [x])
    ])

    AC_DEFUN([RT_C],
    [
      RT_D([arg1], [arg 2])
      AC_CACHE_CHECK([whether it runs], [rt_cv_runs],
        [AC_RUN_IFELSE(
           [AC_LANG_PROGRAM([[#include <stdio.h>
             int a@<:@2@:>@;]], [[return a[0];]])],
           [rt_cv_runs=yes],
           [rt_cv_runs=no],
           [case "$host_os" in
              mingw* | windows*) rt_cv_runs="guessing yes" ;;
              *)                 rt_cv_runs="guessing no" ;;
            esac
           ])])
    ])

    AC_DEFUN([RT_D],
    [
      AC_CHECK_TYPE([size_t])
      AC_MSG_WARN([$1 and $2])
    ])
    """
)

CONFIGURE_AC = textwrap.dedent(
    """\
    AC_INIT([fixture], [1.2.3], [bugs@example.org])
    AC_CONFIG_MACRO_DIRS([m4])
    AC_CONFIG_HEADERS([config.h])
    AC_PROG_CC
    RT_A
    RT_C
    AC_OUTPUT
    """
)


class InstrumentTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.autotools = Autotools(scratch_dir("autotools"))
        src = scratch_dir("fixture-src")
        (src / "m4").mkdir()
        (src / "m4" / "rt.m4").write_text(RT_M4, encoding="utf-8", newline="\n")
        (src / "configure.ac").write_text(CONFIGURE_AC, encoding="utf-8", newline="\n")
        cls.expansion = expand_mod.expand(cls.autotools, src, scratch_dir("fixture-work"))
        cls.text = cls.expansion.read()
        cls.tree = markers.parse(cls.text)

    def test_autom4te_accepted_the_markers(self) -> None:
        self.assertEqual(self.tree.warnings, [])
        forbidden = [w for w in self.expansion.warnings if "possibly undefined macro" in w]
        self.assertEqual(forbidden, [], self.expansion.autom4te_log)
        self.assertNotIn("autom4te exited", "\n".join(self.expansion.warnings),
                         self.expansion.autom4te_log)
        self.assertEqual(self.expansion.m4_files, ["m4/rt.m4"])

    def test_defuns_are_located_and_ordered(self) -> None:
        defuns = self.tree.defuns()
        names = [node.name for node in defuns]
        # RT_B is required by RT_A: hoisted ahead of it, not nested in it.
        self.assertLess(names.index("RT_B"), names.index("RT_A"), names)
        (rt_a,) = self.tree.find("RT_A")
        self.assertNotIn("RT_B", [child.name for child in rt_a.walk()])
        self.assertEqual(rt_a.loc_file, "m4/rt.m4")
        self.assertEqual(rt_a.loc_line, 2)
        (rt_b,) = self.tree.find("RT_B")
        self.assertEqual(rt_b.loc_line, 12)
        # RT_D is called directly by RT_C: nested, with its arguments.
        (rt_c,) = self.tree.find("RT_C")
        (rt_d,) = [child for child in rt_c.children if child.name == "RT_D"]
        self.assertEqual(rt_d.args, ["arg1", "arg 2"])
        self.assertEqual(rt_d.loc_line, 34)
        # Internal registrations created by *_ONCE are visible but flagged.
        internal = [n.name for n in self.tree.defuns(include_internal=True) if n.is_internal]
        self.assertIn("_AC_Func_malloc", internal)
        self.assertNotIn("_AC_Func_malloc", names)

    def test_leaf_calls_and_arguments(self) -> None:
        (rt_a,) = self.tree.find("RT_A")
        # The body's own AC_REQUIRE; the ones nested in AC_CHECK_HEADERS'
        # expansion (AC_CHECK_INCLUDES_DEFAULT) belong to that call.
        self.assertEqual([c.args for c in rt_a.direct_calls("AC_REQUIRE")], [["RT_B"]])
        self.assertIn(["AC_CHECK_INCLUDES_DEFAULT"], [c.args for c in rt_a.calls("AC_REQUIRE")])
        (headers,) = rt_a.calls("AC_CHECK_HEADERS")
        self.assertEqual(headers.args, ["stdio.h stdlib.h"])
        # AC_CHECK_HEADERS expands to AC_DEFINEs of its own (HAVE_STDIO_H...);
        # the body's AC_DEFINE is the one direct child.
        (define,) = rt_a.direct_calls("AC_DEFINE")
        self.assertEqual(define.args, ["A_DEF", "1", "Defined by RT_A."])
        self.assertEqual([c.args for c in headers.calls("AC_DEFINE")],
                         [["HAVE_STDIO_H"], ["HAVE_STDLIB_H"]])
        # The residual shell between markers keeps the control flow.
        self.assertIn('if test "$ac_cv_header_stdio_h" = yes; then', rt_a.shell)
        (subst,) = rt_a.direct_calls("AC_SUBST")
        self.assertEqual(subst.args, ["A_VAR", "yes"])
        (rt_b,) = self.tree.find("RT_B")
        (funcs_once,) = rt_b.direct_calls("AC_CHECK_FUNCS_ONCE")
        self.assertEqual(funcs_once.args, ["malloc"])

    def test_run_ifelse_payloads(self) -> None:
        (rt_c,) = self.tree.find("RT_C")
        (cache,) = rt_c.calls("AC_CACHE_CHECK")
        self.assertEqual(cache.args[:2], ["whether it runs", "rt_cv_runs"])
        (run,) = rt_c.calls("AC_RUN_IFELSE")
        self.assertEqual(len(run.args), 4)
        self.assertTrue(run.args[0].startswith("AC_LANG_PROGRAM("), run.args[0])
        self.assertEqual(run.args[1], "rt_cv_runs=yes")
        self.assertIn('mingw* | windows*) rt_cv_runs="guessing yes" ;;', run.args[3])
        # The program is read back from the conftest heredoc of the expansion,
        # fully expanded and with the quadrigraph brackets restored.
        program = run.program()
        self.assertIsNotNone(program, run.shell)
        self.assertIn("int a[2];", program)
        self.assertIn("return a[0];", program)
        self.assertIn('if test "$cross_compiling" = yes', run.shell)

    def test_configure_ac_level_markers(self) -> None:
        (init,) = self.tree.find("AC_INIT")
        self.assertEqual(init.args, ["fixture", "1.2.3", "bugs@example.org"])
        (headers,) = [n for n in self.tree.find("AC_CONFIG_HEADERS") if n.kind == "call"]
        self.assertEqual(headers.args, ["config.h"])
        # Library macros are not instrumented: no range for AC_PROG_CC.
        self.assertEqual(self.tree.find("AC_PROG_CC"), [])


if __name__ == "__main__":
    unittest.main()
