AC_INIT([test_flock], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from flock.m4
gl_FUNC_FLOCK
gl_PREREQ_FLOCK

# Account for global Bazel defaults: the Bazel flock port depends on
# //gnulib/m4/fcntl, which lowers the gl_FCNTL_H_DEFAULTS substitution
# HAVE_FCNTL to 0 where the libc has no fcntl (MSVC), as gl_REPLACE_FCNTL
# does (m4/fcntl.m4 line 148). Mirror just that, without the rest of the
# fcntl.h defaults that this template does not list.
AC_CHECK_FUNCS_ONCE([fcntl])
if test $ac_cv_func_fcntl = no; then
  HAVE_FCNTL=0
else
  HAVE_FCNTL=1
fi
AC_SUBST([HAVE_FCNTL])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
