AC_INIT([test_c32rtomb], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from c32rtomb.m4
gl_FUNC_C32RTOMB
gl_CHECK_FUNC_C32RTOMB
gl_C32RTOMB_SANITYCHECK

# Account for global Bazel defaults
HAVE_C32RTOMB=1
AC_SUBST([HAVE_C32RTOMB])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
