AC_INIT([test_relocatable-lib], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from relocatable-lib.m4
gl_RELOCATABLE_LIBRARY
gl_RELOCATABLE_LIBRARY_BODY
gl_RELOCATABLE_NOP

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
