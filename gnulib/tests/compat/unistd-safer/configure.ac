AC_INIT([test_unistd-safer], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from unistd-safer.m4
gl_UNISTD_SAFER

# Account for global Bazel defaults
HAVE_PIPE=1
AC_SUBST([HAVE_PIPE])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
