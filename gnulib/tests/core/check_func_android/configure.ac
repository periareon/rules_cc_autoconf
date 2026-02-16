AC_INIT([test_gl_check_func_android], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Test gl_CHECK_FUNCS_ANDROID macro behavior
# This macro checks if a function exists and:
# - AC_DEFINE's HAVE_<FUNCTION> in config.h ONLY when function exists
# - AC_SUBST's HAVE_<FUNCTION> in subst.h ALWAYS (0 or 1)
#
# Note: gl_CHECK_FUNCS_ANDROID sets up the check and conditional AC_DEFINE,
# but we need to manually set up AC_SUBST based on the cache variable result.

# Should exist on all platforms
gl_CHECK_FUNCS_ANDROID([printf], [[#include <stdio.h>]])
# Extract result from cache variable and set up AC_SUBST
# gl_CHECK_FUNCS_ANDROID uses ac_cv_func_<function> cache variable (standard Autoconf naming)
if test "$ac_cv_func_printf" = yes; then
    HAVE_PRINTF=1
else
    HAVE_PRINTF=0
fi
AC_SUBST([HAVE_PRINTF])

# Doesn't exist anywhere
gl_CHECK_FUNCS_ANDROID([doesntexist], [[#include <unistd.h>]])
# Extract result from cache variable and set up AC_SUBST
if test "$ac_cv_func_doesntexist" = yes; then
    HAVE_DOESNTEXIST=1
else
    HAVE_DOESNTEXIST=0
fi
AC_SUBST([HAVE_DOESNTEXIST])

AC_CONFIG_FILES([subst.h:subst.h.in])

AC_OUTPUT
