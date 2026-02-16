AC_INIT([test_open-slash], [1.0])
AC_CONFIG_HEADERS([config.h])

gl_OPEN_TRAILING_SLASH_BUG

# Account for global Bazel defaults
HAVE_LSTAT=1
AC_SUBST([HAVE_LSTAT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
