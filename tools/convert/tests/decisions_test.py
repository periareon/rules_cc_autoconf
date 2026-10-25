"""Tests for the Starlark decisions file: parsing, per-kind shapes, and the in-place skeleton."""

from __future__ import annotations

import dataclasses
import re
import textwrap
from pathlib import Path

import pytest

from tools.convert import decisions as dec
from tools.convert.tests.helpers import must

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


def shape(decision: dec.Decision) -> set[str]:
    """The fields the decision sets besides its kind and key: the one shape a value has."""
    return {
        field.name
        for field in dataclasses.fields(decision)
        if field.name not in ("kind", "key") and getattr(decision, field.name) is not None
    }


@pytest.fixture
def decisions() -> dec.Decisions:
    """FILE parsed afresh for each test: resolving records pending and unused keys."""
    return dec.Decisions.from_text(FILE, "test.bzl")


# ---- Parsing and the per-kind shapes


def test_values(decisions: dec.Decisions) -> None:
    def resolve(key: str, kind: str) -> dec.Decision:
        decision = must(decisions.resolve(request(key, kind)))
        assert (decision.kind, decision.key) == (kind, key)
        return decision

    a = resolve("probe:gl_cv_a", "probe")
    assert (shape(a), a.value) == ({"value"}, "no")
    b = resolve("probe:gl_cv_b", "probe")
    assert (shape(b), b.values) == ({"values"}, {"@platforms//os:windows": 0, "//conditions:default": 1})
    c = resolve("probe:gl_cv_c", "probe")
    assert shape(c) == {"try_compile"}
    probe = must(c.try_compile)
    assert probe.fn == "checks.AC_TRY_COMPILE"
    # The probe sets the cache variable it replaces.
    assert probe.kwargs["name"] == "gl_cv_c"
    assert probe.kwargs["code"] == "int main(void) { return 0; }"
    d = resolve("probe:gl_cv_d", "probe")
    assert shape(d) == {"checks"}
    calls = must(d.checks)
    assert [c.fn for c in calls] == ["checks.AC_DEFINE", "macros.AC_CHECK_FUNCS"]
    assert calls[1].returns_list
    v = resolve("variable:V", "variable")
    assert (shape(v), v.ignore, v.accept_partial) == ({"word"}, True, False)
    w = resolve("variable:W", "variable")
    assert (shape(w), w.accept_partial, w.ignore) == ({"word"}, True, False)
    guard = resolve("guard:t:checks.AC_DEFINE(X)", "guard")
    assert (shape(guard), guard.requires) == ({"requires"}, ["gl_cv_a", "gl_cv_b"])
    macro = resolve("macro:gl_FOO", "macro")
    assert (shape(macro), macro.dep) == ({"dep"}, "@rules_cc_autoconf//gnulib/m4/foo")
    flag = must(decisions.optional("flag:gcc_warnings", "flag"))
    assert (shape(flag), flag.default) == ({"default"}, False)
    condition = resolve("src_condition:GL_X", "src_condition")
    assert (shape(condition), condition.condition) == ({"condition"}, "REPLACE_X==1")
    arm = resolve("am_conditional:AMC", "am_conditional")
    assert (shape(arm), arm.arm) == ({"arm"}, ":dfltcc_enabled")
    assert decisions.pending == {}
    assert decisions.unused == ["probe:gl_cv_later"]


def test_guard_select_arms() -> None:
    """A guard's `select()`: a requires list per arm, `[]` for an ALWAYS arm, `"never"` for a NEVER arm."""
    decisions = dec.Decisions.from_text(
        'DECISIONS = {"guard:t:checks.AC_DEFINE(X)": select({"@platforms//os:windows": decision.NEVER,'
        ' "@platforms//os:macos": decision.ALWAYS, "//conditions:default": ["gl_cv_a"]})}'
    )
    guard = must(decisions.resolve(dec.Request("guard:t:checks.AC_DEFINE(X)", "guard", "", "")))
    assert shape(guard) == {"arms"}
    assert guard.arms == {
        "@platforms//os:windows": "never",
        "@platforms//os:macos": [],
        "//conditions:default": ["gl_cv_a"],
    }


