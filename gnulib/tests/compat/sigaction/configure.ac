AC_INIT([test_sigaction], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from sigaction.m4
gl_SIGACTION
gl_PREREQ_SIGACTION
gl_PREREQ_SIG_HANDLER_H

# Match Bazel's dependencies by checking standard headers
# These come from AC_CHECK_INCLUDES_DEFAULT in Bazel
AC_CHECK_HEADERS([inttypes.h stdint.h stdio.h stdlib.h strings.h string.h sys/stat.h sys/types.h unistd.h])
AC_PROG_EGREP
AC_STDC_HEADERS

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
