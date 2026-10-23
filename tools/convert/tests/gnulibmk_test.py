"""Unit tests for the automake fragment parser."""

from __future__ import annotations

import textwrap
from pathlib import Path

from tools.convert import gnulibmk
from tools.convert.automake import _link_vars
from tools.convert.translate import ProjectState

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


def test_gnulib_mk() -> None:
    mk = gnulibmk.parse(GNULIB_MK)
    (library,) = mk.libraries.values()
    assert (library.name, library.archive) == ("libgzip_a", "libgzip.a")
    assert [(s.path, s.condition, s.extra, s.module) for s in library.sources] == [
        ("c-ctype.h", None, False, "c-ctype"),
        ("c-ctype.c", None, False, "c-ctype"),
        ("calloc.c", None, True, "calloc-gnu"),
        ("fcntl.c", "GL_COND_OBJ_FCNTL", False, "fcntl"),
    ]
    assert [(h.out, h.template, h.condition, h.extra_inputs) for h in mk.headers] == [
        ("alloca.h", "alloca.in.h", "GL_GENERATE_ALLOCA_H", []),
        ("assert.h", "assert.in.h", "GL_GENERATE_ASSERT_H", ["verify.h"]),
        ("sys/stat.h", "sys_stat.in.h", None, []),
    ]
    assert mk.conditionals == {"GL_GENERATE_ALLOCA_H", "GL_GENERATE_ASSERT_H", "GL_COND_OBJ_FCNTL"}
    assert mk.built_sources == [("$(ALLOCA_H)", None), ("$(ASSERT_H)", None), ("sys/stat.h", None)]
    assert gnulibmk.header_stem("sys/stat.h") == "sys_stat"
    assert gnulibmk.generate_variable("getopt-cdefs.h") == "GL_GENERATE_GETOPT_CDEFS_H"


def test_makefile_am() -> None:
    mk = gnulibmk.parse(MAKEFILE_AM)
    gzip = mk.programs["gzip"]
    assert [(s.path, s.condition) for s in gzip.sources] == [
        ("bits.c", None),
        ("deflate.c", None),
        ("gzip.c", None),
        ("zip.c", None),
        ("dfltcc.c", "IBM_Z_DFLTCC"),
    ]
    assert gzip.ldadd == ["libver.a", "lib/libgzip.a", "$(CLOCK_TIME_LIB)", "$(FDATASYNC_LIB)"]
    assert _link_vars(gzip.ldadd, ProjectState()) == ["CLOCK_TIME_LIB", "FDATASYNC_LIB"]
    # A variable configure sets to `-l` words of registry libraries only (`LIBLZMA` = `-llzma`,
    # the module xz) names deps every target has already: not a linkopts variable.  One with a
    # word the registry does not cover stays.
    state = ProjectState()
    state.configure_libs = {"lzma": "@xz//:lzma"}
    state.subst_values = {"LIBLZMA": ["", "-llzma"], "LIBFOO": ["-lfoo -llzma"]}
    assert _link_vars(["$(LIBLZMA)", "$(LIBFOO)"], state) == ["LIBFOO"]
    assert [s.path for s in mk.libraries["libver_a"].sources] == ["version.c", "version.h"]


def test_named_else_and_endif() -> None:
    """`endif COND` / `else COND` close and switch the block like the bare words (grub's
    generated Makefile.util.am); a program after such an `endif` is unconditional."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
        bin_PROGRAMS =
        if COND_GRUB_PROTECT
        bin_PROGRAMS += grub-protect
        grub_protect_SOURCES = util/grub-protect.c
        else !COND_GRUB_PROTECT
        noinst_HEADERS = x.h
        endif COND_GRUB_PROTECT
        bin_PROGRAMS += grub-mkrelpath
        grub_mkrelpath_SOURCES = util/grub-mkrelpath.c
        """)
    )
    assert [s.condition for s in mk.programs["grub_protect"].sources] == ["COND_GRUB_PROTECT"]
    assert [s.condition for s in mk.programs["grub_mkrelpath"].sources] == [None]
    assert [(h.path, h.condition) for h in mk.private_headers] == [("x.h", "!COND_GRUB_PROTECT")]


