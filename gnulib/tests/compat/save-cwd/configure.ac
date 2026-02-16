AC_INIT([test_save-cwd], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from save-cwd.m4
gl_SAVE_CWD

# Account for global Bazel defaults
HAVE_FCHDIR=1
AC_SUBST([HAVE_FCHDIR])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