def test_define_values_pin_config_header_lines() -> None:
    """`define:<NAME>` with a value or a select() of values (never IGNORE's only form any more)."""
    decisions = dec.Decisions.from_text(
        'DECISIONS = {"define:NL_LANGINFO_MTSAFE": select({"@platforms//os:macos": 1}), "define:X": 2,'
        ' "define:Y": decision.IGNORE}'
    )
    pinned = must(decisions.optional("define:NL_LANGINFO_MTSAFE", "define"))
    assert (shape(pinned), pinned.values) == ({"values"}, {"@platforms//os:macos": 1})
    x = must(decisions.optional("define:X", "define"))
    assert (shape(x), x.value) == ({"value"}, 2)
    y = must(decisions.optional("define:Y", "define"))
    assert (shape(y), y.ignore) == ({"word"}, True)


def test_includes_spelled_as_directives() -> None:
    """`includes = ["dirent.h"]` on a decision's check becomes an `#include` line, as the
    converter's own checks spell it; a directive is left alone."""
    decisions = dec.Decisions.from_text("""DECISIONS = {
    "macro:AC_STRUCT_DIRENT_D_TYPE": [checks.AC_CHECK_MEMBER("struct dirent.d_type", includes = ["dirent.h", "#include <sys/types.h>"])],
    "probe:my_cv_x": checks.AC_TRY_COMPILE(name = "my_cv_x", code = "int main(void) { return 0; }", includes = "<sys/mtio.h>"),
}""")
    macro = must(decisions.resolve(dec.Request("macro:AC_STRUCT_DIRENT_D_TYPE", "macro", "", "")))
    assert must(macro.checks)[0].kwargs["includes"] == ["#include <dirent.h>", "#include <sys/types.h>"]
    probe = must(decisions.resolve(dec.Request("probe:my_cv_x", "probe", "", "")))
    assert must(probe.try_compile).kwargs["includes"] == "#include <sys/mtio.h>"


def test_defaults_by_kind() -> None:
    """`DEFAULTS` answers every undecided key of a kind; an entry overrides it; the answered
    keys are reported apart and never pending; a bad default is an error at load."""
    decisions = dec.Decisions.from_text("""DEFAULTS = {
    "probe": decision.IGNORE,
}

DECISIONS = {
    "probe:my_cv_kept": decision.ACCEPT,
    "probe:my_cv_open": decision.UNDECIDED,
}""")

    def resolve(key: str, kind: str) -> dec.Decision | None:
        return decisions.resolve(dec.Request(key, kind, "configure.ac:1", "why"))

    assert must(resolve("probe:my_cv_kept", "probe")).accept
    assert must(resolve("probe:my_cv_open", "probe")).ignore
    assert must(resolve("probe:my_cv_new", "probe")).ignore
    assert resolve("variable:X", "variable") is None
    assert sorted(decisions.defaulted) == ["probe:my_cv_new", "probe:my_cv_open"]
    assert sorted(decisions.pending) == ["variable:X"]
    assert decisions.missing == ["variable:X"]
    assert decisions.unused == []
    assert "-> `decision.IGNORE`" in decisions.defaulted_lines()[0]
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text('DEFAULTS = {"guard": decision.IGNORE}\nDECISIONS = {}')
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text('DEFAULTS = {"nonsense": decision.IGNORE}\nDECISIONS = {}')


