AC_INIT([test_fcntl-o], [1.0])
AC_CONFIG_HEADERS([config.h])

gl_FCNTL_O_FLAGS

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
