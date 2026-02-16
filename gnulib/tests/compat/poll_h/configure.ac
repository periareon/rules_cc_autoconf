AC_INIT([test_poll_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from poll_h.m4
gl_POLL_H
gl_POLL_MODULE_INDICATOR

# Account for global Bazel defaults
HAVE_SYS_SOCKET_H=1
AC_SUBST([HAVE_SYS_SOCKET_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
