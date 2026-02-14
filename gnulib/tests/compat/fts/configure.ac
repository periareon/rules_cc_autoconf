AC_INIT([test_fts], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fts.m4
gl_FUNC_FTS
gl_FUNC_FTS_CORE

# Account for global Bazel defaults
HAVE_SYMLINK=1
AC_SUBST([HAVE_SYMLINK])
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
