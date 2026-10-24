"""Unit tests for the Starlark emitter."""

from __future__ import annotations

import textwrap

from tools.convert import emit
from tools.convert.common import NO_WARNINGS, no_warnings_statement
from tools.convert.ir import Assign, Call, Dep, Each, Loc, Package, Raw, Rule, Target


def test_target_attrs_in_buildifier_order() -> None:
    """Extra attributes of an autoconf target (copts) land in buildifier's order."""
    target = Target("autoconf", checks=[], attrs={"copts": ["-w"]})
    text = emit.render_target(target)
    assert text.splitlines()[1:] == [
        '    name = "autoconf",',
        '    copts = ["-w"],',
        '    visibility = ["//visibility:public"],',
        ")",
    ]


def test_values() -> None:
    assert emit.fmt('a"b', "") == '"a\\"b"'
    assert emit.fmt(1, "") == "1"
    assert emit.fmt(True, "") == "True"
    assert emit.fmt(["x"], "") == '["x"]'
    assert emit.fmt(["x", "y"], "    ") == '[\n        "x",\n        "y",\n    ]'
    assert emit.fmt("int a;\nint b;", "") == '"""\\\nint a;\nint b;"""'
    assert emit.fmt(Call("checks.AC_SUBST", ["X", 1]), "") == 'checks.AC_SUBST("X", 1)'
    assert emit.fmt(Call("checks.AC_TRY_COMPILE", [], {"language": "cpp", "name": "v", "code": "x"}), "") == (
        'checks.AC_TRY_COMPILE(\n    name = "v",\n    code = "x",\n    language = "cpp",\n)'
    )


def test_render_package() -> None:
    target = Target(name="gl_FOO", loc=Loc("m4/foo.m4", 9))
    target.checks = [
        Call("checks.AC_DEFINE", ["HAVE_FOO", 1], comments=["m4/foo.m4:~12: AC_DEFINE([HAVE_FOO])"]),
        Call("macros.AC_CHECK_HEADERS", [["a.h", "b.h"]], returns_list=True, comments=["headers"]),
        Call("checks.AC_SUBST", ["BAR", "x"]),
    ]
    target.add_dep("//gnulib/m4/zed", "AC_REQUIRE([gl_ZED])")
    target.add_dep(":gl_BAR")
    target.add_dep("@other//x")
    package = Package(
        path="m4/foo",
        docstring="https://example.org/foo.m4",
        targets=[
            target,
            Target(name="foo", deps=[Dep(":gl_FOO")]),
        ],
    )
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
    assert emit.render_package(package) == expected


def test_multiline_comments_stay_comments() -> None:
    target = Target(
        name="t",
        checks=[
            Call("checks.AC_DEFINE", ["X"], comments=["a\nb", "first\nsecond\n"]),
        ],
    )
    text = emit.render_target(target)
    for line in text.splitlines():
        assert (
            line.strip() == ""
            or line.lstrip().startswith("#")
            or "(" in line
            or line.strip() in (")", "],", "]")
            or line.lstrip().startswith(("name", "checks", "visibility", "checks."))
        ), line
    assert "        # a\n        # b\n        # first\n        # second\n        #\n" in text


def test_strip_annotations() -> None:
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
    assert "#" not in text.split("\n", 1)[1].replace("//", ""), text


def test_external_loads() -> None:
    package = Package(
        path="",
        docstring="d",
        targets=[
            Target(name="t", checks=[Call("gl_macros.GL_NEXT_HEADERS", [["x.h"]], returns_list=True)]),
        ],
    )
    text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert 'load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")' in text
    assert 'load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")' in text
    assert "checks.bzl" not in text


def test_one_select_per_flag() -> None:
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
    assert text.count("select({") == 3
    assert text.count('"//conditions:default": []') == 3
    assert text.index("os:windows") < text.index(":a_enabled")
    assert text.index(":a_enabled") < text.index(":b_enabled")


