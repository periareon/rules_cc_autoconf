AC_INIT([test_fmaf], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fmaf.m4
gl_FUNC_FMAF
gl_FUNC_FMAF_WORKS
gl_PREREQ_FMAF

# Account for global Bazel defaults
HAVE_FEGETROUND=1
AC_SUBST([HAVE_FEGETROUND])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
