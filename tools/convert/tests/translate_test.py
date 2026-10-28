"""Unit tests for the translator on a synthetic marker stream (no autotools)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.convert import dataflow as df
from tools.convert import emit, gnulibmk, lower, markers
from tools.convert.automake import _programs_tree, _split_deps, _template_var_toolchains
from tools.convert.cli import _manual_programs_section
from tools.convert.common import (
    NO_WARNINGS,
    _platform_labels,
    header_patterns,
    headers_glob,
    no_warnings_statement,
)
from tools.convert.decisions import Decisions, Word
from tools.convert.emit import render_statement
from tools.convert.gnulib_lib import (
    _classify_sources,
    _condition_table,
    _emit_gnulib_library,
    _gnulib_names,
    _literal_sed_substs,
    _template_indicators,
    _wrapper_header_rules,
)
from tools.convert.gnulibmk import Library, Source
from tools.convert.gnulibmk import parse as parse_mk
from tools.convert.index import Port, PortIndex, parse_build
from tools.convert.install_dirs import expand_install_dirs
from tools.convert.ir import Assign, Call, CheckItem, Dep, Package, Raw, Rule, Target, strings
from tools.convert.makefiles import _automake_tree, _configured_subdirs, _library_target, _parse_tree_makefile
from tools.convert.modulebazel import _module_bazel
from tools.convert.project import (
    SubdirGate,
    _gate_package,
    _gnulib_file_list,
    _module_flags,
    _option_rules,
    _pinned_defines,
    _repo_bazel,
    _root_includes,
    _standard_flags,
    _subdir_gates,
)
from tools.convert.results import _confdefs, _drop_duplicate_defines
from tools.convert.sources import (
    _conditional_sources,
    _preprocessor_flags,
    _probe_conditioned_srcs,
    _require_standards,
    _written_content,
    am_decision,
    simplified_conditions,
)
from tools.convert.tests.helpers import as_call, as_raw, as_str, must
from tools.convert.translate import (
    FlagInfo,
    GnulibLibrary,
    ListedProgram,
    Options,
    ProjectState,
    Translator,
    condition_names,
    default_from_help,
    default_tarname,
    describe,
    fold_define,
    list_append_words,
    m4_cache_name,
    produced_keys,
    provided_names,
    value_option_parts,
)

T = "\t"


def marker(kind: str, *fields: str) -> str:
    return "#RCCAC" + T + kind + T + T.join(fields)


def call(node_id: int, macro: str, *args: str, body: str = "") -> list[str]:
    lines = [marker("CALL", f"id={node_id}", f"macro={macro}")]
    for index, arg in enumerate(args, start=1):
        first, *rest = arg.split("\n")
        lines.append(marker("ARG", f"id={node_id}", f"n={index}", first))
        lines.extend("#RCCAC|" + line for line in rest)
    if body:
        lines.append(body)
    lines.append(marker("ENDCALL", f"id={node_id}", f"macro={macro}"))
    return lines


def rules_by_name(package: Package) -> dict[str, Rule]:
    """The rules among the statements after the package's targets, by name."""
    return {statement.name: statement for statement in package.after if isinstance(statement, Rule)}


def as_calls(items: list[CheckItem]) -> list[Call]:
    """The check items, every one of which is a Call."""
    calls: list[Call] = []
    for item in items:
        assert isinstance(item, Call), item
        calls.append(item)
    return calls


INDEX = PortIndex(
    [
        parse_build("autoconf/macros/AC_CANONICAL_HOST", 'autoconf(name = "AC_CANONICAL_HOST")'),
        parse_build(
            "gnulib/m4/other",
            '"""https://github.com/coreutils/gnulib/blob/abc/m4/other.m4"""\n'
            'autoconf(name = "gl_OTHER")\nautoconf(name = "other")',
        ),
    ]
)

M4 = """\
AC_DEFUN([gl_FOO],
[
  AC_REQUIRE([AC_CANONICAL_HOST])
  AC_REQUIRE([gl_OTHER])
  AC_CHECK_HEADERS([a.h b.h])
  AC_CHECK_HEADER([c.h], [AC_DEFINE([HAVE_C_H], [1], [c])], [], [[#include <a.h>]])
  AC_CACHE_CHECK([for foo], [gl_cv_foo],
    [AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[#include <a.h>]], [[return 0;]])],
       [gl_cv_foo=yes], [gl_cv_foo=no])])
  if test $gl_cv_foo = yes; then
    AC_DEFINE([HAVE_FOO], [1], [foo])
  fi
  FOO_VAR=7
  AC_SUBST([FOO_VAR])
])
"""


def expansion() -> str:
    lines = ["#! /bin/sh"]
    lines.append(marker("BEGIN", "id=1", "name=gl_FOO", "loc=m4/foo.m4:1"))
    lines += call(2, "AC_REQUIRE", "AC_CANONICAL_HOST")
    lines += call(3, "AC_REQUIRE", "gl_OTHER")
    lines += call(4, "AC_CHECK_HEADERS", "a.h b.h", body="\n".join(call(5, "AC_REQUIRE", "AC_CHECK_INCLUDES_DEFAULT")))
    lines += call(6, "AC_CHECK_HEADER", "c.h", "AC_DEFINE([HAVE_C_H], [1], [c])", "", "#include <a.h>")
    lines += [
        marker("CALL", "id=7", "macro=AC_CACHE_CHECK"),
        marker("ARG", "id=7", "n=1", "for foo"),
        marker("ARG", "id=7", "n=2", "gl_cv_foo"),
        marker("ARG", "id=7", "n=3", "AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[#include <a.h>]], [[return 0;]])],"),
        "#RCCAC|       [gl_cv_foo=yes], [gl_cv_foo=no])",
        "printf checking",
    ]
    lines += call(
        8,
        "AC_COMPILE_IFELSE",
        "AC_LANG_PROGRAM([[#include <a.h>]], [[return 0;]])",
        "gl_cv_foo=yes",
        "gl_cv_foo=no",
        body="if ac_fn_c_try_compile; then :; fi",
    )
    lines.append(marker("ENDCALL", "id=7", "macro=AC_CACHE_CHECK"))
    lines.append("if test $gl_cv_foo = yes; then")
    lines += call(9, "AC_DEFINE", "HAVE_FOO", "1", "foo")
    lines.append("fi")
    lines.append("FOO_VAR=7")
    lines += call(10, "AC_SUBST", "FOO_VAR")
    lines.append(marker("END", "id=1", "name=gl_FOO"))
    return "\n".join(lines)


# ---- Translator: the synthetic gl_FOO expansion ----


Translation = tuple[Translator, list[Package]]


@pytest.fixture
def translation() -> Translation:
    """The gl_FOO expansion translated: the translator and the packages it produced."""
    tree = markers.parse(expansion())
    assert tree.warnings == []
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"foo.m4"},
        m4_sources={"m4/foo.m4": M4},
        options=Options(),
    )
    return translator, translator.translate()


def test_volunteered_guard_adds_a_condition_to_an_unconditional_check() -> None:
    """ppp: configure checks <crypt.h> and `AC_CHECK_LIB([crypt])` independently, and a sysroot
    with the header and no library compiles crypt() calls it cannot link.  A `guard:` decision
    nobody asked for, keyed by the one header, splits it out of the grouped check with the
    requires list; the other headers stay grouped, and NEVER drops one."""
    tree = markers.parse(expansion())

    def translate(text: str) -> tuple[Decisions, list[Call]]:
        decisions = Decisions.from_text(text)
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"foo.m4"},
            m4_sources={"m4/foo.m4": M4},
            options=Options(),
            decisions=decisions,
        )
        (package,) = translator.translate()
        foo = package.target("gl_FOO")
        assert foo is not None
        return decisions, [c for c in foo.checks if isinstance(c, Call)]

    decided, calls = translate('DECISIONS = {"guard:gl_FOO:AC_CHECK_HEADER:b_h": ["ac_cv_header_a_h"]}')
    headers = {
        " ".join(strings(c.args[0])): strings(c.kwargs.get("requires"))
        for c in calls
        if c.fn == "macros.AC_CHECK_HEADERS"
    }
    assert headers["a.h"] == []
    assert headers["b.h"] == ["ac_cv_header_a_h"]
    assert decided.unused == []
    _, calls = translate('DECISIONS = {"guard:gl_FOO:AC_CHECK_HEADER:b_h": NEVER}')
    grouped = [" ".join(strings(c.args[0])) for c in calls if c.fn == "macros.AC_CHECK_HEADERS"]
    assert "a.h" in grouped and not any("b.h" in g for g in grouped)


def test_targets_and_deps(translation: Translation) -> None:
    _, packages = translation
    (package,) = packages
    assert package.path == "m4/foo"
    assert [t.name for t in package.targets] == ["gl_FOO", "foo"]
    foo = package.target("gl_FOO")
    assert foo is not None
    assert sorted(d.label for d in foo.deps) == ["//autoconf/macros/AC_CANONICAL_HOST", "//gnulib/m4/other:gl_OTHER"]
    plain = package.target("foo")
    assert plain is not None
    assert plain.deps[0].label == ":gl_FOO"


def test_checks(translation: Translation) -> None:
    translator, packages = translation
    foo = packages[0].target("gl_FOO")
    assert foo is not None
    calls = [c for c in foo.checks if isinstance(c, Call)]
    assert [c.fn for c in calls] == [
        "macros.AC_CHECK_HEADERS",
        "checks.AC_CHECK_HEADER",
        "checks.AC_TRY_COMPILE",
        "checks.AC_DEFINE",
        "checks.AC_SUBST",
    ]
    headers, header, probe, define, subst = calls
    assert headers.args == [["a.h", "b.h"]]
    assert headers.returns_list
    assert header.args == ["c.h"]
    assert header.kwargs == {"define": "HAVE_C_H", "includes": ["#include <a.h>"]}
    assert probe.kwargs["name"] == "gl_cv_foo"
    assert as_call(probe.kwargs["code"]).fn == "utils.AC_LANG_PROGRAM"
    assert as_call(probe.kwargs["code"]).args == [["#include <a.h>"], ["return 0;"]]
    assert probe.comments[0].startswith("m4/foo.m4:~7: AC_CACHE_CHECK([for foo], [gl_cv_foo]"), probe.comments
    assert define.args == ["HAVE_FOO", 1]
    # The `if test $gl_cv_foo = yes` around the define became its `requires`.
    assert define.kwargs == {"requires": ["gl_cv_foo"]}
    # `AC_SUBST([FOO_VAR])` takes the value of the body's `FOO_VAR=7`.
    assert subst.args == ["FOO_VAR", 7]
    # Everything was followed: no decision was requested.
    assert translator.decisions.pending == {}
    # Source locations were recovered from the m4 text.
    assert any(c.startswith("m4/foo.m4:~5:") for c in headers.comments), headers.comments
    assert any(c.startswith("m4/foo.m4:~11:") for c in define.comments), define.comments


def test_renders(translation: Translation) -> None:
    _, packages = translation
    text = emit.render_package(packages[0])
    assert 'load("//autoconf:checks.bzl", "checks", "macros", "utils")' in text
    assert (
        'checks.AC_DEFINE(\n            "HAVE_FOO",\n            1,\n            requires = ["gl_cv_foo"],\n        ),'
        in text
    )


def test_fold_define() -> None:
    assert fold_define("AC_DEFINE([HAVE_X], [1], [desc])") == "HAVE_X"
    assert fold_define("AC_DEFINE([HAVE_X])") == "HAVE_X"
    assert fold_define("AC_DEFINE([HAVE_X], [2])") is None
    assert fold_define("x=yes") is None


# ---- Translator helpers and project assembly ----


def test_default_from_help() -> None:
    assert default_from_help("AS_HELP_STRING([--disable-perl-regexp], [disable PCRE])")
    assert default_from_help("AS_HELP_STRING([--without-libiconv], [no iconv])")
    assert not default_from_help("AS_HELP_STRING([--enable-gcc-warnings], [warnings])")
    assert not default_from_help("AS_HELP_STRING([--with-foo=DIR], [foo])")
    assert not default_from_help("")


def test_condition_names() -> None:
    assert condition_names("!SHLIB_STATUS==unsupported") == ["SHLIB_STATUS"]
    assert condition_names("USE_NLS && !gt_val") == ["USE_NLS", "gt_val"]
    assert condition_names("(a_cv_x || b_cv_y) && HAVE_Z!=no") == ["a_cv_x", "b_cv_y", "HAVE_Z"]


def test_provided_names() -> None:
    assert provided_names(Call("macros.AC_CHECK_HEADERS", [["sys/wait.h", "dirent.h"]])) == {
        "ac_cv_header_sys_wait_h",
        "HAVE_SYS_WAIT_H",
        "ac_cv_header_dirent_h",
        "HAVE_DIRENT_H",
    }
    assert provided_names(Call("macros.AC_CHECK_DECLS", [["alarm"]])) == {"ac_cv_have_decl_alarm", "HAVE_DECL_ALARM"}
    assert provided_names(Call("checks.AC_TRY_COMPILE", [], {"name": "gl_cv_x", "define": "HAVE_X"})) == {
        "gl_cv_x",
        "HAVE_X",
    }
    assert provided_names(Call("checks.AC_SUBST", ["FOO_VAR", 7])) == {"FOO_VAR"}
    assert provided_names(Call("checks.AC_CHECK_LIB", ["dl", "dlopen"])) == {"ac_cv_lib_dl_dlopen", "HAVE_LIBDL"}


def test_dangling_conditions_become_guard_decisions() -> None:
    """A check conditional on `cross_compiling` (no check produces it) is a
    guard decision; a self-guarding default assignment is unconditional."""
    lines = ["#! /bin/sh"]
    lines.append(marker("BEGIN", "id=1", "name=gl_ENV", "loc=m4/env.m4:1"))
    lines.append(
        'if test "x$GCC" = "xyes"; then\n  CROSS_COMPILE="-DCROSS_COMPILING"\nelse\n'
        '  CROSS_COMPILE=""\nfi\ntest -n "$ARFLAGS" || ARFLAGS="cr"'
    )
    lines += call(2, "AC_SUBST", "CROSS_COMPILE")
    lines += call(3, "AC_SUBST", "ARFLAGS")
    lines.append(marker("END", "id=1", "name=gl_ENV"))
    tree = markers.parse("\n".join(lines) + "\n")
    m4 = "AC_DEFUN([gl_ENV], [AC_SUBST([CROSS_COMPILE]) AC_SUBST([ARFLAGS])])\n"

    def translate(decisions: Decisions) -> tuple[Translator, Target]:
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"m4/env.m4"},
            m4_sources={"m4/env.m4": m4},
            options=Options(),
            decisions=decisions,
        )
        (package,) = translator.translate()
        return translator, package.targets[0]

    asked = Decisions()
    _, target = translate(asked)
    names = [c.args[0] for c in target.checks if isinstance(c, Call)]
    assert names == ["ARFLAGS"]  # unconditional: ARFLAGS is unset before the m4 sets it
    assert "requires" not in next(c for c in target.checks if isinstance(c, Call)).kwargs
    # `$GCC = yes` is the compiler, a select() dimension: no decision needed.
    assert list(asked.pending) == []
    (family,) = target.selects
    assert family == "@rules_cc//cc/compiler:"
    arms = {label: [(c.args[0], c.args[1]) for c in as_calls(items)] for label, items in target.selects[family].items()}
    assert arms == {
        "@rules_cc//cc/compiler:msvc-cl": [("CROSS_COMPILE", "")],
        "@rules_cc//cc/compiler:clang-cl": [("CROSS_COMPILE", "")],
        "//conditions:default": [("CROSS_COMPILE", "-DCROSS_COMPILING")],
    }


def test_port_variable_compares_by_value() -> None:
    """`test "$gl_cv_have_unlimited_file_name_length" = no` on a variable a port provides with
    M4_VARIABLE (words "no" / "yes", as GNU assigns them) lowers to `==no`; the same test on a
    probe's cache variable is the probe's success (`!gl_cv_foo`)."""
    index = PortIndex(
        [
            parse_build(
                "gnulib/m4/chdir-long",
                'autoconf(name = "gl_FUNC_CHDIR_LONG", checks = [checks.M4_VARIABLE('
                '"gl_cv_have_unlimited_file_name_length", condition = "x", if_true = "no", if_false = "yes")])',
            ),
            parse_build(
                "gnulib/m4/x_h", 'autoconf(name = "gl_X_H", checks = [checks.M4_VARIABLE("GL_GENERATE_X_H", "true")])'
            ),
        ]
    )
    assert index.variables == {"gl_cv_have_unlimited_file_name_length", "GL_GENERATE_X_H"}
    lines = ["#! /bin/sh"]
    lines.append(marker("BEGIN", "id=1", "name=gl_USE", "loc=m4/use.m4:1"))
    lines += call(2, "AC_REQUIRE", "gl_FUNC_CHDIR_LONG")
    lines.append('if test "$gl_cv_have_unlimited_file_name_length" = no; then')
    lines += call(3, "AC_DEFINE", "NEED_CHDIR_LONG", "1", "x")
    lines.append("fi")
    lines += call(
        4,
        "AC_COMPILE_IFELSE",
        "AC_LANG_PROGRAM([[]], [[return 0;]])",
        "gl_cv_foo=yes",
        "gl_cv_foo=no",
        body="if ac_fn_c_try_compile; then :; fi",
    )
    lines.append("if test $gl_cv_foo = no; then")
    lines += call(5, "AC_DEFINE", "NO_FOO", "1", "x")
    lines.append("fi")
    # The body's own assignment of a port variable decides (gl_INIT's defaults for the
    # headers of modules not in use): no reference to a result the port would have to provide.
    lines.append("GL_GENERATE_X_H=false\nif test $GL_GENERATE_X_H = true; then")
    lines += call(6, "AC_DEFINE", "GENERATED_X", "1", "x")
    lines.append("fi")
    lines.append(marker("END", "id=1", "name=gl_USE"))
    tree = markers.parse("\n".join(lines) + "\n")
    m4 = "AC_DEFUN([gl_USE], [AC_REQUIRE([gl_FUNC_CHDIR_LONG])])\n"
    translator = Translator(
        tree, index=index, selected_files={"m4/use.m4"}, m4_sources={"m4/use.m4": m4}, options=Options()
    )
    (package,) = translator.translate()
    target = package.targets[0]
    requires = {
        c.args[0]: c.kwargs.get("requires") for c in target.checks if isinstance(c, Call) and c.fn == "checks.AC_DEFINE"
    }
    assert requires == {"NEED_CHDIR_LONG": ["gl_cv_have_unlimited_file_name_length==no"], "NO_FOO": ["!gl_cv_foo"]}


def test_library_target_names() -> None:
    """`libfoo.a` -> `foo`; `lib/foo` plus a `libfoo` alias only when a program `foo` exists;
    an archive in a subdirectory (`lib/libnvc.a`) keeps the directory in front."""
    assert _library_target("", "libgzip.a", set()) == ("gzip", None)
    assert _library_target("", "libnvc.a", {"nvc"}) == ("lib/nvc", "libnvc")
    assert _library_target("src", "libssh2.la", set()) == ("src/ssh2", None)
    assert _library_target("", "lib/libnvc.a", set()) == ("lib/nvc", None)
    assert _library_target("", "lib/libnvc.a", {"lib/nvc"}) == ("lib/lib/nvc", "lib/libnvc")
    assert _library_target("", "plugin.la", set()) == ("plugin", None)


def test_configured_subdirs(tmp_path: Path) -> None:
    """A computed SUBDIRS plus children with their own configure.ac: texlive's libs/."""
    root = tmp_path
    (root / "Makefile.am").write_text("SUBDIRS = . $(MAKE_SUBDIRS)\n")
    for child, has_configure in (("zlib", True), ("mpfi", True), ("doc", True), ("am", False)):
        (root / child).mkdir()
        if has_configure:
            (root / child / "configure.ac").write_text("AC_INIT\n")
    assert _configured_subdirs(root, {"zlib"}) == ["mpfi"]
    (root / "Makefile.am").write_text("SUBDIRS = zlib mpfi\n")
    assert _configured_subdirs(root, set()) == []


