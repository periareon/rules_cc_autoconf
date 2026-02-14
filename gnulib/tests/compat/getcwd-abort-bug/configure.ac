AC_INIT([test_getcwd-abort-bug], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from getcwd-abort-bug.m4
gl_FUNC_GETCWD_ABORT_BUG

# Match Bazel's pathmax module which AC_SUBSTs HAVE_SYS_PARAM_H
# (the M4 only does AC_CHECK_HEADERS_ONCE which doesn't subst)
HAVE_SYS_PARAM_H=1
AC_SUBST([HAVE_SYS_PARAM_H])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
