"""Unit tests for the automake fragment parser."""

from __future__ import annotations

import textwrap
import unittest

from tools.convert import gnulibmk

GNULIB_MK = textwrap.dedent("""\
    noinst_LIBRARIES += libgzip.a

    libgzip_a_SOURCES =
    libgzip_a_LIBADD = $(gl_libgzip_LIBOBJS)
    EXTRA_libgzip_a_SOURCES =

    ## begin gnulib module alloca-opt

    BUILT_SOURCES += $(ALLOCA_H)

    if GL_GENERATE_ALLOCA_H
    alloca.h: alloca.in.h $(top_builddir)/config.status
    \t$(gl_V_at)$(SED_HEADER_STDOUT) \\
    \t  -e 's|@''HAVE_ALLOCA_H''@|$(HAVE_ALLOCA_H)|g' \\
    \t  $(srcdir)/alloca.in.h > $@-t
    \t$(AM_V_at)mv $@-t $@
    else
    alloca.h: $(top_builddir)/config.status
    \trm -f $@
    endif
    MOSTLYCLEANFILES += alloca.h alloca.h-t

    ## end   gnulib module alloca-opt

    ## begin gnulib module assert-h

    BUILT_SOURCES += $(ASSERT_H)

    if GL_GENERATE_ASSERT_H
    assert.h: assert.in.h verify.h $(top_builddir)/config.status
    \t$(gl_V_at){ $(SED_HEADER_STDOUT) \\
    \t      < $(srcdir)/assert.in.h && \\
    \t  sed -e 's|_gl_verify|_gl_static_assert|g' \\
    \t      < $(srcdir)/verify.h; \\
    \t} > $@-t
    endif

    ## end   gnulib module assert-h

    ## begin gnulib module c-ctype

    libgzip_a_SOURCES += c-ctype.h c-ctype.c

    ## end   gnulib module c-ctype

    ## begin gnulib module calloc-gnu

    EXTRA_libgzip_a_SOURCES += calloc.c

    ## end   gnulib module calloc-gnu

    ## begin gnulib module fcntl

    if GL_COND_OBJ_FCNTL
    libgzip_a_SOURCES += fcntl.c
    endif

    ## end   gnulib module fcntl

    ## begin gnulib module sys_stat-h

    BUILT_SOURCES += sys/stat.h

    sys/stat.h: sys_stat.in.h $(top_builddir)/config.status $(CXXDEFS_H)
    \t$(AM_V_GEN)$(MKDIR_P) '%reldir%/sys'
    \t$(gl_V_at)$(SED_HEADER_STDOUT) \\
    \t      -e 's|@''GUARD_PREFIX''@|GL|g' \\
    \t      $(srcdir)/sys_stat.in.h > $@-t
    \t$(AM_V_at)mv $@-t $@

    ## end   gnulib module sys_stat-h
""")

MAKEFILE_AM = textwrap.dedent("""\
    bin_PROGRAMS = gzip
    noinst_LIBRARIES = libver.a
    nodist_libver_a_SOURCES = version.c version.h

    gzip_SOURCES = \\
      bits.c deflate.c gzip.c \\
      zip.c
    gzip_LDADD = libver.a lib/libgzip.a
    gzip_LDADD += $(CLOCK_TIME_LIB) $(FDATASYNC_LIB)
    if IBM_Z_DFLTCC
    gzip_SOURCES += dfltcc.c
    endif
""")


