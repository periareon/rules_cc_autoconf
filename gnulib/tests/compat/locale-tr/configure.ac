AC_INIT([test_locale-tr], [1.0])
AC_CONFIG_HEADERS([config.h])

# Invoke macros from locale-tr.m4
gt_LOCALE_TR_UTF8

# For more consistent test results, force the output to known values
AC_CANONICAL_HOST
case "$host_os" in
  linux*)
    LOCALE_TR_UTF8="tr_TR.UTF-8"
    AC_SUBST([LOCALE_TR_UTF8])
    ;;
esac

AC_CONFIG_FILES([subst.h:subst.h.in])
AC_OUTPUT
