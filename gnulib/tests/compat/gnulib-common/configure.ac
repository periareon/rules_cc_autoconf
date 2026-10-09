AC_INIT([test_gnulib-common], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from gnulib-common.m4
# Only the argument-less entry points; the rest (gl_MODULE_INDICATOR*, gl_W,
# gl_CONDITIONAL*, gl_SILENT, gl_CACHE_VAL_SILENT, gl_CHECK_FUNCS_*, GL_MDA_*)
# take mandatory arguments or are internal helpers.
gl_COMMON
gl_ASSERT_NO_GNULIB_POSIXCHECK
gl_ASSERT_NO_GNULIB_TESTS
gl_FEATURES_H
gl_PROG_CC_C99
gl_PROG_AR_RANLIB
AC_C_RESTRICT
gl_BIGENDIAN
gl_CC_ALLOW_WARNINGS
gl_CXX_ALLOW_WARNINGS
gl_CC_GNULIB_WARNINGS
gl_PREPARE_CHECK_FUNCS_MACOS

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