def test_project_kinds() -> None:
    """generated / cppflag / link: the Makefile.am constructs the converter hands over."""
    decisions = dec.Decisions.from_text("""DECISIONS = {
    "generated:src/gen.c": genrule(
        name = "gen_c",
        srcs = ["src/gen.sh"],
        outs = ["src/gen.c"],
        cmd = "$(execpath src/gen.sh) > $@",
    ),
    "generated:src/tab.c": ['load("@bazel_skylib//rules:run_binary.bzl", "run_binary")', run_binary(name = "tab")],
    "generated:src/skip.c": IGNORE,
    "generated:src/keys.h": ":keys_h",
    "cppflag:.:SHLIBEXT": "\\"so\\"",
    "link:src/foo:../lib/libbar.a": ":lib/bar",
    "link:src/foo:$(top_builddir)/x.a": "-lx",
    "license:COPYING": "GPL-3.0-or-later",
    "license:LICENSE": IGNORE,
}""")
    copying = must(decisions.optional("license:COPYING", "license"))
    assert (shape(copying), copying.spdx) == ({"spdx"}, "GPL-3.0-or-later")
    assert must(decisions.optional("license:LICENSE", "license")).ignore

    def resolve(key: str, kind: str) -> dec.Decision:
        return must(decisions.resolve(dec.Request(key, kind, "", "")))

    gen = resolve("generated:src/gen.c", "generated")
    assert (shape(gen), gen.loads) == ({"rule", "loads"}, [])
    assert must(gen.rule).startswith("genrule(\n") and must(gen.rule).endswith(")")
    tab = resolve("generated:src/tab.c", "generated")
    assert (shape(tab), tab.rule, tab.loads) == (
        {"rule", "loads"},
        'run_binary(name = "tab")',
        ['load("@bazel_skylib//rules:run_binary.bzl", "run_binary")'],
    )
    assert resolve("generated:src/skip.c", "generated").ignore
    keys = resolve("generated:src/keys.h", "generated")
    assert (shape(keys), keys.label) == ({"label"}, ":keys_h")
    cppflag = resolve("cppflag:.:SHLIBEXT", "cppflag")
    assert (shape(cppflag), cppflag.value) == ({"value"}, '"so"')
    bar = resolve("link:src/foo:../lib/libbar.a", "link")
    assert (shape(bar), bar.dep) == ({"dep"}, ":lib/bar")
    x = resolve("link:src/foo:$(top_builddir)/x.a", "link")
    assert (shape(x), x.linkopt) == ({"linkopt"}, "-lx")
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text('DECISIONS = {"cppflag:.:X": 1}').resolve(dec.Request("cppflag:.:X", "cppflag", "", ""))
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text('DECISIONS = {"link:a/b:c": "c"}').resolve(dec.Request("link:a/b:c", "link", "", ""))


def test_program_kind() -> None:
    """`program:` (never requested): ALWAYS, NEVER or the platforms the program builds on."""
    decisions = dec.Decisions.from_text("""DECISIONS = {
    "program:hostid": ["@platforms//os:linux", "@platforms//os:macos"],
    "program:stdbuf": decision.ALWAYS,
    "program:arch": decision.NEVER,
}""")
    hostid = must(decisions.optional("program:hostid", "program"))
    assert (shape(hostid), hostid.platforms) == ({"platforms"}, ["@platforms//os:linux", "@platforms//os:macos"])
    assert must(decisions.optional("program:stdbuf", "program")).always
    assert must(decisions.optional("program:arch", "program")).never
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text('DECISIONS = {"program:x": ["linux"]}').optional("program:x", "program")


def test_subdir_kind() -> None:
    """`subdir:`: ALWAYS, NEVER (not converted), the labels the sub-configure's package builds under
    (platform constraints or config_settings), or one `!a,b` entry for everywhere but those."""
    decisions = dec.Decisions.from_text("""DECISIONS = {
    "subdir:extension": ["!@rules_cc//cc/compiler:msvc-cl,//:extensions_off"],
    "subdir:libs/zlib": ["@platforms//os:linux", ":bundled_zlib"],
    "subdir:doc": decision.NEVER,
    "subdir:tests": decision.ALWAYS,
}""")
    extension = must(decisions.optional("subdir:extension", "subdir"))
    assert (shape(extension), extension.platforms) == (
        {"platforms"},
        ["!@rules_cc//cc/compiler:msvc-cl,//:extensions_off"],
    )
    assert must(decisions.optional("subdir:libs/zlib", "subdir")).platforms == ["@platforms//os:linux", ":bundled_zlib"]
    assert must(decisions.optional("subdir:doc", "subdir")).never
    assert must(decisions.optional("subdir:tests", "subdir")).always
    for bad in ('["linux"]', '["!a", "@platforms//os:linux"]', "[]", '"@platforms//os:linux"'):
        with pytest.raises(dec.DecisionError):
            dec.Decisions.from_text(f'DECISIONS = {{"subdir:x": {bad}}}').optional("subdir:x", "subdir")


