AC_INIT([test_filenamecat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from filenamecat.m4
gl_FILE_NAME_CONCAT
gl_FILE_NAME_CONCAT_LGPL

# Account for global Bazel defaults
HAVE_MEMPCPY=1
AC_SUBST([HAVE_MEMPCPY])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
