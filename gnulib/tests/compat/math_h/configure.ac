AC_INIT([test_math_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from math_h.m4
gl_MATH_H
gl_MATH_MODULE_INDICATOR
gl_LONG_DOUBLE_VS_DOUBLE

# Account for global Bazel defaults
HAVE_MATH_H=1
AC_SUBST([HAVE_MATH_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
