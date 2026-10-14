"""Unit tests for the Starlark emitter."""

from __future__ import annotations

import textwrap
import unittest

from tools.convert import emit
from tools.convert.ir import Call, Dep, Loc, Package, Raw, Target


class EmitTest(unittest.TestCase):

    def test_target_attrs_in_buildifier_order(self) -> None:
        """Extra attributes of an autoconf target (copts) land in buildifier's order."""
        target = Target("autoconf", checks=[], attrs={"copts": ["-w"]})
        text = emit.render_target(target)
        self.assertEqual(text.splitlines()[1:], ['    name = "autoconf",', '    copts = ["-w"],',
                                                 '    visibility = ["//visibility:public"],', ")"])

    def test_values(self) -> None:
        self.assertEqual(emit.fmt("a\"b", ""), '"a\\"b"')
        self.assertEqual(emit.fmt(1, ""), "1")
        self.assertEqual(emit.fmt(True, ""), "True")
        self.assertEqual(emit.fmt(["x"], ""), '["x"]')
        self.assertEqual(emit.fmt(["x", "y"], "    "), '[\n        "x",\n        "y",\n    ]')
        self.assertEqual(emit.fmt("int a;\nint b;", ""), '"""\\\nint a;\nint b;"""')
        self.assertEqual(emit.fmt(Call("checks.AC_SUBST", ["X", 1]), ""), 'checks.AC_SUBST("X", 1)')
        self.assertEqual(
            emit.fmt(Call("checks.AC_TRY_COMPILE", [], {"language": "cpp", "name": "v", "code": "x"}), ""),
            'checks.AC_TRY_COMPILE(\n    name = "v",\n    code = "x",\n    language = "cpp",\n)',
        )

    def test_render_package(self) -> None:
        target = Target(name="gl_FOO", loc=Loc("m4/foo.m4", 9))
        target.checks = [
            Call("checks.AC_DEFINE", ["HAVE_FOO", 1], comments=["m4/foo.m4:~12: AC_DEFINE([HAVE_FOO])"]),
            Call("macros.AC_CHECK_HEADERS", [["a.h", "b.h"]], returns_list=True, comments=["headers"]),
            Call("checks.AC_SUBST", ["BAR", "x"]),
        ]
        target.add_dep("//gnulib/m4/zed", "AC_REQUIRE([gl_ZED])")
        target.add_dep(":gl_BAR")
        target.add_dep("@other//x")
        package = Package(path="m4/foo", docstring="https://example.org/foo.m4", targets=[
            target,
            Target(name="foo", deps=[Dep(":gl_FOO")]),
        ])
        expected = textwrap.dedent('''\
            """https://example.org/foo.m4"""

            load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
            load("//autoconf:checks.bzl", "checks", "macros")

            # m4/foo.m4:9
            autoconf(
                name = "gl_FOO",
                checks = [
                    # m4/foo.m4:~12: AC_DEFINE([HAVE_FOO])
                    checks.AC_DEFINE("HAVE_FOO", 1),
                ] + macros.AC_CHECK_HEADERS(
                    # headers
                    [
                        "a.h",
                        "b.h",
                    ],
                ) + [
                    checks.AC_SUBST("BAR", "x"),
                ],
                visibility = ["//visibility:public"],
                deps = [
                    ":gl_BAR",
                    "//gnulib/m4/zed",  # AC_REQUIRE([gl_ZED])
                    "@other//x",
                ],
            )

            autoconf(
                name = "foo",
                visibility = ["//visibility:public"],
                deps = [":gl_FOO"],
            )
        ''')
        self.assertEqual(emit.render_package(package), expected)

    def test_multiline_comments_stay_comments(self) -> None:
        target = Target(name="t", checks=[
            Call("checks.AC_DEFINE", ["X"], comments=["a\nb", "first\nsecond\n"]),
        ])
        text = emit.render_target(target)
        for line in text.splitlines():
            self.assertTrue(line.strip() == "" or line.lstrip().startswith("#") or "(" in line or line.strip() in (")", "],", "]")
                            or line.lstrip().startswith(("name", "checks", "visibility", "checks.")), line)
        self.assertIn("        # a\n        # b\n        # first\n        # second\n        #\n", text)

    def test_strip_annotations(self) -> None:
        target = Target(name="gl_FOO", loc=Loc("m4/foo.m4", 9), comments=["AC_DEFUN([gl_FOO])"])
        target.checks = [
            Call("checks.AC_DEFINE", ["HAVE_FOO", 1], comments=["m4/foo.m4:~12: AC_DEFINE([HAVE_FOO])"]),
            Call("checks.AC_TRY_COMPILE", [], {"code": Call("utils.AC_LANG_PROGRAM", [[], []], comments=["x"])}),
        ]
        target.arm("@platforms//os:windows").append(Call("checks.AC_DEFINE", ["W"], comments=["w"]))
        target.add_dep("//gnulib/m4/zed", "AC_REQUIRE([gl_ZED])")
        package = Package(path="", docstring="d", targets=[target])
        emit.strip_annotations(package)
        text = emit.render_package(package)
        self.assertNotIn("#", text.split("\n", 1)[1].replace("//", ""), text)

    def test_external_loads(self) -> None:
        package = Package(path="", docstring="d", targets=[
            Target(name="t", checks=[Call("gl_macros.GL_NEXT_HEADERS", [["x.h"]], returns_list=True)]),
        ])
        text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
        self.assertIn('load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")', text)
        self.assertIn('load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")', text)
        self.assertNotIn("checks.bzl", text)


    def test_one_select_per_flag(self) -> None:
        """Host OS arms share a select(); each configure option gets its own,
        with its own default arm, so two enabled options never match together."""
        target = Target(name="t")
        target.arm("@platforms//os:windows").append(Call("checks.AC_DEFINE", ["W", 1]))
        target.arm("//conditions:default")
        target.arm(":a_enabled", ":a_enabled").append(Call("checks.AC_DEFINE", ["A", 1]))
        target.arm("//conditions:default", ":a_enabled")
        target.arm(":b_enabled", ":b_enabled").append(Call("checks.AC_DEFINE", ["B", 1]))
        target.arm("//conditions:default", ":b_enabled")
        text = emit.render_target(target)
        self.assertEqual(text.count("select({"), 3)
        self.assertEqual(text.count('"//conditions:default": []'), 3)
        self.assertLess(text.index("os:windows"), text.index(":a_enabled"))
        self.assertLess(text.index(":a_enabled"), text.index(":b_enabled"))

    def test_conditional_includes_keep_their_order(self) -> None:
        """buildifier sorts `includes` lists; a preprocessor conditional must not be."""
        target = Target(name="t")
        target.checks.append(Call("macros.AC_CHECK_MEMBERS", [["struct dirent.d_ino"]], {
            "includes": ["#if HAVE_DIRENT_H", "# include <dirent.h>", "#else", "# include <sys/dir.h>", "#endif"]}))
        target.checks.append(Call("checks.AC_CHECK_DECL", ["x"], {"includes": ["#include <a.h>", "#include <b.h>"]}))
        text = emit.render_target(target)
        self.assertEqual(text.count("# do not sort"), 1)
        self.assertLess(text.index("# do not sort"), text.index("#if HAVE_DIRENT_H"))

    def test_append_fragment(self) -> None:
        """`--append`: the fragment's load()s join the generated ones at the top, the rest is
        appended as given after the generated statements."""
        package = Package(path="", docstring="")
        package.targets.append(Target("autoconf", checks=[Call("checks.AC_DEFINE", ["X", 1])]))
        fragment = textwrap.dedent("""\
            load("@rules_shell//shell:sh_test.bzl", "sh_test")
            load(
                "@bazel_skylib//rules:diff_test.bzl",
                "diff_test",
            )

            # The smoke test.
            sh_test(
                name = "smoke",
                srcs = ["smoke.sh"],
                data = [":gzip"],
            )
            """)
        emit.append_fragment(package, fragment, "tests.BUILD")
        text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
        lines = text.splitlines()
        loads = [i for i, line in enumerate(lines) if line.startswith("load(")]
        first_rule = next(i for i, line in enumerate(lines) if line.startswith("autoconf("))
        self.assertTrue(all(i < first_rule for i in loads), text)
        self.assertIn('load("@rules_shell//shell:sh_test.bzl", "sh_test")', text)
        self.assertIn('load(\n    "@bazel_skylib//rules:diff_test.bzl",\n    "diff_test",\n)', text)
        self.assertEqual(text.count("load("), 4)  # autoconf, checks, and the two of the fragment
        self.assertLess(text.index("autoconf("), text.index("# -- tests.BUILD: appended as given --"))
        self.assertLess(text.index("# -- tests.BUILD"), text.index("# The smoke test.\nsh_test("))
        self.assertTrue(text.rstrip().endswith(")"))

    def test_split_loads(self) -> None:
        loads, rest = emit.split_loads('x = 1\nload("a", "b")\nload(\n  "c",\n  "d",\n)\ny = 2\n')
        self.assertEqual(loads, ['load("a", "b")', 'load(\n  "c",\n  "d",\n)'])
        self.assertEqual(rest, "x = 1\ny = 2\n")


if __name__ == "__main__":
    unittest.main()
