AC_INIT([test_access], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from access.m4
gl_FUNC_ACCESS

# Account for global Bazel defaults
HAVE_LSTAT=1
AC_SUBST([HAVE_LSTAT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
