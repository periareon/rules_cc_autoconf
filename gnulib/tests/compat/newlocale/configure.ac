AC_INIT([test_newlocale], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from newlocale.m4
gl_FUNC_NEWLOCALE
gl_PREREQ_NEWLOCALE

# Account for global Bazel defaults
HAVE_NL_LANGINFO_L=1
AC_SUBST([HAVE_NL_LANGINFO_L])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
