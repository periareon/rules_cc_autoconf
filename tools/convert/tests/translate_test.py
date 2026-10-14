"""Unit tests for the translator on a synthetic marker stream (no autotools)."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.convert import emit, markers
from tools.convert.index import PortIndex, parse_build
from tools.convert.decisions import Decisions
from tools.convert.ir import Call
from tools.convert.translate import Options, Translator, fold_define

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


INDEX = PortIndex([
    parse_build("autoconf/macros/AC_CANONICAL_HOST", 'autoconf(name = "AC_CANONICAL_HOST")'),
    parse_build("gnulib/m4/other", '"""https://github.com/coreutils/gnulib/blob/abc/m4/other.m4"""\n'
                                   'autoconf(name = "gl_OTHER")\nautoconf(name = "other")'),
])

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
    lines += call(4, "AC_CHECK_HEADERS", "a.h b.h", body="\n".join(
        call(5, "AC_REQUIRE", "AC_CHECK_INCLUDES_DEFAULT")))
    lines += call(6, "AC_CHECK_HEADER", "c.h", "AC_DEFINE([HAVE_C_H], [1], [c])", "", "#include <a.h>")
    lines += [marker("CALL", "id=7", "macro=AC_CACHE_CHECK"),
              marker("ARG", "id=7", "n=1", "for foo"),
              marker("ARG", "id=7", "n=2", "gl_cv_foo"),
              marker("ARG", "id=7", "n=3", "AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[#include <a.h>]], "
                                           "[[return 0;]])],"),
              "#RCCAC|       [gl_cv_foo=yes], [gl_cv_foo=no])",
              "printf checking"]
    lines += call(8, "AC_COMPILE_IFELSE", "AC_LANG_PROGRAM([[#include <a.h>]], [[return 0;]])",
                  "gl_cv_foo=yes", "gl_cv_foo=no", body="if ac_fn_c_try_compile; then :; fi")
    lines.append(marker("ENDCALL", "id=7", "macro=AC_CACHE_CHECK"))
    lines.append("if test $gl_cv_foo = yes; then")
    lines += call(9, "AC_DEFINE", "HAVE_FOO", "1", "foo")
    lines.append("fi")
    lines.append("FOO_VAR=7")
    lines += call(10, "AC_SUBST", "FOO_VAR")
    lines.append(marker("END", "id=1", "name=gl_FOO"))
    return "\n".join(lines)


class TranslateTest(unittest.TestCase):

    def setUp(self) -> None:
        tree = markers.parse(expansion())
        self.assertEqual(tree.warnings, [])
        self.translator = Translator(
            tree, index=INDEX, selected_files={"foo.m4"}, m4_sources={"m4/foo.m4": M4},
            options=Options(),
        )
        self.packages = self.translator.translate()

    def test_targets_and_deps(self) -> None:
        (package,) = self.packages
        self.assertEqual(package.path, "m4/foo")
        self.assertEqual([t.name for t in package.targets], ["gl_FOO", "foo"])
        foo = package.target("gl_FOO")
        self.assertEqual(
            sorted(d.label for d in foo.deps),
            ["//autoconf/macros/AC_CANONICAL_HOST", "//gnulib/m4/other:gl_OTHER"],
        )
        self.assertEqual(package.target("foo").deps[0].label, ":gl_FOO")

    def test_checks(self) -> None:
        foo = self.packages[0].target("gl_FOO")
        calls = [c for c in foo.checks if isinstance(c, Call)]
        self.assertEqual([c.fn for c in calls], [
            "macros.AC_CHECK_HEADERS", "checks.AC_CHECK_HEADER", "checks.AC_TRY_COMPILE",
            "checks.AC_DEFINE", "checks.AC_SUBST",
        ])
        headers, header, probe, define, subst = calls
        self.assertEqual(headers.args, [["a.h", "b.h"]])
        self.assertTrue(headers.returns_list)
        self.assertEqual(header.args, ["c.h"])
        self.assertEqual(header.kwargs, {"define": "HAVE_C_H", "includes": ["#include <a.h>"]})
        self.assertEqual(probe.kwargs["name"], "gl_cv_foo")
        self.assertEqual(probe.kwargs["code"].fn, "utils.AC_LANG_PROGRAM")
        self.assertEqual(probe.kwargs["code"].args, [["#include <a.h>"], ["return 0;"]])
        self.assertTrue(probe.comments[0].startswith("m4/foo.m4:~7: AC_CACHE_CHECK([for foo], [gl_cv_foo]"), probe.comments)
        self.assertEqual(define.args, ["HAVE_FOO", 1])
        # The `if test $gl_cv_foo = yes` around the define became its `requires`.
        self.assertEqual(define.kwargs, {"requires": ["gl_cv_foo"]})
        # `AC_SUBST([FOO_VAR])` takes the value of the body's `FOO_VAR=7`.
        self.assertEqual(subst.args, ["FOO_VAR", 7])
        # Everything was followed: no decision was requested.
        self.assertEqual(self.translator.decisions.pending, {})
        # Source locations were recovered from the m4 text.
        self.assertTrue(any(c.startswith("m4/foo.m4:~5:") for c in headers.comments), headers.comments)
        self.assertTrue(any(c.startswith("m4/foo.m4:~11:") for c in define.comments), define.comments)

    def test_renders(self) -> None:
        text = emit.render_package(self.packages[0])
        self.assertIn('load("//autoconf:checks.bzl", "checks", "macros", "utils")', text)
        self.assertIn('checks.AC_DEFINE(\n            "HAVE_FOO",\n            1,\n            requires = ["gl_cv_foo"],\n        ),', text)

    def test_fold_define(self) -> None:
        self.assertEqual(fold_define("AC_DEFINE([HAVE_X], [1], [desc])"), "HAVE_X")
        self.assertEqual(fold_define("AC_DEFINE([HAVE_X])"), "HAVE_X")
        self.assertIsNone(fold_define("AC_DEFINE([HAVE_X], [2])"))
        self.assertIsNone(fold_define("x=yes"))



