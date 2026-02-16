AC_INIT([test_pty_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from pty_h.m4
gl_PTY_H
gl_PTY_CHECK_UTIL_H
gl_PTY_MODULE_INDICATOR

# Account for global Bazel defaults
HAVE_TERMIOS_H=1
AC_SUBST([HAVE_TERMIOS_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
