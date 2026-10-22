"""Decisions the converter bundles with the ports.

Read beneath every project's own decisions file (`--bundled-decisions`): the answers to the
questions every gnulib project raises the same way, so a conversion asks only what is its own.
An entry for a key in the project's file overrides the one here, and the report lists each
answer taken from this file under "Decisions inherited".  Bazel never loads this file.

Two families so far.  GNU gettext (AM_GNU_GETTEXT, the `intl` library): message catalogs are
not built, so the libintl probes, the NLS defines and the substituted link variables are left
out.  gnulib's own witnesses and helper variables: configure-time values of the gnulib tree
with no Bazel counterpart.

Seeded from the registry entries converted with rules_cc_autoconf 0.30: every key decided the
same way in three or more of them; each entry says how many of the 19 it was seen in.
Reference: https://periareon.github.io/rules_cc_autoconf/convert.html#decisions
"""

# The words a decision may be, besides a literal, a select() or checks.X(...).
decision = struct(
    # Not decided yet: the construct stays out and the run exits 3.
    UNDECIDED = "UNDECIDED",
    # Leave the construct out for good.
    IGNORE = "IGNORE",
    # Keep the converter\'s own translation of the probe.
    ACCEPT = "ACCEPT",
    # Keep the assignments the converter could follow, drop the rest.
    ACCEPT_PARTIAL = "ACCEPT_PARTIAL",
    # The guarded checks or sources always apply.
    ALWAYS = "ALWAYS",
    # The guarded checks or sources never apply.
    NEVER = "NEVER",
)

