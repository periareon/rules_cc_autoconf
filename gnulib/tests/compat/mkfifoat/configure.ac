AC_INIT([test_mkfifoat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from mkfifoat.m4
gl_FUNC_MKFIFOAT

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
