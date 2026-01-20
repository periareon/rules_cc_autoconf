AC_INIT([test_defines], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Custom defines
AC_DEFINE([CUSTOM_VALUE], [42], [Custom value])
AC_DEFINE([ENABLE_FEATURE], [1], [Enable feature])
AC_DEFINE([PROJECT_YEAR], [2025], [Project year])
AC_DEFINE([HAVE_FOO], [1], [Have foo])
AC_DEFINE([HAVE_FOO_BAR], [1], [Have foo bar])
AC_DEFINE([EMPTY_VALUE], [], [Have empty value])
AC_DEFINE_UNQUOTED([UNQUOTED_VALUE], [test], [Unquoted value])
variable=""
AC_DEFINE_UNQUOTED([UNQUOTED_EMPTY], [$variable], [Unquoted empty value])

AC_OUTPUT
