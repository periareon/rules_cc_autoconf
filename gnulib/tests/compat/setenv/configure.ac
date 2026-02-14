AC_INIT([test_setenv], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from setenv.m4
gl_FUNC_SETENV
gl_FUNC_SETENV_SEPARATE
gl_FUNC_UNSETENV
gl_PREREQ_SETENV
gl_PREREQ_UNSETENV

# Account for global Bazel defaults
HAVE_DECL__PUTENV=0
AC_SUBST([HAVE_DECL__PUTENV])
HAVE_TSEARCH=1
AC_SUBST([HAVE_TSEARCH])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
