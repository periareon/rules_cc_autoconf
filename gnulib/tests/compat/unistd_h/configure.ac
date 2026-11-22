AC_INIT([test_unistd_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# gnulib-tool emits this first for modules that depend on extensions.
gl_USE_SYSTEM_EXTENSIONS

# Invoke macros from unistd_h.m4
gl_UNISTD_H
gl_UNISTD_MODULE_INDICATOR

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
