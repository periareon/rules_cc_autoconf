AC_INIT([test_putenv], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from putenv.m4
gl_FUNC_PUTENV
gl_PREREQ_PUTENV

# Account for global Bazel defaults
HAVE_DECL__PUTENV=0
AC_SUBST([HAVE_DECL__PUTENV])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
