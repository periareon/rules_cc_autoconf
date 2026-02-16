AC_INIT([test_strerror_r], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from strerror_r.m4
gl_FUNC_STRERROR_R
gl_PREREQ_STRERROR_R
gl_FUNC_STRERROR_R_WORKS

# Account for global Bazel defaults
HAVE_STRERROR_R=1
AC_SUBST([HAVE_STRERROR_R])
REPLACE_STRERROR_0=1
AC_SUBST([REPLACE_STRERROR_0])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
