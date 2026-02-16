AC_INIT([test_unlockpt], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from unlockpt.m4
gl_FUNC_UNLOCKPT
gl_PREREQ_UNLOCKPT

# Account for global Bazel defaults
HAVE_REVOKE=1
AC_SUBST([HAVE_REVOKE])
HAVE_UNLOCKPT=1
AC_SUBST([HAVE_UNLOCKPT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
