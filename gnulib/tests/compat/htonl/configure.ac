AC_INIT([test_htonl], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from htonl.m4
gl_FUNC_HTONL

# Account for global Bazel defaults
HAVE_SYS_SOCKET_H=1
AC_SUBST([HAVE_SYS_SOCKET_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
