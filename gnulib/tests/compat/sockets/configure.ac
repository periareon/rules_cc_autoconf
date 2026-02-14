AC_INIT([test_sockets], [1.0])
AC_CONFIG_HEADERS([config.h])

gl_SOCKETS

# Account for global Bazel defaults
HAVE_SYS_SOCKET_H=1
AC_SUBST([HAVE_SYS_SOCKET_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
