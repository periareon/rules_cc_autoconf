AC_INIT([test_regex], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from regex.m4
gl_REGEX
gl_PREREQ_REGEX

# Account for global Bazel defaults
HAVE_ISBLANK=1
AC_SUBST([HAVE_ISBLANK])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
