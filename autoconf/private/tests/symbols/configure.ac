AC_INIT([test_symbols], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for symbols
# Note: AC_CHECK_SYMBOL is not a standard autoconf macro
# We use AC_COMPILE_IFELSE to check if symbols are defined

AC_MSG_CHECKING([for NULL])
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[
#include <stddef.h>
]], [[
#ifndef NULL
#error "NULL not defined"
#endif
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([HAVE_NULL], [1], [Define to 1 if NULL is defined])],
[AC_MSG_RESULT([no])])

AC_MSG_CHECKING([for __linux__])
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[
#ifndef __linux__
#error "__linux__ not defined"
#endif
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([HAVE___LINUX__], [1], [Define to 1 if __linux__ is defined])],
[AC_MSG_RESULT([no])])

AC_MSG_CHECKING([for NONEXISTENT_SYMBOL])
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[
#ifndef NONEXISTENT_SYMBOL
#error "NONEXISTENT_SYMBOL not defined"
#endif
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([HAVE_NONEXISTENT_SYMBOL], [1], [Define to 1 if NONEXISTENT_SYMBOL is defined])],
[AC_MSG_RESULT([no])])

AC_OUTPUT
