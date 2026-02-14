AC_INIT([test_fnmatch], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fnmatch.m4
gl_FUNC_FNMATCH_POSIX
gl_FUNC_FNMATCH_GNU
gl_PREREQ_FNMATCH

# Account for global Bazel defaults
HAVE_MBSRTOWCS=1
AC_SUBST([HAVE_MBSRTOWCS])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
