AC_INIT([test_renameat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from renameat.m4
gl_FUNC_RENAMEAT

# Account for global Bazel defaults
HAVE_LINK=1
AC_SUBST([HAVE_LINK])
HAVE_LSTAT=1
AC_SUBST([HAVE_LSTAT])
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
