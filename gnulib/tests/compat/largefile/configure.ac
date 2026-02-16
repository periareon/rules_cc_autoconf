AC_INIT([test_largefile], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from largefile.m4
gl_LARGEFILE
gl_SET_LARGEFILE_SOURCE

# Account for global Bazel defaults
# These variables are substituted by Bazel's transitive dependencies
HAVE_FSEEKO=1
AC_SUBST([HAVE_FSEEKO])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