def test_words_per_kind() -> None:
    """A word is only a decision for the kinds that list it: MANUAL for `program:` and `subdir:`
    (the two that say whether a target is in `//...`), not elsewhere; IGNORE never for those two.
    The legend (`FORMS`) names exactly the words `WORD_FORMS` accepts, every word is one the
    `decision` struct defines (`WORDS`), and a key read as another kind than it spells is refused."""
    for kind, words in dec.WORD_FORMS.items():
        assert kind in dec.FORMS, kind
        assert set(words) <= set(dec.WORDS), (kind, words)
        assert set(re.findall(r"decision\.([A-Z_]+)", dec.FORMS[kind])) == set(words), kind
    for kind in dec.FORMS:
        assert not re.search(r"decision\.[A-Z_]+", dec.FORMS[kind]) or kind in dec.WORD_FORMS, kind
    assert must(
        dec.Decisions.from_text('DECISIONS = {"program:x": decision.MANUAL}').optional("program:x", "program")
    ).manual
    assert must(
        dec.Decisions.from_text('DECISIONS = {"subdir:x": decision.MANUAL}').optional("subdir:x", "subdir")
    ).manual
    for kind in sorted(set(dec.FORMS) - {"program", "subdir"}):
        with pytest.raises(dec.DecisionError):
            dec.Decisions.from_text(f'DECISIONS = {{"{kind}:x": decision.MANUAL}}').optional(f"{kind}:x", kind)
    for kind in ("program", "subdir"):
        with pytest.raises(dec.DecisionError):
            dec.Decisions.from_text(f'DECISIONS = {{"{kind}:x": decision.IGNORE}}').optional(f"{kind}:x", kind)
    with pytest.raises(dec.DecisionError, match="its key says 'program'"):
        dec.Decisions.from_text('DECISIONS = {"program:x": decision.ALWAYS}').optional("program:x", "subdir")
    with pytest.raises(dec.DecisionError, match="not a kind"):
        dec.normalize("nosuch", "nosuch:x", dec.Word("ALWAYS"))


def test_multiline_reason_stays_a_comment() -> None:
    """A `why` spanning lines (a `case` condition) is commented on every line."""
    request = dec.Request(
        "guard:autoconf:X", "guard", "autoconf", "cannot be expressed (case $x in\n  *yes*) ;; esac)", {}
    )
    lines = dec.comment_block(request, "    ")
    assert all(line.lstrip().startswith("#") for line in lines), lines
    assert "    #        *yes*) ;; esac)" in lines


def test_undecided_and_missing_are_pending(decisions: dec.Decisions) -> None:
    assert decisions.resolve(request("probe:gl_cv_later", "probe")) is None
    assert decisions.resolve(request("probe:gl_cv_new", "probe")) is None
    assert sorted(decisions.pending) == ["probe:gl_cv_later", "probe:gl_cv_new"]
    # UNDECIDED entries are in the file already: only the new key is missing from it.
    assert decisions.missing == ["probe:gl_cv_new"]
    assert "probe:gl_cv_later" not in decisions.unused


