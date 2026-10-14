"""Unit tests for vendoring the hand ports into a converted project."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.convert import vendor
from tools.convert.index import PortIndex
from tools.convert.ir import Assign, Call, Package, Raw, Rule, Target
from tools.convert.translate import ProjectState

FOO = '''"""https://example.invalid/foo.m4"""

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")

autoconf(
    name = "foo",
    checks = [
        checks.AC_CHECK_FUNC(
            "foo",
            define = "HAVE_FOO",
        ),
    ],
    visibility = ["//visibility:public"],
    deps = [
        "//gnulib/m4/bar:gl_BAR_H",
        "//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT",
    ],
)
'''

BAR = '''load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")
load("//gnulib:macros.bzl", gl_macros = "macros")

autoconf(
    name = "gl_BAR_H",
    checks = [
        checks.AC_SUBST("HAVE_BAR", 1),
    ],
    visibility = ["//gnulib:__subpackages__"],
)

autoconf(
    name = "gl_BAR_H_DEFAULTS",
    checks = [
        checks.AC_SUBST("REPLACE_BAR", 0),
        checks.M4_VARIABLE("GL_GENERATE_BAR_H", "false"),
    ],
    visibility = ["//visibility:private"],
)
'''

BAZ_DEFAULTS = '''load("//autoconf:autoconf_defaults.bzl", "autoconf_defaults")

autoconf_defaults(
    name = "gl_BAZ_H_DEFAULTS",
    substs = {
        "REPLACE_BAZ": "0",
    },
    variables = {
        "GL_GENERATE_BAZ_H": "true",
    },
    visibility = ["//visibility:private"],
)

autoconf_defaults(
    name = "defaults",
    visibility = ["//gnulib:__subpackages__"],
    deps = [":gl_BAZ_H_DEFAULTS"],
)
'''

TOOLCHAIN = '''autoconf_toolchain(
    name = "gnulib_toolchain",
    cache_deps = [
        "//gnulib/m4/foo",
    ],
)
'''

INCLUDES = '''load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")

