AC_INIT([test_linkat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from linkat.m4
gl_FUNC_LINKAT

# Account for global Bazel defaults
HAVE_HASMNTOPT=1
AC_SUBST([HAVE_HASMNTOPT])
HAVE_SETMNTENT=1
AC_SUBST([HAVE_SETMNTENT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
