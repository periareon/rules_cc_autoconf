AC_INIT([test_time_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from time_h.m4
gl_TIME_H
gl_CHECK_TYPE_STRUCT_TIMESPEC
gl_TIME_MODULE_INDICATOR

# Account for global Bazel defaults
HAVE_SYS_TIME_H=1
AC_SUBST([HAVE_SYS_TIME_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
