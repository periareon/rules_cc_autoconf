AC_INIT([test_custom], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for stdatomic.h support
AC_MSG_CHECKING([for stdatomic.h support])
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[
#include <stdatomic.h>
]], [[
atomic_int x = 0;
(void)x;
return 0;
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([HAVE_STDATOMIC], [1], [Define to 1 if stdatomic.h is supported])],
[AC_MSG_RESULT([no])])

# Check if basic compilation works
AC_MSG_CHECKING([if basic compilation works])
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[
return 0;
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([BASIC_COMPILE_WORKS], [1], [Define to 1 if basic compilation works])],
[AC_MSG_RESULT([no])])

AC_OUTPUT
