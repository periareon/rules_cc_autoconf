AC_INIT([test_ptsname], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from ptsname.m4
gl_FUNC_PTSNAME
gl_PREREQ_PTSNAME

# Account for global Bazel defaults
HAVE_PTSNAME=1
AC_SUBST([HAVE_PTSNAME])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