def test_else_and_negated_if_are_negations() -> None:
    """make's `if WINDOWSENV ... else make_SOURCES += src/posixos.c endif`: the else branch is the
    conditional's negation, as `if !COND` is; an `else` of a negated `if` is the conditional."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
        bin_PROGRAMS = make
        make_SOURCES = src/main.c
        if WINDOWSENV
          make_SOURCES += src/w32/w32os.c
          AM_CPPFLAGS += -I $(top_srcdir)/src/w32/include
        else
          make_SOURCES += src/posixos.c
        endif
        if !STATIC
          make_SOURCES += dynamic.c
        else
          make_SOURCES += static.c
        endif
        """)
    )
    assert [(s.path, s.condition) for s in mk.programs["make"].sources] == [
        ("src/main.c", None),
        ("src/w32/w32os.c", "WINDOWSENV"),
        ("src/posixos.c", "!WINDOWSENV"),
        ("dynamic.c", "!STATIC"),
        ("static.c", "STATIC"),
    ]
    assert mk.conditional_variables["AM_CPPFLAGS"] == [
        ("-I", "WINDOWSENV"),
        ("$(top_srcdir)/src/w32/include", "WINDOWSENV"),
    ]  # joined by the flags reader


def test_target_kinds_and_generator_flags() -> None:
    """Every installed or convenience target counts; yacc/lex recipes keep their options."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
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
        """)
    )
    assert sorted(mk.programs) == ["daemon"]
    assert mk.libraries["plugin_la"].archive == "plugin.la"
    assert mk.generator_flags == {"grammar.y": [], "lexer.l": ["-P", "zz", "--nounistd"]}
    assert mk.recipes["grammar.c"][0] == "$(YACC) -o $@ $(AM_YFLAGS) $(YFLAGS) $<"


def test_written_sources() -> None:
    """gzip's version.c: a printf recipe, kept for the converter to translate."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
        noinst_LIBRARIES = libver.a
        nodist_libver_a_SOURCES = version.c version.h
        BUILT_SOURCES = version.c
        version.c: Makefile
        \t$(AM_V_GEN)rm -f $@
        \t$(AM_V_at)printf '#include <config.h>\\n' > $@t
        \t$(AM_V_at)printf 'char const *Version = "$(PACKAGE_VERSION)";\\n' >> $@t
        \t$(AM_V_at)mv $@t $@
        """)
    )
    assert len(mk.recipes["version.c"]) == 4
    assert [s.path for s in mk.libraries["libver_a"].sources] == ["version.c", "version.h"]


def test_substituted_program_list() -> None:
    """`bin_PROGRAMS = @bin_PROGRAMS@` (coreutils): the candidates come from EXTRA_PROGRAMS, the
    placeholder is no program, and the candidates' sources and LDADD attach as usual."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
        default__progs = src/ls src/cat
        EXTRA_PROGRAMS = $(default__progs) src/hostid
        bin_PROGRAMS = @bin_PROGRAMS@
        noinst_PROGRAMS = src/getlimits
        src_hostid_SOURCES = src/hostid.c src/extra.c
        src_ls_LDADD = $(LDADD)
        """)
    )
    assert mk.substituted_targets == {"bin_PROGRAMS": "bin_PROGRAMS"}
    assert sorted(p.name for p in mk.programs.values()) == ["src/cat", "src/getlimits", "src/hostid", "src/ls"]
    assert mk.programs["src_hostid"].substituted
    assert not mk.programs["src_getlimits"].substituted
    assert [s.path for s in mk.programs["src_hostid"].sources] == ["src/hostid.c", "src/extra.c"]
    assert mk.programs["src_ls"].ldadd == ["$(LDADD)"]


