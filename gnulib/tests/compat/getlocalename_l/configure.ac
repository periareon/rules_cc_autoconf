AC_INIT([test_getlocalename_l], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getlocalename_l.m4
gl_FUNC_GETLOCALENAME_L_SIMPLE
gl_PREREQ_GETLOCALENAME_L_SIMPLE
gl_FUNC_GETLOCALENAME_L_UNSAFE
gl_PREREQ_GETLOCALENAME_L_UNSAFE

# Account for global Bazel defaults
HAVE_LANGINFO_H=1
AC_SUBST([HAVE_LANGINFO_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
