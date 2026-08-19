AC_INIT([test_libraries], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for a symbol in the math library
AC_CHECK_LIB([m], [cos])

# Check whether the linker accepts the math library.  Using main avoids
# compiler builtin and optimization behavior for ordinary math functions.
AC_CHECK_LIB([m], [main],
  [AC_DEFINE([HAVE_LIBM_MAIN], [1],
    [Define to 1 if the linker accepts the `m' library (-lm).])])

# Check for pthread library
AC_CHECK_LIB([pthread], [pthread_create])

# Check for Windows kernel library (Windows only)
AC_CHECK_LIB([kernel32], [GetLastError])

# Check for a library that doesn't exist
AC_CHECK_LIB([nonexistent], [fake_function])
AC_CHECK_LIB([nonexistent], [main],
  [AC_DEFINE([HAVE_LIBNONEXISTENT_MAIN], [1],
    [Define to 1 if the linker accepts the `nonexistent' library.])])

AC_OUTPUT