def test_makefile_in_targets(tmp_path: Path) -> None:
    """An autoconf project without automake: libraries and programs from Makefile.in's rules, the
    install rule saying which of them `make install` ships."""
    root = tmp_path
    (root / "Makefile.in").write_text(
        "CSRC = fptype.c tcpdump.c\nLIBSRC = print.c @LOCALSRC@\nOBJ = $(CSRC:.c=.o)\n"
        "LIBOBJ = $(LIBSRC:.c=.o) @LIBOBJS@\nPROG = tcpdump\nLIB = libnetdissect.a\nTERMCAP_LIB = @TERMCAP_LIB@\n"
        "all: $(PROG)\n"
        "$(PROG): $(OBJ) @V_PCAPDEP@ $(LIB)\n\t@rm -f $@\n"
        "\t$(CC) $(CFLAGS) -o $@ $(OBJ) $(LIB) $(LIBS) $(TERMCAP_LIB) $(LIBUTIL)\n"
        "extra: extra.o $(LIB)\n\t$(CC) $(CFLAGS) -o $@ extra.o $(LIB)\n"
        "$(LIB): $(LIBOBJ)\n\t$(AR) cr $@ $(LIBOBJ)\n\t$(RANLIB) $@\n"
        "strlcpy.o: $(srcdir)/missing/strlcpy.c\n\t$(CC) $(CFLAGS) -o $@ -c $(srcdir)/missing/strlcpy.c\n"
        "install: $(PROG)\n\t$(INSTALL_PROGRAM) $(PROG) $(DESTDIR)$(sbindir)/$(PROG)\n"
        "clean:\n\trm -f *.o\n"
    )
    (root / "missing").mkdir()
    for name in ("fptype.c", "tcpdump.c", "print.c", "missing/strlcpy.c", "extra.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.literal_substs = {"LOCALSRC": ""}
    # AC_REPLACE_FUNCS(strlcpy): the object is in @LIBOBJS@, its source where the rule says.
    state.libobj_names = {"GL_COND_OBJ_STRLCPY": "strlcpy"}
    state.src_conditions = {"GL_COND_OBJ_STRLCPY": [df.neg(df.Truthy("ac_cv_func_strlcpy"))]}
    state.produced_results = {"ac_cv_func_strlcpy"}
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    assert rules["netdissect"].kind == "cc_library"
    assert "print.c" in strings(rules["netdissect"].attrs["srcs"])
    assert rules["tcpdump"].kind == "cc_binary"
    assert rules["tcpdump"].attrs["srcs"] == ["fptype.c", "tcpdump.c"]
    assert ":netdissect" in strings(rules["tcpdump"].attrs["deps"])
    # The install rule ships the program alone: it is public, the library is the module's own
    # (the visibility pass confines what no builder marked).
    assert rules["tcpdump"].attrs["visibility"] == ["//visibility:public"]
    assert "visibility" not in rules["netdissect"].attrs
    assert not any("@LIBOBJS@" in n for n in notes), notes
    assert any("@V_PCAPDEP@" in n for n in notes)
    conditional = next(r for r in package.after if isinstance(r, Rule) and r.kind == "autoconf_srcs")
    assert "missing/strlcpy.c" in str(conditional.attrs["srcs"])
    # The link line's configure substitution is a link variable, through the shared linkopts
    # target; `$(LIBUTIL)`, which the Makefile never defines, is empty under make and nothing here.
    assert rules["linkopts"].attrs["vars"] == ["TERMCAP_LIB"]
    assert ":linkopts" in strings(rules["tcpdump"].attrs["deps"])
    # `extra` has a rule but `all` does not reach it (readline's rlptytest, in OTHEREXE alone):
    # no target, and the note names the decision that builds it.
    assert "extra" not in rules
    assert any("extra is not among the programs configure builds" in n and "program:extra" in n for n in notes)
    for text, built in (('"program:extra": NEVER', False), ('"program:extra": ["@platforms//os:linux"]', True)):
        package = Package(path="", docstring="")
        decided = Decisions.from_text("DECISIONS = {" + text + "}")
        _programs_tree(package, root, decided, state, [])
        rules = rules_by_name(package)
        assert ("extra" in rules) is built
        if built:
            gate = as_raw(rules["extra"].attrs["target_compatible_with"]).text
            assert '"@platforms//os:linux": []' in gate
        assert decided.unused == []


def test_plain_makefiles_including_a_configured_file(tmp_path: Path) -> None:
    """tftp-hpa: no Makefile.am / Makefile.in; plain Makefiles `-include ../config/MCONFIG` (an
    AC_CONFIG_FILES output whose MCONFIG.in gives `O = @OBJEXT@`, `X = @EXEEXT@`) and build with
    literal lists: `common/libcommon.a` is a library, `tftp$(X)` a program linking it through the
    link line's `$(TFTP_LIBS)`."""
    root = tmp_path
    for d in ("config", "common", "tftp"):
        (root / d).mkdir()
    (root / "Makefile").write_text("SUB = common tftp\nall: $(patsubst %, %.build, $(SUB))\n")
    (root / "config/MCONFIG.in").write_text(
        "O = @OBJEXT@\nX = @EXEEXT@\nCC = @CC@\nTFTP_LIBS = ../common/libcommon.a @TFTP_LIBS@\n"
    )
    (root / "MRULES").write_text(".c.o:\n\t$(CC) $(CFLAGS) -c $<\n")
    (root / "common/Makefile").write_text(
        "-include ../config/MCONFIG\ninclude ../MRULES\nOBJS = tftpsubs.$(O) signal.$(O)\nLIB = libcommon.a\n"
        "all: $(LIB)\n$(LIB): $(OBJS)\n\t$(AR) $(LIB) $(OBJS)\n"
    )
    (root / "tftp/Makefile").write_text(
        "-include ../config/MCONFIG\ninclude ../MRULES\nOBJS = tftp.$(O) main.$(O)\nall: tftp$(X)\n"
        "tftp$(X): $(OBJS) $(filter %.a,$(TFTP_LIBS))\n\t$(CC) $(CFLAGS) -o tftp$(X) $(OBJS) $(TFTP_LIBS)\n"
    )
    for name in ("common/tftpsubs.c", "common/signal.c", "tftp/tftp.c", "tftp/main.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.config_files = ["config/MCONFIG"]
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    assert rules["common/common"].kind == "cc_library"
    assert rules["common/common"].attrs["srcs"] == ["common/signal.c", "common/tftpsubs.c"]
    assert rules["tftp/tftp"].kind == "cc_binary"
    assert rules["tftp/tftp"].attrs["srcs"] == ["tftp/main.c", "tftp/tftp.c"]
    assert ":common/common" in strings(rules["tftp/tftp"].attrs["deps"])
    assert any("@TFTP_LIBS@" in n for n in notes), notes


def test_search_libs_actions_seen_through() -> None:
    """tftp-hpa's PA_SEARCH_LIBS_AND_ADD: `AC_SEARCH_LIBS([daemon], [], [...], [AC_LIBOBJ(daemon)
    pa_add_daemon=true])`: the not-found action's AC_LIBOBJ is translated under the search's
    failure and the assignment is seen by the test after it."""
    lines = [
        "#! /bin/sh",
        marker("CALL", "id=2", "macro=AC_SEARCH_LIBS"),
        marker("ARG", "id=2", "n=1", "daemon"),
        marker("ARG", "id=2", "n=2", ""),
        marker("ARG", "id=2", "n=3", "pa_add_daemon=false"),
        marker("ARG", "id=2", "n=4", "AC_LIBOBJ(daemon)"),
        "#RCCAC|    pa_add_daemon=true",
        'if test "$ac_cv_search_daemon" != no',
        "then :",
        "  pa_add_daemon=false",
        "else",
        *call(3, "AC_LIBOBJ", "daemon"),
        "pa_add_daemon=true",
        "fi",
        marker("ENDCALL", "id=2", "macro=AC_SEARCH_LIBS"),
        'if test "$pa_add_daemon" = true',
        "then :",
        *call(4, "AC_DEFINE", "NEED_DAEMON", "1", "x"),
        "fi",
    ]
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [c for c in target.checks if isinstance(c, Call)]
    assert [c.fn for c in calls][:1] == ["checks.AC_SEARCH_LIBS"]
    define = next(c for c in calls if c.fn == "checks.AC_DEFINE" and c.args[0] == "NEED_DAEMON")
    assert define.kwargs.get("requires") == ["!ac_cv_search_daemon"]
    assert list(translator.decisions.pending) == []
    assert "daemon" in state.libobj_names.values()


def test_module_flags_decisions() -> None:
    """`copts:.` / `cxxopts:.` decisions: COPTS is NO_WARNINGS plus the flags and replaces it in every
    cc target's copts; CXXOPTS becomes the attribute."""
    package = Package(path="", docstring="")
    package.before.append(no_warnings_statement())
    package.after.append(Rule("cc_library", "x", {"srcs": ["x.c"], "copts": Raw(NO_WARNINGS + ' + ["-DX"]')}))
    package.after.append(Rule("cc_binary", "y", {"srcs": ["y.c"]}))
    notes = _module_flags(
        package, Decisions({"copts:.": ["-Wno-error=incompatible-pointer-types"], "cxxopts:.": ["-std=c++17"]}), "."
    )
    names = [st.name for st in package.before if isinstance(st, Assign)]
    assert names[names.index(NO_WARNINGS) : names.index(NO_WARNINGS) + 3] == [NO_WARNINGS, "COPTS", "CXXOPTS"]
    copts_constant = next(st for st in package.before if isinstance(st, Assign) and st.name == "COPTS")
    copts = as_raw(copts_constant.value)
    assert copts.text == 'NO_WARNINGS + ["-Wno-error=incompatible-pointer-types"]'
    x, y = package.after
    assert isinstance(x, Rule) and isinstance(y, Rule)
    assert as_raw(x.attrs["copts"]).text == 'COPTS + ["-DX"]'
    assert as_raw(y.attrs["copts"]).text == "COPTS"
    assert as_raw(x.attrs["cxxopts"]).text == "CXXOPTS"
    assert len(notes) == 2


def test_port_provided_names_drop_local_checks() -> None:
    """A local check whose define or subst a port dependency already provides goes (gzip's
    AC_CHECK_FUNCS([fchown]) next to gl_FUNC_CHOWN; libtool's OBJDUMP next to a port that substs it)."""
    index = PortIndex(
        [
            Port(package="gnulib/m4/chown", targets={"gl_FUNC_CHOWN"}, defines={"HAVE_FCHOWN"}),
            Port(
                package="gnulib/m4/sys_socket_h",
                targets={"gl_SYS_SOCKET_H"},
                defines={"HAVE_SHUTDOWN"},
                substs={"OBJDUMP"},
            ),
        ]
    )
    main = Target(
        name="autoconf",
        checks=[
            Call("macros.AC_CHECK_FUNCS", [["fchown", "fchmod"]]),
            Call("checks.AC_SUBST", ["OBJDUMP", "objdump"]),
            Call("checks.AC_SUBST", ["OBJDUMP", "objdump"]),
            Call("checks.AC_SUBST", ["NM", "nm"]),
            Call("checks.AC_TRY_LINK", [], {"name": "ac_cv_func_shutdown", "define": "HAVE_SHUTDOWN", "code": "x"}),
            # a define next to a port's *subst* of the same name: different namespaces, both stay
            Call("checks.AC_DEFINE", ["OBJDUMP", 1]),
            Call("checks.AC_SUBST", ["HAVE_SHUTDOWN", "1"]),
        ],
    )
    main.add_dep("@rules_cc_autoconf//gnulib/m4/chown:gl_FUNC_CHOWN")
    main.add_dep("//gnulib/m4/sys_socket_h")
    main.add_dep(":package")
    notes = _drop_duplicate_defines(main, [], index)
    # fchown's cache variable stays as a define-less probe (gnulib.mk conditions read it)
    assert [(c.fn, c.args) for c in as_calls(main.checks)] == [
        ("macros.AC_CHECK_FUNCS", [["fchmod"]]),
        ("checks.AC_CHECK_FUNC", ["fchown"]),
        ("checks.AC_SUBST", ["NM", "nm"]),
        ("checks.AC_DEFINE", ["OBJDUMP", 1]),
        ("checks.AC_SUBST", ["HAVE_SHUTDOWN", "1"]),
    ]
    assert any("gl_FUNC_CHOWN already provides HAVE_FCHOWN" in n or "fchown" in n for n in notes), notes


def test_pinned_defines() -> None:
    """`define:` values become the config header's `substitutions`: a dict, or a select() of dicts with
    an empty default arm, each rewriting the `#undef` line the resolver would otherwise fill."""
    pinned, notes = _pinned_defines(
        Decisions.from_text(
            'DECISIONS = {"define:NL_LANGINFO_MTSAFE": select({"@platforms//os:macos": 1}), "define:Y": decision.IGNORE}'
        )
    )
    assert isinstance(pinned, Raw)
    text = pinned.text.replace("\n", "").replace(" ", "")
    assert text == (
        'select({"@platforms//os:macos":{"#undefNL_LANGINFO_MTSAFE":"#defineNL_LANGINFO_MTSAFE1",},'
        '"//conditions:default":{},})'
    )
    assert any("NL_LANGINFO_MTSAFE" in n for n in notes)
    pinned, _notes = _pinned_defines(Decisions.from_text('DECISIONS = {"define:X": 2}'))
    assert pinned == {"#undef X": "#define X 2"}
    assert _pinned_defines(Decisions())[0] == None
    # A package scope: `.` is the root, another path pins only that package's header.
    scoped = Decisions.from_text('DECISIONS = {"define:.:A": 1, "define:ext:B": 2, "define:C": 3}')
    assert _pinned_defines(scoped, "")[0] == {"#undef A": "#define A 1", "#undef C": "#define C 3"}
    assert _pinned_defines(scoped, "ext")[0] == {"#undef B": "#define B 2", "#undef C": "#define C 3"}


def test_platform_labels_mingw_is_windows() -> None:
    """`mingw*` alone (a gnulib before 2024) is the Windows constraint; a pattern set that tells
    MinGW from MSVC (`windows*` without `mingw*`) is not a platform condition."""
    assert _platform_labels(df.HostOs(("mingw*",))) == ["@platforms//os:windows"]
    assert _platform_labels(df.HostOs(("mingw*", "windows*"))) == ["@platforms//os:windows"]
    assert _platform_labels(df.HostOs(("windows*",))) is None


def test_gnulib_file_list() -> None:
    """gl_FILE_LIST names the m4 files gnulib-tool copied in: their macros are gnulib's whatever
    the snapshot called the file."""
    comp = (
        "AC_DEFUN([gl_INIT], [\n  gl_FUNC_PRINTF_POSIX\n])\n"
        "AC_DEFUN([gl_FILE_LIST], [\n  build-aux/config.rpath\n  lib/printf.c\n"
        "  m4/printf-posix-rpl.m4\n  m4/printf-posix.m4\n])\n"
    )
    assert _gnulib_file_list({"m4/gnulib-comp.m4": comp, "m4/own.m4": "AC_DEFUN([gl_FILE_LIST], [m4/no.m4])"}) == {
        "printf-posix-rpl.m4",
        "printf-posix.m4",
    }


def test_gnulib_conditions_spell_probes() -> None:
    """gnulib.mk's `test $HAVE_WCWIDTH = 0`: the port's probe by its cache variable, where the
    bare name would reach the header's default; a name a port's own AC_SUBST produces stays."""
    state = ProjectState(
        port_probes={
            "HAVE_WCWIDTH": "ac_cv_func_wcwidth",
            "HAVE_FSEEKO": "ac_cv_func_fseeko",
            "HAVE_POSIX_SPAWN": "ac_cv_func_posix_spawn",
        },
        port_plain_substs={"HAVE_FSEEKO"},
        port_ambiguous={"HAVE_FSEEKO"},
        port_result_substs={"HAVE_FSEEKO", "HAVE_POSIX_SPAWN"},
    )
    spell = _gnulib_names(state)
    assert spell(df.Eq("HAVE_WCWIDTH", "0", by_value=True)) == df.neg(df.Truthy("ac_cv_func_wcwidth"))
    assert spell(df.Eq("HAVE_WCWIDTH", "1", by_value=True)) == df.Truthy("ac_cv_func_wcwidth")
    assert spell(df.Eq("HAVE_FSEEKO", "0", by_value=True)) == df.Eq("ac_cv_subst_HAVE_FSEEKO", "0")
    # spawn_h substitutes HAVE_POSIX_SPAWN on every platform (a constant where the probe does
    # not run): the bare name is that result.
    assert spell(df.Eq("HAVE_POSIX_SPAWN", "0", by_value=True)) == df.Eq("HAVE_POSIX_SPAWN", "0", by_value=True)


def test_platform_arm_constant_beside_probe_define() -> None:
    """make's `AS_CASE([$host], [*-*-mingw32], [AC_DEFINE([HAVE_DOS_PATHS])])` next to the probe-driven
    define of the same name in a split body: one producer, the probe's; a different value stays."""
    body = Target(
        name="autoconf_1",
        checks=[
            Call("checks.AC_TRY_COMPILE", [], {"name": "ac_cv_dos_paths", "code": "x"}),
            Call("checks.AC_DEFINE_UNQUOTED", ["HAVE_DOS_PATHS", 1], {"requires": ["ac_cv_dos_paths"]}),
            Call("checks.AC_DEFINE", ["PATH_SEPARATOR_CHAR", "':'"], {"requires": ["!ac_cv_dos_paths"]}),
        ],
    )
    main = Target(name="autoconf")
    main.arm("@platforms//os:windows").extend(
        [
            Call("checks.AC_DEFINE", ["WINDOWS32", 1]),
            Call("checks.AC_DEFINE", ["HAVE_DOS_PATHS", 1]),
            Call("checks.AC_DEFINE", ["PATH_SEPARATOR_CHAR", "';'"]),
        ]
    )
    notes = _drop_duplicate_defines(main, [body], PortIndex([]))
    assert [c.args[0] for c in as_calls(main.arm("@platforms//os:windows"))] == ["WINDOWS32", "PATH_SEPARATOR_CHAR"]
    assert len(body.checks) == 3
    assert any(
        "HAVE_DOS_PATHS is defined by a platform arm of autoconf and by a probe of autoconf_1" in n for n in notes
    ), notes


def test_produced_keys_keep_namespaces_apart() -> None:
    """lib-link's probe defines HAVE_LIBSSL and `AC_SUBST([HAVE_LIBSSL])` substitutes it: not one
    result twice, so a select() arm keeps both."""
    probe = Call("checks.AC_TRY_LINK", [], {"name": "ac_cv_libssl", "code": "x", "define": "HAVE_LIBSSL"})
    subst = Call("checks.AC_SUBST", ["HAVE_LIBSSL"], {"condition": "ac_cv_libssl", "if_true": "yes", "if_false": "no"})
    assert produced_keys(probe) == {("cache", "ac_cv_libssl"), ("define", "HAVE_LIBSSL")}
    assert produced_keys(subst) == {("subst", "HAVE_LIBSSL")}
    dual = Call("checks.AC_CHECK_FUNC", ["openat"], {"define": "HAVE_OPENAT", "subst": True})
    assert produced_keys(dual) == {("cache", "ac_cv_func_openat"), ("define", "HAVE_OPENAT"), ("subst", "HAVE_OPENAT")}


def test_m4_cache_name() -> None:
    """bison names one cache variable per language with m4: the C and C++ spellings."""
    name = "bison_cv_[]_AC_LANG_ABBREV[]_supports_posixly_correct"
    assert m4_cache_name(name, "c") == "bison_cv_c_supports_posixly_correct"
    assert m4_cache_name(name, "cpp") == "bison_cv_cxx_supports_posixly_correct"
    assert m4_cache_name("ac_cv_[]_AC_LANG_PREFIX[]_works", "cpp") == "ac_cv_CXX_works"


def test_check_tool_substitutes_the_program() -> None:
    """AC_CHECK_TOOL(VAR, prog, not-found): the program, not the not-found value (libtool's
    `AC_CHECK_TOOL(OBJDUMP, objdump, false)` must not substitute "false")."""
    lines = (
        ["#! /bin/sh"]
        + call(2, "AC_CHECK_TOOL", "OBJDUMP", "objdump", "false")
        + call(3, "AC_CHECK_PROG", "HAVE_GPERF", "gperf", "yes", "no")
        + call(4, "AC_PATH_PROG", "PERL", "perl")
    )
    tree = markers.parse("\n".join(lines) + "\n")
    translator = Translator(
        tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={}, options=Options(), decisions=Decisions()
    )
    translator.translate()
    main = translator.translate_root("autoconf", loc_file="configure.ac")
    assert [c.args for c in main.checks if isinstance(c, Call) and c.fn == "checks.AC_SUBST"] == [
        ["OBJDUMP", "objdump"],
        ["HAVE_GPERF", "yes"],
        ["PERL", "perl"],
    ]


def test_am_conditional_reads_inlined_macro_assignment() -> None:
    """gmp's `want_cxx=no`, a project macro expanded in place setting `want_cxx=yes` under a probe,
    then AM_CONDITIONAL(WANT_CXX, test $want_cxx = yes): the conditional is the probe's."""
    lines = (
        ["#! /bin/sh", "want_cxx=no"]
        + call(2, "AC_CHECK_FUNCS", "bar")
        + [
            marker("BEGIN", "id=3", "name=GMP_PROG_CXX_WORKS", "loc=acinclude.m4:5"),
            "if test $ac_cv_func_bar = yes; then",
            "  want_cxx=yes",
            "fi",
            marker("END", "id=3", "name=GMP_PROG_CXX_WORKS"),
        ]
        + call(4, "AM_CONDITIONAL", "WANT_CXX", "test $want_cxx = yes")
    )
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac", "acinclude.m4"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    translator.translate_root("autoconf", loc_file="configure.ac")
    assert describe(state.am_conditionals["WANT_CXX"]) == "ac_cv_func_bar"


def test_inline_conditional_header() -> None:
    """gnulib-comp.m4 carries the stdckdint-h module's code inline: the header check, the
    STDCKDINT_H assignment and gl_CONDITIONAL_HEADER; GL_GENERATE_STDCKDINT_H follows."""
    lines = (
        ["#! /bin/sh"]
        + call(2, "AC_CHECK_HEADERS_ONCE", "stdckdint.h")
        + [
            "if test $ac_cv_header_stdckdint_h = yes; then",
            "  GL_GENERATE_STDCKDINT_H=false",
            "else",
            "  GL_GENERATE_STDCKDINT_H=true",
            "fi",
        ]
        + call(3, "gl_CONDITIONAL_HEADER", "stdckdint.h")
    )
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    translator.translate_root("autoconf", loc_file="configure.ac")
    assert "GL_GENERATE_STDCKDINT_H" in state.header_conditions
    assert describe(state.header_conditions["GL_GENERATE_STDCKDINT_H"]) == "!ac_cv_header_stdckdint_h"


def test_indicator_variables() -> None:
    """gl_MODULE_INDICATOR_INIT_VARIABLE([GNULIB_STDIO_H_SIGPIPE], [1]) and
    gl_MODULE_INDICATOR_SET_VARIABLE turn a wrapper header's @GNULIB_X@ on; a 0 is the default."""
    lines = (
        ["#! /bin/sh"]
        # m4_require hoists the stdlib_h port's `_MODULE_INDICATOR_DEFAULTS` body out of gl_INIT: a default
        + call(8, "gl_MODULE_INDICATOR_INIT_VARIABLE", "GNULIB_MDA_GCVT", "1", body="GL_GNULIB_MDA_GCVT=1")
        + [marker("BEGIN", "id=1", "name=gl_INIT", "loc=m4/gnulib-comp.m4:385")]
        + call(
            2, "gl_MODULE_INDICATOR_INIT_VARIABLE", "GNULIB_STDIO_H_SIGPIPE", "1", body="GL_GNULIB_STDIO_H_SIGPIPE=1"
        )
        + call(
            3, "gl_MODULE_INDICATOR_INIT_VARIABLE", "GNULIB_UNISTD_H_GETOPT", "0", body="GL_GNULIB_UNISTD_H_GETOPT=0"
        )
        + call(4, "gl_MODULE_INDICATOR_INIT_VARIABLE", "GNULIB_SIGNAL_H_SIGPIPE", body="GL_GNULIB_SIGNAL_H_SIGPIPE=0")
        + call(
            5,
            "gl_MODULE_INDICATOR_SET_VARIABLE",
            "GNULIB_UNISTD_H_NONBLOCKING",
            body="GL_GNULIB_UNISTD_H_NONBLOCKING=1",
        )
        + call(9, "AC_CHECK_FUNCS", "getopt_long")
        + ["if test $ac_cv_func_getopt_long = no; then"]
        + call(6, "gl_MODULE_INDICATOR_SET_VARIABLE", "GNULIB_UNISTD_H_GETOPT", body="GL_GNULIB_UNISTD_H_GETOPT=1")
        + ["fi"]
        + [marker("END", "id=1", "name=gl_INIT")]
    )
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState(flatten_files={"gnulib-comp.m4"})
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    assert state.indicator_substs == ["GNULIB_STDIO_H_SIGPIPE", "GNULIB_UNISTD_H_NONBLOCKING"]
    assert state.indicator_test_substs == {}
    getopt = next(c for c in target.checks if isinstance(c, Call) and c.args[:1] == ["GNULIB_UNISTD_H_GETOPT"])
    assert (getopt.fn, getopt.args, getopt.kwargs.get("requires")) == (
        "checks.AC_SUBST",
        ["GNULIB_UNISTD_H_GETOPT", 1],
        ["!ac_cv_func_getopt_long"],
    )
    assert list(translator.decisions.pending) == []


def test_program_shell_references() -> None:
    """Shell variables in a probe's program: a literal of the body is expanded, `$ac_includes_default`
    is the rules' constant, and `$srcdir` makes the probe a decision (a value defines SIZEOF_* too)."""

    def translate(decisions: Decisions) -> tuple[Translator, list[Call]]:
        lines = (
            [
                "#! /bin/sh",
                "FOO=7",
                # gnulib's stat-prog.m4: a multi-line string with the default includes inside
                'statvfs_includes="\\',
                "$ac_includes_default",
                "#include <sys/statvfs.h>",
                '"',
            ]
            + call(
                2,
                "AC_COMPILE_IFELSE",
                "AC_LANG_PROGRAM([[#define NAILS $FOO]], [[return NAILS;]])",
                "ok=yes",
                "ok=no",
                body="if ac_fn_c_try_compile; then :; fi",
            )
            + call(3, "AC_CHECK_SIZEOF", "mp_limb_t", "", '#include "$srcdir/gmp-h.in"')
            + call(4, "AC_CHECK_HEADER", "sys/mount.h", "", "", "$ac_includes_default\n#include <sys/param.h>")
            + call(5, "AC_CHECK_MEMBER", "struct statvfs.f_basetype", "", "", "$statvfs_includes")
        )
        tree = markers.parse("\n".join(lines) + "\n")
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"configure.ac"},
            m4_sources={},
            options=Options(),
            decisions=decisions,
            project=ProjectState(),
        )
        translator.translate()
        target = translator.translate_root("autoconf", loc_file="configure.ac")
        return translator, [c for c in target.checks if isinstance(c, Call)]

    translator, calls = translate(Decisions())
    probe = next(c for c in calls if c.fn == "checks.AC_TRY_COMPILE")
    assert as_call(probe.kwargs["code"]).args == [["#define NAILS 7"], ["return NAILS;"]]
    header = next(c for c in calls if c.fn == "checks.AC_CHECK_HEADER")
    assert header.kwargs["includes"] == [Raw("utils.AC_INCLUDES_DEFAULT"), "#include <sys/param.h>"]
    member = next(c for c in calls if c.fn == "checks.AC_CHECK_MEMBER")
    assert member.kwargs["includes"] == [Raw("utils.AC_INCLUDES_DEFAULT"), "#include <sys/statvfs.h>"]
    assert not any(c.fn == "checks.AC_CHECK_SIZEOF" for c in calls)
    assert list(translator.decisions.pending) == ["probe:ac_cv_sizeof_mp_limb_t"]
    request = translator.decisions.pending["probe:ac_cv_sizeof_mp_limb_t"]
    assert "$srcdir" in request.summary
    assert request.context["shell_variables"] == ["srcdir"]

    translator, calls = translate(Decisions({"probe:ac_cv_sizeof_mp_limb_t": 8}))
    assert list(translator.decisions.pending) == []
    assert [(c.fn, c.args) for c in calls if "mp_limb_t" in str(c.args) or "SIZEOF" in str(c.args)] == [
        ("checks.M4_VARIABLE", ["ac_cv_sizeof_mp_limb_t", 8]),
        ("checks.AC_DEFINE", ["SIZEOF_MP_LIMB_T", 8]),
    ]


