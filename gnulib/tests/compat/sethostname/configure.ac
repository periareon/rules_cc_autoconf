AC_INIT([test_sethostname], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from sethostname.m4
gl_FUNC_SETHOSTNAME

# Account for global Bazel defaults
HAVE_NETDB_H=1
AC_SUBST([HAVE_NETDB_H])
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
