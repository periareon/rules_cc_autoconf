"""Decisions for converting gzip 1.15.

Written by `bazel run @rules_cc_autoconf//tools/convert -- convert` and read
back on every run.  Each key below is a construct of the configure.ac or m4
that the converter cannot translate on its own; the comments above it show
the source it stands for and what the converter found in it.  Replace each
`UNDECIDED` with one of these values, then run the same command again:

    UNDECIDED        not decided yet: the construct stays out, the run exits 3
    IGNORE           leave the construct out for good
    ACCEPT           keep the converter's own translation of the probe as it is
    ACCEPT_PARTIAL   keep the assignments the converter could follow, drop the rest
    ALWAYS / NEVER   the guarded checks or sources always / never apply
    "text", 0, 1     a literal: the cache value, the substituted value, a dep
                     label, a condition expression or a config_setting label
    True / False     the default of a configure option's bool_flag
    select({...})    a literal per configuration label ("//conditions:default" last)
    checks.X(...)    a check written exactly as it would appear in BUILD.bazel,
    macros.X(...)    or a list of them; a replacement probe must set the cache
                     variable it replaces (name = "...")

Which of these a key takes depends on its kind, the part before the colon:

    probe:<cache variable>    IGNORE | ACCEPT | a literal | select() | a replacement
                              probe (checks.AC_TRY_COMPILE / checks.AC_TRY_LINK) | checks
    variable:<NAME>           IGNORE | ACCEPT_PARTIAL | a literal | select() | checks
    guard:<target>:<check>    ALWAYS | NEVER | ["<cache variable>", ...] (requires) |
                              select({"<label>": ["<cache variable>", ...]})
    macro:<NAME>              IGNORE | "<dep label>" | checks
    actions:<macro>:<arg>     IGNORE | checks
    flag:<option>             IGNORE | True | False
    am_conditional:<NAME>     ALWAYS | NEVER | ":<config_setting>"
    src_condition:<NAME>      ALWAYS | NEVER | "<condition expression>"

These words are read by the converter, not by Bazel: the file is parsed, never
loaded.  Entries the converter stops asking for are reported as unused.
"""

