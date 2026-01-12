AC_INIT([test_gettime], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from gettime.m4
gl_GETTIME
gl_CHECK_FUNC_TIMESPEC_GET
gl_GETTIME_RES

AC_CONFIG_FILES([gnulib_gettime.h:gnulib_gettime.h.in])
AC_OUTPUT