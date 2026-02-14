AC_INIT([test_readutmp], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from readutmp.m4
gl_READUTMP
gl_PREREQ_READUTMP_H

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
