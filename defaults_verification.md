# Defaults Verification Status

Checking each module's `defaults` target against upstream m4 `*_DEFAULTS` function.

## Completed Checks

- ✅ **arpa_inet_h**: All AC_SUBST values match
- ✅ **ctype_h**: All AC_SUBST values match (added NEXT_* variables)
- ✅ **dirent_h**: All AC_SUBST values match
- ✅ **fcntl_h**: All AC_SUBST values match
- ✅ **fnmatch_h**: All AC_SUBST values match
- ✅ **glob_h**: All AC_SUBST values match
- ✅ **iconv_h**: All AC_SUBST values match
- ✅ **langinfo_h**: All AC_SUBST values match
- ✅ **locale_h**: All AC_SUBST values match (added REPLACE_STRUCT_LCONV=0)
- ✅ **netdb_h**: All AC_SUBST values match
- ✅ **poll_h**: All AC_SUBST values match
- ✅ **pthread_h**: All AC_SUBST values match (added HAVE_PTHREAD_T=1)

## Remaining to Check

- ⏳ **math_h**: Very large, needs detailed verification
- ⏳ **sched_h**
- ⏳ **signal_h**
- ⏳ **spawn_h**
- ⏳ **stdint**
- ⏳ **stdio_h**
- ⏳ **stdlib_h**
- ⏳ **string_h**
- ⏳ **sys_ioctl_h**
- ⏳ **sys_resource_h**
- ⏳ **sys_select_h**
- ⏳ **sys_socket_h**
- ⏳ **sys_stat_h**
- ⏳ **sys_time_h**
- ⏳ **termios_h**
- ⏳ **time_h**
- ⏳ **unistd_h**
- ⏳ **wchar_h**
- ⏳ **wctype_h**
