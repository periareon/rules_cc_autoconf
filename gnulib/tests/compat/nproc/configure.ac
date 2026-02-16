AC_INIT([test_nproc], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from nproc.m4
gl_NPROC
gl_PREREQ_NPROC

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])
HAVE_SETMNTENT=1
AC_SUBST([HAVE_SETMNTENT])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
