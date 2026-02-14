AC_INIT([test_fcntl_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fcntl_h.m4
gl_FCNTL_H
gl_FCNTL_MODULE_INDICATOR

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
