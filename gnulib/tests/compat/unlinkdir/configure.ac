AC_INIT([test_unlinkdir], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from unlinkdir.m4
gl_UNLINKDIR

# Account for global Bazel defaults
HAVE_UNISTD_H=1
AC_SUBST([HAVE_UNISTD_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
