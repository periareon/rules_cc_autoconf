AC_INIT([test_inet_pton], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from inet_pton.m4
gl_FUNC_INET_PTON
gl_PREREQ_INET_PTON

# Account for global Bazel defaults
HAVE_NETDB_H=1
AC_SUBST([HAVE_NETDB_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
