"""Decisions for converting gzip 1.15: the one hand-maintained file of this conversion.

Written once by `bazel run @rules_cc_autoconf//tools/convert -- convert` and
read back on every run; BUILD.bazel and MODULE.bazel next to it are generated
from it.  Each key below is a construct of the configure.ac or m4
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
    generated:<path>          IGNORE | a rule writing the file, as it would appear in
                              BUILD.bazel (genrule(...), run_binary(...)); a load() it
                              needs goes first: ["load(...)", run_binary(...)]
    cppflag:<dir>:<NAME>      IGNORE | "<value>" (a -D value only configure knows)
    link:<dir>/<target>:<item> IGNORE | ":<label>" (a dep) | "-l<name>" (a linkopt)

Three kinds are never asked for and apply when present: when Bazel reports a
define, subst or check "defined both locally and in dependencies", the
project's own copy is dropped with

    define:<NAME>             IGNORE
    subst:<NAME>              IGNORE
    check:<cache variable>    IGNORE

These words are read by the converter, not by Bazel: the file is parsed, never
loaded.  Entries the converter stops asking for are reported as unused.
"""

DECISIONS = {
    # autoconf (configure.ac): the condition under which AC_DEFINE:ASMV runs cannot be expressed (DEFS matches *NO_ASM*)
    # checks: AC_DEFINE:ASMV
    # condition: DEFS matches *NO_ASM*
    "guard:autoconf:AC_DEFINE:ASMV": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_CACHE_VAL([ac_cv_path_shell], [ac_command=']): the cache value comes from shell around the probe, not from one probe
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
    "probe:ac_cv_path_shell": UNDECIDED,
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
    "probe:autoconf:egrep_Special": UNDECIDED,
    # autoconf (configure.ac): AC_COMPILE_IFELSE: the actions decide autoconf_probe_1, not the probe's success
    # configure.ac:245-248:
    #      AC_COMPILE_IFELSE(
    #        [AC_LANG_PROGRAM([[int foo() {return 0;}]], [[]])],
    #          [$NM conftest.$OBJEXT | grep _foo >/dev/null 2>&1 ||
    #           gzip_cv_underline=no])])
    # program:
    #   int foo() {return 0;}
    #   int
    #   main (void)
    #   {
    #
    #     ;
    #     return 0;
    #   }
    # action_if_true:
    #   $NM conftest.$OBJEXT | grep _foo >/dev/null 2>&1 ||
    #           gzip_cv_underline=no
    "probe:autoconf_probe_1": UNDECIDED,
    # gnulib.mk: the condition for the sources under GL_COND_OBJ_MATCH cannot be expressed (DEFS matches *NO_ASM*)
    # conditions: DEFS matches *NO_ASM*
    "src_condition:GL_COND_OBJ_MATCH": UNDECIDED,
    # gnulib.mk: the condition for the sources under GL_COND_OBJ_STDIO_CONSOLESAFE cannot be expressed (never true)
    # gnulib.mk:1622-1624:
    #   if GL_COND_OBJ_STDIO_CONSOLESAFE
    #   libgzip_a_SOURCES += stdio-consolesafe.c
    #   endif
    # conditions: never true
    "src_condition:GL_COND_OBJ_STDIO_CONSOLESAFE": UNDECIDED,
    # autoconf (configure.ac): ASFLAGS_config: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~298: ASFLAGS_config='-Wa,--noexecstack'
    # assignments:
    #   ASFLAGS_config='-Wa,--noexecstack'  when DEFS matches *NO_ASM*
    #   ASFLAGS_config=  when DEFS matches *NO_ASM*
    "variable:ASFLAGS_config": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([GNULIB_WARN_CFLAGS]): GNULIB_WARN_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:230:   AC_SUBST([GNULIB_WARN_CFLAGS])
    "variable:GNULIB_WARN_CFLAGS": UNDECIDED,
    # autoconf (configure.ac): GZIP_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~304: GZIP_TRANSFORMED=` echo gzip  | sed "$program_transform_name"`
    # assignments: GZIP_TRANSFORMED=$( echo gzip  | sed "$program_transform_name")  when always
    "variable:GZIP_TRANSFORMED": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([LIBGZIP_LIBDEPS]): LIBGZIP_LIBDEPS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:826:   AC_SUBST([LIBGZIP_LIBDEPS])
    "variable:LIBGZIP_LIBDEPS": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([LIBGZIP_LTLIBDEPS]): LIBGZIP_LTLIBDEPS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:826:   AC_SUBST([LIBGZIP_LIBDEPS])
    "variable:LIBGZIP_LTLIBDEPS": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([SHELL]): SHELL is never assigned in this body; its value is not known here
    # m4/shell.m4:95:    AC_SUBST(SHELL)])
    "variable:SHELL": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([WARN_CFLAGS]): WARN_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:208:   AC_SUBST([WARN_CFLAGS])
    "variable:WARN_CFLAGS": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([WERROR_CFLAGS]): WERROR_CFLAGS is never assigned in this body; its value is not known here
    # configure.ac:135:   AC_SUBST([WERROR_CFLAGS])
    "variable:WERROR_CFLAGS": UNDECIDED,
    # autoconf (configure.ac): ZDIFF_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~305: ZDIFF_TRANSFORMED=`echo zdiff | sed "$program_transform_name"`
    # assignments: ZDIFF_TRANSFORMED=$(echo zdiff | sed "$program_transform_name")  when always
    "variable:ZDIFF_TRANSFORMED": UNDECIDED,
    # autoconf (configure.ac): ZGREP_TRANSFORMED: the assignments cannot be expressed as one check
    # m4 (shell): configure.ac:~306: ZGREP_TRANSFORMED=`echo zgrep | sed "$program_transform_name"`
    # assignments: ZGREP_TRANSFORMED=$(echo zgrep | sed "$program_transform_name")  when always
    "variable:ZGREP_TRANSFORMED": UNDECIDED,
    # autoconf (configure.ac): configure.ac: AC_SUBST([gltests_WITNESS]): gltests_WITNESS is never assigned in this body; its value is not known here
    # m4/gnulib-comp.m4:768:   AC_SUBST([gltests_WITNESS])
    "variable:gltests_WITNESS": UNDECIDED,
}