def test_pkg_check_modules_verdict() -> None:
    """PKG_CHECK_MODULES as pkg_stub.m4 expands it: the converter picks the found action when the
    registry provides every package at a satisfying version (the variables substituted, the
    action's assignment folded in, the module registered as a dep of `$(X_LIBS)`), the
    not-found action otherwise; nothing is probed."""

    def pkg(start: int, var: str, specs: str, found: str, missing: str) -> list[str]:
        lines = [
            marker("BEGIN", f"id={start}", "name=PKG_CHECK_MODULES", "loc=rccac_m4/pkg.m4:20"),
            marker("ARG", f"id={start}", "n=1", var),
            marker("ARG", f"id={start}", "n=2", specs),
            marker("BEGIN", f"id={start + 1}", "name=RCCAC_PKG_FOUND", "loc=rccac_m4/pkg.m4:16"),
            marker("ARG", f"id={start + 1}", "n=1", var),
            marker("ARG", f"id={start + 1}", "n=2", specs),
            f'{var}_CFLAGS=""',
            f'{var}_LIBS=""',
            found,
        ]
        lines += call(start + 2, "AC_SUBST", f"{var}_CFLAGS") + call(start + 3, "AC_SUBST", f"{var}_LIBS")
        lines += [
            marker("END", f"id={start + 1}", "name=RCCAC_PKG_FOUND"),
            marker("BEGIN", f"id={start + 4}", "name=RCCAC_PKG_MISSING", "loc=rccac_m4/pkg.m4:17"),
            marker("ARG", f"id={start + 4}", "n=1", var),
            marker("ARG", f"id={start + 4}", "n=2", specs),
            missing,
            marker("END", f"id={start + 4}", "name=RCCAC_PKG_MISSING"),
            marker("END", f"id={start}", "name=PKG_CHECK_MODULES"),
        ]
        return lines

    lines = ["#! /bin/sh"]
    lines += pkg(2, "ZLIB", "zlib >= 1.2", 'ZLIB_CFLAGS="$ZLIB_CFLAGS -DHAVE_Z=1"', "have_z=no")
    lines += pkg(8, "GLIB", "glib-2.0 >= 2.0", "have_glib=yes", "have_glib=no")
    lines += ['if test "$have_glib" = no; then'] + call(14, "AC_DEFINE", "NO_GLIB", "1", "x") + ["fi"]
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = {(c.fn, c.args[0]): c for c in target.checks if isinstance(c, Call)}
    assert calls[("checks.AC_SUBST", "ZLIB_CFLAGS")].args[1] == "-DHAVE_Z=1"
    assert calls[("checks.AC_SUBST", "ZLIB_LIBS")].args[1] == ""
    assert ("checks.AC_SUBST", "GLIB_CFLAGS") not in calls  # glib-2.0 is not in the registry
    assert ("checks.AC_DEFINE", "NO_GLIB") in calls  # the not-found action's assignment held
    assert "requires" not in calls[("checks.AC_DEFINE", "NO_GLIB")].kwargs
    assert state.pkg_libs == {"ZLIB_LIBS": ["@zlib//:z"]}
    assert state.pkg_cc_deps == {"@zlib//:z"}
    assert state.external_modules == {"zlib": "1.3.2.bcr.2"}
    notes = "\n".join(translator.report.notes)
    assert "pkg-config [zlib >= 1.2]: the registry's zlib 1.3.2 provides it; taken as found" in notes
    assert "`library:.:zlib` IGNORE leaves it out" in notes
    assert "pkg-config [glib-2.0 >= 2.0]: no registry module provides glib-2.0; taken as not found" in notes

    # `library:.:zlib` IGNORE: the not-found action runs and nothing of zlib reaches the output.
    state = ProjectState()
    decisions = Decisions.from_text('DECISIONS = {"library:.:zlib": decision.IGNORE}')
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=decisions,
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    remaining = {(c.fn, c.args[0]) for c in target.checks if isinstance(c, Call)}
    assert ("checks.AC_SUBST", "ZLIB_CFLAGS") not in remaining
    assert (state.pkg_libs, state.pkg_cc_deps, state.external_modules) == ({}, set(), {})
    assert "pkg-config [zlib >= 1.2]: library:.:zlib is IGNORE; taken as not found" in "\n".join(
        translator.report.notes
    )


def test_lib_have_linkflags() -> None:
    """libssh2: gettext's AC_LIB_HAVE_LINKFLAGS is a link probe against the libraries, with
    HAVE_LIBSSL defined and the LIBSSL / LTLIBSSL words substituted; the registry's openssl
    is the dependency, as for AC_CHECK_LIB."""
    lines = [
        "#! /bin/sh",
        marker("BEGIN", "id=2", "name=AC_LIB_HAVE_LINKFLAGS", "loc=rccac_m4/lib-link.m4:40"),
        marker("ARG", "id=2", "n=1", "ssl"),
        marker("ARG", "id=2", "n=2", "crypto"),
        marker("ARG", "id=2", "n=3", "[#include <openssl/ssl.h>]"),
        marker("ARG", "id=2", "n=4", "[SSL_new(0)]"),
        "ac_cv_libssl=yes",
        marker("END", "id=2", "name=AC_LIB_HAVE_LINKFLAGS"),
    ]
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [c for c in target.checks if isinstance(c, Call)]
    assert [c.fn for c in calls] == ["checks.AC_TRY_LINK"] + ["checks.AC_SUBST"] * 3
    assert calls[0].kwargs["name"] == "ac_cv_libssl"
    assert calls[0].kwargs["linkopts"] == ["-lssl", "-lcrypto"]
    assert calls[0].kwargs["define"] == "HAVE_LIBSSL"
    assert "#include <openssl/ssl.h>" in as_str(calls[0].kwargs["code"])
    assert [c.args[0] for c in calls[1:]] == ["HAVE_LIBSSL", "LIBSSL", "LTLIBSSL"]
    assert calls[2].kwargs == {"condition": "ac_cv_libssl", "if_true": "-lssl -lcrypto", "if_false": ""}
    assert "openssl" in state.external_modules


def test_compiler_option_test_under_a_pushdef_alias() -> None:
    """nasm's PA_ADD_CFLAGS: `AC_CACHE_VAL([cache], ...)` (an AS_VAR_PUSHDEF alias the expansion spells
    `pa_cv_CFLAGS__Wall`) around an empty-program probe is a compiler-option test: no probe, no decision,
    and the define under `test "x$pa_cv_CFLAGS__Wall" = xyes` is left out (the toolchain owns the options)."""
    lines = [
        "#! /bin/sh",
        "_orig=$CFLAGS",
        marker("CALL", "id=2", "macro=AC_CACHE_VAL"),
        marker("ARG", "id=2", "n=1", "cache"),
        marker("ARG", "id=2", "n=2", "AS_VAR_APPEND([flags],[' -Wall'])"),
        "if test ${pa_cv_CFLAGS__Wall+y}",
        "then :",
        "else",
        "  as_fn_append CFLAGS ' -Wall'",
        "  pa_cv_CFLAGS__Wall=yes",
        *call(
            3,
            "AC_COMPILE_IFELSE",
            "AC_LANG_PROGRAM([[]],[[]])",
            "",
            "AS_VAR_SET([cache],[no])",
            body="if ac_fn_c_try_compile; then :; else pa_cv_CFLAGS__Wall=no; fi",
        ),
        "  CFLAGS=$_orig",
        "fi",
        marker("ENDCALL", "id=2", "macro=AC_CACHE_VAL"),
        'if test "x$pa_cv_CFLAGS__Wall" = xyes',
        "then :",
        *call(4, "AC_DEFINE", "CFLAGS_WALL", "1", "wall"),
        "fi",
    ]
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    assert [c.fn for c in target.checks if isinstance(c, Call)] == []
    assert list(translator.decisions.pending) == []
    assert any("compiler-option test" in n and "pa_cv_CFLAGS__Wall" in n for n in translator.report.notes), (
        translator.report.notes
    )


def test_pushdef_alias_in_probe_actions() -> None:
    """nasm's PA_HAVE_FUNC: `AC_CACHE_CHECK([for f], [cache], [AC_LINK_IFELSE(..., [AS_VAR_SET([cache],[yes])],
    [AS_VAR_SET([cache],[no])])])` is the canonical probe of the variable the alias stands for."""
    lines = [
        "#! /bin/sh",
        marker("CALL", "id=2", "macro=AC_CACHE_CHECK"),
        marker("ARG", "id=2", "n=1", "for __builtin_expect"),
        marker("ARG", "id=2", "n=2", "cache"),
        marker(
            "ARG",
            "id=2",
            "n=3",
            "AC_LINK_IFELSE([AC_LANG_PROGRAM([],[(void)__builtin_expect(1,1);])],"
            "[AS_VAR_SET([cache],[yes])],[AS_VAR_SET([cache],[no])])",
        ),
        "if test ${pa_cv_func___builtin_expect+y}",
        "then :",
        "else",
        *call(
            3,
            "AC_LINK_IFELSE",
            "AC_LANG_PROGRAM([],[(void)__builtin_expect(1,1);])",
            "AS_VAR_SET([cache],[yes])",
            "AS_VAR_SET([cache],[no])",
            body="if ac_fn_c_try_link; then pa_cv_func___builtin_expect=yes; else pa_cv_func___builtin_expect=no; fi",
        ),
        "fi",
        marker("ENDCALL", "id=2", "macro=AC_CACHE_CHECK"),
        'if test "x$pa_cv_func___builtin_expect" = xyes',
        "then :",
        *call(4, "AC_DEFINE", "HAVE___BUILTIN_EXPECT", "1", "expect"),
        "fi",
    ]
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [c for c in target.checks if isinstance(c, Call)]
    assert [c.fn for c in calls] == ["checks.AC_TRY_LINK", "checks.AC_DEFINE"]
    assert calls[0].kwargs["name"] == "pa_cv_func___builtin_expect"
    assert calls[1].kwargs.get("requires") == ["pa_cv_func___builtin_expect"]
    assert list(translator.decisions.pending) == []


def test_probe_arms_substituting_one_name() -> None:
    """mpc: an anonymous link probe whose two arms substitute one name to constants becomes
    one conditional AC_SUBST over the probe, not a decision."""
    lines = ["#! /bin/sh"] + call(
        2,
        "AC_LINK_IFELSE",
        "AC_LANG_PROGRAM([[#include <complex.h>]], [[return 0;]])",
        "AC_SUBST([MPC_HAVE_COMPLEX_H], [1])",
        "AC_SUBST([MPC_HAVE_COMPLEX_H], [0])",
    )
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    decisions = Decisions()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=decisions,
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [c for c in target.checks if isinstance(c, Call)]
    assert [c.fn for c in calls] == ["checks.AC_TRY_LINK", "checks.AC_SUBST"]
    probe_name = calls[0].kwargs["name"]
    assert calls[1].args == ["MPC_HAVE_COMPLEX_H"]
    assert calls[1].kwargs == {"condition": probe_name, "if_true": 1, "if_false": 0}
    assert decisions.pending == {}


def test_program_lookup_seeds_the_shell() -> None:
    """flex: AC_PATH_PROGS_FEATURE_CHECK inside AC_CACHE_CHECK, then AC_SUBST([M4], [$ac_cv_path_M4])
    and AC_DEFINE_UNQUOTED([M4], ["$M4"]): the plain program name stands in."""
    inner = "\n".join(call(3, "AC_PATH_PROGS_FEATURE_CHECK", "M4", "gm4 gnum4 m4", "test x = x", "false"))
    lines = (
        ["#! /bin/sh"]
        + call(2, "AC_CACHE_CHECK", "for m4", "ac_cv_path_M4", inner, body=inner)
        + call(4, "AC_SUBST", "M4", "$ac_cv_path_M4", body="M4=$ac_cv_path_M4")
        + call(5, "AC_DEFINE_UNQUOTED", "M4", '"$M4"', "Define to the m4 executable name.")
    )
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    main = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [(c.fn, c.args) for c in main.checks if isinstance(c, Call)]
    assert ("checks.AC_SUBST", ["M4", "m4"]) in calls, (calls, translator.report.notes)
    assert ("checks.AC_DEFINE", ["M4", '"m4"']) in calls, (calls, translator.report.notes)


def test_config_subdirs() -> None:
    """AC_CONFIG_SUBDIRS: literal directories are recorded, computed ones reported."""
    lines = ["#! /bin/sh"] + call(2, "AC_CONFIG_SUBDIRS", "libs utils $extra")
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    translator.translate_root("autoconf", loc_file="configure.ac")
    assert state.config_subdirs == ["libs", "utils"]
    assert any("$extra" in n for n in translator.report.notes)


