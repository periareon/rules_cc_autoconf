AC_INIT([test_ptsname_r], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from ptsname_r.m4
gl_FUNC_PTSNAME_R
gl_PREREQ_PTSNAME_R

# Account for global Bazel defaults
HAVE_PTSNAME_R=1
AC_SUBST([HAVE_PTSNAME_R])
HAVE_MEMPCPY=1
AC_SUBST([HAVE_MEMPCPY])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
