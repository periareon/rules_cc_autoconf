AC_INIT([test_manywarnings], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from manywarnings.m4
# gl_MANYWARN_COMPLEMENT(OUTVAR, LISTVAR, REMOVEVAR) only manipulates shell
# variables; it produces no defines or substs.
gl_MANYWARN_COMPLEMENT([gl_warn_result], [-Wall -Wextra -Wformat], [-Wformat])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
