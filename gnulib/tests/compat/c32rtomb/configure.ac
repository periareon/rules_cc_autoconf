AC_INIT([test_c32rtomb], [1.0])
AC_CONFIG_HEADERS([config.h])

# gl_UCHAR_H_DEFAULTS, which the toolchain supplies on the Bazel side: the
# default the macros below override (gl_FUNC_C32RTOMB sets HAVE_C32RTOMB=0
# when the probe finds no c32rtomb, as on a macOS SDK that does not declare
# it), so it has to come first, as it does in gnulib.
HAVE_C32RTOMB=1
AC_SUBST([HAVE_C32RTOMB])

# Invoke macros from c32rtomb.m4
gl_FUNC_C32RTOMB
gl_CHECK_FUNC_C32RTOMB
gl_C32RTOMB_SANITYCHECK

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