def test_wrong_shapes(decisions: dec.Decisions) -> None:
    with pytest.raises(dec.DecisionError):
        decisions.resolve(request("probe:gl_cv_a", "flag"))  # a string is not a flag default
    with pytest.raises(dec.DecisionError):
        decisions.resolve(request("variable:V", "guard"))  # IGNORE is not a guard decision
    wrong_name = dec.Decisions.from_text('DECISIONS = {"probe:a": checks.AC_TRY_LINK(name = "b", code = "x")}\n')
    with pytest.raises(dec.DecisionError):
        wrong_name.resolve(request("probe:a", "probe"))


@pytest.mark.parametrize(
    "text",
    [
        pytest.param('DECISIONS = {"k": foo.bar()}\n', id="unknown-call"),
        pytest.param('DECISIONS = {"k": None}\n', id="none-value"),
        pytest.param("x = 1\n", id="no-table"),
        pytest.param('DECISIONS = {"k": SOMETHING}\n', id="unknown-word"),
        pytest.param('DECISIONS = {"k": 1, "k": 2}\n', id="duplicate-key"),
        pytest.param('DECISIONS = {"k": b"x"}\n', id="bytes-literal"),
        pytest.param('DECISIONS = {"k": checks.AC_DEFINE("X", decision.IGNORE)}\n', id="word-in-call"),
        pytest.param(
            'DECISIONS = {"k": checks.AC_DEFINE("X", select({"//conditions:default": 1}))}\n', id="select-in-call"
        ),
    ],
)
def test_rejected_syntax(text: str) -> None:
    with pytest.raises(dec.DecisionError):
        dec.Decisions.from_text(text)


# ---- The in-place skeleton


def test_create_then_append(tmp_path: Path) -> None:
    path = tmp_path / "decisions.bzl"
    first = dec.Decisions.load(path)
    assert first.resolve(request("probe:gl_cv_x", "probe")) is None
    assert first.resolve(request("variable:Y", "variable")) is None
    assert first.write_needed(path, "a test") == ["probe:gl_cv_x", "variable:Y"]
    text = path.read_text(encoding="utf-8")
    assert text.startswith('"""Decisions for a test.')
    assert '    "probe:gl_cv_x": decision.UNDECIDED,\n' in text
    # The probe program sits in a fenced block tagged with its language.
    assert "    # program:\n    # ```c\n    #   int x;\n    #   int y;\n    # ```\n" in text
    assert "Generated by: rules_cc_autoconf ?\n" in text  # no version given here
    # The user decides one key and hand-formats the file; a later run adds the new key only.
    edited = text.replace('"probe:gl_cv_x": decision.UNDECIDED,', '"probe:gl_cv_x": "no",  # decided')
    path.write_text(edited, encoding="utf-8")
    second = dec.Decisions.load(path)
    assert must(second.resolve(request("probe:gl_cv_x", "probe"))).value == "no"
    assert second.resolve(request("variable:Y", "variable")) is None
    assert second.resolve(request("macro:gl_Z", "macro")) is None
    assert second.write_needed(path) == ["macro:gl_Z"]
    final = path.read_text(encoding="utf-8")
    # The new key sorts first: its block goes in right after the opening brace and the
    # existing text follows unchanged, hand formatting included.
    head, body = edited.split("DECISIONS = {\n", 1)
    assert final.startswith(head + "DECISIONS = {\n    # ---- macro: gl_Z\n"), final[:400]
    assert final.endswith(body), "existing text is kept"
    assert '"macro:gl_Z": decision.UNDECIDED,\n\n    # ---- probe: gl_cv_x\n' in final
    assert final.count("UNDECIDED,") == 2
    third = dec.Decisions.load(path)
    assert sorted(third.data) == ["macro:gl_Z", "probe:gl_cv_x", "variable:Y"]


def test_append_to_empty_table(tmp_path: Path) -> None:
    path = tmp_path / "decisions.bzl"
    path.write_text('"""Doc."""\n\nDECISIONS = {}\n', encoding="utf-8")
    decisions = dec.Decisions.load(path)
    decisions.resolve(request("flag:x", "flag"))
    assert decisions.write_needed(path) == ["flag:x"]
    text = path.read_text(encoding="utf-8")
    assert "DECISIONS = {\n" in text
    assert text.rstrip().endswith('    "flag:x": decision.UNDECIDED,\n}'), text
    dec.Decisions.load(path)  # still parses


