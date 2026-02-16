AC_INIT([test_getopt], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getopt.m4
gl_FUNC_GETOPT_POSIX
gl_FUNC_GETOPT_GNU
gl_GETOPT_CHECK_HEADERS
gl_GETOPT_SUBSTITUTE_HEADER

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=0
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
