AC_INIT([test_login_tty], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from login_tty.m4
gl_FUNC_LOGIN_TTY

# Account for global Bazel defaults
HAVE_LOGIN_TTY=1
AC_SUBST([HAVE_LOGIN_TTY])
HAVE_TERMIOS_H=1
AC_SUBST([HAVE_TERMIOS_H])
HAVE_UTIL_H=1

# Linux and macos resolve differently as `HAVE_UTIL_H` is actually found.
AC_CANONICAL_HOST
case "$host_os" in
  linux*)
    HAVE_UTIL_H=0
    ;;
esac
AC_SUBST([HAVE_UTIL_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
