AC_INIT([test_rpmatch], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from rpmatch.m4
gl_FUNC_RPMATCH
gl_PREREQ_RPMATCH

# Account for global Bazel defaults
HAVE_LANGINFO_H=1
AC_SUBST([HAVE_LANGINFO_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
