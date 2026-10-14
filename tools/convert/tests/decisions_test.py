"""Tests for the Starlark decisions file: parsing, per-kind shapes, and the in-place skeleton."""

import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.convert import decisions as dec
from tools.convert.ir import Call

FILE = textwrap.dedent('''\
    """Decisions for a test."""

    load("@rules_cc_autoconf//autoconf:checks.bzl", "checks", "macros")

    DECISIONS = {
        # a comment
        "probe:gl_cv_a": "no",
        "probe:gl_cv_b": select({
            "@platforms//os:windows": 0,
            "//conditions:default": 1,
        }),
        "probe:gl_cv_c": checks.AC_TRY_COMPILE(
            includes = ["#include <x.h>"],
            code = """\\
    int main(void) { return 0; }""",
        ),
        "probe:gl_cv_d": [checks.AC_DEFINE("HAVE_D", 1)] + macros.AC_CHECK_FUNCS(["d"]),
        "variable:V": IGNORE,
        "variable:W": ACCEPT_PARTIAL,
        "guard:t:checks.AC_DEFINE(X)": ["gl_cv_a", "gl_cv_b"],
        "macro:gl_FOO": "@rules_cc_autoconf//gnulib/m4/foo",
        "flag:gcc_warnings": False,
        "src_condition:GL_X": "REPLACE_X==1",
        "am_conditional:AMC": ":dfltcc_enabled",
        "probe:gl_cv_later": UNDECIDED,
    }
''')


def request(key: str, kind: str) -> dec.Request:
    return dec.Request(key, kind, "t (m4/t.m4:1)", f"summary of {key}", {"program": "int x;\nint y;"})


class ParseTest(unittest.TestCase):

    def setUp(self) -> None:
        self.decisions = dec.Decisions.from_text(FILE, "test.bzl")

    def test_values(self) -> None:
        resolve = lambda key, kind: self.decisions.resolve(request(key, kind))  # noqa: E731
        self.assertEqual(resolve("probe:gl_cv_a", "probe"), {"value": "no"})
        self.assertEqual(resolve("probe:gl_cv_b", "probe"),
                         {"values": {"@platforms//os:windows": 0, "//conditions:default": 1}})
        probe = resolve("probe:gl_cv_c", "probe")["try_compile"]
        self.assertIsInstance(probe, Call)
        self.assertEqual(probe.fn, "checks.AC_TRY_COMPILE")
        # The probe sets the cache variable it replaces.
        self.assertEqual(probe.kwargs["name"], "gl_cv_c")
        self.assertEqual(probe.kwargs["code"], "int main(void) { return 0; }")
        calls = resolve("probe:gl_cv_d", "probe")["checks"]
        self.assertEqual([c.fn for c in calls], ["checks.AC_DEFINE", "macros.AC_CHECK_FUNCS"])
        self.assertTrue(calls[1].returns_list)
        self.assertEqual(resolve("variable:V", "variable"), {"ignore": True})
        self.assertEqual(resolve("variable:W", "variable"), {"accept_partial": True})
        self.assertEqual(resolve("guard:t:checks.AC_DEFINE(X)", "guard"), {"requires": ["gl_cv_a", "gl_cv_b"]})
        self.assertEqual(resolve("macro:gl_FOO", "macro"), {"dep": "@rules_cc_autoconf//gnulib/m4/foo"})
        self.assertEqual(self.decisions.optional("flag:gcc_warnings", "flag"), {"default": False})
        self.assertEqual(resolve("src_condition:GL_X", "src_condition"), {"condition": "REPLACE_X==1"})
        self.assertEqual(resolve("am_conditional:AMC", "am_conditional"), {"select": ":dfltcc_enabled"})
        self.assertEqual(self.decisions.pending, {})
        self.assertEqual(self.decisions.unused, ["probe:gl_cv_later"])

    def test_project_kinds(self) -> None:
        """generated / cppflag / link: the Makefile.am constructs the converter hands over."""
        decisions = dec.Decisions.from_text('''DECISIONS = {
    "generated:src/gen.c": genrule(
        name = "gen_c",
        srcs = ["src/gen.sh"],
        outs = ["src/gen.c"],
        cmd = "$(location src/gen.sh) > $@",
    ),
    "generated:src/tab.c": ['load("@bazel_skylib//rules:run_binary.bzl", "run_binary")', run_binary(name = "tab")],
    "generated:src/skip.c": IGNORE,
    "cppflag:.:SHLIBEXT": "\\"so\\"",
    "link:src/foo:../lib/libbar.a": ":lib/bar",
    "link:src/foo:$(top_builddir)/x.a": "-lx",
}''')
        resolve = lambda key, kind: decisions.resolve(dec.Request(key, kind, "", ""))  # noqa: E731
        gen = resolve("generated:src/gen.c", "generated")
        self.assertEqual(gen["loads"], [])
        self.assertTrue(gen["rule"].startswith("genrule(\n") and gen["rule"].endswith(")"))
        tab = resolve("generated:src/tab.c", "generated")
        self.assertEqual(tab, {"rule": 'run_binary(name = "tab")',
                               "loads": ['load("@bazel_skylib//rules:run_binary.bzl", "run_binary")']})
        self.assertEqual(resolve("generated:src/skip.c", "generated"), {"ignore": True})
        self.assertEqual(resolve("cppflag:.:SHLIBEXT", "cppflag"), {"value": '"so"'})
        self.assertEqual(resolve("link:src/foo:../lib/libbar.a", "link"), {"dep": ":lib/bar"})
        self.assertEqual(resolve("link:src/foo:$(top_builddir)/x.a", "link"), {"linkopt": "-lx"})
        with self.assertRaises(dec.DecisionError):
            dec.Decisions.from_text('DECISIONS = {"cppflag:.:X": 1}').resolve(dec.Request("cppflag:.:X", "cppflag", "", ""))
        with self.assertRaises(dec.DecisionError):
            dec.Decisions.from_text('DECISIONS = {"link:a/b:c": "c"}').resolve(dec.Request("link:a/b:c", "link", "", ""))

    def test_undecided_and_missing_are_pending(self) -> None:
        self.assertIsNone(self.decisions.resolve(request("probe:gl_cv_later", "probe")))
        self.assertIsNone(self.decisions.resolve(request("probe:gl_cv_new", "probe")))
        self.assertEqual(sorted(self.decisions.pending), ["probe:gl_cv_later", "probe:gl_cv_new"])
        # UNDECIDED entries are in the file already: only the new key is missing from it.
        self.assertEqual(self.decisions.missing, ["probe:gl_cv_new"])
        self.assertNotIn("probe:gl_cv_later", self.decisions.unused)

    def test_wrong_shapes(self) -> None:
        with self.assertRaises(dec.DecisionError):
            self.decisions.resolve(request("probe:gl_cv_a", "flag"))  # a string is not a flag default
        with self.assertRaises(dec.DecisionError):
            self.decisions.resolve(request("variable:V", "guard"))  # IGNORE is not a guard decision
        wrong_name = dec.Decisions.from_text(
            'DECISIONS = {"probe:a": checks.AC_TRY_LINK(name = "b", code = "x")}\n')
        with self.assertRaises(dec.DecisionError):
            wrong_name.resolve(request("probe:a", "probe"))

    def test_rejected_syntax(self) -> None:
        for text in ('DECISIONS = {"k": foo.bar()}\n', 'DECISIONS = {"k": None}\n', 'x = 1\n',
                     'DECISIONS = {"k": SOMETHING}\n', 'DECISIONS = {"k": 1, "k": 2}\n'):
            with self.assertRaises(dec.DecisionError, msg=text):
                dec.Decisions.from_text(text)


