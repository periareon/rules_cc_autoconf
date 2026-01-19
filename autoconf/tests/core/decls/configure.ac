AC_INIT([test_decls], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for declarations
AC_CHECK_DECLS([NULL], [], [], [#include <stddef.h>])
AC_CHECK_DECLS([stdout], [], [], [#include <stdio.h>])
AC_CHECK_DECLS([nonexistent_symbol], [], [], [#include <stdio.h>])
AC_CHECK_DECLS([nonexistent_function], [], [], [#include <stdio.h>])

AC_OUTPUT