def test_echo_written_headers() -> None:
    """libgmp-mpz: `lib/gmp.h` written by echo lines under nested conditionals; the `rm -f`
    branch and a rule with other commands are not echo headers."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
        if GL_GENERATE_GMP_H
        if GL_GENERATE_MINI_GMP_H
        lib/gmp.h: $(top_builddir)/config.status
        \t$(AM_V_GEN)$(MKDIR_P) '%reldir%'
        \t$(gl_V_at)echo '#include "mini-gmp.h"' > $@-t
        \t$(AM_V_at)echo '#if GNULIB_LIBGMP_MPQ' >> $@-t
        \t$(AM_V_at)mv $@-t $@
        endif
        if GL_GENERATE_GMP_GMP_H
        lib/gmp.h: $(top_builddir)/config.status
        \t$(gl_V_at)echo '#include <gmp/gmp.h>' > $@-t
        \t$(AM_V_at)mv $@-t $@
        endif
        else
        lib/gmp.h: $(top_builddir)/config.status
        \trm -f $@
        endif
        lib/other.h: $(top_builddir)/config.status
        \tsed 's/a/b/' < x > $@-t
        \tmv $@-t $@
        """)
    )
    assert [(h.out, h.lines, h.condition) for h in mk.echo_headers] == [
        (
            "lib/gmp.h",
            ['#include "mini-gmp.h"', "#if GNULIB_LIBGMP_MPQ"],
            "GL_GENERATE_GMP_H && GL_GENERATE_MINI_GMP_H",
        ),
        ("lib/gmp.h", ["#include <gmp/gmp.h>"], "GL_GENERATE_GMP_H && GL_GENERATE_GMP_GMP_H"),
    ]


def test_sources_before_declaration() -> None:
    """libssh2: `libssh2_la_SOURCES` above `lib_LTLIBRARIES = libssh2.la` still attaches."""
    mk = gnulibmk.parse("libssh2_la_SOURCES = a.c b.c\nlib_LTLIBRARIES = libssh2.la\n")
    assert [s.path for s in mk.libraries["libssh2_la"].sources] == ["a.c", "b.c"]
    assert mk.notes == []


def test_headers_variables() -> None:
    """`*_HEADERS`: installed ones are the public interface, noinst ones are not."""
    mk = gnulibmk.parse(
        "include_HEADERS = api.h\nnobase_pkginclude_HEADERS = sub/x.h\n"
        "noinst_HEADERS = priv.h\nif COND\nlibfooinclude_HEADERS = cond.h\nendif\n"
    )
    assert [(h.path, h.condition) for h in mk.installed_headers] == [
        ("api.h", None),
        ("sub/x.h", None),
        ("cond.h", "COND"),
    ]
    assert [h.path for h in mk.private_headers] == ["priv.h"]


def test_expand_variables_and_rules() -> None:
    """`$(CSRC:.c=.o)`, `${VAR}` and the rules' prerequisites, as a hand-written Makefile.in has them."""
    mk = gnulibmk.parse(
        "CSRC = a.c b.c\nOBJ = $(CSRC:.c=.o) extra.o\nPROG = tool\n"
        "$(PROG): $(OBJ) libx.a\n\t$(CC) -o $@ $(OBJ)\nlibx.a: ${OBJ}\n\t$(AR) cr $@ $(OBJ)\n"
    )
    assert gnulibmk.expand_variables(["$(OBJ)"], mk.variables) == ["a.o", "b.o", "extra.o"]
    assert gnulibmk.expand_variables(["${OBJ}", "$(NOPE)"], mk.variables) == ["a.o", "b.o", "extra.o", "$(NOPE)"]
    assert mk.rules["$(PROG)"] == ["$(OBJ)", "libx.a"]
    assert mk.rules["libx.a"] == ["${OBJ}"]


