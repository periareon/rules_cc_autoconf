AC_INIT([test_openat2], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from openat2.m4
gl_FUNC_OPENAT2
gl_PREREQ_OPENAT2

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
