AC_INIT([test_mountlist], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from mountlist.m4
gl_MOUNTLIST
gl_PREREQ_MOUNTLIST_EXTRA
AC_FUNC_GETMNTENT

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
