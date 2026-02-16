AC_INIT([test_gethostname], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from gethostname.m4
gl_FUNC_GETHOSTNAME
gl_PREREQ_HOST_NAME_MAX
gl_PREREQ_GETHOSTNAME

# Account for global Bazel defaults
HAVE_UNAME=1
AC_SUBST([HAVE_UNAME])
HAVE_NETDB_H=1
AC_SUBST([HAVE_NETDB_H])
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
