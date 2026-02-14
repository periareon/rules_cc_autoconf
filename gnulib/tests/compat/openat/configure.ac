AC_INIT([test_openat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from openat.m4
gl_FUNC_OPENAT
gl_PREREQ_OPENAT

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