def test_public_headers_and_implementation_deps(tmp_path: Path) -> None:
    """A library's hdrs are the headers automake installs; a dependency only the sources
    use is an implementation dep, one a public header includes stays a dep."""
    root = tmp_path
    (root / "Makefile.am").write_text("SUBDIRS = include lib\n")
    (root / "include").mkdir()
    (root / "include/Makefile.am").write_text("include_HEADERS = bar_api.h\n")
    (root / "include/bar_api.h").write_text("#include <stddef.h>\n")
    (root / "lib").mkdir()
    (root / "lib/Makefile.am").write_text(
        "lib_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\ninclude_HEADERS = bar.h\nnoinst_HEADERS = priv.h\n"
    )
    (root / "lib/a.c").write_text("")
    (root / "lib/priv.h").write_text("")
    for includes in ('#include "priv.h"\n', '#include "config.h"\n#include "priv.h"\n'):
        (root / "lib/bar.h").write_text(includes)
        package = Package(path="", docstring="")
        state = ProjectState()
        state.config_targets = ["config"]
        state.config_headers = ["config.h"]
        state.header_libraries = [":config_headers"]
        _programs_tree(package, root, Decisions(), state, [])
        rules = rules_by_name(package)
        bar = rules["lib/bar"]
        # The tree's one installed library also carries include/'s headers.
        assert bar.attrs["hdrs"] == ["include/bar_api.h", "lib/bar.h"]
        assert bar.attrs["includes"] == ["include", "lib"]
        # The non-installed headers: a private headers library (lib/bar.h excluded from its glob).
        private = rules["lib/bar_private_headers"]
        assert '"lib/bar.h"' in as_raw(private.attrs["hdrs"]).text
        assert private.attrs["visibility"] == ["//:__subpackages__"]
        # bar.h includes priv.h, so consumers need the private headers: a public dep.
        if "config.h" in includes:
            assert bar.attrs["deps"] == [":config_headers", ":lib/bar_private_headers"]
            assert "implementation_deps" not in bar.attrs
        else:
            assert bar.attrs["deps"] == [":lib/bar_private_headers"]
            assert bar.attrs["implementation_deps"] == [":config_headers"]


def test_language_standards() -> None:
    """AX_CXX_COMPILE_STDCXX / AC_PROG_CC_C99 / CFLAGS=-std=: the mode configure switches the compiler
    into is pinned on the package and recorded by a check that does not stop the build (autoconf's
    macros do not stop either).  A macro's pin is the first one's; the script's CFLAGS come later
    on the compile line and override it."""
    # The configure.ac top level (project mode): no DEFUN markers around the calls.
    lines = ["#! /bin/sh", 'CFLAGS="$CFLAGS -std=gnu11"']
    lines += call(2, "AX_CXX_COMPILE_STDCXX", "17", "noext", "mandatory")
    lines += call(3, "AC_PROG_CC_C99")
    tree = markers.parse("\n".join(lines) + "\n")
    state = ProjectState()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    main = translator.translate_root("autoconf", loc_file="configure.ac")
    assert state.c_standard == "gnu11"
    assert state.cxx_standard is None
    assert [(c.fn, c.args, c.kwargs) for c in main.checks if isinstance(c, Call)] == [
        ("checks.AX_CXX_COMPILE_STDCXX", ["17", "noext"], {}),
        ("checks.AC_LANG_STANDARD", ["c99"], {}),
    ]
    assert state.required_standards == {"c++17", "c99"}
    assert state.pinned_standards == {"C": "gnu99", "CXX": "c++17"}
    notes: list[str] = []
    _require_standards(main, state, notes)
    assert state.pinned_standards == {"C": "gnu11", "CXX": "c++17"}
    assert state.standard_origins["C"] == "CFLAGS: -std=gnu11"
    assert [c.args for c in main.checks if isinstance(c, Call) and c.fn == "checks.AC_LANG_STANDARD"] == [
        ["c99"],
        ["gnu11"],
    ]


def test_standard_macro_error_is_the_scripts_own() -> None:
    """`AC_PROG_CC_C99` then `if test "$ac_cv_prog_cc_c99" = no; then AC_MSG_ERROR(...)`: the macro's
    cache variable is the standard check's result, and the script's own abort, which the
    expansion spells as `as_fn_error` in the shell, is the one fatal part, carried as the check's
    `error` message; nothing is asked."""
    lines = (
        ["#! /bin/sh"]
        + call(1, "AC_PROG_CC_C99")
        + [
            'if test "$ac_cv_prog_cc_c99" = "no"; then',
            '  as_fn_error $? "a C99 compiler is required" "$LINENO" 5',
            "fi",
        ]
    )
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=ProjectState(),
    )
    translator.translate()
    main = translator.translate_root("autoconf", loc_file="configure.ac")
    assert [(c.fn, c.args, c.kwargs) for c in as_calls(main.checks)] == [
        ("checks.AC_LANG_STANDARD", ["c99"], {"error": "a C99 compiler is required"})
    ]
    assert translator.decisions.pending == {}


def test_repo_bazel_carries_the_module_defaults() -> None:
    """REPO.bazel: the license metadata as every package's default, and the pinned standard as the
    toolchain feature it will be, commented out with the rules_cc change it waits for; nothing
    when there is neither."""
    state = ProjectState()
    assert _repo_bazel(state, metadata=False) is None
    state.pinned_standards = {"C": "gnu99", "CXX": "gnu++17"}
    state.standard_origins = {"C": "AC_PROG_CC_C99", "CXX": "AX_CXX_COMPILE_STDCXX"}
    text = _repo_bazel(state, metadata=True)
    assert text is not None and text.startswith("# @gener" + "ated")
    assert 'default_package_metadata = ["//:package_metadata"],' in text
    assert '# features = ["gnu99", "gnu++17"],' in text
    assert "TODO(bazelbuild/rules_cc#936)" in text
    assert "features = " not in text.replace("# features", "")


def test_standard_flags_pin_every_target() -> None:
    """The pinned standard is one `C_STD_OPTS` select per package, appended to the `conlyopts` of
    every cc target and autoconf target (`/std:c11` on MSVC and clang-cl, `-std=gnu99` elsewhere),
    with the toolchain-feature follow-up noted."""
    package = Package(path="", docstring="")
    package.before.append(no_warnings_statement())
    package.after.append(Rule("cc_library", "src/x", {"copts": Raw(NO_WARNINGS)}))
    package.after.append(Rule("cc_binary", "tool", {"conlyopts": ["-fno-builtin"]}))
    package.targets.append(Target(name="autoconf"))
    state = ProjectState()
    state.pinned_standards = {"C": "gnu99"}
    state.standard_origins = {"C": "AC_PROG_CC_C99"}
    _standard_flags(package, state)
    assert [s.name for s in package.before if isinstance(s, Assign)] == [NO_WARNINGS, "C_STD_OPTS"]
    rules = rules_by_name(package)
    assert as_raw(rules["src/x"].attrs["conlyopts"]).text == "C_STD_OPTS"
    assert as_raw(rules["tool"].attrs["conlyopts"]).text == '["-fno-builtin"] + C_STD_OPTS'
    assert as_raw(package.targets[0].attrs["conlyopts"]).text == "C_STD_OPTS"
    text = render_statement(package.before[1])
    assert '"-std=gnu99"' in text and '"/std:c11"' in text and "TODO(bazelbuild/rules_cc#936)" in text


def test_subst_decision_drops_local_copy() -> None:
    """`subst:X: IGNORE` leaves out a substitution a dependency already provides."""
    lines = [
        "#! /bin/sh",
        marker("BEGIN", "id=1", "name=gl_W", "loc=m4/w.m4:1"),
        "GNULIBHEADERS_OVERRIDE_WINT_T=0\nOTHER=1",
    ]
    lines += call(2, "AC_SUBST", "GNULIBHEADERS_OVERRIDE_WINT_T")
    lines += call(3, "AC_SUBST", "OTHER")
    lines.append(marker("END", "id=1", "name=gl_W"))
    tree = markers.parse("\n".join(lines) + "\n")
    m4 = "AC_DEFUN([gl_W], [AC_SUBST([GNULIBHEADERS_OVERRIDE_WINT_T]) AC_SUBST([OTHER])])\n"

    def names(decisions: Decisions) -> list[str]:
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"m4/w.m4"},
            m4_sources={"m4/w.m4": m4},
            options=Options(),
            decisions=decisions,
        )
        (package,) = translator.translate()
        return [as_str(c.args[0]) for c in package.targets[0].checks if isinstance(c, Call)]

    assert names(Decisions()) == ["GNULIBHEADERS_OVERRIDE_WINT_T", "OTHER"]
    decided = Decisions.from_text('DECISIONS = {"subst:GNULIBHEADERS_OVERRIDE_WINT_T": IGNORE}')
    assert names(decided) == ["OTHER"]


def test_define_decision_drops_list_member() -> None:
    """`define:HAVE_NL_LANGINFO: IGNORE` drops nl_langinfo from AC_CHECK_FUNCS, as `check:` by its
    cache variable does: the rules' duplicate-producer error names the define."""
    lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_F", "loc=m4/f.m4:1")]
    lines += call(2, "AC_CHECK_FUNCS", "nl_langinfo setrlimit")
    lines.append(marker("END", "id=1", "name=gl_F"))
    tree = markers.parse("\n".join(lines) + "\n")
    m4 = "AC_DEFUN([gl_F], [AC_CHECK_FUNCS([nl_langinfo setrlimit])])\n"

    def subjects(decisions: Decisions) -> list[list[str]]:
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"m4/f.m4"},
            m4_sources={"m4/f.m4": m4},
            options=Options(),
            decisions=decisions,
        )
        (package,) = translator.translate()
        return [strings(c.args[0]) for c in package.targets[0].checks if isinstance(c, Call)]

    assert subjects(Decisions()) == [["nl_langinfo", "setrlimit"]]
    for text in ('{"define:HAVE_NL_LANGINFO": IGNORE}', '{"check:ac_cv_func_nl_langinfo": IGNORE}'):
        assert subjects(Decisions.from_text("DECISIONS = " + text)) == [["setrlimit"]], text


def test_builtin_without_port() -> None:
    """AC_HEADER_DIRENT has a fixed translation; AC_FUNC_MMAP is a decision."""
    lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_B", "loc=m4/b.m4:1")]
    lines += call(2, "AC_HEADER_DIRENT")
    lines += call(3, "AC_FUNC_MMAP")
    lines += call(4, "AC_PROG_SED")
    lines.append(marker("END", "id=1", "name=gl_B"))
    tree = markers.parse("\n".join(lines) + "\n")
    asked = Decisions()
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"m4/b.m4"},
        m4_sources={"m4/b.m4": "AC_DEFUN([gl_B], [AC_HEADER_DIRENT AC_FUNC_MMAP AC_PROG_SED])"},
        options=Options(),
        decisions=asked,
    )
    (package,) = translator.translate()
    (check,) = [c for c in package.targets[0].checks if isinstance(c, Call)]
    assert (check.fn, check.args) == ("macros.AC_CHECK_HEADERS", [["dirent.h"]])
    assert list(asked.pending) == ["macro:AC_FUNC_MMAP"]
    assert any("AC_PROG_SED" in n and "dropped" in n for n in translator.report.notes)


def test_project_helpers() -> None:
    assert default_tarname("GNU M4") == "m4"
    assert default_tarname("gzip") == "gzip"
    assert default_tarname("GNU Libtool Plus") == "libtool-plus"
    windows = df.HostOs(("mingw*", "windows*"))
    assert _platform_labels(windows) == ["@platforms//os:windows"]
    assert _platform_labels(df.disj(windows, df.HostOs(("darwin*",)))) == [
        "@platforms//os:windows",
        "@platforms//os:macos",
    ]
    assert _platform_labels(df.neg(windows)) is None  # holds on the default arm too
    assert _platform_labels(df.conj(windows, df.Truthy("gl_cv_x"))) is None
    assert _platform_labels(df.Truthy("gl_cv_x")) is None


def test_case_conditions() -> None:
    tree = markers.parse("#! /bin/sh\n")
    translator = Translator(tree, index=INDEX, selected_files=set(), m4_sources={}, options=Options())
    assert translator._test_condition('case "$host_os" in mingw* | windows*) true;; *) false;; esac') == df.HostOs(
        ("mingw*", "windows*")
    )
    assert translator._test_condition('case "$host_os" in darwin*) false ;; *) true ;; esac') == df.neg(
        df.HostOs(("darwin*",))
    )
    assert isinstance(translator._test_condition('case "$host_os" in darwin*) true ;; *) true ;; esac'), df.Unknown)


def test_never_set_cache_variable_is_empty_in_project_mode() -> None:
    """`if test "$am_cv_func_iconv" = yes` when nothing sets it: configure
    reads an empty variable, so the guarded check never applies (project
    mode); a single m4 file cannot know and keeps the requires."""
    lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_U", "loc=m4/u.m4:1")]
    lines.append('if test "$am_cv_func_iconv" = yes; then')
    lines += call(2, "AC_CHECK_FUNCS", "locale_charset")
    lines.append("fi")
    lines.append(marker("END", "id=1", "name=gl_U"))
    tree = markers.parse("\n".join(lines) + "\n")
    m4 = 'AC_DEFUN([gl_U], [if test "$am_cv_func_iconv" = yes; then AC_CHECK_FUNCS([locale_charset]) fi])'

    def checks(project: ProjectState | None) -> list[Call]:
        translator = Translator(
            tree,
            index=INDEX,
            selected_files={"m4/u.m4"},
            m4_sources={"m4/u.m4": m4},
            options=Options(layout="package" if project else "per-file"),
            project=project,
        )
        packages = translator.translate()
        if project:  # project mode: the project's macros expand into the configure.ac target
            target = translator.translate_root("autoconf", loc_file="configure.ac")
        else:
            (package,) = packages
            target = package.targets[0]
        return [c for c in target.checks if isinstance(c, Call)]

    assert checks(ProjectState()) == []
    (kept,) = checks(None)
    assert kept.kwargs.get("requires") == ["am_cv_func_iconv"]


def test_confdefs_segments_and_compile_defines() -> None:
    """A probe whose preamble tests HAVE_SYS_TIME_H needs that result from a
    dependency: the checks before it become an earlier target and the
    probe lists the define; declaration checks list the extension
    macros when AC_USE_SYSTEM_EXTENSIONS is a dependency."""
    main = Target(name="autoconf")
    main.deps.append(Dep("@rules_cc_autoconf//autoconf/macros/AC_USE_SYSTEM_EXTENSIONS"))
    main.checks.append(Call("macros.AC_CHECK_HEADERS", [["sys/time.h"]]))
    main.checks.append(
        Call(
            "checks.AC_TRY_COMPILE",
            [],
            {
                "name": "bash_cv_struct_timeval",
                "code": Call(
                    "utils.AC_LANG_PROGRAM",
                    [["#if HAVE_SYS_TIME_H", "#include <sys/time.h>", "#endif"], ["struct timeval x;"]],
                ),
            },
        )
    )
    main.checks.append(Call("macros.AC_CHECK_DECLS", [["execvpe"]]))
    earlier, extensions = _confdefs(main)
    assert extensions
    assert [t.name for t in earlier] == ["autoconf_1"]
    first = as_calls(earlier[0].checks)
    assert [c.fn for c in first] == ["macros.AC_CHECK_HEADERS"]
    probe, decls = as_calls(main.checks)
    assert as_raw(probe.kwargs["compile_defines"]).text == 'SYSTEM_EXTENSIONS + ["HAVE_SYS_TIME_H"]'
    assert as_raw(decls.kwargs["compile_defines"]).text == "SYSTEM_EXTENSIONS"
    assert "compile_defines" not in first[0].kwargs  # header existence: no feature macros
    assert [d.label for d in main.deps][-1] == ":autoconf_1"


def test_other_compiler_makefile_emits_nothing(tmp_path: Path) -> None:
    """A Makefile.am that sets CC to another compiler (grub-core's `CC=$(TARGET_CC)`) builds
    firmware, not host code: its programs and libraries are left out and reported."""
    text = (
        "CC=$(TARGET_CC)\nCPP=$(TARGET_CC)\nplatform_PROGRAMS = kernel.exec disk.module\n"
        "kernel_exec_SOURCES = kern/main.c\ndisk_module_SOURCES = lib/disk.c\n"
        "noinst_LIBRARIES = libgnu.a\nlibgnu_a_SOURCES = x.c\nnoinst_HEADERS = kern/x.h\n"
    )
    notes: list[str] = []
    mk = _parse_tree_makefile("grub-core", tmp_path / "Makefile.am", text, tmp_path, None, notes)
    assert mk.programs == {}
    assert mk.libraries == {}
    assert [h.path for h in mk.private_headers] == ["kern/x.h"]
    assert len(notes) == 1
    assert "grub-core/Makefile.am: CC = $(TARGET_CC): its 2 programs and 1 libraries" in notes[0]
    # The host side's `CC = @CC@`-style assignments do not count.
    host = _parse_tree_makefile(
        "", tmp_path / "Makefile.am", "CC = $(CC)\nbin_PROGRAMS = x\nx_SOURCES = x.c\n", None, None, notes
    )
    assert list(host.programs) == ["x"]


