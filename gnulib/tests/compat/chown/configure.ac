AC_INIT([test_chown], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from chown.m4
AC_FUNC_CHOWN
gl_FUNC_CHOWN
gl_FUNC_CHOWN_FOLLOWS_SYMLINK

# Account for global Bazel defaults
HAVE_DECL_EXECVPE=1
AC_SUBST([HAVE_DECL_EXECVPE])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
