AC_INIT([test_strerror], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from strerror.m4
gl_FUNC_STRERROR
gl_FUNC_STRERROR_0

# Account for global Bazel defaults
HAVE_STRERROR_R=1
AC_SUBST([HAVE_STRERROR_R])
AC_SUBST([REPLACE_STRERROR_0])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