DECISIONS = {
    # AC_PROG_SHELL (m4/shell.m4): m4/shell.m4:~24: AC_CACHE_VAL([ac_cv_path_shell], [ac_command=']): the cache value comes from shell around the probe, not from one probe
    # m4/shell.m4:24-85:
    #      AC_CACHE_VAL(ac_cv_path_shell,
    #        [ac_command='
    #           # Test the noclobber option, using the portable POSIX.2 syntax.
    #           set -C
    #           rm -f conftest.c
    #           >conftest.c || exit
    #           >|conftest.c || exit
    #           !>conftest.c || exit
    #           # Test that $(...) works.
    #           test "$(expr 3 + 4)" -eq 7 || exit
    #         '
    #         # Solaris 11 /bin/sh (AT&T Research) 93u 2011-02-08 has a file
    #         # descriptor bug that breaks zgrep and is hard to test for
    #         # directly.  At some point $(...) is reading a pipe from the
    #         # subshell, but the pipe is also open in the parent shell, so
    #         # the read deadlocks.  Prefer some other shell if available.
    #         ac_maybe_solaris_sh_bug='test "${.sh.version}"'
    #
    #         ac_cv_path_shell=no
    #
    #         case $SHELL in
    #         /*)
    #           rm -f conftest.c
    #           if ("$SHELL" -c "$ac_command") 2>/dev/null; then
    #             "$SHELL" -c "$ac_maybe_solaris_sh_bug" 2>/dev/null ||
    #               ac_cv_path_shell=$SHELL
    #           fi
    #         esac
    #
    #         case $ac_cv_path_shell in
    #         no)
    #           # Prefer shells that are more likely to be installed in the
    #           # same place on all hosts of this platform.  Therefore, prefer
    #           # shells in /bin and /usr/bin to shells in the installer's
    #           # PATH.  Also, loop through PATH first and then through
    #           # shells, since less-"nice" shells in /bin and /usr/bin are
    #           # more likely to be installed than "nicer" shells elsewhere.
    #           ac_break_if_good_shell=:
    #           as_save_IFS=$IFS; IFS=:
    #           for as_dir in /bin /usr/bin $PATH
    #   ... (22 more lines)
    # Decision: The scripts (zgrep, zdiff...) are not built here: any POSIX shell will do.
    "probe:ac_cv_path_shell": "/bin/sh",
    # autoconf (configure.ac): AC_EGREP_CPP([Special]) greps preprocessor output; needs a compile-time probe
    # m4/gnulib-comp.m4:616-621:
    #         AC_EGREP_CPP([Special], [
    #     #ifndef _UCRT
    #      Special
    #     #endif
    #           ],
    #           [USES_MSVCRT=1])
    # pattern: Special
    # program:
    #   #ifndef _UCRT
    #    Special
    #   #endif
    # action_if_found: USES_MSVCRT=1
    # Decision: gnulib sets USES_MSVCRT=1 on mingw when the C runtime is the old msvcrt
    # (_UCRT undefined).  Left out: with it unset, stdio-consolesafe.c is never
    # compiled, which is right for MSVC and UCRT-based mingw.
    "probe:autoconf:egrep_Special": IGNORE,
    # autoconf (configure.ac): configure.ac: AC_CACHE_CHECK([for an assembler syntax supported by this package], [gzip_cv_assembler], [gzi...: the cache value comes from shell around the probe, not from one probe
    # configure.ac:258-291:
    #   AC_CACHE_CHECK([for an assembler syntax supported by this package],
    #     [gzip_cv_assembler],
    #     [gzip_cv_assembler=no
    #      AS_CASE([$DEFS],
    #        [*NO_ASM*], [],
    #        [# 'host?' ensures Gnulib sets host as usual.
    #         AS_CASE([${host?}],
    #           [*-musl* | *-midipix*],
    #             [# Avoid a crash in musl libc's startup code,
    #              # because musl doesn't support TEXTRELs.
    #              # https://www.openwall.com/lists/musl/2020/09/25/1
    #             ],
    #           [i386*-openbsd*],
    #             [# Avoid issue in OpenBSD i386 <https://bugs.gnu.org/81766>.
    #             ],
    #           [if cp $srcdir/lib/match.c _match.S &&
    #               eval "$CPP $CPPFLAGS $ASCPPFLAGS _match.S > _match.i" &&
    #               eval "$ASCPPPOST < _match.i > match_.s"; then
    #              if test ! -s match_.s || grep error < match_.s > /dev/null; then
    #                :
    #              elif eval "$CC $CPPFLAGS $CFLAGS -c match_.s >/dev/null" &&
    #                   test -f match_.$OBJEXT; then
    #                rm -f match_.$OBJEXT
    #                gzip_cv_assembler=yes
    #                if echo 'void foo (void) {}' > conftest.c &&
    #                   eval "$CC $CPPFLAGS $CFLAGS -S conftest.c >/dev/null" &&
    #                   grep '\.note\.GNU-stack' conftest.s >/dev/null &&
    #                   eval "$CC $CPPFLAGS $CFLAGS -c -Wa,--noexecstack match_.s >/dev/null" &&
    #                   test -f match_.$OBJEXT; then
    #                  gzip_cv_assembler='yes, with -Wa,--noexecstack'
    #                fi
    #              fi
    #            fi
    #            rm -f conftest* _match.i _match.S match_.s match_.$OBJEXT])])])
    # Decision: `no` keeps ASMV undefined: the C longest_match is used.
    "probe:gzip_cv_assembler": "no",
    # autoconf (configure.ac): configure.ac: AC_CACHE_CHECK([for underline in external names], [gzip_cv_underline], [gzip_cv_underline=yes]): the cache value comes from shell around the probe, not from one probe
    # configure.ac:243-248:
    #   AC_CACHE_CHECK([for underline in external names], [gzip_cv_underline],
    #     [gzip_cv_underline=yes
    #      AC_COMPILE_IFELSE(
    #        [AC_LANG_PROGRAM([[int foo() {return 0;}]], [[]])],
    #          [$NM conftest.$OBJEXT | grep _foo >/dev/null 2>&1 ||
    #           gzip_cv_underline=no])])
    # Decision: Only matters for the assembler version, which is not built.
    "probe:gzip_cv_underline": "no",
    # gnulib.mk: the condition for the sources under GL_COND_OBJ_STDIO_CONSOLESAFE cannot be expressed (never true)
    # gnulib.mk:1622-1624:
    #   if GL_COND_OBJ_STDIO_CONSOLESAFE
    #   libgzip_a_SOURCES += stdio-consolesafe.c
    #   endif
    # conditions: never true
    # Decision: USES_MSVCRT is assigned inside AC_EGREP_CPP's action (see
    # probe:autoconf:egrep_Special), which the converter does not follow.
    "src_condition:GL_COND_OBJ_STDIO_CONSOLESAFE": "USES_MSVCRT==1",
    # autoconf (configure.ac): ASFLAGS_config: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~298: ASFLAGS_config='-Wa,--noexecstack'
    # assignments:
    #   ASFLAGS_config='-Wa,--noexecstack'  when gzip_cv_assembler == 'yes, with -Wa,--noexecstack'
    #   ASFLAGS_config=  when gzip_cv_assembler == 'yes, with -Wa,--noexecstack'
    # Decision: No assembler sources are built.
    "variable:ASFLAGS_config": IGNORE,
    # autoconf (configure.ac): configure.ac: AC_SUBST([GNULIB_WARN_CFLAGS]): GNULIB_WARN_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:230:   AC_SUBST([GNULIB_WARN_CFLAGS])
    # Decision: Warning flags are copts, not configure results.
    "variable:GNULIB_WARN_CFLAGS": IGNORE,
    # autoconf (configure.ac): GZIP_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~304: GZIP_TRANSFORMED=` echo gzip  | sed "$program_transform_name"`
    # assignments: GZIP_TRANSFORMED=$( echo gzip  | sed "$program_transform_name")  when always
    # Decision: No --program-transform-name here.
    "variable:GZIP_TRANSFORMED": "gzip",
    # autoconf (configure.ac): configure.ac: AC_SUBST([LIBGZIP_LIBDEPS]): LIBGZIP_LIBDEPS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:826:   AC_SUBST([LIBGZIP_LIBDEPS])
    # Decision: libtool-style dependency lists; Bazel deps replace them.
    "variable:LIBGZIP_LIBDEPS": IGNORE,
    # autoconf (configure.ac): configure.ac: AC_SUBST([LIBGZIP_LTLIBDEPS]): LIBGZIP_LTLIBDEPS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:826:   AC_SUBST([LIBGZIP_LIBDEPS])
    # Decision: libtool-style dependency lists; Bazel deps replace them.
    "variable:LIBGZIP_LTLIBDEPS": IGNORE,
    # AC_PROG_SHELL (m4/shell.m4): SHELL: the assignments cannot be expressed as one check
    # m4 (shell):
    #   m4/shell.m4:~87: SHELL=$ac_cv_path_shell
    #   m4/shell.m4:~89: SHELL=/bin/sh
    # assignments:
    #   SHELL=$ac_cv_path_shell  when always
    #   SHELL=/bin/sh  when !ac_cv_path_shell
    # Decision: See probe:ac_cv_path_shell.
    "variable:SHELL": "/bin/sh",
    # autoconf (configure.ac): configure.ac: AC_SUBST([WARN_CFLAGS]): WARN_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:208:   AC_SUBST([WARN_CFLAGS])
    # Decision: Warning flags are copts, not configure results.
    "variable:WARN_CFLAGS": IGNORE,
    # autoconf (configure.ac): configure.ac: AC_SUBST([WERROR_CFLAGS]): WERROR_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:135:   AC_SUBST([WERROR_CFLAGS])
    # Decision: Warning flags are copts, not configure results.
    "variable:WERROR_CFLAGS": IGNORE,
    # autoconf (configure.ac): ZDIFF_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~305: ZDIFF_TRANSFORMED=`echo zdiff | sed "$program_transform_name"`
    # assignments: ZDIFF_TRANSFORMED=$(echo zdiff | sed "$program_transform_name")  when always
    # Decision: No --program-transform-name here.
    "variable:ZDIFF_TRANSFORMED": "zdiff",
    # autoconf (configure.ac): ZGREP_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~306: ZGREP_TRANSFORMED=`echo zgrep | sed "$program_transform_name"`
    # assignments: ZGREP_TRANSFORMED=$(echo zgrep | sed "$program_transform_name")  when always
    # Decision: No --program-transform-name here.
    "variable:ZGREP_TRANSFORMED": "zgrep",
    # autoconf (configure.ac): configure.ac: AC_SUBST([gltests_WITNESS]): gltests_WITNESS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:768:   AC_SUBST([gltests_WITNESS])
    # Decision: The gnulib tests are not built.
    "variable:gltests_WITNESS": IGNORE,
}
