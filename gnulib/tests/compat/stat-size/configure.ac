AC_INIT([test_stat-size], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from stat-size.m4
gl_STAT_SIZE

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
