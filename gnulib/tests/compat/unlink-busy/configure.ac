AC_INIT([test_unlink-busy], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from unlink-busy.m4
gl_FUNC_UNLINK_BUSY_TEXT

# Account for global Bazel defaults
HAVE_UNISTD_H=1
AC_SUBST([HAVE_UNISTD_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
