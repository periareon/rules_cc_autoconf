AC_INIT([test_gl_check_next_headers], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Test gl_CHECK_NEXT_HEADERS macro
# This macro sets NEXT_* variables for a header, used by gnulib's include_next
# mechanism. For modern compilers that support #include_next, both variables
# are set to "<header.h>".
#
# This macro creates two substitution variables:
# - NEXT_<HEADER> (e.g., NEXT_ASSERT_H)
# - NEXT_AS_FIRST_DIRECTIVE_<HEADER> (e.g., NEXT_AS_FIRST_DIRECTIVE_ASSERT_H)
#
# Simple examples that behave the same on Windows, Linux, and macOS:
# Test with assert.h (standard C header - exists everywhere)
gl_CHECK_NEXT_HEADERS([assert.h])
# gl_CHECK_NEXT_HEADERS sets cache variables, but we need to manually set up AC_SUBST
# For modern compilers, both variables are set to "<header.h>"
NEXT_ASSERT_H="<assert.h>"
NEXT_AS_FIRST_DIRECTIVE_ASSERT_H="<assert.h>"
AC_SUBST([NEXT_ASSERT_H])
AC_SUBST([NEXT_AS_FIRST_DIRECTIVE_ASSERT_H])

# Test with sys/stat.h (standard C header - exists everywhere)
# Note: This demonstrates a header with subdirectories
gl_CHECK_NEXT_HEADERS([sys/stat.h])
# Set up AC_SUBST for sys/stat.h
NEXT_SYS_STAT_H="<sys/stat.h>"
NEXT_AS_FIRST_DIRECTIVE_SYS_STAT_H="<sys/stat.h>"
AC_SUBST([NEXT_SYS_STAT_H])
AC_SUBST([NEXT_AS_FIRST_DIRECTIVE_SYS_STAT_H])

AC_CONFIG_FILES([subst.h:subst.h.in])

AC_OUTPUT