def test_conditional_includes_keep_their_order() -> None:
    """buildifier sorts `includes` lists; a preprocessor conditional must not be."""
    target = Target(name="t")
    target.checks.append(
        Call(
            "macros.AC_CHECK_MEMBERS",
            [["struct dirent.d_ino"]],
            {"includes": ["#if HAVE_DIRENT_H", "# include <dirent.h>", "#else", "# include <sys/dir.h>", "#endif"]},
        )
    )
    target.checks.append(Call("checks.AC_CHECK_DECL", ["x"], {"includes": ["#include <a.h>", "#include <b.h>"]}))
    text = emit.render_target(target)
    assert text.count("# do not sort") == 1
    assert text.index("# do not sort") < text.index("#if HAVE_DIRENT_H")


def test_omit_replaced() -> None:
    """A generated target whose name the hand-written BUILD.bazel declares is left out, whatever
    statement kind carried it; the names come back for the report and the macro's docstring,
    and the rest is untouched."""
    package = Package(path="", docstring="")
    package.targets.append(Target("autoconf", checks=[Call("checks.AC_DEFINE", ["X", 1])]))
    package.after.append(Rule("cc_library", "foo", {"srcs": ["foo.c"]}))
    package.after.append(Rule("cc_binary", "bar", {"srcs": ["bar.c"], "deps": [":foo"]}))
    package.after.append(Raw('genrule(\n    name = "gen_h",\n    outs = ["gen.h"],\n    cmd = "touch $@",\n)'))
    hand = textwrap.dedent("""\
        load(":autoconf.bzl", "autoconf_targets")
        load("@rules_cc//cc:cc_library.bzl", "cc_library")

        autoconf_targets()

        cc_library(
            name = "foo",
            srcs = ["foo.c", "foo_extra.c"],
            hdrs = ["foo.h"],
        )

        genrule(
            name = "gen_h",
            srcs = ["gen.h.in"],
            outs = ["gen.h"],
            cmd = "sed s/@V@/1/ $< > $@",
        )
        """)
    assert emit.omit_replaced(package, set(emit.rule_names(hand))) == ["foo", "gen_h"]
    text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert 'name = "foo"' not in text
    assert 'name = "gen_h"' not in text
    assert 'name = "bar"' in text  # untouched, still depends on :foo
    assert emit.rule_names('cc_library(\n    name = "x",\n    srcs = select({"a": []}),\n)\n') == ["x"]
    assert emit.rule_kinds(
        'native.alias(\n    name = "x",\n    actual = ":y",\n)\nstring_flag(name = "f", build_setting_default = "a")\n'
    ) == {
        "x": "alias",
        "f": "string_flag",
    }
    # Not Python (a Starlark-only construct): the column-0 scan still finds the names.
    assert emit.rule_names(
        'x = [1,]\nfoo(\n    name = "a",\n)\nbar(name = "b", deps = [":a"] + glob(["*"]),)\nif True:\n'
    ) == ["a", "b"]


