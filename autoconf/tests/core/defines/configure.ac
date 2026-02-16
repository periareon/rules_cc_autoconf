AC_INIT([test_defines], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Custom defines
AC_DEFINE([CUSTOM_VALUE], [42], [Custom value])
AC_DEFINE([ENABLE_FEATURE], [1], [Enable feature])
AC_DEFINE([ENABLE_FEATURE_STR], [1], [Enable feature])
AC_DEFINE([ENABLE_FEATURE_QUOTED], ["1"], [Enable feature])
AC_DEFINE([PROJECT_YEAR], [2025], [Project year])
AC_DEFINE([HAVE_FOO], [1], [Have foo])
AC_DEFINE([HAVE_FOO_STR], [1], [Have foo])
AC_DEFINE([HAVE_FOO_QUOTED], ["1"], [Have foo])
AC_DEFINE([HAVE_BAR], [true], [Have bar])
AC_DEFINE([HAVE_BAR_STR], [True], [Have bar])
AC_DEFINE([HAVE_BAZ], [false], [Have baz])
AC_DEFINE([HAVE_BAZ_STR], [False], [Have baz])
AC_DEFINE([EMPTY_VALUE], [], [Have empty value])
AC_DEFINE([INDENTED_VALUE_1], [1])
AC_DEFINE([INDENTED_VALUE_2], [1])
AC_DEFINE_UNQUOTED([UNQUOTED_VALUE], [test], [Unquoted value])
variable=""
AC_DEFINE_UNQUOTED([UNQUOTED_EMPTY], [$variable], [Unquoted empty value])

AC_OUTPUT
