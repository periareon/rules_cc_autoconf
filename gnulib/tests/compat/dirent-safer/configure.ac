AC_INIT([test_dirent-safer], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from dirent-safer.m4
gl_DIRENT_SAFER

# Account for global Bazel defaults
HAVE_FDOPENDIR=1
AC_SUBST([HAVE_FDOPENDIR])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
