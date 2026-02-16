AC_INIT([test_copy-file], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from copy-file.m4
gl_COPY_FILE

# Account for global Bazel defaults
HAVE_CHOWN=1
AC_SUBST([HAVE_CHOWN])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
