AC_INIT([test_lock], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from lock.m4
gl_LOCK
gl_PREREQ_LOCK

# Account for global Bazel defaults
HAVE_PTHREAD_MUTEX_RECURSIVE=1
AC_SUBST([HAVE_PTHREAD_MUTEX_RECURSIVE])

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