DECISIONS = {
    # ---- actions: AC_CHECK_LIB:socket
    # why:   AC_CHECK_LIB([socket]) has actions the converter does not translate
    # seen:   3 of 19 modules (gawk, libpcap, tcpdump)
    "actions:AC_CHECK_LIB:socket": decision.IGNORE,

    # ---- guard: autoconf:AC_DEFINE:ENABLE_NLS
    # why:   the condition under which AC_DEFINE:ENABLE_NLS runs cannot be expressed (test $gt_use_preinstalled_gnugettext = yes)
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "guard:autoconf:AC_DEFINE:ENABLE_NLS": decision.NEVER,

    # ---- guard: autoconf:AC_DEFINE:HAVE_DCGETTEXT
    # why:   the condition under which AC_DEFINE:HAVE_DCGETTEXT runs cannot be expressed (test $USE_NLS = yes)
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "guard:autoconf:AC_DEFINE:HAVE_DCGETTEXT": decision.NEVER,

    # ---- guard: autoconf:AC_DEFINE:HAVE_GETTEXT
    # why:   the condition under which AC_DEFINE:HAVE_GETTEXT runs cannot be expressed (test $USE_NLS = yes)
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "guard:autoconf:AC_DEFINE:HAVE_GETTEXT": decision.NEVER,

    # ---- macro: AC_CHECK_TOOLS
    # why:   AC_CHECK_TOOLS is an autoconf builtin without a port in rules_cc_autoconf (configure.ac: AC_CHECK_TOOLS([AR], [ar], [false]))
    # seen:   7 of 19 modules (flex, gawk, grub, libssh2, mpc, mpfr, ppp)
    "macro:AC_CHECK_TOOLS": decision.IGNORE,

    # ---- probe: ac_cv_path_lt_DD
    # why:   configure.ac: AC_CACHE_CHECK([for a working dd], [ac_cv_path_lt_DD], [printf 0123456789abcdef0123456789abcd...: the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:ac_cv_path_lt_DD": decision.IGNORE,

    # ---- probe: autoconf:egrep_Special
    # why:   AC_EGREP_CPP([Special]) greps preprocessor output; needs a compile-time probe
    # seen:   5 of 19 modules (grep, gzip, m4, patch, sed)
    "probe:autoconf:egrep_Special": decision.IGNORE,

    # ---- probe: autoconf:egrep_yes
    # why:   AC_EGREP_CPP([yes]) greps preprocessor output; needs a compile-time probe
    # seen:   3 of 19 modules (bison, gmp, tcpdump)
    "probe:autoconf:egrep_yes": decision.IGNORE,

    # ---- probe: autoconf:gt_func_gnugettext_libc
    # why:   configure.ac: AC_CACHE_CHECK([for GNU gettext in libc], [$gt_func_gnugettext_libc], [AC_LINK_IFELSE(]): the cache variable's name is computed by the shell ($gt_func_gnugettext_libc); only replacement checks (or IGNORE) can stand for it
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "probe:autoconf:gt_func_gnugettext_libc": decision.IGNORE,

    # ---- probe: autoconf:gt_func_gnugettext_libintl
    # why:   configure.ac: AC_CACHE_CHECK([for GNU gettext in libintl], [$gt_func_gnugettext_libintl], [gt_save_CPPFLAGS...: the cache variable's name is computed by the shell ($gt_func_gnugettext_libintl); only replacement checks (or IGNORE) can stand for it
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "probe:autoconf:gt_func_gnugettext_libintl": decision.IGNORE,

    # ---- probe: lt_cv_ar_at_file
    # why:   configure.ac: AC_CACHE_CHECK([for archiver @FILE support], [lt_cv_ar_at_file], [lt_cv_ar_at_file=no]): the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_ar_at_file": decision.IGNORE,

    # ---- probe: lt_cv_cc_needs_belf
    # why:   configure.ac: AC_CACHE_CHECK([whether the C compiler needs -belf], [lt_cv_cc_needs_belf], [AC_LANG_PUSH(C)]): the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_cc_needs_belf": decision.IGNORE,

    # ---- probe: lt_cv_ld_exported_symbols_list
    # why:   configure.ac: AC_CACHE_CHECK([for -exported_symbols_list linker flag], [lt_cv_ld_exported_symbols_list], [l...: the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_ld_exported_symbols_list": decision.IGNORE,

    # ---- probe: lt_cv_sharedlib_from_linklib_cmd
    # why:   configure.ac: AC_CACHE_CHECK([how to associate runtime and link libraries], [lt_cv_sharedlib_from_linklib_c...: the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_sharedlib_from_linklib_cmd": decision.IGNORE,

    # ---- probe: lt_cv_sys_global_symbol_pipe
    # why:   configure.ac: AC_CACHE_VAL([lt_cv_sys_global_symbol_pipe], []): the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_sys_global_symbol_pipe": decision.IGNORE,

    # ---- probe: lt_cv_truncate_bin
    # why:   configure.ac: AC_CACHE_CHECK([how to truncate binary pipes], [lt_cv_truncate_bin], [printf 0123456789abcdef...: the cache value comes from shell around the probe, not from one probe
    # seen:   6 of 19 modules (flex, gawk, libssh2, mpc, mpfr, ppp)
    "probe:lt_cv_truncate_bin": decision.IGNORE,

    # ---- src_condition: GL_COND_OBJ_ASNPRINTF
    # why:   asnprintf.c is an EXTRA source compiled by AC_LIBOBJ inside the ported macro of module vasnprintf; neither the port nor gnulib's replacement rule says when
    # seen:   4 of 19 modules (bison, cpio, grub, m4)
    "src_condition:GL_COND_OBJ_ASNPRINTF": decision.ALWAYS,

    # ---- src_condition: GL_COND_OBJ_PRINTF_ARGS
    # why:   printf-args.c is an EXTRA source compiled by AC_LIBOBJ inside the ported macro of module vasnprintf; neither the port nor gnulib's replacement rule says when
    # seen:   4 of 19 modules (bison, cpio, grub, m4)
    "src_condition:GL_COND_OBJ_PRINTF_ARGS": decision.ALWAYS,

    # ---- src_condition: GL_COND_OBJ_PRINTF_PARSE
    # why:   printf-parse.c is an EXTRA source compiled by AC_LIBOBJ inside the ported macro of module vasnprintf; neither the port nor gnulib's replacement rule says when
    # seen:   4 of 19 modules (bison, cpio, grub, m4)
    "src_condition:GL_COND_OBJ_PRINTF_PARSE": decision.ALWAYS,

    # ---- src_condition: GL_COND_OBJ_VASNPRINTF
    # why:   vasnprintf.c is an EXTRA source compiled by AC_LIBOBJ inside the ported macro of module vasnprintf; neither the port nor gnulib's replacement rule says when
    # seen:   4 of 19 modules (bison, cpio, grub, m4)
    "src_condition:GL_COND_OBJ_VASNPRINTF": decision.ALWAYS,

    # ---- variable: GNULIB_WARN_CFLAGS
    # why:   configure.ac: AC_SUBST([GNULIB_WARN_CFLAGS]): GNULIB_WARN_CFLAGS is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (grep, gzip, patch)
    "variable:GNULIB_WARN_CFLAGS": decision.IGNORE,

    # ---- variable: HARD_LOCALE_LIB
    # why:   configure.ac: AC_SUBST([HARD_LOCALE_LIB]): HARD_LOCALE_LIB is never assigned in this body; its value is not known here
    # seen:   5 of 19 modules (cpio, grep, m4, patch, sed)
    "variable:HARD_LOCALE_LIB": decision.IGNORE,

    # ---- variable: INTLLIBS
    # why:   configure.ac: AC_SUBST([INTLLIBS]): INTLLIBS is never assigned in this body; its value is not known here
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "variable:INTLLIBS": decision.IGNORE,

    # ---- variable: LIBGNU_LIBDEPS
    # why:   configure.ac: AC_SUBST([LIBGNU_LIBDEPS]): LIBGNU_LIBDEPS is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (cpio, grub, make)
    "variable:LIBGNU_LIBDEPS": decision.IGNORE,

    # ---- variable: LIBGNU_LTLIBDEPS
    # why:   configure.ac: AC_SUBST([LIBGNU_LTLIBDEPS]): LIBGNU_LTLIBDEPS is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (cpio, grub, make)
    "variable:LIBGNU_LTLIBDEPS": decision.IGNORE,

    # ---- variable: LIBINTL
    # why:   configure.ac: AC_SUBST([LIBINTL]): LIBINTL is never assigned in this body; its value is not known here
    # seen:   10 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, patch, sed)
    "variable:LIBINTL": decision.IGNORE,

    # ---- variable: LIBTESTS_LIBDEPS
    # why:   configure.ac: AC_SUBST([LIBTESTS_LIBDEPS]): LIBTESTS_LIBDEPS is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (grep, m4, sed)
    "variable:LIBTESTS_LIBDEPS": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNICASE_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNICASE_H]): LIBUNISTRING_UNICASE_H is never assigned in this body; its value is not known here
    # seen:   4 of 19 modules (cpio, grep, m4, sed)
    "variable:LIBUNISTRING_UNICASE_H": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNICTYPE_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNICTYPE_H]): LIBUNISTRING_UNICTYPE_H is never assigned in this body; its value is not known here
    # seen:   5 of 19 modules (cpio, grep, m4, patch, sed)
    "variable:LIBUNISTRING_UNICTYPE_H": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNINORM_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNINORM_H]): LIBUNISTRING_UNINORM_H is never assigned in this body; its value is not known here
    # seen:   4 of 19 modules (cpio, grep, m4, sed)
    "variable:LIBUNISTRING_UNINORM_H": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNISTR_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNISTR_H]): LIBUNISTRING_UNISTR_H is never assigned in this body; its value is not known here
    # seen:   4 of 19 modules (bison, cpio, grep, m4)
    "variable:LIBUNISTRING_UNISTR_H": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNITYPES_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNITYPES_H]): LIBUNISTRING_UNITYPES_H is never assigned in this body; its value is not known here
    # seen:   7 of 19 modules (bison, cpio, grep, grub, m4, patch, sed)
    "variable:LIBUNISTRING_UNITYPES_H": decision.IGNORE,

    # ---- variable: LIBUNISTRING_UNIWIDTH_H
    # why:   configure.ac: AC_SUBST([LIBUNISTRING_UNIWIDTH_H]): LIBUNISTRING_UNIWIDTH_H is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (bison, grub, m4)
    "variable:LIBUNISTRING_UNIWIDTH_H": decision.IGNORE,

    # ---- variable: LIB_HARD_LOCALE
    # why:   configure.ac: AC_SUBST([LIB_HARD_LOCALE]): LIB_HARD_LOCALE is never assigned in this body; its value is not known here
    # seen:   7 of 19 modules (bison, cpio, grep, grub, m4, patch, sed)
    "variable:LIB_HARD_LOCALE": decision.IGNORE,

    # ---- variable: LOCALCHARSET_TESTS_ENVIRONMENT
    # why:   configure.ac: AC_SUBST([LOCALCHARSET_TESTS_ENVIRONMENT]): LOCALCHARSET_TESTS_ENVIRONMENT is never assigned in this body; its value is not known here
    # seen:   7 of 19 modules (bison, cpio, grep, grub, m4, patch, sed)
    "variable:LOCALCHARSET_TESTS_ENVIRONMENT": decision.IGNORE,

    # ---- variable: LTLIBINTL
    # why:   configure.ac: AC_SUBST([LTLIBINTL]): LTLIBINTL is never assigned in this body; its value is not known here
    # seen:   10 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, patch, sed)
    "variable:LTLIBINTL": decision.IGNORE,

    # ---- variable: POSUB
    # why:   configure.ac: AC_SUBST([POSUB]): POSUB is never assigned in this body; its value is not known here
    # seen:   9 of 19 modules (bison, cpio, flex, gawk, grep, grub, m4, make, sed)
    "variable:POSUB": decision.IGNORE,

    # ---- variable: WARN_CFLAGS
    # why:   configure.ac: AC_SUBST([WARN_CFLAGS]): WARN_CFLAGS is never assigned in this body; its value is not known here
    # seen:   5 of 19 modules (grep, gzip, m4, patch, sed)
    "variable:WARN_CFLAGS": decision.IGNORE,

    # ---- variable: WERROR_CFLAGS
    # why:   configure.ac: AC_SUBST([WERROR_CFLAGS]): WERROR_CFLAGS is never assigned in this body; its value is not known here
    # seen:   5 of 19 modules (grep, gzip, m4, patch, sed)
    "variable:WERROR_CFLAGS": decision.IGNORE,

    # ---- variable: abs_aux_dir
    # why:   configure.ac: AC_SUBST([abs_aux_dir]): abs_aux_dir is never assigned in this body; its value is not known here
    # seen:   3 of 19 modules (grep, m4, sed)
    "variable:abs_aux_dir": decision.IGNORE,

    # ---- variable: gltests_WITNESS
    # why:   configure.ac: AC_SUBST([gltests_WITNESS]): gltests_WITNESS is never assigned in this body; its value is not known here
    # seen:   8 of 19 modules (bison, cpio, grep, grub, gzip, make, patch, sed)
    "variable:gltests_WITNESS": decision.IGNORE,

    # ---- variable: host_cpu
    # why:   AC_SUBST([host_cpu]) takes its value from shell: $[1]
    # seen:   3 of 19 modules (gawk, grub, readline)
    "variable:host_cpu": decision.IGNORE,

    # ---- variable: host_os
    # why:   host_os: the assignments cannot be expressed as one check
    # seen:   3 of 19 modules (gawk, grub, readline)
    "variable:host_os": decision.IGNORE,
}
