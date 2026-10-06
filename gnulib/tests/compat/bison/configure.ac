AC_INIT([test_bison], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from bison.m4
# gl_PROG_BISON(VARIABLE, MIN_VERSION): calling it without arguments makes
# AC_CHECK_PROGS fail on an empty variable name.
gl_PROG_BISON([PARSE_DATETIME_BISON], [2.4])
gl_BISON

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
