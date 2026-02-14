AC_INIT([test_ttyname_r], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from ttyname_r.m4
gl_FUNC_TTYNAME_R
gl_PREREQ_TTYNAME_R

# Account for global Bazel defaults
HAVE_TTYNAME_R=1
AC_SUBST([HAVE_TTYNAME_R])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
