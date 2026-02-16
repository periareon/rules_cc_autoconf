AC_INIT([test_getdomainname], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getdomainname.m4
gl_FUNC_GETDOMAINNAME
gl_PREREQ_GETDOMAINNAME

# Account for global Bazel defaults
HAVE_GETDOMAINNAME=1
AC_SUBST([HAVE_GETDOMAINNAME])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
