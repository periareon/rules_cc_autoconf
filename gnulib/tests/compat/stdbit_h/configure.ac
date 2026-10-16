AC_INIT([test_stdbit_h], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from stdbit_h.m4
gl_STDBIT_H

# The GNULIB_STDC_* indicators stdbit.in.h reads: every stdc_* module's
# gl_STDBIT_MODULE_INDICATOR initializes them through this macro, and the
# Bazel module carries them (//gnulib/m4/stdbit_h reads :defaults).
gl_STDBIT_H_REQUIRE_DEFAULTS

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
