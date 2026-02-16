AC_INIT([test_fchmodat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fchmodat.m4
gl_FUNC_FCHMODAT
gl_PREREQ_FCHMODAT

# Account for global Bazel defaults
HAVE_READLINKAT=1
AC_SUBST([HAVE_READLINKAT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
