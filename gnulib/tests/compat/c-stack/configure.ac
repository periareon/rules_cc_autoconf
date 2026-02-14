AC_INIT([test_c-stack], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from c-stack.m4
gl_C_STACK

# Bazel does not handle the configure conditions so outputs will
# currently never agree
HAVE_LIBSIGSEGV="no"
AC_SUBST([HAVE_LIBSIGSEGV])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
