AC_INIT([test_strtof], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from strtof.m4
gl_FUNC_STRTOF
gl_PREREQ_STRTOF

# Account for global Bazel defaults
HAVE_NL_LANGINFO=1
AC_SUBST([HAVE_NL_LANGINFO])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
