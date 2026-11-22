AC_INIT([test_obstack-printf], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from obstack-printf.m4. gl_FUNC_OBSTACK_PRINTF runs
# gl_REPLACE_OBSTACK_PRINTF only when gnulib's obstack replaces the system's;
# calling it here directly would make REPLACE_OBSTACK_PRINTF unconditional.
gl_FUNC_OBSTACK_PRINTF

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
