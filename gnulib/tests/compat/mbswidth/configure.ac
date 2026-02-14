AC_INIT([test_mbswidth], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from mbswidth.m4
gl_MBSWIDTH

# Account for global Bazel defaults
HAVE_MBSINIT=1
AC_SUBST([HAVE_MBSINIT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
