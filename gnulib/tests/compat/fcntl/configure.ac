AC_INIT([test_fcntl], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fcntl.m4
gl_FUNC_FCNTL
gl_REPLACE_FCNTL

# Account for global Bazel defaults
HAVE_FCNTL=1
AC_SUBST([HAVE_FCNTL])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
