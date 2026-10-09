AC_INIT([test_libtextstyle], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from libtextstyle.m4
gl_LIBTEXTSTYLE
# gl_LIBTEXTSTYLE_NEWEST_VERSION expands to a bare version string and is only
# meant as an argument to other macros; invoking it as a statement produces
# `0.20.5: command not found` in configure.
gl_LIBTEXTSTYLE_INITIALIZE
gl_LIBTEXTSTYLE_SEARCH

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
