AC_INIT([test_fegetround], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fegetround.m4
gl_FUNC_FEGETROUND

# Account for global Bazel defaults
HAVE_FEGETROUND=1
AC_SUBST([HAVE_FEGETROUND])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
