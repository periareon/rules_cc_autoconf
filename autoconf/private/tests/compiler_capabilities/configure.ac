AC_INIT([test_compiler_capabilities], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Check for C compiler
AC_PROG_CC

# Test C language keywords (still useful for portability)
AC_C_RESTRICT
AC_C_INLINE

# Test compiler flag support
# Note: AC_CHECK_C_COMPILER_FLAG is not a standard autoconf macro
# It's typically provided by ax_check_compile_flag.m4 from autoconf-archive

# For demonstration, we'll use AC_COMPILE_IFELSE to check flags manually
AC_MSG_CHECKING([whether $CC accepts -Wall])
save_CFLAGS="$CFLAGS"
CFLAGS="$CFLAGS -Wall"
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[]])],
  [AC_MSG_RESULT([yes])
   AC_DEFINE([HAVE_FLAG_WALL], [1], [Define to 1 if compiler supports -Wall])],
  [AC_MSG_RESULT([no])])
CFLAGS="$save_CFLAGS"

AC_MSG_CHECKING([whether $CC accepts -std=c99])
save_CFLAGS="$CFLAGS"
CFLAGS="$CFLAGS -std=c99"
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[]])],
  [AC_MSG_RESULT([yes])
   AC_DEFINE([HAVE_FLAG_STD_C99], [1], [Define to 1 if compiler supports -std=c99])],
  [AC_MSG_RESULT([no])])
CFLAGS="$save_CFLAGS"

# Test C++ compiler flag
AC_PROG_CXX
AC_LANG_PUSH([C++])
AC_MSG_CHECKING([whether $CXX accepts -std=c++17])
save_CXXFLAGS="$CXXFLAGS"
CXXFLAGS="$CXXFLAGS -std=c++17"
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[]])],
  [AC_MSG_RESULT([yes])
   AC_DEFINE([HAVE_FLAG_STD_CXX17], [1], [Define to 1 if compiler supports -std=c++17])],
  [AC_MSG_RESULT([no])])
CXXFLAGS="$save_CXXFLAGS"
AC_LANG_POP([C++])

# Test compiler capability
AC_PROG_CC_C_O

AC_OUTPUT
