AC_INIT([test_getloadavg], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getloadavg.m4
gl_GETLOADAVG
gl_PREREQ_GETLOADAVG

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