class SkeletonTest(unittest.TestCase):

    def test_create_then_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.bzl"
            first = dec.Decisions.load(path)
            self.assertIsNone(first.resolve(request("probe:gl_cv_x", "probe")))
            self.assertIsNone(first.resolve(request("variable:Y", "variable")))
            self.assertEqual(first.write_needed(path, "a test"), ["probe:gl_cv_x", "variable:Y"])
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith('"""Decisions for a test:'))
            self.assertIn('    "probe:gl_cv_x": UNDECIDED,\n', text)
            self.assertIn("    # program:\n    #   int x;\n    #   int y;\n", text)
            # The user decides one key and hand-formats the file; a later run adds the new key only.
            edited = text.replace('"probe:gl_cv_x": UNDECIDED,', '"probe:gl_cv_x": "no",  # decided')
            path.write_text(edited, encoding="utf-8")
            second = dec.Decisions.load(path)
            self.assertEqual(second.resolve(request("probe:gl_cv_x", "probe")), {"value": "no"})
            self.assertIsNone(second.resolve(request("variable:Y", "variable")))
            self.assertIsNone(second.resolve(request("macro:gl_Z", "macro")))
            self.assertEqual(second.write_needed(path), ["macro:gl_Z"])
            final = path.read_text(encoding="utf-8")
            self.assertTrue(final.startswith(edited.rstrip("}\n")), "existing text is kept")
            self.assertTrue(final.rstrip().endswith('    "macro:gl_Z": UNDECIDED,\n}'), final[-200:])
            self.assertEqual(final.count("UNDECIDED,"), 2)
            third = dec.Decisions.load(path)
            self.assertEqual(sorted(third.data), ["macro:gl_Z", "probe:gl_cv_x", "variable:Y"])

    def test_append_to_empty_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decisions.bzl"
            path.write_text('"""Doc."""\n\nDECISIONS = {}\n', encoding="utf-8")
            decisions = dec.Decisions.load(path)
            decisions.resolve(request("flag:x", "flag"))
            self.assertEqual(decisions.write_needed(path), ["flag:x"])
            text = path.read_text(encoding="utf-8")
            self.assertIn("DECISIONS = {\n", text)
            self.assertTrue(text.rstrip().endswith('    "flag:x": UNDECIDED,\n}'), text)
            dec.Decisions.load(path)  # still parses


if __name__ == "__main__":
    unittest.main()