class GnulibMkTest(unittest.TestCase):

    def test_gnulib_mk(self) -> None:
        mk = gnulibmk.parse(GNULIB_MK)
        (library,) = mk.libraries.values()
        self.assertEqual((library.name, library.archive), ("libgzip_a", "libgzip.a"))
        self.assertEqual(
            [(s.path, s.condition, s.extra, s.module) for s in library.sources],
            [
                ("c-ctype.h", None, False, "c-ctype"),
                ("c-ctype.c", None, False, "c-ctype"),
                ("calloc.c", None, True, "calloc-gnu"),
                ("fcntl.c", "GL_COND_OBJ_FCNTL", False, "fcntl"),
            ],
        )
        self.assertEqual(
            [(h.out, h.template, h.condition, h.extra_inputs) for h in mk.headers],
            [
                ("alloca.h", "alloca.in.h", "GL_GENERATE_ALLOCA_H", []),
                ("assert.h", "assert.in.h", "GL_GENERATE_ASSERT_H", ["verify.h"]),
                ("sys/stat.h", "sys_stat.in.h", None, []),
            ],
        )
        self.assertEqual(mk.conditionals, {"GL_GENERATE_ALLOCA_H", "GL_GENERATE_ASSERT_H", "GL_COND_OBJ_FCNTL"})
        self.assertEqual(mk.built_sources, [("$(ALLOCA_H)", None), ("$(ASSERT_H)", None), ("sys/stat.h", None)])
        self.assertEqual(gnulibmk.header_stem("sys/stat.h"), "sys_stat")
        self.assertEqual(gnulibmk.generate_variable("getopt-cdefs.h"), "GL_GENERATE_GETOPT_CDEFS_H")

    def test_makefile_am(self) -> None:
        mk = gnulibmk.parse(MAKEFILE_AM)
        gzip = mk.programs["gzip"]
        self.assertEqual([(s.path, s.condition) for s in gzip.sources], [
            ("bits.c", None), ("deflate.c", None), ("gzip.c", None), ("zip.c", None),
            ("dfltcc.c", "IBM_Z_DFLTCC"),
        ])
        self.assertEqual(gzip.ldadd, ["libver.a", "lib/libgzip.a", "$(CLOCK_TIME_LIB)", "$(FDATASYNC_LIB)"])
        self.assertEqual(mk.ldadd_vars("gzip"), ["CLOCK_TIME_LIB", "FDATASYNC_LIB"])
        self.assertEqual([s.path for s in mk.libraries["libver_a"].sources], ["version.c", "version.h"])

    def test_target_kinds_and_generator_flags(self) -> None:
        """Every installed or convenience target counts; yacc/lex recipes keep their options."""
        mk = gnulibmk.parse(textwrap.dedent("""\
            sbin_PROGRAMS = daemon
            pkgextension_LTLIBRARIES = plugin.la
            check_PROGRAMS = unit
            EXTRA_PROGRAMS = maybe
            daemon_SOURCES = main.c grammar.y lexer.l
            plugin_la_SOURCES = plugin.c
            $(srcdir)/grammar.c: grammar.y
            \t$(YACC) -o $@ $(AM_YFLAGS) $(YFLAGS) $<
            \tsed 's/parse error/syntax error/g' < $@ > $@.tmp && mv $@.tmp $@
            $(srcdir)/lexer.c: lexer.l
            \t$(AM_V_LEX)$(LEX) -o$@ -P zz --nounistd $< && touch $@
            """))
        self.assertEqual(sorted(mk.programs), ["daemon"])
        self.assertEqual(mk.libraries["plugin_la"].archive, "plugin.la")
        self.assertEqual(mk.generator_flags, {"grammar.y": [], "lexer.l": ["-P", "zz", "--nounistd"]})
        self.assertEqual(mk.recipes["grammar.c"][0], "$(YACC) -o $@ $(AM_YFLAGS) $(YFLAGS) $<")

    def test_written_sources(self) -> None:
        """gzip's version.c: a printf recipe, kept for the converter to translate."""
        mk = gnulibmk.parse(textwrap.dedent("""\
            noinst_LIBRARIES = libver.a
            nodist_libver_a_SOURCES = version.c version.h
            BUILT_SOURCES = version.c
            version.c: Makefile
            \t$(AM_V_GEN)rm -f $@
            \t$(AM_V_at)printf '#include <config.h>\\n' > $@t
            \t$(AM_V_at)printf 'char const *Version = "$(PACKAGE_VERSION)";\\n' >> $@t
            \t$(AM_V_at)mv $@t $@
            """))
        self.assertEqual(len(mk.recipes["version.c"]), 4)
        self.assertEqual([s.path for s in mk.libraries["libver_a"].sources], ["version.c", "version.h"])


    def test_sources_before_declaration(self) -> None:
        """libssh2: `libssh2_la_SOURCES` above `lib_LTLIBRARIES = libssh2.la` still attaches."""
        mk = gnulibmk.parse("libssh2_la_SOURCES = a.c b.c\nlib_LTLIBRARIES = libssh2.la\n")
        self.assertEqual([s.path for s in mk.libraries["libssh2_la"].sources], ["a.c", "b.c"])
        self.assertEqual(mk.notes, [])

    def test_headers_variables(self) -> None:
        """`*_HEADERS`: installed ones are the public interface, noinst ones are not."""
        mk = gnulibmk.parse("include_HEADERS = api.h\nnobase_pkginclude_HEADERS = sub/x.h\n"
                            "noinst_HEADERS = priv.h\nif COND\nlibfooinclude_HEADERS = cond.h\nendif\n")
        self.assertEqual([(h.path, h.condition) for h in mk.installed_headers],
                         [("api.h", None), ("sub/x.h", None), ("cond.h", "COND")])
        self.assertEqual([h.path for h in mk.private_headers], ["priv.h"])

    def test_expand_variables_and_rules(self) -> None:
        """`$(CSRC:.c=.o)`, `${VAR}` and the rules' prerequisites, as a hand-written Makefile.in has them."""
        mk = gnulibmk.parse("CSRC = a.c b.c\nOBJ = $(CSRC:.c=.o) extra.o\nPROG = tool\n"
                            "$(PROG): $(OBJ) libx.a\n\t$(CC) -o $@ $(OBJ)\nlibx.a: ${OBJ}\n\t$(AR) cr $@ $(OBJ)\n")
        self.assertEqual(gnulibmk.expand_variables(["$(OBJ)"], mk.variables), ["a.o", "b.o", "extra.o"])
        self.assertEqual(gnulibmk.expand_variables(["${OBJ}", "$(NOPE)"], mk.variables), ["a.o", "b.o", "extra.o", "$(NOPE)"])
        self.assertEqual(mk.rules["$(PROG)"], ["$(OBJ)", "libx.a"])
        self.assertEqual(mk.rules["libx.a"], ["${OBJ}"])

    def test_read_makefile_inlines_includes(self) -> None:
        """automake `include` fragments are spliced in, with %reldir% / %D% / %C% resolved."""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "Makefile.am").write_text("bin_PROGRAMS =\ninclude src/Makemodule.am\ninclude $(top_srcdir)/extra.am\n")
            (root / "src" / "Makemodule.am").write_text(
                "bin_PROGRAMS += bin/%D%tool\nbin_%C%_tool_SOURCES = %reldir%/main.c %D%/util.c\n")
            (root / "extra.am").write_text("noinst_LIBRARIES = libx.a\nlibx_a_SOURCES = %reldir%/x.c\n")
            text = gnulibmk.read_makefile(root / "Makefile.am", root)
            mk = gnulibmk.parse(text)
            self.assertEqual(sorted(mk.programs), ["bin_srctool"])
            self.assertEqual([s.path for s in mk.programs["bin_srctool"].sources], [])  # canonical name differs
            self.assertIn("bin_src_tool_SOURCES = src/main.c src/util.c", text)
            self.assertIn("libx_a_SOURCES = x.c", text)  # a fragment in the Makefile.am's own directory
            self.assertEqual([s.path for s in mk.libraries["libx_a"].sources], ["x.c"])


if __name__ == "__main__":
    unittest.main()
