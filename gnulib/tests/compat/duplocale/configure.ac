AC_INIT([test_duplocale], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from duplocale.m4
gl_FUNC_DUPLOCALE
gl_PREREQ_DUPLOCALE

# Account for global Bazel defaults
HAVE_NL_LANGINFO_L=1
AC_SUBST([HAVE_NL_LANGINFO_L])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
