AC_INIT([test_stdint], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from stdint.m4
gl_STDINT_H

# Account for global Bazel defaults
BITSIZEOF_WCHAR_T="@BITSIZEOF_WCHAR_T@"
AC_SUBST([BITSIZEOF_WCHAR_T])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