def test_omit_replaced_keeps_no_warnings_behind_copts() -> None:
    """NO_WARNINGS stays when BUILD.bazel replaces every rule that named it but COPTS still reads
    it (bison: BUILD.bazel declares the program, the gnulib library compiles with COPTS), and
    goes when nothing is left that does (gmp)."""
    package = Package(path="", docstring="")
    package.before.append(no_warnings_statement())
    package.before.append(Assign("COPTS", Raw(NO_WARNINGS + ' + ["-DX"]')))
    package.after.append(Rule("cc_library", "lib", {"srcs": ["lib.c"], "copts": Raw("COPTS")}))
    package.after.append(Rule("cc_binary", "prog", {"srcs": ["prog.c"], "copts": Raw(NO_WARNINGS)}))
    assert emit.omit_replaced(package, {"prog"}) == ["prog"]
    text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert "NO_WARNINGS = select(" in text
    assert 'COPTS = NO_WARNINGS + ["-DX"]' in text
    package = Package(path="", docstring="")
    package.before.append(no_warnings_statement())
    package.after.append(Rule("cc_binary", "prog", {"srcs": ["prog.c"], "copts": Raw(NO_WARNINGS)}))
    emit.omit_replaced(package, {"prog"})
    assert "NO_WARNINGS" not in emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    # BUILD.bazel's own program reads it (libpcap): kept at the top level, where it is loadable.
    package = Package(path="", docstring="")
    package.before.append(no_warnings_statement())
    package.after.append(Rule("cc_binary", "prog", {"srcs": ["prog.c"], "copts": Raw(NO_WARNINGS)}))
    hand = (
        'load(":autoconf.bzl", "NO_WARNINGS", "autoconf_targets")\n\nautoconf_targets()\n\n'
        'cc_binary(\n    name = "prog",\n    srcs = ["prog.c"],\n    copts = NO_WARNINGS,\n)\n'
    )
    emit.omit_replaced(package, {"prog"}, hand)
    assert "NO_WARNINGS = select(" in emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    # Any constant: one only the replaced rule read goes, with the one only it read in turn.
    package = Package(path="", docstring="")
    package.before.append(Assign("PROG_HDRS", ["prog.h"]))
    package.before.append(Assign("PROG_SRCS", Raw('["prog.c"] + PROG_HDRS')))
    package.before.append(Assign("LIB_SRCS", ["lib.c"]))
    package.after.append(Rule("cc_library", "lib", {"srcs": Raw("LIB_SRCS")}))
    package.after.append(Rule("cc_binary", "prog", {"srcs": Raw("PROG_SRCS")}))
    assert emit.omit_replaced(package, {"prog"}) == ["prog"]
    text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert "LIB_SRCS = " in text and "PROG_SRCS" not in text and "PROG_HDRS" not in text


def test_each() -> None:
    """A comprehension statement: the rule once over a table, its load() found, comments stripped
    with the rest."""
    package = Package(path="", docstring="")
    package.before.append(Assign("BOOL_OPTIONS", {"nls": True, "dfltcc": False}, comments=["nls: NLS"]))
    package.before.append(
        Each(
            "bool_flag",
            {"name": Raw("name"), "build_setting_default": Raw("default"), "visibility": ["//visibility:public"]},
            "name, default in BOOL_OPTIONS.items()",
            load_from="@bazel_skylib//rules:common_settings.bzl",
        )
    )
    text = emit.render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert 'load("@bazel_skylib//rules:common_settings.bzl", "bool_flag")' in text
    assert (
        '# nls: NLS\nBOOL_OPTIONS = {\n    "dfltcc": False,\n    "nls": True,\n}\n\n[\n    bool_flag(\n'
        "        name = name,\n        build_setting_default = default,\n"
        '        visibility = ["//visibility:public"],\n    )\n    for name, default in BOOL_OPTIONS.items()\n]'
    ) in text
    emit.strip_annotations(package)
    options = package.before[0]
    assert isinstance(options, Assign)
    assert options.comments == []
    assert emit.statement_names(package.before[1]) == []


def test_dict_items_in_buildifier_order() -> None:
    """Dict keys as buildifier's unsorted-dict-items wants them: string order, the default
    condition after the rest, underscore keys last."""
    text = emit.fmt({"zeta": 1, "_under": 1, "//conditions:default": 1, "__dunder": 1, "<lt>": 1, "libc": 1}, "")
    assert [line.split('"')[1] for line in text.splitlines()[1:-1]] == [
        "<lt>",
        "libc",
        "zeta",
        "//conditions:default",
        "__dunder",
        "_under",
    ]


def test_split_loads() -> None:
    loads, rest = emit.split_loads('x = 1\nload("a", "b")\nload(\n  "c",\n  "d",\n)\ny = 2\n')
    assert loads == ['load("a", "b")', 'load(\n  "c",\n  "d",\n)']
    assert rest == "x = 1\ny = 2\n"
