AC_INIT([test_lchown], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from lchown.m4
gl_FUNC_LCHOWN

# Account for global Bazel defaults
HAVE_LCHMOD=1
AC_SUBST([HAVE_LCHMOD])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