autoconf(
    name = "AC_CHECK_INCLUDES_DEFAULT",
    checks = [checks.AC_CHECK_HEADER("stdlib.h", define = "HAVE_STDLIB_H")],
    visibility = ["//visibility:public"],
)
'''


class VendorTest(unittest.TestCase):

    def test_vendoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pkg, text in (("gnulib/m4/foo", FOO), ("gnulib/m4/bar", BAR), ("gnulib/m4/baz", BAZ_DEFAULTS),
                              ("autoconf/macros/AC_CHECK_INCLUDES_DEFAULT", INCLUDES)):
                (root / pkg).mkdir(parents=True)
                (root / pkg / "BUILD.bazel").write_text(text)
            (root / "gnulib" / "macros").mkdir()
            (root / "gnulib" / "toolchain").mkdir()
            (root / "gnulib" / "toolchain" / "BUILD.bazel").write_text(TOOLCHAIN)
            (root / "autoconf" / "macros" / "AC_C_INLINE").mkdir()
            (root / "autoconf" / "macros" / "AC_C_INLINE" / "BUILD.bazel").write_text(INCLUDES)
            template = root / "stdbaz.in.h"
            template.write_text("#if @REPLACE_BAZ@ && @GL_GENERATE_BAZ_H@ && @HAVE_BAR@ && @NOBODY_SETS_THIS@\n")
            index = PortIndex.from_workspace(root)

            main = Target(name="autoconf", checks=[Call("checks.AC_DEFINE", ["X", 1])])
            main.add_dep("@rules_cc_autoconf//gnulib/m4/foo")
            main.add_dep("@rules_cc_autoconf//autoconf/macros/AC_C_INLINE")
            main.add_dep(":package")
            main.add_default_dep("@rules_cc_autoconf//gnulib/m4/baz:defaults")
            package = Package(path="", docstring="")
            package.targets.append(main)
            package.before.append(Assign("GNULIB_M4_DEPS", ["@rules_cc_autoconf//gnulib/m4/foo",
                                                            "@rules_cc_autoconf//gnulib/m4/bar:gl_BAR_H"]))
            package.after.append(Raw("autoconf_hdr(\n        name = \"x\",\n    )"))
            package.after.append(Rule("autoconf_srcs", "cond", {"srcs": {"a.c": "REPLACE_BAZ==1"},
                                                                 "deps": [":autoconf"]}))
            state = ProjectState()
            state.templates = [template]
            state.src_condition_exprs = {"GL_COND_OBJ_A": "REPLACE_BAZ==1"}

            vendored = vendor.vendor_ports(package, state, index, root, "1.2.3")

            # The closure: foo, bar (through foo), AC_CHECK_INCLUDES_DEFAULT (through foo), baz (the
            # defaults, through default_deps); not AC_C_INLINE, a rule implementation.
            self.assertEqual(vendored.packages, ["autoconf/macros/AC_CHECK_INCLUDES_DEFAULT", "gnulib/m4/bar",
                                                 "gnulib/m4/baz", "gnulib/m4/foo"])
            baz = vendored.files["third_party/rules_cc_autoconf/gnulib/m4/baz/BUILD.bazel"]
            self.assertIn('load("@rules_cc_autoconf//autoconf:autoconf_defaults.bzl", "autoconf_defaults")', baz)
            self.assertIn('visibility = ["//visibility:public"]', baz)
            self.assertEqual([d.label for d in main.default_deps], ["//third_party/rules_cc_autoconf/gnulib/m4/baz:defaults"])
            foo = vendored.files["third_party/rules_cc_autoconf/gnulib/m4/foo/BUILD.bazel"]
            self.assertIn('load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")', foo)
            self.assertIn('load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")', foo)
            self.assertIn('"//third_party/rules_cc_autoconf/gnulib/m4/bar:gl_BAR_H"', foo)
            self.assertIn('"//third_party/rules_cc_autoconf/autoconf/macros/AC_CHECK_INCLUDES_DEFAULT"', foo)
            self.assertTrue(foo.startswith("# @generated: vendored from rules_cc_autoconf 1.2.3"))
            bar = vendored.files["third_party/rules_cc_autoconf/gnulib/m4/bar/BUILD.bazel"]
            self.assertIn('visibility = ["//visibility:public"]', bar)
            self.assertNotIn("__subpackages__", bar)
            self.assertIn('load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")', bar)
            # The project's references point at the copies; the rule-backed port stays external.
            self.assertEqual([d.label for d in main.deps],
                             ["//third_party/rules_cc_autoconf/gnulib/m4/foo",
                              "@rules_cc_autoconf//autoconf/macros/AC_C_INLINE", ":package"])
            self.assertEqual(package.before[0].value, ["//third_party/rules_cc_autoconf/gnulib/m4/foo",
                                                       "//third_party/rules_cc_autoconf/gnulib/m4/bar:gl_BAR_H"])
            self.assertTrue(any("AC_C_INLINE" in n for n in vendored.notes), vendored.notes)
            self.assertFalse(any("defaults = True" in n for n in vendored.notes), vendored.notes)

    def test_module_deps_for_vendored_loads(self) -> None:
        module = 'module(name = "x")\nbazel_dep(name = "rules_cc", version = "0.2.9")\nbazel_dep(name = "platforms", version = "1.0.0")\n'
        files = {"a/BUILD.bazel": 'load("@bazel_skylib//lib:selects.bzl", "selects")\n'}
        out = vendor.ensure_module_deps(module, files)
        self.assertIn('bazel_dep(name = "bazel_skylib", version = "1.8.2")\nbazel_dep(name = "platforms"', out)
        self.assertEqual(vendor.ensure_module_deps(out, files), out)  # idempotent
        self.assertEqual(vendor.ensure_module_deps(module, {"a/BUILD.bazel": "autoconf()"}), module)
