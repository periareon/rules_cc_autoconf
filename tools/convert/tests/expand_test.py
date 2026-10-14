"""Unit tests for the staging step of the expansion."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.convert import expand


class StageTest(unittest.TestCase):

    def test_macro_dirs_outside_the_project(self) -> None:
        """texlive's `libs/mpfi/configure.ac` says AC_CONFIG_MACRO_DIRS([../../m4]) and
        m4_include([../../m4/kpse-common.m4]): both are staged inside the work directory."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m4").mkdir()
            (root / "m4" / "kpse-common.m4").write_text("AC_DEFUN([KPSE_COMMON], [])\n")
            project = root / "libs" / "mpfi"
            project.mkdir(parents=True)
            (project / "configure.ac").write_text(
                "AC_INIT([mpfi], [1])\nAC_CONFIG_MACRO_DIRS([../../m4])\n"
                "m4_include([../../m4/kpse-common.m4])\nm4_include([../../version.ac])\nKPSE_COMMON\nAC_OUTPUT\n")
            (root / "version.ac").write_text("m4_define([tex_live_version], [2026])\n")
            work = root / "work"
            work.mkdir()
            staged, dirs = expand.stage(project, work)
            text = staged.read_text()
            self.assertEqual(dirs[0], "_outside_m4")
            self.assertTrue((work / "_outside_m4" / "kpse-common.m4").is_file())
            self.assertIn("AC_CONFIG_MACRO_DIRS([_outside_m4])", text)
            self.assertIn("m4_include([_outside_m4/kpse-common.m4])", text)
            self.assertIn("m4_include([_outside_version_ac])", text)
            self.assertTrue((work / "_outside_version_ac").is_file())
            self.assertFalse((root / "m4" / "work").exists())  # nothing written outside the work dir

    def test_hand_written_aclocal_includes_are_staged(self) -> None:
        """postgres: aclocal.m4 is hand-written and m4_includes `config/*.m4`; tcl's includes
        `../unix/tcl.m4`.  Both land in the work directory and the text says the staged names."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "project"
            (project / "config").mkdir(parents=True)
            (project / "configure.ac").write_text("AC_INIT([pg], [1])\nPGAC_X\nAC_OUTPUT\n")
            (project / "aclocal.m4").write_text(
                "m4_include([config/general.m4])\nm4_include([../shared/tcl.m4])\n")
            (project / "config" / "general.m4").write_text("AC_DEFUN([PGAC_X], [])\n")
            (root / "shared").mkdir()
            (root / "shared" / "tcl.m4").write_text("AC_DEFUN([TCL_X], [])\n")
            work = root / "work"
            work.mkdir()
            expand.stage(project, work)
            self.assertTrue((work / "config" / "general.m4").is_file())
            self.assertTrue((work / "_outside_shared_tcl_m4").is_file())
            self.assertIn("m4_include([_outside_shared_tcl_m4])", (work / "aclocal.m4").read_text())
            self.assertIn("m4_include([config/general.m4])", (work / "aclocal.m4").read_text())

    def test_plain_macro_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m4").mkdir()
            (root / "m4" / "x.m4").write_text("")
            (root / "configure.ac").write_text(
                "AC_INIT([x], [1])\nAC_CONFIG_MACRO_DIRS([m4])\nm4_include([version])\nm4_include([ac/extra.ac])\n")
            (root / "version").write_text("1.0\n")
            (root / "ac").mkdir()
            (root / "ac" / "extra.ac").write_text("m4_define([api], m4_include([ac/level.txt]))\n")
            (root / "ac" / "level.txt").write_text("3\n")
            work = root / "work"
            work.mkdir()
            _staged, dirs = expand.stage(root, work)
            self.assertEqual(dirs[0], "m4")
            self.assertTrue((work / "version").is_file())
            self.assertTrue((work / "ac" / "level.txt").is_file())  # included by an included file


if __name__ == "__main__":
    unittest.main()
