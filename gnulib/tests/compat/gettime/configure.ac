AC_INIT([test_gettime], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from gettime.m4
gl_GETTIME
gl_CHECK_FUNC_TIMESPEC_GET
gl_GETTIME_RES

# Account for global Bazel defaults
HAVE_TIMESPEC_GET=1
AC_SUBST([HAVE_TIMESPEC_GET])
# HAVE_TIMESPEC_GETRES: same as gl_GETTIME_RES / gl_CHECK_FUNCS_ANDROID([timespec_getres], [[#include <time.h>]])
AC_CHECK_FUNC([timespec_getres], [HAVE_TIMESPEC_GETRES=1], [HAVE_TIMESPEC_GETRES=0])
AC_SUBST([HAVE_TIMESPEC_GETRES])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
