AC_INIT([test_idpriv], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from idpriv.m4
gl_IDPRIV

# Account for global Bazel defaults
HAVE_TIMESPEC_GETRES=1
AC_SUBST([HAVE_TIMESPEC_GETRES])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
