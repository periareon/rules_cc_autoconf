AC_INIT([test_strtold], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from strtold.m4
gl_FUNC_STRTOLD
gl_PREREQ_STRTOLD

# Account for global Bazel defaults
HAVE_NL_LANGINFO=1
AC_SUBST([HAVE_NL_LANGINFO])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