def test_read_makefile_inlines_includes(tmp_path: Path) -> None:
    """automake `include` fragments are spliced in, with %reldir% / %D% / %C% resolved."""
    root = tmp_path
    (root / "src").mkdir()
    (root / "Makefile.am").write_text("bin_PROGRAMS =\ninclude src/Makemodule.am\ninclude $(top_srcdir)/extra.am\n")
    (root / "src/Makemodule.am").write_text(
        "bin_PROGRAMS += bin/%D%tool\nbin_%C%_tool_SOURCES = %reldir%/main.c %D%/util.c\n"
    )
    (root / "extra.am").write_text("noinst_LIBRARIES = libx.a\nlibx_a_SOURCES = %reldir%/x.c\n")
    text = gnulibmk.read_makefile(root / "Makefile.am", root)
    mk = gnulibmk.parse(text)
    assert sorted(mk.programs) == ["bin_srctool"]
    # The `_SOURCES` line names another canonical form, so automake's default source applies.
    assert [s.path for s in mk.programs["bin_srctool"].sources] == ["bin/srctool.c"]
    assert mk.programs["bin_srctool"].default_sources
    assert "bin_src_tool_SOURCES = src/main.c src/util.c" in text
    assert "libx_a_SOURCES = x.c" in text  # a fragment in the Makefile.am's own directory
    assert [s.path for s in mk.libraries["libx_a"].sources] == ["x.c"]


def test_read_makefile_follows_includes_through_reldir(tmp_path: Path) -> None:
    """A fragment that includes further fragments through its own `%D%` (newlib's
    libc/Makefile.inc: `include %D%/argz/Makefile.inc`): the token is the fragment's directory
    before the include is resolved, so the chain is followed to the bottom."""
    root = tmp_path
    (root / "libc/argz").mkdir(parents=True)
    (root / "Makefile.am").write_text("noinst_LIBRARIES =\ninclude libc/Makefile.inc\n")
    (root / "libc/Makefile.inc").write_text("noinst_LIBRARIES += %D%/libc.a\ninclude %D%/argz/Makefile.inc\n")
    (root / "libc/argz/Makefile.inc").write_text("libc_libc_a_SOURCES += %D%/argz_add.c\n")
    text = gnulibmk.read_makefile(root / "Makefile.am", root)
    assert "include libc/argz/Makefile.inc" in text  # the include line, resolved and spliced
    assert "libc_libc_a_SOURCES += libc/argz/argz_add.c" in text
    mk = gnulibmk.parse(text)
    assert [s.path for s in mk.libraries["libc_libc_a"].sources] == ["libc/argz/argz_add.c"]


def test_non_recursive_header_inputs() -> None:
    """sed's and coreutils' gnulib.mk name every file with its directory: the second input of
    the assert.h recipe is kept (it is not a make variable) and strip_directory cuts it with the
    rest."""
    mk = gnulibmk.parse(
        textwrap.dedent("""\
            noinst_LIBRARIES += lib/libsed.a
            lib_libsed_a_SOURCES = lib/x.c lib/y.c
            if GL_GENERATE_ASSERT_H
            lib/assert.h: lib/assert.in.h lib/verify.h $(top_builddir)/config.status
            \t$(gl_V_at){ $(SED_HEADER_STDOUT) < $(srcdir)/lib/assert.in.h && \\
            \t  sed -e 's|_gl_verify|_gl_static_assert|g' < $(srcdir)/lib/verify.h; } > $@-t
            \t$(AM_V_at)mv $@-t $@
            endif
            """)
    )
    assert [(h.out, h.template, h.extra_inputs) for h in mk.headers] == [
        ("lib/assert.h", "lib/assert.in.h", ["lib/verify.h"])
    ]
    assert gnulibmk.strip_directory(mk, "lib")
    assert [(h.out, h.template, h.extra_inputs) for h in mk.headers] == [("assert.h", "assert.in.h", ["verify.h"])]
