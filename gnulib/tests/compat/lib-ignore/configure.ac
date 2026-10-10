AC_INIT([test_lib-ignore], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from lib-ignore.m4
gl_IGNORE_UNUSED_LIBRARIES
# _gl_IGNORE_UNUSED_LIBRARIES_OPTIONS is an internal helper that expands to a
# quoted list of linker options; invoking it as a statement yields
# `-Wl,--as-needed: command not found` in configure.

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