def test_new_entries_keep_the_table_sorted(tmp_path: Path) -> None:
    """A later run's entries go in at their sorted place, above the comment block of the key
    that follows them, or at the end; the file stays free of buildifier's unsorted-dict-items."""
    path = tmp_path / "decisions.bzl"
    path.write_text(
        '"""Doc."""\n\nDECISIONS = {\n'
        '    # Why a.\n    "guard:a": decision.ALWAYS,\n\n'
        '    # Why p.\n    # Two lines.\n    "probe:p": "yes",\n}\n',
        encoding="utf-8",
    )
    decisions = dec.Decisions.load(path)
    for key, kind in (("variable:z", "variable"), ("macro:m", "macro"), ("probe:b", "probe")):
        decisions.resolve(request(key, kind))
    assert decisions.write_needed(path) == ["macro:m", "probe:b", "variable:z"]
    text = path.read_text(encoding="utf-8")
    keys = [line.split('"')[1] for line in text.splitlines() if line.startswith('    "')]
    assert keys == ["guard:a", "macro:m", "probe:b", "probe:p", "variable:z"]
    # Each new entry carries its own comment block; probe:p's two comment lines stay with it.
    assert '"macro:m": decision.UNDECIDED,\n\n    # ---- probe: b\n' in text
    assert (
        '"probe:b": decision.UNDECIDED,\n\n    # Why p.\n    # Two lines.\n    "probe:p": "yes",\n\n'
        "    # ---- variable: z\n"
    ) in text
    assert text.rstrip().endswith('    "variable:z": decision.UNDECIDED,\n}'), text
    assert sorted(dec.Decisions.load(path).data) == keys  # still parses, nothing lost


def test_bundled_layer() -> None:
    """The bundled decisions answer beneath the project's own: a key the project leaves out or
    UNDECIDED takes the bundled answer and is recorded as inherited; the project's own entry
    wins; the bundled file's keys never count as unused; `decided` sees both."""
    bundled = dec.Decisions.from_text(
        'DECISIONS = {"variable:LIBINTL": decision.IGNORE, "variable:POSUB": decision.IGNORE, "probe:x": "no"}\n',
        "gnulib/m4/decisions.bzl",
    )
    own = dec.Decisions.from_text(
        'DECISIONS = {"variable:LIBINTL": decision.UNDECIDED, "probe:x": "yes", "probe:own": "1"}\n',
        "decisions.bzl",
    )
    own.shared = bundled
    request = dec.Request("variable:LIBINTL", "variable", "configure.ac", "libintl")
    assert must(own.resolve(request)).ignore
    assert must(own.optional("variable:POSUB", "variable")).ignore
    assert must(own.resolve(dec.Request("probe:x", "probe", "configure.ac", "x"))).value == "yes"
    assert own.resolve(dec.Request("probe:y", "probe", "configure.ac", "y")) is None
    assert own.inherited == {"variable:LIBINTL": "decision.IGNORE", "variable:POSUB": "decision.IGNORE"}
    assert own.inherited_lines() == [
        "- `variable:LIBINTL` -> `decision.IGNORE`",
        "- `variable:POSUB` -> `decision.IGNORE`",
    ]
    assert sorted(own.pending) == ["probe:y"]
    assert own.unused == ["probe:own"]
    assert own.decided("variable:POSUB") and own.decided("probe:x") and not own.decided("probe:y")
    # The presence tests the translator makes see both layers, the project's entry first.
    assert own.lookup("variable:POSUB") is not None and own.lookup("probe:y") is None
    assert [k for k, _ in own.items()] == ["probe:own", "probe:x", "variable:LIBINTL", "variable:POSUB"]
    assert dict(own.items())["probe:x"] == "yes"
    assert own.unused == ["probe:own"]  # a presence test counts nothing as used
