AC_INIT([test_chdir-long], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from chdir-long.m4
gl_FUNC_CHDIR_LONG
gl_PREREQ_CHDIR_LONG

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
