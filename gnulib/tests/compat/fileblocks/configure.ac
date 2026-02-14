AC_INIT([test_fileblocks], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fileblocks.m4
gl_FILEBLOCKS
gl_PREREQ_FILEBLOCKS

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
