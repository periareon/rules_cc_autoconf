AC_INIT([test_physmem], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from physmem.m4
gl_SYS__SYSTEM_CONFIGURATION
gl_PHYSMEM

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
