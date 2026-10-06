"""Smoke test: the bundled autotools run end to end without host autotools."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from autoconf.tests.gnu.autotools import Autotools, scratch_dir


class AutotoolsSmokeTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.autotools = Autotools(scratch_dir("autotools"))
        cls.work = scratch_dir("smoke")

    def test_versions(self) -> None:
        versions = self.autotools.versions(self.work)
        self.assertIn(self.autotools.autoconf_version, versions["autoconf"], versions)
        self.assertIn(self.autotools.automake_version, versions["aclocal"], versions)
        self.assertIn("GNU M4", versions["m4"], versions)
        self.assertTrue(versions["perl"].startswith("v"), versions)

    def test_aclocal_autoconf_configure(self) -> None:
        (self.work / "m4").mkdir(exist_ok=True)
        (self.work / "m4" / "local.m4").write_text(
            "AC_DEFUN([LOCAL_MACRO], [AC_SUBST([LOCAL_VALUE], [smoke])])\n",
            encoding="utf-8",
        )
        (self.work / "configure.ac").write_text(
            "AC_INIT([smoke], [1.0])\n"
            "AC_CONFIG_MACRO_DIRS([m4])\n"
            "LOCAL_MACRO\n"
            "AC_CONFIG_FILES([out.txt:out.txt.in])\n"
            "AC_OUTPUT\n",
            encoding="utf-8",
        )
        (self.work / "out.txt.in").write_text("value=@LOCAL_VALUE@\n", encoding="utf-8")

        aclocal = self.autotools.run("aclocal", ["-I", "m4"], cwd=self.work)
        self.assertEqual(aclocal.returncode, 0, aclocal.stdout)
        aclocal_m4 = (self.work / "aclocal.m4").read_text(encoding="utf-8")
        # Native perl on Windows joins the include path with a backslash.
        self.assertRegex(aclocal_m4, r"m4_include\(\[m4[/\\]local\.m4\]\)")

        autoconf = self.autotools.run("autoconf", [], cwd=self.work)
        self.assertEqual(autoconf.returncode, 0, autoconf.stdout)
        self.assertTrue((self.work / "configure").exists())

        # `environment()` already carries CONFIG_SHELL in the space-free
        # spelling configure needs to re-exec itself.
        env = self.autotools.environment()
        configure = subprocess.run(
            [self.autotools.shell, "./configure"],
            cwd=self.work,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        self.assertEqual(configure.returncode, 0, configure.stdout)
        self.assertEqual(
            (self.work / "out.txt").read_text(encoding="utf-8"), "value=smoke\n"
        )


if __name__ == "__main__":
    unittest.main()
