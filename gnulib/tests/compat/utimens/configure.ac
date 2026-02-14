AC_INIT([test_utimens], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from utimens.m4
gl_UTIMENS

# Account for global Bazel defaults
HAVE_FUTIMENS=1
AC_SUBST([HAVE_FUTIMENS])
HAVE_SYS_TIME_H=1
AC_SUBST([HAVE_SYS_TIME_H])
HAVE_UTIMENSAT=1
AC_SUBST([HAVE_UTIMENSAT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
