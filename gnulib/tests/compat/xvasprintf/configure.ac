AC_INIT([test_xvasprintf], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from xvasprintf.m4
gl_XVASPRINTF

# Account for global Bazel defaults
HAVE_STRERRORNAME_NP=1
AC_SUBST([HAVE_STRERRORNAME_NP])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
