AC_INIT([test_supersede], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from supersede.m4
gl_SUPERSEDE

# Account for global Bazel defaults
HAVE_CHOWN=1
AC_SUBST([HAVE_CHOWN])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
