AC_INIT([test_fma], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fma.m4
gl_FUNC_FMA
gl_FUNC_FMA_WORKS
gl_PREREQ_FMA

# Account for global Bazel defaults
HAVE_FEGETROUND=1
AC_SUBST([HAVE_FEGETROUND])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
