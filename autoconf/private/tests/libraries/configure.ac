AC_INIT([test_libraries], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for math library
AC_CHECK_LIB([m], [cos])

# Check for pthread library
AC_CHECK_LIB([pthread], [pthread_create])

# Check for Windows kernel library (Windows only)
AC_CHECK_LIB([kernel32], [GetLastError])

# Check for a library that doesn't exist
AC_CHECK_LIB([nonexistent], [fake_function])

AC_OUTPUT
