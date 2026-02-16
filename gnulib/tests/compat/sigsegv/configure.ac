AC_INIT([test_sigsegv], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from sigsegv.m4
gl_SIGSEGV

# Bazel does not handle the configure conditions so outputs will
# currently never agree
HAVE_LIBSIGSEGV="no"
AC_SUBST([HAVE_LIBSIGSEGV])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
