AC_INIT([test_getcwd], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getcwd.m4
gl_FUNC_GETCWD_NULL
gl_FUNC_GETCWD_SIGNATURE
gl_FUNC_GETCWD_LGPL
gl_FUNC_GETCWD
gl_PREREQ_GETCWD

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
