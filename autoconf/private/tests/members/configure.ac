AC_INIT([test_members], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for struct members
AC_CHECK_MEMBERS([struct tm.tm_sec, struct tm.tm_year, struct tm.tm_nonexistent], [], [], [#include <time.h>])

AC_OUTPUT
