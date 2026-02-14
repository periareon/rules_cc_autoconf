AC_INIT([test_arpa_inet_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from arpa_inet_h.m4
gl_ARPA_INET_H
gl_ARPA_INET_MODULE_INDICATOR

# Account for global Bazel defaults
HAVE_SYS_SOCKET_H=1
AC_SUBST([HAVE_SYS_SOCKET_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
