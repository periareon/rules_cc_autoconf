AC_INIT([test_flock], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from flock.m4
gl_FUNC_FLOCK
gl_PREREQ_FLOCK

# Account for global Bazel defaults
# fcntl module provides HAVE_FCNTL via dependency
HAVE_FCNTL=1
AC_SUBST([HAVE_FCNTL])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
