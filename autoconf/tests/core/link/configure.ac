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

# Test extern declaration pattern (AC_CHECK_FUNC default pattern)
# This verifies that linking works with the exact pattern used by AC_CHECK_FUNC
# Note: AC_LANG_PROGRAM wraps the body in main(), so we don't include "int main(void)"
AC_LINK_IFELSE(
    [AC_LANG_PROGRAM(
        [],
        [[/* Override any GCC internal prototype to avoid an error.
   Use char because int might match the return type of a GCC
   builtin and then its argument prototype would still apply.  */
#ifdef __cplusplus
extern "C"
#endif
char malloc();

return malloc();]])],
    [AC_DEFINE([HAVE_MALLOC_EXTERN_PATTERN], [1],
        [Define if malloc links with extern declaration pattern.])],
    [])

AC_OUTPUT
