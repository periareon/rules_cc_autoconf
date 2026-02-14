AC_INIT([test_fsusage], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from fsusage.m4
gl_FSUSAGE
gl_FILE_SYSTEM_USAGE
gl_STATFS_TRUNCATES
gl_PREREQ_FSUSAGE_EXTRA

# Account for global Bazel defaults
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
