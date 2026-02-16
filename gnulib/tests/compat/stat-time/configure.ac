AC_INIT([test_stat-time], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from stat-time.m4
gl_STAT_TIME
gl_STAT_BIRTHTIME

# Account for global Bazel defaults
HAVE_SYS_TIME_H=1
AC_SUBST([HAVE_SYS_TIME_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
