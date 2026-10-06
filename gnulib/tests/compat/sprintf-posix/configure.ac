AC_INIT([test_sprintf-posix], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from sprintf-posix.m4
gl_FUNC_SPRINTF_POSIX
gl_FUNC_SPRINTF_IS_POSIX
gl_PREREQ_SPRINTF

# Account for global Bazel defaults
HAVE_NL_LANGINFO=1
AC_SUBST([HAVE_NL_LANGINFO])
HAVE_WCRTOMB=1
AC_SUBST([HAVE_WCRTOMB])
HAVE_WCSLEN=1
AC_SUBST([HAVE_WCSLEN])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
