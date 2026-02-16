AC_INIT([test_strtod], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from strtod.m4
gl_FUNC_STRTOD
gl_PREREQ_STRTOD

# Account for global Bazel defaults
HAVE_NL_LANGINFO=1
AC_SUBST([HAVE_NL_LANGINFO])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
