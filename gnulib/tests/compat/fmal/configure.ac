AC_INIT([test_fmal], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fmal.m4
gl_FUNC_FMAL
gl_FUNC_FMAL_WORKS
gl_PREREQ_FMAL

# Account for global Bazel defaults
HAVE_FEGETROUND=1
AC_SUBST([HAVE_FEGETROUND])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