class HelperTest(unittest.TestCase):

    def test_default_from_help(self) -> None:
        from tools.convert.translate import default_from_help

        self.assertTrue(default_from_help("AS_HELP_STRING([--disable-perl-regexp], [disable PCRE])"))
        self.assertTrue(default_from_help("AS_HELP_STRING([--without-libiconv], [no iconv])"))
        self.assertFalse(default_from_help("AS_HELP_STRING([--enable-gcc-warnings], [warnings])"))
        self.assertFalse(default_from_help("AS_HELP_STRING([--with-foo=DIR], [foo])"))
        self.assertFalse(default_from_help(""))

    def test_condition_names(self) -> None:
        from tools.convert.translate import condition_names

        self.assertEqual(condition_names("!SHLIB_STATUS==unsupported"), ["SHLIB_STATUS"])
        self.assertEqual(condition_names("USE_NLS && !gt_val"), ["USE_NLS", "gt_val"])
        self.assertEqual(condition_names("(a_cv_x || b_cv_y) && HAVE_Z!=no"), ["a_cv_x", "b_cv_y", "HAVE_Z"])

    def test_provided_names(self) -> None:
        from tools.convert.translate import provided_names

        self.assertEqual(provided_names(Call("macros.AC_CHECK_HEADERS", [["sys/wait.h", "dirent.h"]])),
                         {"ac_cv_header_sys_wait_h", "HAVE_SYS_WAIT_H", "ac_cv_header_dirent_h", "HAVE_DIRENT_H"})
        self.assertEqual(provided_names(Call("macros.AC_CHECK_DECLS", [["alarm"]])),
                         {"ac_cv_have_decl_alarm", "HAVE_DECL_ALARM"})
        self.assertEqual(provided_names(Call("checks.AC_TRY_COMPILE", [], {"name": "gl_cv_x", "define": "HAVE_X"})),
                         {"gl_cv_x", "HAVE_X"})
        self.assertEqual(provided_names(Call("checks.AC_SUBST", ["FOO_VAR", 7])), {"FOO_VAR"})
        self.assertEqual(provided_names(Call("checks.AC_CHECK_LIB", ["dl", "dlopen"])),
                         {"ac_cv_lib_dl_dlopen", "HAVE_LIBDL"})

    def test_dangling_conditions_become_guard_decisions(self) -> None:
        """A check conditional on `cross_compiling` (no check produces it) is a
        guard decision; a self-guarding default assignment is unconditional."""
        from tools.convert.decisions import Decisions

        lines = ["#! /bin/sh"]
        lines.append(marker("BEGIN", "id=1", "name=gl_ENV", "loc=m4/env.m4:1"))
        lines.append('if test "x$GCC" = "xyes"; then\n  CROSS_COMPILE="-DCROSS_COMPILING"\nelse\n'
                     '  CROSS_COMPILE=""\nfi\ntest -n "$ARFLAGS" || ARFLAGS="cr"')
        lines += call(2, "AC_SUBST", "CROSS_COMPILE")
        lines += call(3, "AC_SUBST", "ARFLAGS")
        lines.append(marker("END", "id=1", "name=gl_ENV"))
        tree = markers.parse("\n".join(lines) + "\n")
        m4 = "AC_DEFUN([gl_ENV], [AC_SUBST([CROSS_COMPILE]) AC_SUBST([ARFLAGS])])\n"

        def translate(decisions: Decisions):
            translator = Translator(tree, index=INDEX, selected_files={"m4/env.m4"}, m4_sources={"m4/env.m4": m4},
                                    options=Options(), decisions=decisions)
            (package,) = translator.translate()
            return translator, package.targets[0]

        asked = Decisions()
        _, target = translate(asked)
        names = [c.args[0] for c in target.checks if isinstance(c, Call)]
        self.assertEqual(names, ["ARFLAGS"])  # unconditional: ARFLAGS is unset before the m4 sets it
        self.assertNotIn("requires", [c for c in target.checks if isinstance(c, Call)][0].kwargs)
        # `$GCC = yes` is the compiler, a select() dimension: no decision needed.
        self.assertEqual(list(asked.pending), [])
        (family,) = target.selects
        self.assertEqual(family, "@rules_cc//cc/compiler:")
        arms = {label: [(c.args[0], c.args[1]) for c in items] for label, items in target.selects[family].items()}
        self.assertEqual(arms, {"@rules_cc//cc/compiler:msvc-cl": [("CROSS_COMPILE", "")],
                                "@rules_cc//cc/compiler:clang-cl": [("CROSS_COMPILE", "")],
                                "//conditions:default": [("CROSS_COMPILE", "-DCROSS_COMPILING")]})

    def test_library_target_names(self) -> None:
        """`libfoo.a` -> `foo`; `lib/foo` plus a `libfoo` alias only when a program `foo` exists;
        an archive in a subdirectory (`lib/libnvc.a`) keeps the directory in front."""
        from tools.convert.project import _library_target
        self.assertEqual(_library_target("", "libgzip.a", set()), ("gzip", None))
        self.assertEqual(_library_target("", "libnvc.a", {"nvc"}), ("lib/nvc", "libnvc"))
        self.assertEqual(_library_target("src", "libssh2.la", set()), ("src/ssh2", None))
        self.assertEqual(_library_target("", "lib/libnvc.a", set()), ("lib/nvc", None))
        self.assertEqual(_library_target("", "lib/libnvc.a", {"lib/nvc"}), ("lib/lib/nvc", "lib/libnvc"))
        self.assertEqual(_library_target("", "plugin.la", set()), ("plugin", None))

    def test_configured_subdirs(self) -> None:
        """A computed SUBDIRS plus children with their own configure.ac: texlive's libs/."""
        import tempfile
        from tools.convert.project import _configured_subdirs
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text("SUBDIRS = . $(MAKE_SUBDIRS)\n")
            for child, has_configure in (("zlib", True), ("mpfi", True), ("doc", True), ("am", False)):
                (root / child).mkdir()
                if has_configure:
                    (root / child / "configure.ac").write_text("AC_INIT\n")
            self.assertEqual(_configured_subdirs(root, {"zlib"}), ["mpfi"])
            (root / "Makefile.am").write_text("SUBDIRS = zlib mpfi\n")
            self.assertEqual(_configured_subdirs(root, set()), [])

    def test_makefile_in_targets(self) -> None:
        """An autoconf project without automake: libraries and programs from Makefile.in's rules."""
        import tempfile
        from tools.convert.decisions import Decisions
        from tools.convert.ir import Package
        from tools.convert.project import _programs_tree
        from tools.convert.translate import ProjectState
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.in").write_text(
                "CSRC = fptype.c tcpdump.c\nLIBSRC = print.c @LOCALSRC@\nOBJ = $(CSRC:.c=.o)\n"
                "LIBOBJ = $(LIBSRC:.c=.o) @LIBOBJS@\nPROG = tcpdump\nLIB = libnetdissect.a\n"
                "$(PROG): $(OBJ) @V_PCAPDEP@ $(LIB)\n\t@rm -f $@\n\t$(CC) $(CFLAGS) -o $@ $(OBJ) $(LIB) $(LIBS)\n"
                "$(LIB): $(LIBOBJ)\n\t$(AR) cr $@ $(LIBOBJ)\n\t$(RANLIB) $@\n"
                "clean:\n\trm -f *.o\n")
            for name in ("fptype.c", "tcpdump.c", "print.c"):
                (root / name).write_text("")
            package = Package(path="", docstring="")
            state = ProjectState()
            state.config_targets = ["config"]
            state.header_libraries = [":config_headers"]
            state.literal_substs = {"LOCALSRC": ""}
            notes: list[str] = []
            _programs_tree(package, root, None, Decisions(), state, notes)
            rules = {r.name: r for r in package.after if hasattr(r, "kind")}
            self.assertEqual(rules["netdissect"].kind, "cc_library")
            self.assertEqual(rules["netdissect"].attrs["srcs"], ["print.c"])
            self.assertEqual(rules["tcpdump"].kind, "cc_binary")
            self.assertEqual(rules["tcpdump"].attrs["srcs"], ["fptype.c", "tcpdump.c"])
            self.assertIn(":netdissect", rules["tcpdump"].attrs["deps"])
            self.assertTrue(any("@LIBOBJS@" in n for n in notes))
            self.assertTrue(any("@V_PCAPDEP@" in n for n in notes))

    def test_port_provided_names_drop_local_checks(self) -> None:
        """A local check whose define or subst a port dependency already provides goes (gzip's
        AC_CHECK_FUNCS([fchown]) next to gl_FUNC_CHOWN; libtool's OBJDUMP next to a port that substs it)."""
        from tools.convert.index import Port, PortIndex
        from tools.convert.ir import Target
        from tools.convert.project import _drop_duplicate_defines
        index = PortIndex([Port(package="gnulib/m4/chown", targets={"gl_FUNC_CHOWN"}, defines={"HAVE_FCHOWN"}),
                           Port(package="gnulib/m4/sys_socket_h", targets={"gl_SYS_SOCKET_H"},
                                defines={"HAVE_SHUTDOWN"}, substs={"OBJDUMP"})])
        main = Target(name="autoconf", checks=[
            Call("macros.AC_CHECK_FUNCS", [["fchown", "fchmod"]]),
            Call("checks.AC_SUBST", ["OBJDUMP", "objdump"]),
            Call("checks.AC_SUBST", ["OBJDUMP", "objdump"]),
            Call("checks.AC_SUBST", ["NM", "nm"]),
            Call("checks.AC_TRY_LINK", [], {"name": "ac_cv_func_shutdown", "define": "HAVE_SHUTDOWN", "code": "x"}),
            # a define next to a port's *subst* of the same name: different namespaces, both stay
            Call("checks.AC_DEFINE", ["OBJDUMP", 1]),
            Call("checks.AC_SUBST", ["HAVE_SHUTDOWN", "1"]),
        ])
        main.add_dep("@rules_cc_autoconf//gnulib/m4/chown:gl_FUNC_CHOWN")
        main.add_dep("//gnulib/m4/sys_socket_h")
        main.add_dep(":package")
        notes = _drop_duplicate_defines(main, [], index)
        # fchown's cache variable stays as a define-less probe (gnulib.mk conditions read it)
        self.assertEqual([(c.fn, c.args) for c in main.checks],
                         [("macros.AC_CHECK_FUNCS", [["fchmod"]]), ("checks.AC_CHECK_FUNC", ["fchown"]),
                          ("checks.AC_SUBST", ["NM", "nm"]),
                          ("checks.AC_DEFINE", ["OBJDUMP", 1]), ("checks.AC_SUBST", ["HAVE_SHUTDOWN", "1"])])
        self.assertTrue(any("gl_FUNC_CHOWN already provides HAVE_FCHOWN" in n or "fchown" in n for n in notes), notes)

    def test_m4_cache_name(self) -> None:
        """bison names one cache variable per language with m4: the C and C++ spellings."""
        from tools.convert.translate import m4_cache_name
        name = "bison_cv_[]_AC_LANG_ABBREV[]_supports_posixly_correct"
        self.assertEqual(m4_cache_name(name, "c"), "bison_cv_c_supports_posixly_correct")
        self.assertEqual(m4_cache_name(name, "cpp"), "bison_cv_cxx_supports_posixly_correct")
        self.assertEqual(m4_cache_name("ac_cv_[]_AC_LANG_PREFIX[]_works", "cpp"), "ac_cv_CXX_works")

    def test_check_tool_substitutes_the_program(self) -> None:
        """AC_CHECK_TOOL(VAR, prog, not-found): the program, not the not-found value (libtool's
        `AC_CHECK_TOOL(OBJDUMP, objdump, false)` must not substitute "false")."""
        from tools.convert.decisions import Decisions
        lines = ["#! /bin/sh"] + call(2, "AC_CHECK_TOOL", "OBJDUMP", "objdump", "false") + \
            call(3, "AC_CHECK_PROG", "HAVE_GPERF", "gperf", "yes", "no") + call(4, "AC_PATH_PROG", "PERL", "perl")
        tree = markers.parse("\n".join(lines) + "\n")
        translator = Translator(tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={},
                                options=Options(), decisions=Decisions())
        translator.translate()
        main = translator.translate_root("autoconf", loc_file="configure.ac")
        self.assertEqual([c.args for c in main.checks if isinstance(c, Call) and c.fn == "checks.AC_SUBST"],
                         [["OBJDUMP", "objdump"], ["HAVE_GPERF", "yes"], ["PERL", "perl"]])

    def test_inline_conditional_header(self) -> None:
        """gnulib-comp.m4 carries the stdckdint-h module's code inline: the header check, the
        STDCKDINT_H assignment and gl_CONDITIONAL_HEADER; GL_GENERATE_STDCKDINT_H follows."""
        from tools.convert.decisions import Decisions
        from tools.convert.translate import ProjectState, describe
        lines = ["#! /bin/sh"] + call(2, "AC_CHECK_HEADERS_ONCE", "stdckdint.h") + [
            "if test $ac_cv_header_stdckdint_h = yes; then", "  GL_GENERATE_STDCKDINT_H=false", "else",
            "  GL_GENERATE_STDCKDINT_H=true", "fi"] + call(3, "gl_CONDITIONAL_HEADER", "stdckdint.h")
        tree = markers.parse("\n".join(lines) + "\n")
        state = ProjectState()
        translator = Translator(tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={},
                                options=Options(), decisions=Decisions(), project=state)
        translator.translate()
        translator.translate_root("autoconf", loc_file="configure.ac")
        self.assertIn("GL_GENERATE_STDCKDINT_H", state.header_conditions)
        self.assertEqual(describe(state.header_conditions["GL_GENERATE_STDCKDINT_H"]), "!ac_cv_header_stdckdint_h")

    def test_program_lookup_seeds_the_shell(self) -> None:
        """flex: AC_PATH_PROGS_FEATURE_CHECK inside AC_CACHE_CHECK, then AC_SUBST([M4], [$ac_cv_path_M4])
        and AC_DEFINE_UNQUOTED([M4], ["$M4"]): the plain program name stands in."""
        from tools.convert.decisions import Decisions
        from tools.convert.translate import ProjectState
        inner = "\n".join(call(3, "AC_PATH_PROGS_FEATURE_CHECK", "M4", "gm4 gnum4 m4", "test x = x", "false"))
        lines = ["#! /bin/sh"] + call(2, "AC_CACHE_CHECK", "for m4", "ac_cv_path_M4", inner, body=inner) + \
            call(4, "AC_SUBST", "M4", "$ac_cv_path_M4", body="M4=$ac_cv_path_M4") + \
            call(5, "AC_DEFINE_UNQUOTED", "M4", '"$M4"', "Define to the m4 executable name.")
        tree = markers.parse("\n".join(lines) + "\n")
        state = ProjectState()
        translator = Translator(tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={},
                                options=Options(), decisions=Decisions(), project=state)
        translator.translate()
        main = translator.translate_root("autoconf", loc_file="configure.ac")
        calls = [(c.fn, c.args) for c in main.checks if isinstance(c, Call)]
        self.assertIn(("checks.AC_SUBST", ["M4", "m4"]), calls, (calls, translator.report.notes))
        self.assertIn(("checks.AC_DEFINE", ["M4", '"m4"']), calls, (calls, translator.report.notes))

    def test_config_subdirs(self) -> None:
        """AC_CONFIG_SUBDIRS: literal directories are recorded, computed ones reported."""
        from tools.convert.decisions import Decisions
        from tools.convert.translate import ProjectState
        lines = ["#! /bin/sh"] + call(2, "AC_CONFIG_SUBDIRS", "libs utils $extra")
        tree = markers.parse("\n".join(lines) + "\n")
        state = ProjectState()
        translator = Translator(tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={},
                                options=Options(), decisions=Decisions(), project=state)
        translator.translate()
        translator.translate_root("autoconf", loc_file="configure.ac")
        self.assertEqual(state.config_subdirs, ["libs", "utils"])
        self.assertTrue(any("$extra" in n for n in translator.report.notes))

    def test_public_headers_and_implementation_deps(self) -> None:
        """A library's hdrs are the headers automake installs; a dependency only the sources
        use is an implementation dep, one a public header includes stays a dep."""
        import tempfile
        from tools.convert.decisions import Decisions
        from tools.convert.ir import Package
        from tools.convert.project import _programs_tree
        from tools.convert.translate import ProjectState
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text("SUBDIRS = include lib\n")
            (root / "include").mkdir()
            (root / "include" / "Makefile.am").write_text("include_HEADERS = bar_api.h\n")
            (root / "include" / "bar_api.h").write_text("#include <stddef.h>\n")
            (root / "lib").mkdir()
            (root / "lib" / "Makefile.am").write_text(
                "lib_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\ninclude_HEADERS = bar.h\nnoinst_HEADERS = priv.h\n")
            (root / "lib" / "a.c").write_text("")
            (root / "lib" / "priv.h").write_text("")
            for includes in ('#include "priv.h"\n', '#include "config.h"\n#include "priv.h"\n'):
                (root / "lib" / "bar.h").write_text(includes)
                package = Package(path="", docstring="")
                state = ProjectState()
                state.config_targets = ["config"]
                state.config_headers = ["config.h"]
                state.header_libraries = [":config_headers"]
                _programs_tree(package, root, None, Decisions(), state, [])
                rules = {r.name: r for r in package.after if hasattr(r, "kind")}
                bar = rules["lib/bar"]
                # The tree's one installed library also carries include/'s headers.
                self.assertEqual(bar.attrs["hdrs"], ["include/bar_api.h", "lib/bar.h"])
                self.assertEqual(bar.attrs["includes"], ["include", "lib"])
                # The non-installed headers: a private headers library (lib/bar.h excluded from its glob).
                private = rules["lib/bar_private_headers"]
                self.assertIn('"lib/bar.h"', private.attrs["hdrs"].text)
                self.assertEqual(private.attrs["visibility"], ["//visibility:private"])
                # bar.h includes priv.h, so consumers need the private headers: a public dep.
                if "config.h" in includes:
                    self.assertEqual(bar.attrs["deps"], [":config_headers", ":lib/bar_private_headers"])
                    self.assertNotIn("implementation_deps", bar.attrs)
                else:
                    self.assertEqual(bar.attrs["deps"], [":lib/bar_private_headers"])
                    self.assertEqual(bar.attrs["implementation_deps"], [":config_headers"])

    def test_language_standards(self) -> None:
        """AX_CXX_COMPILE_STDCXX / AC_PROG_CC_C99 / CFLAGS=-std=: requirement checks, not flags."""
        from tools.convert.decisions import Decisions
        from tools.convert.translate import ProjectState

        # The configure.ac top level (project mode): no DEFUN markers around the calls.
        lines = ["#! /bin/sh", 'CFLAGS="$CFLAGS -std=gnu11"']
        lines += call(2, "AX_CXX_COMPILE_STDCXX", "17", "noext", "mandatory")
        lines += call(3, "AC_PROG_CC_C99")
        tree = markers.parse("\n".join(lines) + "\n")
        state = ProjectState()
        translator = Translator(tree, index=INDEX, selected_files={"configure.ac"}, m4_sources={},
                                options=Options(), decisions=Decisions(), project=state)
        translator.translate()
        main = translator.translate_root("autoconf", loc_file="configure.ac")
        # CFLAGS=-std=gnu11 is the script's own flag; the macros become requirement checks.
        self.assertEqual(state.c_standard, "gnu11")
        self.assertIsNone(state.cxx_standard)
        self.assertEqual(
            [(c.fn, c.args, c.kwargs) for c in main.checks if isinstance(c, Call)],
            [("checks.AX_CXX_COMPILE_STDCXX", ["17", "noext"], {}),
             ("checks.AC_LANG_STANDARD", ["c99"], {"error": True})],
        )
        self.assertEqual(state.required_standards, {"c++17", "c99"})

    def test_subst_decision_drops_local_copy(self) -> None:
        """`subst:X: IGNORE` leaves out a substitution a dependency already provides."""
        from tools.convert.decisions import Decisions

        lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_W", "loc=m4/w.m4:1"),
                 "GNULIBHEADERS_OVERRIDE_WINT_T=0\nOTHER=1"]
        lines += call(2, "AC_SUBST", "GNULIBHEADERS_OVERRIDE_WINT_T")
        lines += call(3, "AC_SUBST", "OTHER")
        lines.append(marker("END", "id=1", "name=gl_W"))
        tree = markers.parse("\n".join(lines) + "\n")
        m4 = "AC_DEFUN([gl_W], [AC_SUBST([GNULIBHEADERS_OVERRIDE_WINT_T]) AC_SUBST([OTHER])])\n"

        def names(decisions: Decisions) -> list[str]:
            translator = Translator(tree, index=INDEX, selected_files={"m4/w.m4"}, m4_sources={"m4/w.m4": m4},
                                    options=Options(), decisions=decisions)
            (package,) = translator.translate()
            return [c.args[0] for c in package.targets[0].checks if isinstance(c, Call)]

        self.assertEqual(names(Decisions()), ["GNULIBHEADERS_OVERRIDE_WINT_T", "OTHER"])
        decided = Decisions.from_text('DECISIONS = {"subst:GNULIBHEADERS_OVERRIDE_WINT_T": IGNORE}')
        self.assertEqual(names(decided), ["OTHER"])

    def test_builtin_without_port(self) -> None:
        """AC_HEADER_DIRENT has a fixed translation; AC_FUNC_MMAP is a decision."""
        from tools.convert.decisions import Decisions

        lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_B", "loc=m4/b.m4:1")]
        lines += call(2, "AC_HEADER_DIRENT")
        lines += call(3, "AC_FUNC_MMAP")
        lines += call(4, "AC_PROG_SED")
        lines.append(marker("END", "id=1", "name=gl_B"))
        tree = markers.parse("\n".join(lines) + "\n")
        asked = Decisions()
        translator = Translator(tree, index=INDEX, selected_files={"m4/b.m4"},
                                m4_sources={"m4/b.m4": "AC_DEFUN([gl_B], [AC_HEADER_DIRENT AC_FUNC_MMAP AC_PROG_SED])"},
                                options=Options(), decisions=asked)
        (package,) = translator.translate()
        (check,) = [c for c in package.targets[0].checks if isinstance(c, Call)]
        self.assertEqual((check.fn, check.args), ("macros.AC_CHECK_HEADERS", [["dirent.h"]]))
        self.assertEqual(list(asked.pending), ["macro:AC_FUNC_MMAP"])
        self.assertTrue(any("AC_PROG_SED" in n and "dropped" in n for n in translator.report.notes))

    def test_project_helpers(self) -> None:
        from tools.convert import dataflow as df
        from tools.convert.project import _platform_labels, default_tarname

        self.assertEqual(default_tarname("GNU M4"), "m4")
        self.assertEqual(default_tarname("gzip"), "gzip")
        self.assertEqual(default_tarname("GNU Libtool Plus"), "libtool-plus")
        windows = df.HostOs(("mingw*", "windows*"))
        self.assertEqual(_platform_labels(windows), ["@platforms//os:windows"])
        self.assertEqual(_platform_labels(df.disj(windows, df.HostOs(("darwin*",)))),
                         ["@platforms//os:windows", "@platforms//os:macos"])
        self.assertIsNone(_platform_labels(df.neg(windows)))  # holds on the default arm too
        self.assertIsNone(_platform_labels(df.conj(windows, df.Truthy("gl_cv_x"))))
        self.assertIsNone(_platform_labels(df.Truthy("gl_cv_x")))

    def test_case_conditions(self) -> None:
        from tools.convert import dataflow as df
        from tools.convert.translate import Translator

        tree = markers.parse("#! /bin/sh\n")
        translator = Translator(tree, index=INDEX, selected_files=set(), m4_sources={}, options=Options())
        self.assertEqual(translator._test_condition('case "$host_os" in mingw* | windows*) true;; *) false;; esac'),
                         df.HostOs(("mingw*", "windows*")))
        self.assertEqual(translator._test_condition('case "$host_os" in darwin*) false ;; *) true ;; esac'),
                         df.neg(df.HostOs(("darwin*",))))
        self.assertIsInstance(translator._test_condition('case "$host_os" in darwin*) true ;; *) true ;; esac'),
                              df.Unknown)

    def test_never_set_cache_variable_is_empty_in_project_mode(self) -> None:
        """`if test "$am_cv_func_iconv" = yes` when nothing sets it: configure
        reads an empty variable, so the guarded check never applies (project
        mode); a single m4 file cannot know and keeps the requires."""
        from tools.convert.translate import ProjectState

        lines = ["#! /bin/sh", marker("BEGIN", "id=1", "name=gl_U", "loc=m4/u.m4:1")]
        lines.append('if test "$am_cv_func_iconv" = yes; then')
        lines += call(2, "AC_CHECK_FUNCS", "locale_charset")
        lines.append("fi")
        lines.append(marker("END", "id=1", "name=gl_U"))
        tree = markers.parse("\n".join(lines) + "\n")
        m4 = "AC_DEFUN([gl_U], [if test \"$am_cv_func_iconv\" = yes; then AC_CHECK_FUNCS([locale_charset]) fi])"

        def checks(project):
            translator = Translator(tree, index=INDEX, selected_files={"m4/u.m4"}, m4_sources={"m4/u.m4": m4},
                                    options=Options(layout="package" if project else "per-file"), project=project)
            packages = translator.translate()
            if project:  # project mode: the project's macros expand into the configure.ac target
                target = translator.translate_root("autoconf", loc_file="configure.ac")
            else:
                (package,) = packages
                target = package.targets[0]
            return [c for c in target.checks if isinstance(c, Call)]

        self.assertEqual(checks(ProjectState()), [])
        (kept,) = checks(None)
        self.assertEqual(kept.kwargs.get("requires"), ["am_cv_func_iconv"])

    def test_confdefs_segments_and_compile_defines(self) -> None:
        """A probe whose preamble tests HAVE_SYS_TIME_H needs that result from a
        dependency: the checks before it become an earlier target and the
        probe lists the define; declaration checks list the extension
        macros when AC_USE_SYSTEM_EXTENSIONS is a dependency."""
        from tools.convert.ir import Dep, Target
        from tools.convert.project import _confdefs

        main = Target(name="autoconf")
        main.deps.append(Dep("@rules_cc_autoconf//autoconf/macros/AC_USE_SYSTEM_EXTENSIONS"))
        main.checks.append(Call("macros.AC_CHECK_HEADERS", [["sys/time.h"]]))
        main.checks.append(Call("checks.AC_TRY_COMPILE", [], {
            "name": "bash_cv_struct_timeval",
            "code": Call("utils.AC_LANG_PROGRAM", [["#if HAVE_SYS_TIME_H", "#include <sys/time.h>", "#endif"],
                                                   ["struct timeval x;"]])}))
        main.checks.append(Call("macros.AC_CHECK_DECLS", [["execvpe"]]))
        earlier, extensions = _confdefs(main)
        self.assertTrue(extensions)
        self.assertEqual([t.name for t in earlier], ["autoconf_1"])
        self.assertEqual([c.fn for c in earlier[0].checks], ["macros.AC_CHECK_HEADERS"])
        probe, decls = main.checks
        self.assertEqual(probe.kwargs["compile_defines"].text, 'SYSTEM_EXTENSIONS + ["HAVE_SYS_TIME_H"]')
        self.assertEqual(decls.kwargs["compile_defines"].text, "SYSTEM_EXTENSIONS")
        self.assertNotIn("compile_defines", earlier[0].checks[0].kwargs)  # header existence: no feature macros
        self.assertEqual([d.label for d in main.deps][-1], ":autoconf_1")

    def test_automake_tree_and_programs(self) -> None:
        """Programs in SUBDIRS get a cc_binary; LDADD names the library."""
        import tempfile
        from tools.convert.ir import Package
        from tools.convert.project import _automake_tree, _programs_tree
        from tools.convert.translate import ProjectState

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text(
                "SUBDIRS = lib src tests\nnoinst_LIBRARIES = libver.a libfoo.a\nlibver_a_SOURCES = version.c\n"
                "libver_a_LIBADD = $(LIBINTL)\nlibfoo_a_SOURCES = foo.c\nbin_PROGRAMS = foo\nfoo_SOURCES = cli.c\n")
            (root / "lib").mkdir()
            (root / "lib" / "Makefile.am").write_text("noinst_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\n")
            (root / "src").mkdir()
            for name in ("version.c", "foo.c", "cli.c", "lib/a.c", "src/main.c", "src/util.c", "src/parse.y",
                         "src/scan.l"):
                (root / name).write_text("")
            (root / "src" / "Makefile.am").write_text(
                "bin_PROGRAMS = foo\nAM_YFLAGS = -d\nfoo_SOURCES = main.c util.c parse.y scan.l\n"
                "pkgdatadir = $(datadir)/awk\nDEFPATH='\".$(PATH_SEPARATOR)$(pkgdatadir)\"'\n"
                "DEFLIBPATH=\"\\\"$(pkgextensiondir)\\\"\"\n"
                "DEFS = -DGAWK -DHAVE_CONFIG_H -DDEFPATH=$(DEFPATH) -DDEFLIBPATH=$(DEFLIBPATH) -DSHLIBEXT=$(SHLIBEXT) "
                "-DLOCALEDIR='\"$(datadir)/locale\"' -I\"$(srcdir)/..\" -I$(top_srcdir)/lib\n"
                "LDADD = ../lib/libbar.a ../libver.a $(LIBINTL) -lm\n"
                "$(srcdir)/parse.c: parse.y\n\t$(YACC) -o $@ -p zz $<\n")
            (root / "tests").mkdir()
            (root / "tests" / "Makefile.am").write_text("check_PROGRAMS = t\nt_SOURCES = t.c\n")
            self.assertEqual([d for d, _, _ in _automake_tree(root)], ["", "lib", "src"])
            package = Package(path="", docstring="")
            state = ProjectState()
            state.config_targets = ["config"]
            state.header_libraries = [":config_headers"]
            state.tarname = "gawk"
            state.literal_substs = {"pkgextensiondir": "${pkglibdir}"}
            notes: list[str] = []
            _programs_tree(package, root, None, Decisions(), state, notes)
            rules = {r.name: r for r in package.after if hasattr(r, "kind")}
            # libfoo.a is `foo` so that the output is libfoo.a; at the top level a
            # program takes that name, so the library is `lib/foo` with the archive
            # name as an alias. Without a conflict (libver.a) there is no alias.
            self.assertEqual(rules["lib/foo"].kind, "cc_library")
            self.assertEqual(rules["lib/foo"].attrs["srcs"], ["foo.c"])
            self.assertEqual(rules["libfoo"].attrs["actual"], ":lib/foo")
            self.assertEqual(rules["foo"].kind, "cc_binary")
            self.assertEqual(rules["ver"].attrs["srcs"], ["version.c"])
            # No installed headers: every dependency is an implementation detail.
            self.assertEqual(rules["ver"].attrs["implementation_deps"], [":config_headers", ":linkopts"])
            self.assertNotIn("deps", rules["ver"].attrs)
            self.assertNotIn("hdrs", rules["ver"].attrs)
            self.assertEqual(rules["lib/bar"].attrs["includes"], ["lib"])
            self.assertEqual(rules["src/headers"].attrs["deps"], [":config_headers"])
            self.assertNotIn("libver", rules)
            # yacc and lex sources become bison()/flex() rules whose outputs the
            # program compiles; the hand-written rule's -p zz carries over, -d not.
            self.assertEqual(rules["src/bison/parse"].load_from, "@bison//:bison.bzl")
            self.assertEqual(rules["src/bison/parse"].attrs, {
                "srcs": ["src/parse.y"], "outs": ["src/bison/parse.c", "src/bison/parse.h"],
                "args": ["-o", "$(execpath src/bison/parse.c)", "--defines=$(execpath src/bison/parse.h)",
                         "-p", "zz", "$(execpath src/parse.y)"]})
            self.assertEqual(rules["src/flex/scan"].attrs, {
                "srcs": ["src/scan.l"], "outs": ["src/flex/scan.c", "src/flex/scan.h"],
                "args": ["-o", "$(execpath src/flex/scan.c)", "--header-file=$(execpath src/flex/scan.h)",
                         "$(execpath src/scan.l)"]})
            foo = rules["src/foo"]
            self.assertEqual(foo.attrs["srcs"], ["src/bison/parse.c", "src/bison/parse.h", "src/flex/scan.c",
                                                 "src/flex/scan.h", "src/main.c", "src/util.c"])
            self.assertEqual(foo.attrs["copts"], ["-w", "-I$(BINDIR)/src/bison", "-I$(BINDIR)/src/flex"])
            self.assertEqual(foo.attrs["includes"], [".", "lib"])  # -I of CPPFLAGS: package-relative, both roots
            self.assertEqual(state.makefile_standards, {})
            self.assertEqual(foo.attrs["local_defines"], [
                "HAVE_CONFIG_H", "GAWK", 'DEFPATH="\\".:/usr/local/share/awk\\""',
                'DEFLIBPATH="\\"/usr/local/lib/gawk\\""', 'LOCALEDIR="\\"/usr/local/share/locale\\""'])
            self.assertTrue(any("SHLIBEXT" in n and "left out" in n for n in notes))
            self.assertEqual(foo.attrs["deps"], [":lib/bar", ":linkopts", ":src/headers", ":ver"])
            self.assertEqual(foo.attrs["linkopts"], ["-lm"])
            self.assertEqual(rules["linkopts"].attrs["vars"], ["LIBINTL"])

    def test_shared_libraries_and_aliases(self) -> None:
        """Installed libtool libraries get a cc_shared_library; subdirectory targets a root alias."""
        import tempfile
        from tools.convert.ir import Package
        from tools.convert.project import _programs_tree
        from tools.convert.translate import ProjectState

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text("SUBDIRS = lib src ext\n")
            for sub, text in (("lib", "lib_LTLIBRARIES = libbar.la\nlibbar_la_SOURCES = a.c\n"),
                              ("src", "bin_PROGRAMS = foo\nfoo_SOURCES = main.c\n"),
                              ("ext", "pkgext_LTLIBRARIES = plug.la\nplug_la_SOURCES = p.c\nplug_la_LDFLAGS = -module\n"
                                      "noinst_LTLIBRARIES = libhelper.la\nlibhelper_la_SOURCES = h.c\n")):
                (root / sub).mkdir()
                (root / sub / "Makefile.am").write_text(text)
            for name in ("lib/a.c", "src/main.c", "ext/p.c", "ext/h.c"):
                (root / name).write_text("")
            package = Package(path="", docstring="")
            state = ProjectState()
            _programs_tree(package, root, None, Decisions(), state, [])
            rules = {r.name: r for r in package.after if hasattr(r, "kind")}
            shared = rules["lib/bar_shared"]
            self.assertEqual(shared.kind, "cc_shared_library")
            self.assertEqual(shared.attrs["deps"], [":lib/bar"])
            self.assertIn('"lib/libbar.dylib"', shared.attrs["shared_lib_name"].text)
            self.assertIn('"@platforms//os:windows": "lib/bar.dll"', shared.attrs["shared_lib_name"].text)
            self.assertIn('"ext/plug.so"', rules["ext/plug_shared"].attrs["shared_lib_name"].text)
            self.assertNotIn("ext/helper_shared", rules)  # noinst_: a convenience library
            self.assertEqual(rules["foo"].attrs["actual"], ":src/foo")
            self.assertEqual(rules["bar"].attrs["actual"], ":lib/bar")
            self.assertEqual(rules["plug"].attrs["actual"], ":ext/plug")
            self.assertNotIn("headers", rules)

    def test_project_decisions(self) -> None:
        """What a Makefile.am leaves to the user: conditional library sources, files no
        translatable recipe writes, -D values only configure knows, odd LDADD items."""
        import tempfile
        from tools.convert.ir import Package, Raw
        from tools.convert.project import _programs_tree
        from tools.convert.translate import ProjectState

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text("SUBDIRS = lib src\n")
            (root / "lib").mkdir()
            (root / "lib" / "Makefile.am").write_text(
                "noinst_LIBRARIES = libbar.a\nlibbar_a_SOURCES = a.c\nif WITH_X\nlibbar_a_SOURCES += x.c\nendif\n")
            (root / "src").mkdir()
            (root / "src" / "Makefile.am").write_text(
                "bin_PROGRAMS = foo\nfoo_SOURCES = main.c gen.c\nBUILT_SOURCES = tab.h\n"
                "DEFS = -DSHLIBEXT=$(SHLIBEXT) -DHAVE_CONFIG_H -std=gnu99\nLDADD = ../lib/libbar.a ../other/libz.a\n"
                "gen.c: gen.sh\n\t./gen.sh > $@\n")
            for name in ("lib/a.c", "lib/x.c", "src/main.c", "src/gen.sh"):
                (root / name).write_text("")

            states: list[ProjectState] = []

            def convert(decisions):
                package = Package(path="", docstring="")
                state = ProjectState()
                state.config_targets = ["config"]
                state.header_libraries = [":config_headers"]
                notes: list[str] = []
                _programs_tree(package, root, None, decisions, state, notes)
                states.append(state)
                return package, {r.name: r for r in package.after if hasattr(r, "kind")}, notes

            asked = Decisions()
            package, rules, notes = convert(asked)
            self.assertEqual(sorted(asked.pending), [
                "am_conditional:WITH_X", "cppflag:src:SHLIBEXT", "generated:src/gen.c", "generated:src/tab.h",
                "link:src/foo:../other/libz.a"])
            # lib/ has no header that is not installed: no private headers library.
            self.assertEqual(rules["lib/bar"].attrs["srcs"], ["lib/a.c"])
            self.assertNotIn("lib/bar_private_headers", rules)
            self.assertEqual(rules["src/foo"].attrs["srcs"], ["src/main.c"])  # gen.c, tab.h: undecided, left out
            self.assertEqual(rules["src/foo"].attrs["local_defines"], ["HAVE_CONFIG_H"])
            self.assertEqual(rules["src/foo"].attrs["deps"], [":lib/bar", ":src/headers"])

            decided = Decisions.from_text('''DECISIONS = {
    "am_conditional:WITH_X": ALWAYS,
    "cppflag:src:SHLIBEXT": "\\"so\\"",
    "generated:src/gen.c": genrule(name = "gen_c", srcs = ["src/gen.sh"], outs = ["src/gen.c"], cmd = "sh $< > $@"),
    "generated:src/tab.h": IGNORE,
    "link:src/foo:../other/libz.a": "@other//:z",
}''')
            package, rules, notes = convert(decided)
            self.assertIn("lib/x.c", rules["lib/bar"].attrs["srcs"])  # the decided source
            self.assertEqual(rules["src/foo"].attrs["srcs"], ["src/gen.c", "src/main.c"])
            self.assertEqual(rules["src/foo"].attrs["local_defines"], ["HAVE_CONFIG_H", 'SHLIBEXT="\\"so\\""'])
            # A `-std=` of the Makefile's flags is not pinned on the target: it is recorded, and the
            # main target checks for it (_require_standards).
            self.assertEqual(rules["src/foo"].attrs["copts"], ["-w"])
            self.assertNotIn("conlyopts", rules["src/foo"].attrs)
            self.assertEqual(states[-1].makefile_standards, {"C": "gnu99"})
            self.assertEqual(rules["src/foo"].attrs["deps"], [":lib/bar", ":src/headers", "@other//:z"])
            self.assertTrue(any(isinstance(s, Raw) and s.text.startswith("genrule(") for s in package.after))
            self.assertEqual(decided.pending, {})

    def test_written_sources(self) -> None:
        """A source a Makefile.am writes with printf becomes a write_file, through an
        autoconf_hdr(mode = "subst") when it carries configure's values."""
        import tempfile
        from tools.convert.ir import Package
        from tools.convert.project import _programs_tree, _written_content
        from tools.convert.translate import ProjectState

        self.assertEqual(_written_content(["$(AM_V_GEN)rm -f $@", "printf 'a\\n' > $@t",
                                           "printf 'v = \"$(PACKAGE_VERSION)\";\\n' >> $@t", "mv $@t $@"]),
                         ["a", "v = @PACKAGE_VERSION@;", ""])
        self.assertIsNone(_written_content(["sed 's/x/y/' < $< > $@"]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile.am").write_text(
                "noinst_LIBRARIES = libver.a\nnodist_libver_a_SOURCES = version.c version.h\n"
                "version.c: Makefile\n\tprintf '#include \"version.h\"\\n' > $@t\n"
                "\tprintf 'char const *Version = \"$(PACKAGE_VERSION)\";\\n' >> $@t\n\tmv $@t $@\n"
                "version.h: Makefile\n\tprintf 'extern char const *Version;\\n' > $@t\n\tmv $@t $@\n")
            package = Package(path="", docstring="")
            state = ProjectState()
            state.config_targets = ["config"]
            state.header_libraries = [":config_headers"]
            notes: list[str] = []
            _programs_tree(package, root, None, Decisions(), state, notes)
            rules = {r.name: r for r in package.after if hasattr(r, "kind")}
            self.assertEqual(rules["version_c_in"].kind, "write_file")
            self.assertEqual(rules["version_c_in"].attrs["content"],
                             ['#include "version.h"', "char const *Version = @PACKAGE_VERSION@;", ""])
            self.assertEqual(rules["version_c"].attrs, {"out": "version.c", "mode": "subst",
                                                        "template": "version.c.in", "deps": [":autoconf"]})
            self.assertEqual(rules["version_h"].kind, "write_file")
            self.assertEqual(rules["version_h"].attrs["content"], ["extern char const *Version;", ""])
            self.assertEqual(rules["ver"].attrs["srcs"], ["version.c", "version.h"])
            self.assertEqual(notes, [])

    def test_template_indicators(self) -> None:
        """The @GNULIB_X@ a wrapper template reads, for the ones no port defaults."""
        import tempfile
        from tools.convert.project import _template_indicators

        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "stdio.in.h"
            template.write_text("#if @GNULIB_VAPRINTF@\n#endif\n#if @GNULIB_PRINTF@ || @HAVE_DECL_X@\n")
            self.assertEqual(_template_indicators([template, Path(tmp) / "missing.in.h"]),
                             {"GNULIB_VAPRINTF", "GNULIB_PRINTF"})

    def test_literal_sed_substs(self) -> None:
        """gnulib.mk's `@''GUARD_PREFIX''@|GL` is a literal, not a configure variable."""
        from tools.convert.project import _literal_sed_substs

        text = ("\t  -e 's|@''GUARD_PREFIX''@|GL|g' \\\n\t  -e 's|@''HAVE_STDDEF_H''@|$(HAVE_STDDEF_H)|g' \\\n"
                "\t  -e 's|@''GUARD_PREFIX''@|GL|g'\n")
        self.assertEqual(_literal_sed_substs(text), {"GUARD_PREFIX": "GL"})

    def test_tool_modules(self) -> None:
        """Parsers and scanners bring the BCR bison / flex modules, nothing else."""
        from tools.convert.project import _module_bazel

        module = _module_bazel("GNU awk", "5.4.0", "0.26.0", None, False, ["bison"])
        self.assertIn('bazel_dep(name = "bison", version = "3.8.2.bcr.11")', module)
        self.assertNotIn('name = "flex"', module)
        self.assertNotIn("rules_bison", module)
        self.assertNotIn("register_toolchains", module)
        self.assertIn('bazel_dep(name = "flex", version = "2.6.4.bcr.8")',
                      _module_bazel("x", "1", "0.26.0", None, False, ["bison", "flex"]))

if __name__ == "__main__":
    unittest.main()
