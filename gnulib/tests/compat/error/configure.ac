AC_INIT([test_error], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from error.m4
gl_ERROR
gl_PREREQ_ERROR

# Account for global Bazel defaults
HAVE_DECL_STRERROR_R=1
AC_SUBST([HAVE_DECL_STRERROR_R])
HAVE_STRERROR_R=1
AC_SUBST([HAVE_STRERROR_R])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
