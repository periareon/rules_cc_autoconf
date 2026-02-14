AC_INIT([test_fopen], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fopen.m4
gl_FUNC_FOPEN_ITSELF
gl_FUNC_FOPEN
gl_FUNC_FOPEN_GNU
gl_PREREQ_FOPEN

# Account for global Bazel defaults
HAVE_SYS_SOCKET_H=1
AC_SUBST([HAVE_SYS_SOCKET_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
