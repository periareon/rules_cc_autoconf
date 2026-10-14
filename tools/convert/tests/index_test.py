"""Unit tests for the port index."""

from __future__ import annotations

import unittest

from tools.convert.index import PortIndex, parse_build

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
    return PortIndex([
        parse_build("gnulib/m4/fstat", FSTAT),
        parse_build("gnulib/m4/sys_stat_h", SYS_STAT_H),
        parse_build("gnulib/m4/extensions", EXTENSIONS),
        parse_build("autoconf/macros/AC_CANONICAL_HOST", CANONICAL_HOST),
        parse_build("autoconf/macros/AC_FUNC_STRERROR_R", STRERROR_R),
    ])


class IndexTest(unittest.TestCase):

    def test_parse_build(self) -> None:
        port = parse_build("gnulib/m4/fstat", FSTAT)
        self.assertEqual(port.m4_file, "fstat.m4")
        self.assertEqual(port.targets, {"gl_FUNC_FSTAT", "fstat"})
        self.assertEqual(port.aggregator, "fstat")
        self.assertEqual(port.label("fstat"), "//gnulib/m4/fstat")
        self.assertEqual(port.label("gl_FUNC_FSTAT"), "//gnulib/m4/fstat:gl_FUNC_FSTAT")
        self.assertTrue(port.url.endswith("/m4/fstat.m4"))

    def test_resolve_by_definition_file(self) -> None:
        index = make_index()
        exact = index.resolve("gl_FUNC_FSTAT", "m4/fstat.m4")
        self.assertEqual((exact.label, exact.reason, exact.exact),
                         ("//gnulib/m4/fstat:gl_FUNC_FSTAT", "target", True))
        # Windows spelling of the location.
        self.assertEqual(index.resolve("gl_FUNC_FSTAT", "m4\\fstat.m4").label, "//gnulib/m4/fstat:gl_FUNC_FSTAT")
        # A macro of a ported file without its own target: the package.
        aggregate = index.resolve("gl_PREREQ_FSTAT", "m4/fstat.m4")
        self.assertEqual((aggregate.label, aggregate.reason, aggregate.exact), ("//gnulib/m4/fstat", "file", False))
        # Macros whose name differs from the file: the file wins.
        self.assertEqual(index.resolve("gl_USE_SYSTEM_EXTENSIONS", "m4/extensions.m4").label,
                         "//gnulib/m4/extensions")

    def test_resolve_by_name(self) -> None:
        index = make_index()
        self.assertEqual(index.resolve("AC_CANONICAL_HOST").label, "//autoconf/macros/AC_CANONICAL_HOST")
        self.assertEqual(index.resolve("AC_CANONICAL_HOST").reason, "macro-package")
        self.assertEqual(index.resolve("HAVE_STRERROR_R").label, "//autoconf/macros/AC_FUNC_STRERROR_R:HAVE_STRERROR_R")
        self.assertEqual(index.resolve("gl_SYS_STAT_H").label, "//gnulib/m4/sys_stat_h:gl_SYS_STAT_H")
        self.assertIsNone(index.resolve("gl_UNKNOWN"))
        self.assertIsNone(index.resolve("gl_UNKNOWN", "m4/unknown.m4"))


if __name__ == "__main__":
    unittest.main()
