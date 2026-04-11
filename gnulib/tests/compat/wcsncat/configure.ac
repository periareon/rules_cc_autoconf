AC_INIT([test_wcsncat], [1.0])
AC_CONFIG_HEADERS([config.h])

# Pre-seed gl_cv_func_wcsncat_works on platforms where the AC_RUN_IFELSE
# test hangs.  The test calls wcsncat(y, NULL, 0) which is undefined
# behavior; on macOS (arm64 libc) the conftest binary loops forever.
# Use the same cross-compile heuristic that wcsncat.m4 already provides.
AC_CANONICAL_HOST
case "$host_os" in
  darwin* | netbsd* | openbsd* | solaris* | cygwin*)
    gl_cv_func_wcsncat_works="guessing no" ;;
esac

# Invoke macros from wcsncat.m4
gl_FUNC_WCSNCAT

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
