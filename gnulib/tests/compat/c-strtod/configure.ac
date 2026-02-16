AC_INIT([test_c-strtod], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from c-strtod.m4
gl_C_STRTOD
gl_C_STRTOF
gl_C_STRTOLD

# Account for global Bazel defaults
HAVE_NL_LANGINFO=1
AC_SUBST([HAVE_NL_LANGINFO])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
