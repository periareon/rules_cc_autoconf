AC_INIT([test_check-math-lib], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from check-math-lib.m4
# gl_CHECK_MATH_LIB(VARIABLE, EXPRESSION, ...) takes mandatory arguments.
gl_CHECK_MATH_LIB([SQRT_LIBM], [x = sqrt (x);])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