def test_flag_classes_and_link_order(tmp_path: Path) -> None:
    """Flag variables are expanded through the Makefile's own variables (grub's
    conf/Makefile.common classes), `-f` options are kept, a configure-only variable is
    reported; archives depend on the ones every LDADD lists after them; a registry library
    is an implementation dep unless a public header includes it."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "CPPFLAGS_DEFAULT = -I$(top_srcdir)/include -DGRUB_UTIL=1\n"
        "CFLAGS_POSIX = -fno-builtin\nCFLAGS_GCRY = -Wno-sign-compare $(CFLAGS_POSIX) -O2\n"
        "AM_CPPFLAGS = $(HOST_CPPFLAGS) $(CPPFLAGS_DEFAULT)\n"
        "noinst_LIBRARIES = libmods.a libkern.a libgcry.a\n"
        "libmods_a_SOURCES = mods.c\nlibkern_a_SOURCES = kern.c\nlibgcry_a_SOURCES = gcry.c\n"
        "libgcry_a_CPPFLAGS = $(AM_CPPFLAGS) $(CPPFLAGS_LIBRARY)\nlibgcry_a_CFLAGS = $(AM_CFLAGS) $(CFLAGS_GCRY)\n"
        "bin_PROGRAMS = tool\ntool_SOURCES = tool.c\ntool_LDADD = libmods.a libkern.a libgcry.a\n"
        "noinst_PROGRAMS = tests/t\ntests_t_SOURCES = tests/t.c\ntests_t_CPPFLAGS = -I$(top_srcdir)\n"
    )
    (root / "tests").mkdir()
    for name in ("mods.c", "kern.c", "gcry.c", "tool.c", "tests/t.c", "tests/shim.h"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.tarname = "grub"
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    gcry = rules["gcry"]
    assert gcry.attrs["includes"] == ["include"]
    assert gcry.attrs["local_defines"] == ["HAVE_CONFIG_H", "GRUB_UTIL=1"]
    assert as_raw(gcry.attrs["copts"]).text == 'NO_WARNINGS + ["-fno-builtin"]'  # not -Wno-..., not -O2
    assert [n for n in notes if "$(HOST_CPPFLAGS)" in n] == [
        (
            "./Makefile.am: $(HOST_CPPFLAGS) in the flag variables: a value only configure computes; "
            "its options are left out (a `variable:` decision gives the substitution, not these flags)"
        )
    ]
    # mods links before kern, kern before gcry: each depends on the next.

    def deps(rule: Rule) -> list[str]:
        linked: list[str] = strings(rule.attrs.get("deps")) + strings(rule.attrs.get("implementation_deps"))
        return linked

    assert ":kern" in deps(rules["mods"])
    assert ":gcry" in deps(rules["kern"])
    assert ":mods" not in deps(rules["gcry"])
    assert ":kern" not in deps(rules["gcry"])
    # A root program compiled with -I$(top_srcdir): its directory's headers come through a
    # library rooted at the repository, not through an `includes` entry of its own.
    t = rules["tests/t"]
    assert t.attrs["includes"] == ["include"]  # the Makefile's -I stays
    assert ":tests_headers" in strings(t.attrs["deps"])
    assert rules["tests_headers"].attrs["strip_include_prefix"] == "/"
    assert '"tests/**/*.h"' in as_raw(rules["tests_headers"].attrs["hdrs"]).text
    assert t.attrs["srcs"] == ["tests/t.c"]
    exports = {":config_headers": {"config.h"}}
    assert _split_deps([":config_headers", "@xz//:lzma"], exports, {"stdio.h"}) == (
        [],
        [":config_headers", "@xz//:lzma"],
    )
    assert _split_deps([":config_headers", "@xz//:lzma"], exports, {"lzma.h"}) == (["@xz//:lzma"], [":config_headers"])
    # The table's headers, where the label would not suggest them: a public header including
    # curses.h keeps ncurses a dep; without it, the library is an implementation detail.
    assert _split_deps(["@ncurses//:ncurses"], exports, {"curses.h"}) == (["@ncurses//:ncurses"], [])
    assert _split_deps(["@ncurses//:ncurses"], exports, {"stdio.h"}) == ([], ["@ncurses//:ncurses"])


def test_replace_lowers_to_nonzero_and_src_condition_override() -> None:
    """gnulib's REPLACE_* are zero or non-zero: `= 1` lowers to `!= 0` (its negation to
    `== 0`); other values and HAVE_* stay literal.  An unasked `src_condition:` key
    overrides a derived condition."""

    def eq(name: str, value: str) -> df.Eq:
        return df.Eq(name, value, by_value=True)

    assert lower.expression(eq("REPLACE_REALLOC_FOR_REALLOC_POSIX", "1")).expr == "REPLACE_REALLOC_FOR_REALLOC_POSIX!=0"
    assert lower.expression(df.neg(eq("REPLACE_FSTAT", "1"))).expr == "REPLACE_FSTAT==0"
    assert lower.expression(eq("ac_cv_subst_REPLACE_FCNTL", "1")).expr == "ac_cv_subst_REPLACE_FCNTL!=0"
    assert lower.expression(eq("REPLACE_X", "2")).expr == "REPLACE_X==2"
    assert lower.expression(eq("HAVE_GETDELIM", "1")).expr == "HAVE_GETDELIM==1"
    state = ProjectState()
    state.src_conditions = {"GL_COND_OBJ_REALLOC": [eq("REPLACE_REALLOC_FOR_REALLOC_POSIX", "1")]}
    sources = [Source("realloc.c", "GL_COND_OBJ_REALLOC", None)]
    notes: list[str] = []
    included, arms, probe, _probe_arms = _conditional_sources(
        sources, state, Decisions(), "lib/Makefile.am", "libgnu.a", "", "lib/", notes
    )
    assert (included, arms, probe) == ([], {}, {"lib/realloc.c": "REPLACE_REALLOC_FOR_REALLOC_POSIX!=0"})
    decided = Decisions.from_text('DECISIONS = {"src_condition:GL_COND_OBJ_REALLOC": ALWAYS}')
    included, arms, probe, _probe_arms = _conditional_sources(
        sources, state, decided, "lib/Makefile.am", "libgnu.a", "", "lib/", notes
    )
    assert ([s.path for s in included], arms, probe) == (["realloc.c"], {}, {})
    assert decided.unused == []  # the override counts as used


def test_negated_and_compound_automake_conditionals() -> None:
    """ppp's pppd/Makefile.am: crypto_ms.c under `if PPP_WITH_CHAPMS`, and in its `else` under
    `if WITH_SRP` (`!PPP_WITH_CHAPMS && WITH_SRP`, NEVER through WITH_SRP's decision); tls.c under
    `if PPP_WITH_EAPTLS` and `else if PPP_WITH_PEAP`: the union of the two options; text2qos.c
    under the `else` of a conditional over a decided variable: a probe-conditioned source."""
    decisions = Decisions.from_text(
        'DECISIONS = {"am_conditional:WITH_SRP": NEVER, "am_conditional:PPP_WITH_CHAPMS": ":chapms_enabled"}'
    )
    assert must(am_decision("!PPP_WITH_CHAPMS && WITH_SRP", decisions)).never
    assert must(am_decision("!WITH_SRP", decisions)).always
    assert must(am_decision("!PPP_WITH_CHAPMS", decisions)).arm == "!:chapms_enabled"
    sources = [
        Source("tls.c", "PPP_WITH_EAPTLS", None),
        Source("tls.c", "!PPP_WITH_EAPTLS && PPP_WITH_PEAP", None),
        Source("crypto_ms.c", "PPP_WITH_CHAPMS", None),
        Source("crypto_ms.c", "!PPP_WITH_CHAPMS && WITH_SRP", None),
    ]
    # crypto_ms.c too: under CHAPMS, else under SRP, is under CHAPMS or SRP.
    assert [s.condition for s in simplified_conditions(sources)] == [
        "PPP_WITH_EAPTLS",
        "PPP_WITH_PEAP",
        "PPP_WITH_CHAPMS",
        "WITH_SRP",
    ]
    state = ProjectState()
    state.am_conditionals = {"WITH_LIBATM": df.Eq("with_atm", "yes", by_value=True)}
    notes: list[str] = []
    included, arms, probe, _probe_arms = _conditional_sources(
        [Source("text2qos.c", "!WITH_LIBATM", None), *sources],
        state,
        decisions,
        "pppd/Makefile.am",
        "pppd",
        "",
        "pppd/",
        notes,
    )
    assert probe == {"pppd/text2qos.c": "!with_atm==yes"}
    assert arms == {":chapms_enabled": ["pppd/crypto_ms.c"]}
    assert included == []
    assert any("tls.c" in n for n in notes), notes  # EAPTLS / PEAP undecided here: left out, noted


def test_platform_arm_probe_sources_build_on_their_platform_alone() -> None:
    """libpcap's pcap-netfilter-linux.c under `host_os = linux*` and `ac_cv_netfilter_can_compile`:
    a probe-conditioned source of the Linux arm, whose `autoconf_srcs` target is `manual`.
    Elsewhere its condition names a check only the Linux arm of the autoconf target runs, and a
    wildcard build (`@libpcap//...` on macOS) would fail evaluating it; the owner reaches the
    target only in that arm of its srcs and deps.  Not `target_compatible_with`, which would
    spread to a consumer listing it unconditionally.  The platform-free target is a plain one."""
    state = ProjectState()
    state.src_conditions = {
        "NETFILTER": [df.conj(df.HostOs(("linux*",)), df.Truthy("ac_cv_netfilter_can_compile"))],
        "HAVE_DAG": [df.Truthy("ac_cv_header_dagapi_h")],
    }
    state.produced_results = {"ac_cv_netfilter_can_compile", "ac_cv_header_dagapi_h"}
    notes: list[str] = []
    included, arms, probe, probe_arms = _conditional_sources(
        [Source("pcap-netfilter-linux.c", "NETFILTER", None), Source("pcap-dag.c", "HAVE_DAG", None)],
        state,
        Decisions(),
        "Makefile.am",
        "libpcap.a",
        "",
        "",
        notes,
    )
    assert (included, arms, probe) == ([], {}, {"pcap-dag.c": "ac_cv_header_dagapi_h"})
    assert probe_arms == {"@platforms//os:linux": {"pcap-netfilter-linux.c": "ac_cv_netfilter_can_compile"}}
    package = Package(path="", docstring="")
    label, arm_labels = _probe_conditioned_srcs(package, "pcap", probe, probe_arms)
    assert (label, arm_labels) == (":pcap_conditional_srcs", {"@platforms//os:linux": ":pcap_conditional_srcs_linux"})
    rules = rules_by_name(package)
    assert "tags" not in rules["pcap_conditional_srcs"].attrs
    assert rules["pcap_conditional_srcs_linux"].attrs["tags"] == ["manual"]
    assert "target_compatible_with" not in rules["pcap_conditional_srcs_linux"].attrs


def test_header_globs_start_at_a_known_directory(tmp_path: Path) -> None:
    """A recursive header glob is fine under a named directory (`lib/**/*.h`), never from the
    root: `**/*.h` walks the whole tree and, with the module as the root workspace (readline's
    presubmit), follows the bazel-* convenience symlinks into bazel-out.  The root becomes
    `*.h` plus one `<top>/**/*.h` per top-level directory holding a header; directories without
    one, .git, bazel-* and symlinks are left out.  `.in.h` gives a gnulib library's exclude."""
    for path in ("pppd/pppd.h", "include/net/ppp.h", "include/doc.txt", "scripts/run.sh", ".git/x.h", "bazel-out/y.h"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("")
    (tmp_path / "config.h").write_text("")
    (tmp_path / "linked").symlink_to(tmp_path / "pppd")
    assert header_patterns(tmp_path, ["."]) == ["*.h", "include/**/*.h", "pppd/**/*.h"]
    assert header_patterns(tmp_path, ["pppd", "include/"]) == ["pppd/**/*.h", "include/**/*.h"]
    assert header_patterns(tmp_path, ["lib/"], ".in.h") == ["lib/**/*.in.h"]
    assert "exclude" not in headers_glob(header_patterns(tmp_path, ["."])).text


def test_gnulib_else_branch_source() -> None:
    """gnulib.mk's `if GL_COND_OBJ_X ... else ... endif` (m4's localename-unsafe.c): the else
    branch's source compiles under the negation of the conditional."""
    state = ProjectState(port_probes={"HAVE_X": "ac_cv_func_x"})
    state.src_conditions = {"GL_COND_OBJ_LIMITED": [df.Eq("HAVE_X", "0", by_value=True)]}
    negated = lower.expression(_gnulib_names(state)(df.neg(df.disj(*state.src_conditions["GL_COND_OBJ_LIMITED"]))))
    assert negated.expr == "ac_cv_func_x"


def test_option_gated_program(tmp_path: Path) -> None:
    """A program whose every source sits under one conditional decided as a config setting
    exists only when the setting is on: `target_compatible_with`, not a select() with an
    empty default arm (grub-mount under COND_GRUB_MOUNT)."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "bin_PROGRAMS = always\nalways_SOURCES = always.c\n"
        "if COND_MOUNT\nbin_PROGRAMS += mount\nendif\nmount_SOURCES = mount.c fuse.c\n"
    )
    for name in ("always.c", "mount.c", "fuse.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.tarname = "grub"
    decisions = Decisions.from_text('DECISIONS = {"am_conditional:COND_MOUNT": ":mount_enabled"}')
    notes: list[str] = []
    _programs_tree(package, root, decisions, state, notes)
    rules = rules_by_name(package)
    mount = rules["mount"]
    assert mount.attrs["srcs"] == ["fuse.c", "mount.c"]
    assert '":mount_enabled": []' in as_raw(mount.attrs["target_compatible_with"]).text
    assert '"//conditions:default": ["@platforms//:incompatible"]' in as_raw(mount.attrs["target_compatible_with"]).text
    assert "target_compatible_with" not in rules["always"].attrs
    assert rules["always"].attrs["srcs"] == ["always.c"]


def test_root_includes() -> None:
    """`includes = ["."]` goes from every root target (gcc would drop the repository root from the
    quote chain); a root library with headers gets `strip_include_prefix = "/"` (Bazel 9's virtual
    include directory) with `include_prefix = "."` (Bazel 7 and 8 build that directory for the
    generated config.h only with a prefix); a library with a prefix of its own keeps it."""
    package = Package(path="", docstring="")
    package.after += [
        Rule("cc_library", "config_headers", {"hdrs": [":config"], "includes": [".", "lib"]}),
        Rule("cc_library", "prefixed", {"hdrs": ["x.h"], "includes": ["."], "include_prefix": "foo"}),
        Rule("cc_binary", "tool", {"srcs": ["t.c"], "includes": ["."]}),
        Rule("cc_library", "lib/bar", {"srcs": ["lib/a.c"], "includes": ["lib"]}),
        # pkgconf: tests include <libpkgconf/stdinc.h>, which -I$(top_srcdir) served.
        Rule("cc_library", "pkgconf", {"hdrs": ["libpkgconf/libpkgconf.h"], "includes": ["libpkgconf"]}),
    ]
    _root_includes(package)
    attrs = {name: rule.attrs for name, rule in rules_by_name(package).items()}
    assert attrs["config_headers"] == {
        "hdrs": [":config"],
        "includes": ["lib"],
        "include_prefix": ".",
        "strip_include_prefix": "/",
    }
    assert attrs["prefixed"] == {"hdrs": ["x.h"], "include_prefix": "foo"}  # its own prefix stands
    assert attrs["tool"] == {"srcs": ["t.c"]}
    assert attrs["lib/bar"] == {"srcs": ["lib/a.c"], "includes": ["lib"]}
    assert attrs["pkgconf"] == {
        "hdrs": ["libpkgconf/libpkgconf.h"],
        "includes": ["libpkgconf"],
        "include_prefix": ".",
        "strip_include_prefix": "/",
    }


def test_library_under_automake_conditional(tmp_path: Path) -> None:
    """teckit: `lib_LTLIBRARIES` differs between the arms of `if OLD_LIB_NAMES`; the arm the
    am_conditional decision selects is built, `+=` sources included, the other left out."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "if OLD_LIB_NAMES\nlib_LTLIBRARIES = libold.la\nlibold_la_SOURCES = old.c\nelse\n"
        "lib_LTLIBRARIES = libnew.la\nlibnew_la_SOURCES = new.c\nlibnew_la_SOURCES += extra.c\nendif\n"
    )
    for name in ("old.c", "new.c", "extra.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.header_libraries = [":config_headers"]
    notes: list[str] = []
    _programs_tree(package, root, Decisions({"am_conditional:OLD_LIB_NAMES": Word("NEVER")}), state, notes)
    rules = rules_by_name(package)
    assert "old" not in rules
    assert rules["new"].attrs["srcs"] == ["extra.c", "new.c"]
    assert any("libold.la left out" in n for n in notes), notes


def _substituted_program_state(name: str) -> ProjectState:
    """A ProjectState in which configure lists `name` in @OPTIONAL_BIN_PROGS@ under a probe."""
    state = ProjectState()
    state.header_libraries = [":config_headers"]
    # Keyed by the automake variable configure assigns (coreutils' `bin_PROGRAMS=...`); the names are
    # bare, as configure spells them (`hostid`), whatever directory the Makefile puts them in.
    state.program_lists = {
        "bin_PROGRAMS": [
            ListedProgram(name, df.Eq("ac_cv_func_gethostid", "yes"), "AC_CHECK_FUNCS([gethostid]) action-if-found")
        ]
    }
    return state


def test_substituted_program_decided_by_its_makefile_path(tmp_path: Path) -> None:
    """coreutils: `bin_PROGRAMS = @OPTIONAL_BIN_PROGS@` with `EXTRA_PROGRAMS = src/hostid` is tagged
    manual under its probe, and the report names the key that decides it; `program:src/hostid`
    (the path the Makefile spells) and the older bare `program:hostid` both decide it."""
    root = tmp_path
    (root / "src").mkdir()
    (root / "Makefile.am").write_text(
        "bin_PROGRAMS = @OPTIONAL_BIN_PROGS@\nEXTRA_PROGRAMS = src/hostid\nsrc_hostid_SOURCES = src/hostid.c\n"
    )
    (root / "src/hostid.c").write_text("")

    def convert(decisions: Decisions) -> tuple[dict[str, Rule], ProjectState, list[str]]:
        package = Package(path="", docstring="")
        state = _substituted_program_state("hostid")
        notes: list[str] = []
        _programs_tree(package, root, decisions, state, notes)
        return rules_by_name(package), state, notes

    rules, state, _ = convert(Decisions())
    assert rules["src/hostid"].attrs["tags"] == ["manual"]
    assert any("decide program:src/hostid" in c for c in rules["src/hostid"].comments), rules["src/hostid"].comments
    assert [name for name, _why in state.manual_programs] == ["src/hostid"]
    # The report's key is the one the lookup reads.
    report = _manual_programs_section(type("Result", (), {"state": state, "manual_package": None})())
    assert "`program:src/hostid`" in "\n".join(report)
    for key in ("program:src/hostid", "program:hostid"):
        rules, state, _ = convert(Decisions({key: Word("ALWAYS")}))
        assert "tags" not in rules["src/hostid"].attrs, key
        assert state.manual_programs == [], key
    rules, _, notes = convert(Decisions({"program:src/hostid": Word("NEVER")}))
    assert "src/hostid" not in rules
    assert any("program:src/hostid is NEVER" in n for n in notes), notes
    # MANUAL: the considered answer that the condition is not expressible; the same shape as undecided.
    rules, state, _ = convert(Decisions({"program:src/hostid": Word("MANUAL")}))
    assert rules["src/hostid"].attrs["tags"] == ["manual"]
    assert state.manual_programs == [("src/hostid", "program:src/hostid is MANUAL")]
    rules, _, _ = convert(Decisions({"program:src/hostid": ["@platforms//os:linux"]}))
    assert "target_compatible_with" in rules["src/hostid"].attrs


def test_substituted_program_of_a_sub_makefile(tmp_path: Path) -> None:
    """A program of `src/Makefile.am` (`EXTRA_PROGRAMS = hostid`): the key the report prints must
    be the key the lookup reads, so a reviewer can copy it from the report into decisions.bzl."""
    root = tmp_path
    (root / "src").mkdir()
    (root / "Makefile.am").write_text("SUBDIRS = src\n")
    (root / "src/Makefile.am").write_text(
        "bin_PROGRAMS = @OPTIONAL_BIN_PROGS@\nEXTRA_PROGRAMS = hostid\nhostid_SOURCES = hostid.c\n"
    )
    (root / "src/hostid.c").write_text("")

    def convert(decisions: Decisions) -> tuple[dict[str, Rule], ProjectState]:
        package = Package(path="", docstring="")
        state = _substituted_program_state("hostid")
        notes: list[str] = []
        _programs_tree(package, root, decisions, state, notes)
        return rules_by_name(package), state

    rules, state = convert(Decisions())
    (rule,) = [r for name, r in rules.items() if name.endswith("hostid") and "tags" in r.attrs]
    assert rule.attrs["tags"] == ["manual"]
    reported = [
        line
        for line in _manual_programs_section(type("Result", (), {"state": state, "manual_package": None})())
        if line.startswith("- `")
    ]
    assert len(reported) == 1, reported
    key = reported[0].rsplit("`", 2)[-2]  # the `program:...` the report tells the reader to decide
    assert key.startswith("program:"), key
    rules, state = convert(Decisions({key: Word("ALWAYS")}))
    assert state.manual_programs == [], f"{key} from the report does not decide the program"
    assert not any("tags" in r.attrs for r in rules.values()), key


def test_sources_and_includes_under_else(tmp_path: Path) -> None:
    """make: `if WINDOWSENV` adds the w32 sources and `-I src/w32/include`, `else` adds
    src/posixos.c; the conditional lowers to the Windows arm, so the else branch is every
    other platform and the include directory is a select() arm of includes."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "bin_PROGRAMS = make\nmake_SOURCES = src/main.c\nif WINDOWSENV\n  make_SOURCES += src/w32/w32os.c\n"
        "  AM_CPPFLAGS += -I $(top_srcdir)/src/w32/include\nelse\n  make_SOURCES += src/posixos.c\nendif\n"
    )
    (root / "src/w32/include").mkdir(parents=True)
    for name in ("src/main.c", "src/w32/w32os.c", "src/posixos.c", "src/w32/include/w32err.h"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.header_libraries = [":config_headers"]
    state.am_conditionals = {"WINDOWSENV": df.HostOs(("mingw32",))}
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    make = next(r for r in package.after if isinstance(r, Rule) and r.name == "make")
    srcs = as_raw(make.attrs["srcs"]).text
    assert '"@platforms//os:windows": ["src/w32/w32os.c"]' in srcs.replace("\n", "").replace("  ", "").replace(
        ",]", "]"
    )
    assert '"//conditions:default": ["src/posixos.c"]' in srcs.replace("\n", "").replace("  ", "").replace(",]", "]")
    includes = as_raw(make.attrs["includes"]).text.replace("\n", "").replace("  ", "").replace(",]", "]")
    assert '"@platforms//os:windows": ["src/w32/include"]' in includes
    assert '"//conditions:default": []' in includes


def test_root_relative_sources(tmp_path: Path) -> None:
    """teckit: `@top_builddir@/source/x.cpp` and `$(top_srcdir)/...` in a subdirectory's
    `_SOURCES` and `_LDADD` name files of the tree, not generated files of the directory."""
    root = tmp_path
    (root / "Makefile.am").write_text("SUBDIRS = source bin\n")
    (root / "source").mkdir()
    (root / "source/Makefile.am").write_text(
        "noinst_LIBRARIES = libengine.a\nlibengine_a_SOURCES = @top_builddir@/source/Engine.cpp "
        "$(top_srcdir)/source/Compiler.cpp\n"
    )
    (root / "bin").mkdir()
    (root / "bin/Makefile.am").write_text(
        "bin_PROGRAMS = teckit_compile\nteckit_compile_SOURCES = @top_builddir@/source/Sample-tools/Compile.cpp\n"
        "teckit_compile_LDADD = @top_builddir@/source/libengine.a\n"
    )
    (root / "source/Sample-tools").mkdir()
    for name in ("source/Engine.cpp", "source/Compiler.cpp", "source/Sample-tools/Compile.cpp"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.header_libraries = [":config_headers"]
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    assert rules["source/engine"].attrs["srcs"] == ["source/Compiler.cpp", "source/Engine.cpp"]
    assert rules["bin/teckit_compile"].attrs["srcs"] == ["source/Sample-tools/Compile.cpp"]
    assert ":source/engine" in strings(rules["bin/teckit_compile"].attrs["deps"])
    assert not any("generated" in n for n in notes), notes


def test_automake_tree_and_programs(tmp_path: Path) -> None:
    """Programs in SUBDIRS get a cc_binary; LDADD names the library."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "SUBDIRS = lib src tests\nnoinst_LIBRARIES = libver.a libfoo.a\nlibver_a_SOURCES = version.c\n"
        "libver_a_LIBADD = $(LIBINTL)\nlibfoo_a_SOURCES = foo.c\nbin_PROGRAMS = foo\nfoo_SOURCES = cli.c\n"
    )
    (root / "lib").mkdir()
    (root / "lib/Makefile.am").write_text("noinst_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\n")
    (root / "src").mkdir()
    for name in ("version.c", "foo.c", "cli.c", "lib/a.c", "src/main.c", "src/util.c", "src/parse.y", "src/scan.l"):
        (root / name).write_text("")
    (root / "src/Makefile.am").write_text(
        "bin_PROGRAMS = foo\nAM_YFLAGS = -d\nfoo_SOURCES = main.c util.c parse.y scan.l\n"
        "pkgdatadir = $(datadir)/awk\nDEFPATH='\".$(PATH_SEPARATOR)$(pkgdatadir)\"'\n"
        'DEFLIBPATH="\\"$(pkgextensiondir)\\""\n'
        "DEFS = -DGAWK -DHAVE_CONFIG_H -DDEFPATH=$(DEFPATH) -DDEFLIBPATH=$(DEFLIBPATH) -DSHLIBEXT=$(SHLIBEXT) "
        '-DLOCALEDIR=\'"$(datadir)/locale"\' -I"$(srcdir)/.." -I$(top_srcdir)/lib\n'
        "LDADD = ../lib/libbar.a ../libver.a $(LIBINTL) -lm\n"
        "$(srcdir)/parse.c: parse.y\n\t$(YACC) -o $@ -p zz $<\n"
    )
    (root / "tests").mkdir()
    (root / "tests/Makefile.am").write_text("check_PROGRAMS = t\nt_SOURCES = t.c\n")
    assert [d for d, _, _ in _automake_tree(root)] == ["", "lib", "src"]
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.tarname = "gawk"
    state.literal_substs = {"pkgextensiondir": "${pkglibdir}"}
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    # libfoo.a is `foo` so that the output is libfoo.a; at the top level a
    # program takes that name, so the library is `lib/foo` with the archive
    # name as an alias. Without a conflict (libver.a) there is no alias.
    assert rules["lib/foo"].kind == "cc_library"
    assert rules["lib/foo"].attrs["srcs"] == ["foo.c"]
    assert rules["libfoo"].attrs["actual"] == ":lib/foo"
    assert rules["foo"].kind == "cc_binary"
    assert rules["ver"].attrs["srcs"] == ["version.c"]
    # No installed headers: every dependency is an implementation detail.
    assert rules["ver"].attrs["implementation_deps"] == [":config_headers", ":linkopts"]
    assert "deps" not in rules["ver"].attrs
    assert "hdrs" not in rules["ver"].attrs
    assert rules["lib/bar"].attrs["includes"] == ["lib"]
    assert rules["src/headers"].attrs["deps"] == [":config_headers"]
    assert "libver" not in rules
    # yacc and lex sources become bison()/flex() rules whose outputs the
    # program compiles; the hand-written rule's -p zz carries over, -d not.
    assert rules["src/bison/parse"].load_from == "@bison//:bison.bzl"
    assert rules["src/bison/parse"].attrs == {
        "srcs": ["src/parse.y"],
        "outs": ["src/bison/parse.c", "src/bison/parse.h"],
        "args": [
            "-o",
            "$(execpath src/bison/parse.c)",
            "--defines=$(execpath src/bison/parse.h)",
            "-Wnone",
            "-p",
            "zz",
            "$(execpath src/parse.y)",
        ],
    }
    assert rules["src/flex/scan"].attrs == {
        "srcs": ["src/scan.l"],
        "outs": ["src/flex/scan.c", "src/flex/scan.h"],
        "args": [
            "-o",
            "$(execpath src/flex/scan.c)",
            "--header-file=$(execpath src/flex/scan.h)",
            "--nowarn",
            "$(execpath src/scan.l)",
        ],
    }
    foo = rules["src/foo"]
    assert foo.attrs["srcs"] == [
        "src/bison/parse.c",
        "src/bison/parse.h",
        "src/flex/scan.c",
        "src/flex/scan.h",
        "src/main.c",
        "src/util.c",
    ]
    assert as_raw(foo.attrs["copts"]).text == "NO_WARNINGS"
    # -I of CPPFLAGS and the generated grammar / scanner directories: package-relative
    # `includes` (Bazel adds both the source and the generated root of each).
    # `-I"$(srcdir)/.."` from src/ is the tree root, which a subdirectory's target reaches
    # through the repository-path headers library rather than `includes = ["."]`.
    assert foo.attrs["includes"] == ["lib", "src/bison", "src/flex"]
    assert state.makefile_standards == {}
    assert foo.attrs["local_defines"] == [
        "HAVE_CONFIG_H",
        "GAWK",
        'DEFPATH="\\".:/usr/local/share/awk\\""',
        'DEFLIBPATH="\\"/usr/local/lib/gawk\\""',
        'LOCALEDIR="\\"/usr/local/share/locale\\""',
    ]
    assert any("SHLIBEXT" in n and "left out" in n for n in notes)
    assert foo.attrs["deps"] == [":lib/bar", ":linkopts", ":src/headers", ":top_srcdir_headers", ":ver"]
    assert rules["top_srcdir_headers"].attrs["strip_include_prefix"] == "/"
    assert foo.attrs["linkopts"] == ["-lm"]
    assert rules["linkopts"].attrs["vars"] == ["LIBINTL"]


def test_shared_libraries_and_aliases(tmp_path: Path) -> None:
    """Installed libtool libraries get a cc_shared_library; subdirectory targets a root alias.  A
    library whose LDFLAGS lack `-no-undefined` may reference symbols its users provide, as
    libtool allows: its shared library links with `-undefined dynamic_lookup` on macOS and is
    not built on Windows; one with `-no-undefined` carries neither."""
    root = tmp_path
    (root / "Makefile.am").write_text("SUBDIRS = lib src ext\n")
    for sub, text in (
        (
            "lib",
            (
                "lib_LTLIBRARIES = libbar.la libwhole.la\nlibbar_la_SOURCES = a.c\n"
                "libwhole_la_SOURCES = w.c\nlibwhole_la_LDFLAGS = -version-info 1:0:0 -no-undefined\n"
            ),
        ),
        ("src", "bin_PROGRAMS = foo\nfoo_SOURCES = main.c\n"),
        (
            "ext",
            (
                "pkgext_LTLIBRARIES = plug.la\nplug_la_SOURCES = p.c\nplug_la_LDFLAGS = -module\n"
                "noinst_LTLIBRARIES = libhelper.la\nlibhelper_la_SOURCES = h.c\n"
            ),
        ),
    ):
        (root / sub).mkdir()
        (root / sub / "Makefile.am").write_text(text)
    for name in ("lib/a.c", "lib/w.c", "src/main.c", "ext/p.c", "ext/h.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    _programs_tree(package, root, Decisions(), state, [])
    rules = rules_by_name(package)
    shared = rules["lib/bar_shared"]
    assert shared.kind == "cc_shared_library"
    assert shared.attrs["deps"] == [":lib/bar"]
    assert '"@platforms//os:macos": ["-Wl,-undefined,dynamic_lookup"]' in as_raw(shared.attrs["user_link_flags"]).text
    assert (
        '"@platforms//os:windows": ["@platforms//:incompatible"]' in as_raw(shared.attrs["target_compatible_with"]).text
    )
    whole = rules["lib/whole_shared"]
    assert "user_link_flags" not in whole.attrs and "target_compatible_with" not in whole.attrs
    assert '"lib/libbar.dylib"' in as_raw(shared.attrs["shared_lib_name"]).text
    assert '"@platforms//os:windows": "lib/bar.dll"' in as_raw(shared.attrs["shared_lib_name"]).text
    assert '"ext/plug.so"' in as_raw(rules["ext/plug_shared"].attrs["shared_lib_name"]).text
    assert "ext/helper_shared" not in rules  # noinst_: a convenience library
    assert rules["foo"].attrs["actual"] == ":src/foo"
    assert rules["bar"].attrs["actual"] == ":lib/bar"
    assert rules["plug"].attrs["actual"] == ":ext/plug"
    assert "headers" not in rules


def test_project_decisions(tmp_path: Path) -> None:
    """What a Makefile.am leaves to the user: conditional library sources, files no
    translatable recipe writes, -D values only configure knows, odd LDADD items."""
    root = tmp_path
    (root / "Makefile.am").write_text("SUBDIRS = lib src\n")
    (root / "lib").mkdir()
    (root / "lib/Makefile.am").write_text(
        "noinst_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\nif WITH_X\nlibbar_a_SOURCES += x.c\nendif\n"
    )
    (root / "src").mkdir()
    (root / "src/Makefile.am").write_text(
        "bin_PROGRAMS = foo\nfoo_SOURCES = main.c gen.c\nBUILT_SOURCES = tab.h\n"
        "DEFS = -DSHLIBEXT=$(SHLIBEXT) -DHAVE_CONFIG_H -std=gnu99\nLDADD = ../lib/libbar.a ../other/libz.a\n"
        "gen.c: gen.sh\n\t./gen.sh > $@\n"
    )
    for name in ("lib/a.c", "lib/x.c", "src/main.c", "src/gen.sh"):
        (root / name).write_text("")

    states: list[ProjectState] = []

    def convert(decisions: Decisions) -> tuple[Package, dict[str, Rule], list[str]]:
        package = Package(path="", docstring="")
        state = ProjectState()
        state.config_targets = ["config"]
        state.header_libraries = [":config_headers"]
        notes: list[str] = []
        _programs_tree(package, root, decisions, state, notes)
        states.append(state)
        return package, rules_by_name(package), notes

    asked = Decisions()
    package, rules, _notes = convert(asked)
    assert sorted(asked.pending) == [
        "am_conditional:WITH_X",
        "cppflag:src:SHLIBEXT",
        "generated:src/gen.c",
        "generated:src/tab.h",
        "link:src/foo:../other/libz.a",
    ]
    # lib/ has no header that is not installed: no private headers library.
    assert rules["lib/bar"].attrs["srcs"] == ["lib/a.c"]
    assert "lib/bar_private_headers" not in rules
    assert rules["src/foo"].attrs["srcs"] == ["src/main.c"]  # gen.c, tab.h: undecided, left out
    assert rules["src/foo"].attrs["local_defines"] == ["HAVE_CONFIG_H"]
    assert rules["src/foo"].attrs["deps"] == [":lib/bar", ":src/headers"]

    decided = Decisions.from_text("""DECISIONS = {
    "am_conditional:WITH_X": ALWAYS,
    "cppflag:src:SHLIBEXT": "\\"so\\"",
    "generated:src/gen.c": genrule(name = "gen_c", srcs = ["src/gen.sh"], outs = ["src/gen.c"], cmd = "sh $< > $@"),
    "generated:src/tab.h": IGNORE,
    "link:src/foo:../other/libz.a": "@other//:z",
}""")
    package, rules, _notes = convert(decided)
    assert "lib/x.c" in strings(rules["lib/bar"].attrs["srcs"])  # the decided source
    assert rules["src/foo"].attrs["srcs"] == ["src/gen.c", "src/main.c"]
    assert rules["src/foo"].attrs["local_defines"] == ["HAVE_CONFIG_H", 'SHLIBEXT="\\"so\\""']
    # A `-std=` of the Makefile's flags is not pinned on the target: it is recorded, and the
    # main target checks for it (_require_standards).
    assert as_raw(rules["src/foo"].attrs["copts"]).text == "NO_WARNINGS"
    assert "conlyopts" not in rules["src/foo"].attrs
    assert states[-1].makefile_standards == {"C": "gnu99"}
    assert rules["src/foo"].attrs["deps"] == [":lib/bar", ":src/headers", "@other//:z"]
    assert any(isinstance(s, Raw) and s.text.startswith("genrule(") for s in package.after)
    assert decided.pending == {}


def test_written_sources(tmp_path: Path) -> None:
    """A source a Makefile.am writes with printf becomes a write_file, through an
    autoconf_hdr(mode = "subst") when it carries configure's values."""
    assert _written_content(
        ["$(AM_V_GEN)rm -f $@", "printf 'a\\n' > $@t", "printf 'v = \"$(PACKAGE_VERSION)\";\\n' >> $@t", "mv $@t $@"]
    ) == ["a", "v = @PACKAGE_VERSION@;", ""]
    assert _written_content(["sed 's/x/y/' < $< > $@"]) is None
    root = tmp_path
    (root / "Makefile.am").write_text(
        "noinst_LIBRARIES = libver.a\nnodist_libver_a_SOURCES = version.c version.h\n"
        "version.c: Makefile\n\tprintf '#include \"version.h\"\\n' > $@t\n"
        "\tprintf 'char const *Version = \"$(PACKAGE_VERSION)\";\\n' >> $@t\n\tmv $@t $@\n"
        "version.h: Makefile\n\tprintf 'extern char const *Version;\\n' > $@t\n\tmv $@t $@\n"
    )
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    assert rules["version_c_in"].kind == "write_file"
    assert rules["version_c_in"].attrs["content"] == [
        '#include "version.h"',
        "char const *Version = @PACKAGE_VERSION@;",
        "",
    ]
    assert rules["version_c"].attrs == {
        "out": "version.c",
        "mode": "subst",
        "template": "version.c.in",
        "deps": [":autoconf"],
    }
    assert rules["version_h"].kind == "write_file"
    assert rules["version_h"].attrs["content"] == ["extern char const *Version;", ""]
    assert rules["ver"].attrs["srcs"] == ["version.c", "version.h"]
    assert notes == []


def test_template_indicators(tmp_path: Path) -> None:
    """The @GNULIB_X@ a wrapper template reads, for the ones no port defaults."""
    template = tmp_path / "stdio.in.h"
    template.write_text("#if @GNULIB_VAPRINTF@\n#endif\n#if @GNULIB_PRINTF@ || @HAVE_DECL_X@\n")
    assert _template_indicators([template, tmp_path / "missing.in.h"]) == {"GNULIB_VAPRINTF", "GNULIB_PRINTF"}


def test_literal_sed_substs() -> None:
    """gnulib.mk's `@''GUARD_PREFIX''@|GL` is a literal, not a configure variable."""
    text = (
        "\t  -e 's|@''GUARD_PREFIX''@|GL|g' \\\n\t  -e 's|@''HAVE_STDDEF_H''@|$(HAVE_STDDEF_H)|g' \\\n"
        "\t  -e 's|@''GUARD_PREFIX''@|GL|g'\n"
    )
    assert _literal_sed_substs(text) == {"GUARD_PREFIX": "GL"}


def test_tool_modules() -> None:
    """Parsers and scanners bring the BCR bison / flex modules, nothing else."""
    module = _module_bazel("GNU awk", "5.4.0", "0.26.0", None, False, ["bison"])
    assert 'bazel_dep(name = "bison", version = "3.8.2.bcr.11")' in module
    assert 'name = "flex"' not in module
    assert "rules_bison" not in module
    assert "register_toolchains" not in module
    assert 'bazel_dep(name = "flex", version = "2.6.4.bcr.8")' in _module_bazel(
        "x", "1", "0.26.0", None, False, ["bison", "flex"]
    )


# ---- list_append_words: probe actions that only append to a shell list ----


def test_gl_add_prog_body() -> None:
    action = """{
          if test -z "$optional_bin_progs"; then
            optional_bin_progs=hostid
          else
            optional_bin_progs="$optional_bin_progs hostid"
          fi
        }"""
    assert list_append_words(action) == ("optional_bin_progs", ["hostid"])


def test_plain_appends_and_other_actions() -> None:
    assert list_append_words('PROGS="$PROGS a b"\nPROGS="$PROGS c"') == ("PROGS", ["a", "b", "c"])
    assert list_append_words('PROGS="$PROGS a"\nOTHER=1') is None
    assert list_append_words("AC_DEFINE([HAVE_X], [1])") is None
    assert list_append_words('LIBS="-lfoo $LIBS"') is None
    assert list_append_words("have_guile=yes") is None  # an ordinary assignment, not a list


def test_wrapper_header_appended_input(tmp_path: Path) -> None:
    """gnulib's assert.h is assembled from assert.in.h and then verify.h through a sed: the
    second file becomes an autoconf_hdr rendering it with the sed as substitutions, spliced
    into the wrapper at the template's closing comment through GNULIB_COND_HDR_INLINES."""
    root = tmp_path
    (root / "lib").mkdir()
    (root / "lib/assert.in.h").write_text(
        "#@INCLUDE_NEXT@ @NEXT_ASSERT_H@\n\n/* The definition of static_assert is copied here.  */\n"
    )
    (root / "lib/verify.h").write_text(
        "#ifndef _GL_VERIFY_H\n#define _GL_VERIFY_H\n#ifdef _GL_STATIC_ASSERT_H\n"
        '# define _Static_assert(...) _GL_VERIFY (__VA_ARGS__, "failed", -)\n#endif\n'
        "template <int w> struct _gl_verify_type {};\n"
        '/* @assert.h omit start@  */\n#define verify(R) _GL_VERIFY (R, "verify", -)\n'
        "/* @assert.h omit end@  */\n#endif\n"
    )
    (root / "lib/alloca.in.h").write_text("#if @HAVE_ALLOCA_H@\n#endif\n")
    mk_text = textwrap.dedent("""\
        noinst_LIBRARIES += libgnu.a
        libgnu_a_SOURCES = x.c
        if GL_GENERATE_ALLOCA_H
        alloca.h: alloca.in.h $(top_builddir)/config.status
        \t$(gl_V_at)$(SED_HEADER_STDOUT) -e 's|@''HAVE_ALLOCA_H''@|$(HAVE_ALLOCA_H)|g' $(srcdir)/alloca.in.h > $@-t
        \t$(AM_V_at)mv $@-t $@
        endif
        if GL_GENERATE_ASSERT_H
        assert.h: assert.in.h verify.h $(top_builddir)/config.status
        \t$(gl_V_at){ $(SED_HEADER_STDOUT) \\
        \t      -e 's|@''NEXT_ASSERT_H''@|$(NEXT_ASSERT_H)|g' \\
        \t      < $(srcdir)/assert.in.h && \\
        \t  sed -e '/@assert.h omit start@/,/@assert.h omit end@/d' \\
        \t      -e 's|_gl_verify|_gl_static_assert|g' \\
        \t      -e 's|_GL_VERIFY|_GL_STATIC_ASSERT|g' \\
        \t      -e 's|_GL\\(_STATIC_ASSERT_H\\)|_GL_M4\\1|g' \\
        \t      < $(srcdir)/verify.h; \\
        \t} > $@-t
        \t$(AM_V_at)mv $@-t $@
        endif
        """)
    mk = parse_mk(mk_text)
    assert [(h.out, h.extra_inputs) for h in mk.headers] == [("alloca.h", []), ("assert.h", ["verify.h"])]

    def render(mk_text: str) -> tuple[Package, list[str]]:
        package = Package(path="", docstring="")
        notes: list[str] = []
        _wrapper_header_rules(
            package, parse_mk(mk_text), root / "lib/gnulib.mk", root, "lib/", "gnu", "GNU", ProjectState(), notes
        )
        return package, notes

    package, notes = render(mk_text)
    assert notes == []
    rules = rules_by_name(package)
    half = rules["lib_assert_h_verify"]
    assert half.kind == "autoconf_hdr"
    assert half.attrs == {
        "out": "_gnulib_cond/assert-verify.h",
        "mode": "subst",
        "substitutions": {
            "#define _GL_VERIFY_H": "#define _GL_M4_STATIC_ASSERT_H",
            "#ifndef _GL_VERIFY_H": "#ifndef _GL_M4_STATIC_ASSERT_H",
            "/* @assert.h omit end@  */": "#endif /* @assert.h omit end@  */",
            "/* @assert.h omit start@  */": "#if 0 /* @assert.h omit start@  */",
            "_GL_STATIC_ASSERT_H": "_GL_M4_STATIC_ASSERT_H",
            "_GL_VERIFY": "_GL_STATIC_ASSERT",
            "_gl_verify": "_gl_static_assert",
        },
        "template": "lib/verify.h",
        "deps": [":autoconf"],
    }
    inlines = next(s for s in package.after if isinstance(s, Assign) and s.name == "GNULIB_COND_HDR_INLINES")
    assert inlines.value == {
        "assert": {"/* The definition of static_assert is copied here.  */": ":lib_assert_h_verify"}
    }
    comprehension = next(s for s in package.after if isinstance(s, Raw) and "_h_processed" in s.text)
    assert "inlines = GNULIB_COND_HDR_INLINES.get(stem, {})," in comprehension.text
    # The half is a rule of its own, before the comprehension that splices it.
    assert package.after.index(half) < package.after.index(comprehension)

    # A non-recursive gnulib.mk reads the inputs as `$(top_srcdir)/lib/verify.h`.
    package, _notes = render(mk_text.replace("$(srcdir)/verify.h", "$(top_srcdir)/lib/verify.h"))
    assert rules_by_name(package)["lib_assert_h_verify"].attrs["template"] == "lib/verify.h"

    # A recipe that is not a sed (cat) is noted and the wrapper keeps the template alone.
    package, notes = render(mk_text.replace("sed -e '/@assert.h omit start@/,/@assert.h omit end@/d'", "cat"))
    assert notes == [
        "lib/assert.h: also assembled from lib/verify.h, left out: its recipe is not a sed the converter follows"
    ]
    assert "lib_assert_h_verify" not in rules_by_name(package)
    assert not any(isinstance(s, Assign) and s.name == "GNULIB_COND_HDR_INLINES" for s in package.after)


def test_option_flag_steps_aside_for_a_hand_target() -> None:
    """flex: BUILD.bazel keeps an alias `libfl` (the archive's old label) while configure.ac has
    --enable-libfl.  The bool_flag takes the name `libfl_option`, as it does beside a generated
    target of that name (libpcap's `pcap`), and the config_setting keeps `libfl_enabled`; the
    flag under the alias's name would be dropped as replaced and the setting would read the
    alias as a flag (a dependency cycle)."""
    state = ProjectState()
    state.flags["libfl"] = FlagInfo("AC_ARG_ENABLE", "libfl", "build libfl", default=True)
    state.option_settings[":libfl_enabled"] = ("libfl", "yes")
    package = Package(path="", docstring="")
    package.after.append(Raw('cc_library(name = "src/fl", target_compatible_with = select({":libfl_enabled": []}))'))
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert rules["libfl"].kind == "bool_flag"
    assert rules["libfl_enabled"].attrs["flag_values"] == {":libfl": "True"}
    state.hand_targets["libfl"] = "alias"
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert "libfl" not in rules
    assert rules["libfl_option"].kind == "bool_flag"
    assert rules["libfl_option"].attrs["build_setting_default"] is True
    assert rules["libfl_enabled"].attrs["flag_values"] == {":libfl_option": "True"}
    # A flag BUILD.bazel declares itself (libssh2's `crypto` string_flag) replaces the generated
    # one: the name stays, and the replacement step drops the generated flag.
    state.hand_targets["libfl"] = "bool_flag"
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert rules["libfl"].kind == "bool_flag"
    assert rules["libfl_enabled"].attrs["flag_values"] == {":libfl": "True"}


def test_flag_decision_flips_the_generated_default_only() -> None:
    """gawk's `flag:mpfr: False` on an option configure turns on by default: the bool_flag's
    default flips, while the arms keep reading configure's own default, so the option still
    gates the MPFR deps and checks (with the decision taken as configure's default, every
    invocation agreed and the selects collapsed to unconditional deps)."""
    state = ProjectState()
    state.flags["mpfr"] = FlagInfo("AC_ARG_ENABLE", "mpfr", "", default=True, build_default=False)
    assert state.default("mpfr") is True  # what lower.flag_arms splits on
    state.option_settings[":mpfr_enabled"] = ("mpfr", "yes")
    package = Package(path="", docstring="")
    package.after.append(Raw('cc_library(name = "x", deps = select({":mpfr_enabled": ["@mpfr"]}))'))
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert rules["mpfr"].attrs["build_setting_default"] is False
    assert rules["mpfr_enabled"].attrs["flag_values"] == {":mpfr": "True"}


def test_sub_configure_shares_the_root_option() -> None:
    """gawk's extension/configure.ac runs the same MPFR macro as the root: the sub-package keys on
    the root's `//:mpfr_enabled` and declares no flag of its own, so one `--//:mpfr` switches the
    whole module; its other options stay its own."""
    state = ProjectState(package_path="extension", shared_options={"mpfr"})
    state.flags["mpfr"] = FlagInfo("AC_ARG_WITH", "mpfr", "", default=True)
    state.flags["pma"] = FlagInfo("AC_ARG_ENABLE", "pma", "", default=True)
    assert state.label("mpfr", "yes") == "//:mpfr_enabled"
    assert state.label("pma", "yes") == ":pma_enabled"
    package = Package(path="extension", docstring="")
    package.after.append(
        Raw('cc_library(name = "x", deps = select({"//:mpfr_enabled": ["@mpfr"], ":pma_enabled": []}))')
    )
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert "mpfr" not in rules and "mpfr_enabled" not in rules
    assert rules["pma"].kind == "bool_flag"


def test_value_option_becomes_a_string_flag_and_a_template_variable() -> None:
    """ppp: `AS_IF([test -n "$with_runtime_dir"], [PPPD_RUNTIME_DIR="$with_runtime_dir"],
    [PPPD_RUNTIME_DIR="${runstatedir}/pppd"])`.  The variable takes the option's value: a
    string_flag named after the option with configure's default at the install directories'
    defaults, no bool flag, and the variable rendered by `autoconf_template_vars`.  A `-D` of the
    variable reads the make variable, and the owner lists the target in `toolchains`."""
    optval = df.Ref(f"{df.OPTVAL_PREFIX}runtime_dir")
    given = df.Flag("runtime_dir")
    decisions = [
        df.Assignment(given, optval, 'PPPD_RUNTIME_DIR="$with_runtime_dir"'),
        df.Assignment(df.neg(given), df.Lit("${runstatedir}/pppd"), 'PPPD_RUNTIME_DIR="${runstatedir}/pppd"'),
    ]
    parts = value_option_parts(decisions)
    assert parts is not None and parts[0] == "runtime_dir" and [d.text for d in parts[1]] == [decisions[1].text]
    # Not that shape: the given arm assigns something else, or a platform is mixed in.
    assert value_option_parts([df.Assignment(given, df.Lit("yes"), ""), decisions[1]]) is None
    assert value_option_parts([df.Assignment(df.conj(given, df.HostOs(("linux*",))), optval, ""), decisions[1]]) is None
    assert expand_install_dirs("${runstatedir}/pppd") == "/usr/local/var/run/pppd"
    assert expand_install_dirs("${libdir}/pppd/$VERSION", {"VERSION": "2.5.2"}) == "/usr/local/lib/pppd/2.5.2"
    assert expand_install_dirs("$(datadir)/$(PACKAGE)/x", {"PACKAGE": "ppp"}) == "/usr/local/share/ppp/x"
    assert expand_install_dirs("${unknown}/y") == "${unknown}/y"

    state = ProjectState()
    state.flags["runtime_dir"] = FlagInfo(
        "AC_ARG_WITH", "runtime-dir", "", takes_value=True, value_default="/usr/local/var/run/pppd"
    )
    state.flags["pma"] = FlagInfo("AC_ARG_ENABLE", "pma", "", default=True)
    state.option_settings[":pma_enabled"] = ("pma", "yes")
    package = Package(path="", docstring="")
    package.after.append(Raw('cc_library(name = "x", deps = select({":pma_enabled": []}))'))
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert rules["runtime_dir"].kind == "string_flag"
    assert rules["runtime_dir"].attrs["build_setting_default"] == "/usr/local/var/run/pppd"
    assert "values" not in rules["runtime_dir"].attrs
    assert "runtime_dir_enabled" not in rules
    assert rules["pma"].kind == "bool_flag"

    # The Makefile's `-DPPPD_RUNTIME_DIR='"@PPPD_RUNTIME_DIR@"'`: the make variable, escaped as
    # a C string, and no cppflag decision asked.
    state.template_vars = {"PPPD_RUNTIME_DIR": "runtime_dir"}
    mk = gnulibmk.parse("pppd_CPPFLAGS = -DPPPD_RUNTIME_DIR='\"@PPPD_RUNTIME_DIR@\"' -DPPPD_VERSION='\"@VERSION@\"'\n")
    asked = Decisions()
    defines, *_ = _preprocessor_flags(mk, "pppd", "pppd", [], state, asked)
    assert 'PPPD_RUNTIME_DIR="\\"$(PPPD_RUNTIME_DIR)\\""' in defines
    assert list(asked.pending) == ["cppflag:pppd:PPPD_VERSION"]
    assert _template_var_toolchains(defines, {}, state) == {"toolchains": [":template_vars"]}
    assert _template_var_toolchains(["HAVE_CONFIG_H"], {}, state) == {}


def test_option_the_hand_written_build_file_keys_on_is_declared() -> None:
    """libssh2: BUILD.bazel selects `src/ssh2`'s deps on `:libz_enabled`, while configure.ac's
    only test of --with-libz every invocation answers alike, so no translated condition opens an
    arm on it; the option is a flag all the same, declared for the hand-written file."""
    state = ProjectState()
    state.flags["libz"] = FlagInfo("AC_ARG_WITH", "libz", "use zlib", default=False)
    package = Package(path="", docstring="")
    assert _option_rules(package, state) == []
    state.hand_build = 'cc_library(name = "src/ssh2", deps = select({":libz_enabled": ["@zlib//:z"]}))'
    rules = {s.name: s for s in _option_rules(package, state) if isinstance(s, Rule)}
    assert rules["libz"].kind == "bool_flag"
    assert rules["libz_enabled"].attrs["flag_values"] == {":libz": "True"}


def test_src_condition_overrides_derived_gnulib_condition(tmp_path: Path) -> None:
    """A `src_condition:` decision for a condition gl_INIT derived (bison's chdir-long.c, grub's
    windows-*.c) overrides it: NEVER leaves the sources out with a note and keeps the name out of
    the table, ALWAYS makes them plain, an expression replaces the derived one and its negation
    serves the gnulib.mk `else` arm; the verror heuristic's GL_COND_OBJ_ERROR is overridable too."""
    state = ProjectState()
    state.src_conditions = {
        "GL_COND_OBJ_A": [df.neg(df.Truthy("ac_cv_func_a"))],
        "GL_COND_OBJ_B": [df.neg(df.Truthy("ac_cv_func_b"))],
        "GL_COND_OBJ_C": [df.neg(df.Truthy("ac_cv_func_c"))],
        "GL_COND_OBJ_D": [df.neg(df.Truthy("ac_cv_func_d"))],
        "GL_COND_OBJ_ERROR": [df.neg(df.Truthy("ac_cv_func_error"))],
    }
    library = Library(
        "libgnu_a",
        "libgnu.a",
        sources=[
            Source("a.c", "GL_COND_OBJ_A", None),
            Source("a-else.c", "!GL_COND_OBJ_A", None),
            Source("b.c", "GL_COND_OBJ_B", None),
            Source("c.c", "GL_COND_OBJ_C", None),
            Source("c-else.c", "!GL_COND_OBJ_C", None),
            Source("d.c", "GL_COND_OBJ_D", None),
            Source("verror.c", None, None),
            Source("error.c", "GL_COND_OBJ_ERROR", None),
        ],
    )
    decisions = Decisions.from_text(
        """DECISIONS = {
    "src_condition:GL_COND_OBJ_A": NEVER,
    "src_condition:GL_COND_OBJ_B": ALWAYS,
    "src_condition:GL_COND_OBJ_C": "REPLACE_C!=0",
    "src_condition:GL_COND_OBJ_ERROR": "!ac_cv_func_error",
}"""
    )
    notes: list[str] = []
    mk_path = tmp_path / "lib/gnulib.mk"
    table, platforms, exceptions = _condition_table(library, tmp_path, state, notes, decisions, mk_path, "")
    assert table == {
        "GL_COND_OBJ_A": "0",
        "GL_COND_OBJ_B": "1",
        "GL_COND_OBJ_C": "REPLACE_C!=0",
        "GL_COND_OBJ_D": "!ac_cv_func_d",  # derived, no decision
        "GL_COND_OBJ_ERROR": "!ac_cv_func_error",  # the decision, not the verror heuristic's `1`
    }
    assert (platforms, exceptions) == ({}, {})
    assert state.src_condition_exprs == {
        "GL_COND_OBJ_C": "REPLACE_C!=0",
        "GL_COND_OBJ_D": "!ac_cv_func_d",
        "GL_COND_OBJ_ERROR": "!ac_cv_func_error",
    }
    assert "GL_COND_OBJ_A: condition decided NEVER (gl_INIT's: !ac_cv_func_a)" in notes
    assert "GL_COND_OBJ_C: condition decided REPLACE_C!=0 (gl_INIT's: !ac_cv_func_c)" in notes
    conditional, platform_srcs, excluded, extra, plain = _classify_sources(
        library, tmp_path, "lib/", state, notes, decisions, mk_path, "", table, platforms, exceptions
    )
    # a.c is out, its else arm is unconditional; c-else.c negates the decided expression.
    assert conditional == {
        "lib/c.c": "GL_COND_OBJ_C",
        "lib/c-else.c": "!GL_COND_OBJ_C",
        "lib/d.c": "GL_COND_OBJ_D",
        "lib/error.c": "GL_COND_OBJ_ERROR",
    }
    assert table["!GL_COND_OBJ_C"] == "!(REPLACE_C!=0)"
    assert plain == ["lib/a-else.c", "lib/b.c", "lib/verror.c"]
    assert (platform_srcs, excluded, extra) == ({}, [], [])
    assert "a.c: left out (src_condition:GL_COND_OBJ_A is NEVER)" in notes
    assert decisions.unused == []


def test_extra_libobj_source_decided_conditional_reaches_the_rendered_table(tmp_path: Path) -> None:
    """coreutils' asprintf.c: an EXTRA source whose AC_LIBOBJ sits inside a ported macro and whose
    HAVE_/REPLACE_ substs the port does not spell is a `src_condition:` decision.  Decided as an
    expression it joins SRC_CONDITIONS and the conditional-sources rule; once those literals were
    rendered before the decision was read, the source silently disappeared while the decision
    counted as used (seq failed to link rpl_asprintf on macOS)."""
    lib = tmp_path / "lib"
    lib.mkdir()
    for name in ("asprintf.c", "vasprintf.c", "dummy.c"):
        (lib / name).write_text("")
    (lib / "Makefile.am").write_text("include gnulib.mk\n")
    (lib / "gnulib.mk").write_text(
        "noinst_LIBRARIES += libgnu.a\nlibgnu_a_SOURCES =\nEXTRA_libgnu_a_SOURCES =\n\n"
        "## begin gnulib module dummy\nlibgnu_a_SOURCES += dummy.c\n## end   gnulib module dummy\n\n"
        "## begin gnulib module vasprintf\nEXTRA_DIST += asprintf.c vasprintf.c\n"
        "EXTRA_libgnu_a_SOURCES += asprintf.c vasprintf.c\n## end   gnulib module vasprintf\n"
    )

    def emit(decisions: Decisions) -> tuple[Package, list[str]]:
        package = Package(path="", docstring="")
        state = ProjectState()
        state.gnulib_packages = {p: "@rules_cc_autoconf//" + p for p in ("gnulib/m4/vasprintf", "gnulib/m4/dummy")}
        notes: list[str] = []
        _emit_gnulib_library(package, lib / "gnulib.mk", tmp_path, state, ["config"], notes, decisions)
        return package, notes

    asked = Decisions()
    emit(asked)
    assert {k for k in asked.pending} >= {"src_condition:GL_COND_OBJ_ASPRINTF", "src_condition:GL_COND_OBJ_VASPRINTF"}
    decided = Decisions.from_text(
        'DECISIONS = {"src_condition:GL_COND_OBJ_ASPRINTF": "REPLACE_VASPRINTF==1", '
        '"src_condition:GL_COND_OBJ_VASPRINTF": NEVER}'
    )
    package, notes = emit(decided)
    assert any("vasprintf.c" in n and "left out" in n for n in notes)
    table = next(s for s in package.after if isinstance(s, Assign) and s.name == "SRC_CONDITIONS")
    assert table.value == {"GL_COND_OBJ_ASPRINTF": "REPLACE_VASPRINTF==1"}
    conditional = next(r for r in package.after if isinstance(r, Rule) and r.kind == "autoconf_srcs")
    assert '"lib/asprintf.c": "GL_COND_OBJ_ASPRINTF"' in as_raw(conditional.attrs["srcs"]).text
    assert "lib/vasprintf.c" not in as_raw(conditional.attrs["srcs"]).text
    assert decided.unused == []


def test_library_linked_under_option_brings_private_headers_into_the_arm(tmp_path: Path) -> None:
    """A program links a library only under an option (`if WITH_X` / `tool_LDADD = libbar.a
    lib/libgnu.a`): the library and the gnulib archive sit in that option's select() arm, and so
    do their private headers libraries, not the unconditional deps, so the headers are visible
    exactly when the archives are linked."""
    root = tmp_path
    (root / "bar").mkdir()
    (root / "Makefile.am").write_text(
        "noinst_LIBRARIES = libbar.a\nlibbar_a_SOURCES = bar/bar.c\n"
        "bin_PROGRAMS = tool\ntool_SOURCES = tool.c\n"
        "if WITH_X\ntool_LDADD = libbar.a lib/libgnu.a\nendif\n"
    )
    for name in ("bar/bar.c", "bar/bar.h", "tool.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.gnulib_libraries.append(GnulibLibrary("lib", ":lib/gnu", "lib/libgnu.a", ":lib/gnu_private_headers"))
    decisions = Decisions.from_text('DECISIONS = {"am_conditional:WITH_X": ":x_enabled"}')
    notes: list[str] = []
    _programs_tree(package, root, decisions, state, notes)
    rules = rules_by_name(package)
    assert "bar_private_headers" in rules  # bar/bar.h is not installed
    deps = as_raw(rules["tool"].attrs["deps"]).text
    unconditional, _plus, arms = deps.partition(" + ")
    for label in (":bar", ":bar_private_headers", ":lib/gnu", ":lib/gnu_private_headers"):
        assert label not in unconditional, label
        assert f'"{label}"' in arms, label
    assert '":x_enabled": [' in arms


def test_pkg_config_libs_under_conditional_ldadd(tmp_path: Path) -> None:
    """`tool_LDADD += $(ZLIB_LIBS)` under `if WITH_Z`, the variable being a pkg-config package the
    registry provides: the registry library goes into that option's select() arm.  The libraries
    configure found (LIBS) are in the unconditional deps already and must not be repeated in
    the arm, which Bazel would reject as a duplicated label."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "bin_PROGRAMS = tool\ntool_SOURCES = tool.c\ntool_LDADD =\nif WITH_Z\ntool_LDADD += $(ZLIB_LIBS)\nendif\n"
    )
    (root / "tool.c").write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.pkg_libs = {"ZLIB_LIBS": ["@zlib//:z"]}
    state.configure_libs = {"gmp": "@gmp//:gmp"}
    decisions = Decisions.from_text('DECISIONS = {"am_conditional:WITH_Z": ":z_enabled"}')
    notes: list[str] = []
    _programs_tree(package, root, decisions, state, notes)
    deps = as_raw(rules_by_name(package)["tool"].attrs["deps"]).text
    unconditional, _plus, arms = deps.partition(" + ")
    assert unconditional.count("@gmp//:gmp") == 1
    assert "@gmp//:gmp" not in arms
    assert "@zlib//:z" not in unconditional
    assert '":z_enabled": [' in arms and '"@zlib//:z"' in arms
    assert not any("linked unconditionally" in n for n in notes)


def test_registry_library_probed_under_an_option_is_linked_in_its_arm(tmp_path: Path) -> None:
    """gawk: configure probes `-lmpfr -lgmp` only under --enable-mpfr (`library_arms`), so the
    program and the tree library depend on both registry libraries in the `:mpfr_enabled` arm,
    not unconditionally (a `gawk_minimal` with the flag off then pulls neither).  A library an
    LDADD line links by hand (`-lgmp`) is unconditional, and not repeated in the arm."""
    root = tmp_path
    (root / "support").mkdir()
    (root / "Makefile.am").write_text(
        "noinst_LIBRARIES = libsupport.a\nlibsupport_a_SOURCES = support/s.c\n"
        "bin_PROGRAMS = tool other\ntool_SOURCES = tool.c\ntool_LDADD = libsupport.a\n"
        "other_SOURCES = other.c\nother_LDADD = -lgmp\n"
    )
    for name in ("support/s.c", "tool.c", "other.c"):
        (root / name).write_text("")
    package = Package(path="", docstring="")
    state = ProjectState()
    state.config_targets = ["config"]
    state.header_libraries = [":config_headers"]
    state.configure_libs = {"gmp": "@gmp//:gmp", "mpfr": "@mpfr//:mpfr"}
    state.library_arms = {"@gmp//:gmp": ":mpfr_enabled", "@mpfr//:mpfr": ":mpfr_enabled"}
    notes: list[str] = []
    _programs_tree(package, root, Decisions(), state, notes)
    rules = rules_by_name(package)
    deps = as_raw(rules["tool"].attrs["deps"]).text
    unconditional, _plus, arms = deps.partition(" + ")
    assert "@gmp//:gmp" not in unconditional and "@mpfr//:mpfr" not in unconditional
    assert '":mpfr_enabled": [' in arms and '"@gmp//:gmp"' in arms and '"@mpfr//:mpfr"' in arms
    # The tree library: the registry libraries are implementation details, in the same arm.
    assert "deps" not in rules["support"].attrs or "@gmp//:gmp" not in str(rules["support"].attrs["deps"])
    implementation = as_raw(rules["support"].attrs["implementation_deps"]).text
    unconditional, _plus, arms = implementation.partition(" + ")
    assert "@gmp//:gmp" not in unconditional
    assert '":mpfr_enabled": [' in arms and '"@gmp//:gmp"' in arms and '"@mpfr//:mpfr"' in arms
    # `-lgmp` on the LDADD line: gmp unconditional, once; mpfr still in the arm.
    deps = as_raw(rules["other"].attrs["deps"]).text
    unconditional, _plus, arms = deps.partition(" + ")
    assert unconditional.count("@gmp//:gmp") == 1
    assert "@gmp//:gmp" not in arms
    assert '":mpfr_enabled": [' in arms and '"@mpfr//:mpfr"' in arms


def test_subdir_gates(tmp_path: Path) -> None:
    """gawk enters extension/ only when its dlfcn.h probe made `$enable_extensions` yes, and the
    Makefile lists it under `if ENABLE_EXTENSIONS`: a target cannot follow a probe, so the
    `subdir:` decision is asked and the package builds everywhere meanwhile.  Decided, the labels
    become the package's `target_compatible_with` (`!a,b`: everywhere but those); NEVER drops the
    directory.  A host-OS condition, or an automake conditional decided to a config_setting,
    needs no decision."""
    root = tmp_path
    (root / "Makefile.am").write_text(
        "SUBDIRS = support .\nif ENABLE_EXTENSIONS\nSUBDIRS += extension\nendif\nSUBDIRS += doc\n"
    )
    state = ProjectState()
    state.subdir_conditions = {
        "extension": df.Eq("enable_extensions", "yes"),
        "doc": df.TRUE,
        "linuxonly": df.host_condition("*-*-linux*"),
    }
    decisions = Decisions()
    notes: list[str] = []
    kept, gates = _subdir_gates(root, state, decisions, ["extension", "doc", "linuxonly"], notes)
    assert kept == ["extension", "doc", "linuxonly"]
    assert "subdir:extension" in decisions.missing
    assert "doc" not in gates
    # Undecided: defined but manual, as a probe-conditioned program is.
    assert gates["extension"].compatible_with is None
    assert must(gates["extension"].manual_reason).startswith("subdir:extension undecided: AC_CONFIG_SUBDIRS")
    assert any("tagged manual until subdir:extension is decided" in n for n in notes), notes
    linux = must(gates["linuxonly"].compatible_with).text
    assert '"@platforms//os:linux": []' in linux and '"//conditions:default": ["@platforms//:incompatible"]' in linux
    decisions = Decisions.from_text(
        'DECISIONS = {"subdir:extension": ["!@rules_cc//cc/compiler:msvc-cl,//:extensions_off"], '
        '"subdir:doc": decision.NEVER}'
    )
    kept, gates = _subdir_gates(root, state, decisions, ["extension", "doc"], [])
    assert kept == ["extension"]
    text = must(gates["extension"].compatible_with).text
    assert '"@rules_cc//cc/compiler:msvc-cl": ["@platforms//:incompatible"]' in text
    assert '"//:extensions_off": ["@platforms//:incompatible"]' in text
    assert '"//conditions:default": []' in text
    assert decisions.missing == []
    decisions = Decisions.from_text('DECISIONS = {"subdir:extension": decision.MANUAL}')
    kept, gates = _subdir_gates(root, state, decisions, ["extension"], [])
    assert kept == ["extension"]
    assert gates["extension"] == SubdirGate(manual_reason="subdir:extension is MANUAL")
    state.subdir_conditions = {}
    decisions = Decisions.from_text('DECISIONS = {"am_conditional:ENABLE_EXTENSIONS": ":extensions_on"}')
    kept, gates = _subdir_gates(root, state, decisions, ["extension"], [])
    assert kept == ["extension"]
    assert '":extensions_on": []' in must(gates["extension"].compatible_with).text
    assert decisions.missing == []


def test_gate_package() -> None:
    """A sub-configure's gate lands on every C/C++ rule of its package that has none of its own."""
    package = Package(path="extension", docstring="")
    gate = Raw("select({})")
    package.after += [
        Rule("cc_library", "a", {"srcs": ["a.c"]}, load_from="@rules_cc//cc:cc_library.bzl"),
        Rule("cc_shared_library", "a_shared", {"deps": [":a"]}, load_from="@rules_cc//cc:cc_shared_library.bzl"),
        Rule(
            "cc_library",
            "b",
            {"target_compatible_with": ["x"], "tags": ["t"]},
            load_from="@rules_cc//cc:cc_library.bzl",
        ),
        Rule("filegroup", "f", {"srcs": [":a"]}),
    ]
    notes: list[str] = []
    _gate_package(package, SubdirGate(compatible_with=gate), notes)
    rules = rules_by_name(package)
    assert rules["a"].attrs["target_compatible_with"] is gate
    assert rules["a_shared"].attrs["target_compatible_with"] is gate
    assert rules["b"].attrs["target_compatible_with"] == ["x"]
    assert "target_compatible_with" not in rules["f"].attrs
    assert notes == ["2 target(s) build where the parent configure enters this directory (its subdir: gate)"]
    # A manual gate tags every C/C++ rule, keeping the tags a rule has; the filegroup is left alone.
    notes = []
    _gate_package(package, SubdirGate(manual_reason="subdir:extension is MANUAL"), notes)
    assert rules["a"].attrs["tags"] == ["manual"]
    assert rules["b"].attrs["tags"] == ["t", "manual"]
    assert "tags" not in rules["f"].attrs
    assert notes == ["3 target(s) tagged manual, out of `//...` (subdir:extension is MANUAL)"]


def test_conditional_flag_variable_spelled_out_into_the_arm() -> None:
    """`tool_CFLAGS = $(AM_CFLAGS) $(ZLIB_CFLAGS)` under `if WITH_Z`, decided as a select() arm:
    the Makefile's own variable and the one-literal substitution configure sets (a pkg-config
    found action's `-D`) are spelled out as for an unconditional line, and the define and the
    code-generation option go into that arm; warnings are dropped, as unconditionally; a
    variable only configure computes is reported once, not as a word left out."""
    mk = parse_mk(
        "AM_CFLAGS = -Wall -fno-builtin\nbin_PROGRAMS = tool\ntool_SOURCES = tool.c\n"
        "if WITH_Z\ntool_CFLAGS = $(AM_CFLAGS) $(ZLIB_CFLAGS) $(HOST_CFLAGS)\nendif\n"
    )
    state = ProjectState()
    state.literal_substs = {"ZLIB_CFLAGS": "-DZ_API=32"}
    decisions = Decisions.from_text('DECISIONS = {"am_conditional:WITH_Z": ":z_enabled"}')
    notes: list[str] = []
    defines, copts, standards, includes, copt_arms, define_arms, include_arms = _preprocessor_flags(
        mk, "tool", "", notes, state, decisions
    )
    # AM_CFLAGS applies to every target unconditionally; its expansion under the conditional
    # adds nothing to the arm.
    assert (defines, copts, standards, includes) == ([], ["-fno-builtin"], [], [])
    assert define_arms == {":z_enabled": ["Z_API=32"]}
    assert (copt_arms, include_arms) == ({}, {})
    assert [n for n in notes if "left out" in n and "ALWAYS" in n] == []
    assert any("$(HOST_CFLAGS)" in n and "only configure" in n for n in notes)


def test_lower_decisions_last_assignment_under_one_condition_wins() -> None:
    """Two assignments under the same condition (`X=""` then `X="$X -flag"` in one branch) lower to
    one check with the later value, under that condition; the earlier literal is not a second
    arm that would make the list inexpressible.  Likewise an assignment the later ones cover on
    every path (`X=$enableval` under the option, then `X=yes` or `X=no` by a check's outcome):
    its value, literal or not, is never the final one."""
    tree = markers.parse("#! /bin/sh\n")
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=ProjectState(),
    )
    cond: df.Cond = df.Truthy("gl_cv_fuse")
    assignments: list[tuple[df.Cond, df.Assignment]] = [
        (cond, df.Assignment(cond, df.Lit(""), 'FUSE_CFLAGS=""')),
        (
            cond,
            df.Assignment(cond, df.Lit(" -DFUSE_USE_VERSION=32"), 'FUSE_CFLAGS="$FUSE_CFLAGS -DFUSE_USE_VERSION=32"'),
        ),
    ]
    check, dropped = translator._lower_decisions("FUSE_CFLAGS", assignments, "checks.AC_SUBST", False)
    assert dropped == []
    assert check == Call("checks.AC_SUBST", ["FUSE_CFLAGS", "-DFUSE_USE_VERSION=32"], {"requires": ["gl_cv_fuse"]})
    # The same two values under different conditions stay two arms, as before.
    other: df.Cond = df.Truthy("gl_cv_other")
    check, _dropped = translator._lower_decisions(
        "X", [(cond, assignments[0][1]), (other, assignments[1][1])], "checks.AC_SUBST", False
    )
    assert check is None
    given: df.Cond = df.Flag("mount")
    covered: list[tuple[df.Cond, df.Assignment]] = [
        (given, df.Assignment(given, df.Ref("__rccac_optval_mount"), "enable_mount=$enableval")),
        (cond, df.Assignment(cond, df.Lit("yes"), "enable_mount=yes")),
        (df.neg(cond), df.Assignment(df.neg(cond), df.Lit("no"), "enable_mount=no")),
    ]
    check, dropped = translator._lower_decisions("enable_mount", covered, "checks.AC_SUBST", False)
    assert dropped == []
    assert check == Call(
        "checks.AC_SUBST", ["enable_mount"], {"condition": "gl_cv_fuse", "if_true": "yes", "if_false": "no"}
    )


def test_probe_decision_answers_a_library_or_header_check() -> None:
    """`probe:ac_cv_lib_tasn1_asn1_write_value: "no"`: the library check is not emitted, the value
    stands in for it (the project built as on a host without the library) and the script's test
    of the cache variable compares it by value; a header answered "yes" also gets its define."""
    lines = (
        ["#! /bin/sh"]
        + call(1, "AC_CHECK_LIB", "tasn1", "asn1_write_value")
        + call(2, "AC_CHECK_HEADER", "foo.h")
        + [
            'if test "$ac_cv_lib_tasn1_asn1_write_value" = yes; then',
            "  enable_protect=yes",
            "else",
            "  enable_protect=no",
            "fi",
        ]
        + call(3, "AC_SUBST", "enable_protect")
    )
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions.from_text(
            'DECISIONS = {"probe:ac_cv_lib_tasn1_asn1_write_value": "no", "probe:ac_cv_header_foo_h": "yes"}'
        ),
        project=ProjectState(),
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [(c.fn, c.args, c.kwargs) for c in as_calls(target.checks)]
    assert ("checks.AC_CHECK_LIB", ["tasn1", "asn1_write_value"], {}) not in calls
    assert ("checks.AC_CHECK_HEADER", ["foo.h"], {}) not in calls
    assert ("checks.M4_VARIABLE", ["ac_cv_lib_tasn1_asn1_write_value", "no"], {}) in calls
    assert ("checks.M4_VARIABLE", ["ac_cv_header_foo_h", "yes"], {}) in calls
    assert ("checks.AC_DEFINE", ["HAVE_FOO_H", 1], {}) in calls
    assert (
        "checks.AC_SUBST",
        ["enable_protect"],
        {"condition": "ac_cv_lib_tasn1_asn1_write_value==yes", "if_true": "yes", "if_false": "no"},
    ) in calls
    assert translator.decisions.pending == {}


def test_system_library_searches_reach_every_link() -> None:
    """`AC_SEARCH_LIBS([dlopen], [dl])` and `AC_CHECK_LIB(m, log10)` with the default action both
    put the library's `-l` flag in LIBS, which every link of the Makefile carries: for a system
    library (not a registry module) the flag becomes a substitution the shared linkopts target
    lists, the search's own value or a sibling of the library check.  A check with its own
    action-if-found, a registry library (a dep already) and a search with no candidate get
    none."""
    lines = (
        ["#! /bin/sh"]
        + call(1, "AC_SEARCH_LIBS", "dlopen", "dl")
        + call(2, "AC_CHECK_LIB", "m", "log10")
        + call(3, "AC_CHECK_LIB", "pthread", "pthread_mutex_lock", "LIBPTHREAD=-lpthread")
        + call(4, "AC_SEARCH_LIBS", "daemon", "")
        + call(5, "AC_CHECK_LIB", "gmp", "__gmpz_init")
    )
    state = ProjectState()
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    calls = [(c.fn, c.args, c.kwargs) for c in as_calls(target.checks)]
    assert ("checks.AC_SEARCH_LIBS", ["dlopen", ["dl"]], {"subst": "ac_cv_search_dlopen_LIBS"}) in calls
    assert (
        "checks.AC_SUBST",
        ["ac_cv_lib_m_log10_LIBS"],
        {"condition": "ac_cv_lib_m_log10", "if_true": "-lm", "if_false": ""},
    ) in calls
    assert ("checks.AC_SEARCH_LIBS", ["daemon", []], {}) in calls
    substs = [str(c[1][0]) for c in calls if c[0] == "checks.AC_SUBST"]
    assert [name for name in substs if "pthread" in name or "gmp" in name] == []
    assert state.configure_link_vars == ["ac_cv_search_dlopen_LIBS", "ac_cv_lib_m_log10_LIBS"]


def test_option_compared_against_no_opens_no_select() -> None:
    """An option off by default whose only test is against `no` (`if test x"$enable_mount" = xno;
    then excuse="explicitly disabled"`, the tool's settings under `test x"$excuse" = x`): the
    `--enable-mount` and not-given invocations agree, and `--disable-mount` has no flag value,
    so the settings are plain checks, not select() arms, and the option is not registered."""
    lines = (
        ["#! /bin/sh"]
        + [
            "if __RCCAC_FLAG_mount__; then",
            "  enable_mount=$__rccac_optval_mount",
            "else",
            "  enable_mount=",
            "fi",
            'if test x"$enable_mount" = xno; then',
            '  mount_excuse="explicitly disabled"',
            "fi",
            'if test x"$mount_excuse" = x; then',
            '  FOO_CFLAGS="-DFOO_API=2"',
            "fi",
        ]
        + call(2, "AC_SUBST", "FOO_CFLAGS")
    )
    state = ProjectState()
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=state,
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    substs = [c for c in as_calls(target.checks) if c.fn == "checks.AC_SUBST" and c.args[:1] == ["FOO_CFLAGS"]]
    assert [(c.args, c.kwargs) for c in substs] == [(["FOO_CFLAGS", "-DFOO_API=2"], {})]
    assert target.selects == {}
    assert state.option_settings == {}
    assert translator.decisions.pending == {}


def test_variable_assigned_only_under_a_condition_is_empty_elsewhere() -> None:
    """`excuse="need foo.h"` under one condition, then `if test x"$excuse" = x` around a tool's
    settings: nothing bound the variable before the script, so where the assignment does not hold
    it is empty, as an unset shell variable is to `test`, and the setting requires the assigning
    condition not to hold, without a decision.  A name configure's own prelude owns (`ac_*`), or
    one another macro's expansion assigns (`target_cpu`, which AC_CANONICAL_TARGET sets and the
    script renames), may hold a value before the body: its emptiness stays undecided, and the
    value a decision."""

    def translate(excuse: str, macro_sets: str = "") -> Translator:
        lines = (
            ["#! /bin/sh"]
            + call(1, "AC_CHECK_HEADERS", "foo.h")
            + call(3, "AC_CANONICAL_HOST", body=macro_sets)
            + [
                'if test "$ac_cv_header_foo_h" = no; then',
                f'  {excuse}="need foo.h"',
                "fi",
                f'if test x"${excuse}" = x; then',
                '  FOO_CFLAGS="-DFOO_API=2"',
                "fi",
            ]
            + call(2, "AC_SUBST", "FOO_CFLAGS")
        )
        translator = Translator(
            markers.parse("\n".join(lines) + "\n"),
            index=INDEX,
            selected_files={"configure.ac"},
            m4_sources={},
            options=Options(),
            decisions=Decisions(),
            project=ProjectState(),
        )
        translator.translate()
        return translator

    translator = translate("foo_excuse")
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    substs = [c for c in as_calls(target.checks) if c.fn == "checks.AC_SUBST" and c.args[:1] == ["FOO_CFLAGS"]]
    assert [(c.args, c.kwargs) for c in substs] == [
        (["FOO_CFLAGS", "-DFOO_API=2"], {"requires": ["ac_cv_header_foo_h"]})
    ]
    assert translator.decisions.pending == {}
    translator = translate("ac_foo_excuse")
    translator.translate_root("autoconf", loc_file="configure.ac")
    assert "variable:FOO_CFLAGS" in translator.decisions.pending
    translator = translate("foo_excuse", macro_sets="foo_excuse=guessed")
    translator.translate_root("autoconf", loc_file="configure.ac")
    assert "variable:FOO_CFLAGS" in translator.decisions.pending


def test_probe_a_decision_answers_is_not_an_unset_variable() -> None:
    """`AC_CACHE_CHECK(..., [my_cv_x], [... my_cv_x=yes ...])` under one condition, `my_cv_x=no` in
    the else, the probe IGNOREd by a decision: the cache variable is the probe's to set, not the
    body's, so where the probe would run it is not empty but unknown; a test of it afterwards
    stays a decision rather than reading the else branch as the only assignment."""
    lines = (
        ["#! /bin/sh"]
        + call(1, "AC_CHECK_HEADERS", "foo.h")
        + ['if test "$ac_cv_header_foo_h" = yes; then']
        + call(3, "AC_CACHE_CHECK", "whether x works", "my_cv_x", "my_cv_x=yes", body="my_cv_x=yes")
        + ["else", "  my_cv_x=no", "fi", 'if test "$my_cv_x" != no; then', '  FOO_CFLAGS="-DFOO_API=2"', "fi"]
        + call(4, "AC_SUBST", "FOO_CFLAGS")
    )
    translator = Translator(
        markers.parse("\n".join(lines) + "\n"),
        index=INDEX,
        selected_files={"configure.ac"},
        m4_sources={},
        options=Options(),
        decisions=Decisions.from_text('DECISIONS = {"probe:my_cv_x": decision.IGNORE}'),
        project=ProjectState(),
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    assert not [c for c in as_calls(target.checks) if c.fn == "checks.AC_SUBST" and c.args[:1] == ["FOO_CFLAGS"]]
    assert translator.decisions.pending


def test_inlined_macro_sees_the_callers_conditional_literal() -> None:
    """Two macros expanded in one branch, the first setting `X=""`, the second appending to `$X`
    (pkg-config's own `X_CFLAGS=` and the found action's `X_CFLAGS="$X_CFLAGS -D..."`): each body
    is analysed on its own, the second seeded with the literal the first settled on that path, so
    its value is a literal and the two assignments under the one condition lower to the later."""
    lines = (
        ["#! /bin/sh"]
        + call(1, "AC_CHECK_HEADERS", "foo.h")
        + ['if test "$ac_cv_header_foo_h" = yes; then']
        + [
            marker("BEGIN", "id=2", "name=MY_PKG", "loc=acinclude.m4:5"),
            'FOO_CFLAGS=""',
            marker("END", "id=2", "name=MY_PKG"),
            marker("BEGIN", "id=3", "name=MY_FOUND", "loc=acinclude.m4:9"),
            'FOO_CFLAGS="$FOO_CFLAGS -DFOO_API=2"',
            marker("END", "id=3", "name=MY_FOUND"),
        ]
        + ["fi"]
        + call(4, "AC_SUBST", "FOO_CFLAGS")
    )
    tree = markers.parse("\n".join(lines) + "\n")
    translator = Translator(
        tree,
        index=INDEX,
        selected_files={"configure.ac", "acinclude.m4"},
        m4_sources={},
        options=Options(),
        decisions=Decisions(),
        project=ProjectState(),
    )
    translator.translate()
    target = translator.translate_root("autoconf", loc_file="configure.ac")
    substs = [c for c in as_calls(target.checks) if c.fn == "checks.AC_SUBST" and c.args[:1] == ["FOO_CFLAGS"]]
    assert [(c.args, c.kwargs) for c in substs] == [
        (["FOO_CFLAGS", "-DFOO_API=2"], {"requires": ["ac_cv_header_foo_h"]})
    ]
    assert translator.decisions.pending == {}
