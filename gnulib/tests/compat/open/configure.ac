AC_INIT([test_open], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from open.m4
gl_FUNC_OPEN
gl_PREREQ_OPEN

# Account for global Bazel defaults
HAVE_LSTAT=1
AC_SUBST([HAVE_LSTAT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
