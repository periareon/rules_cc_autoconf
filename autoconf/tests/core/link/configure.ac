AC_INIT([test_link], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Test AC_TRY_LINK (AC_LINK_IFELSE in autoconf)
# This checks if code compiles AND links successfully
AC_LINK_IFELSE(
    [AC_LANG_PROGRAM(
        [[#include <langinfo.h>]],
        [[char* cs = nl_langinfo(CODESET); return !cs;]])],
    [AC_DEFINE([HAVE_LANGINFO_CODESET], [1],
        [Define if nl_langinfo(CODESET) links successfully.])],
    [])

AC_LINK_IFELSE(
    [AC_LANG_PROGRAM(
        [[#include <stdio.h>]],
        [[printf("test");]])],
    [AC_DEFINE([HAVE_PRINTF_LINK], [1],
        [Define if printf links successfully.])],
    [])

AC_OUTPUT
