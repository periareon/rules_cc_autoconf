AC_INIT([test_link-follow], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from link-follow.m4
gl_FUNC_LINK_FOLLOWS_SYMLINK

# Account for global Bazel defaults
HAVE_READLINK=1
AC_SUBST([HAVE_READLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
