AC_INIT([test_pty], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from pty.m4
gl_PTY_LIB
gl_FUNC_FORKPTY
gl_FUNC_OPENPTY

# Account for global Bazel defaults
HAVE_TERMIOS_H=1
AC_SUBST([HAVE_TERMIOS_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
