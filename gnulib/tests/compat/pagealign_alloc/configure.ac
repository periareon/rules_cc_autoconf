AC_INIT([test_pagealign_alloc], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from pagealign_alloc.m4
gl_PAGEALIGN_ALLOC
gl_PREREQ_PAGEALIGN_ALLOC

# Account for global Bazel defaults
HAVE_POSIX_MEMALIGN=1
AC_SUBST([HAVE_POSIX_MEMALIGN])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
