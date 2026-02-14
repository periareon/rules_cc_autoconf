AC_INIT([test_alignalloc], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from alignalloc.m4
gl_ALIGNALLOC

# Account for global Bazel defaults
HAVE_POSIX_MEMALIGN=1
AC_SUBST([HAVE_POSIX_MEMALIGN])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
