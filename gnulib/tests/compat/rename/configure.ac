AC_INIT([test_rename], [1.0])
AC_CONFIG_HEADERS([config.h])

gl_FUNC_RENAME

# Account for global Bazel defaults
HAVE_LSTAT=1
AC_SUBST([HAVE_LSTAT])
# Account for global Bazel defaults
HAVE_LINK=1
AC_SUBST([HAVE_LINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
