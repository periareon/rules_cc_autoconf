AC_INIT([test_pathmax], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from pathmax.m4
gl_PATHMAX
gl_PATHMAX_SNIPPET
gl_PATHMAX_SNIPPET_PREREQ

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
