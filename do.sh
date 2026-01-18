#!/usr/bin/env bash

# bazel test -- //gnulib/tests/compatibility/cbrt/... \
#     //gnulib/tests/compatibility/chown/... \
#     //gnulib/tests/compatibility/ctype_h/... \
#     //gnulib/tests/compatibility/d-ino/... \
#     //gnulib/tests/compatibility/d-type/... \
#     //gnulib/tests/compatibility/double-slash-root/... \
#     //gnulib/tests/compatibility/errno_h/... \
#     //gnulib/tests/compatibility/exp2/... \
#     //gnulib/tests/compatibility/expm1/... \
#     //gnulib/tests/compatibility/fabsf/... \
#     //gnulib/tests/compatibility/fabsl/... \
#     //gnulib/tests/compatibility/fchdir/... \
#     //gnulib/tests/compatibility/fflush/... \
#     //gnulib/tests/compatibility/floor/... \
#     //gnulib/tests/compatibility/fmod/... \
#     //gnulib/tests/compatibility/fnmatch_h/... \
#     //gnulib/tests/compatibility/frexpf/... \
#     //gnulib/tests/compatibility/getgroups/... \
#     //gnulib/tests/compatibility/glob_h/... \
#     //gnulib/tests/compatibility/hostent/... \
#     //gnulib/tests/compatibility/hypot/... \
#     //gnulib/tests/compatibility/iconv_h/... \
#     //gnulib/tests/compatibility/inet_ntop/... \
#     //gnulib/tests/compatibility/inline/... \
#     //gnulib/tests/compatibility/isnanf/... \
#     //gnulib/tests/compatibility/langinfo_h/... \
#     //gnulib/tests/compatibility/ldexpf/... \
#     //gnulib/tests/compatibility/link/... \
#     //gnulib/tests/compatibility/locale_h/... \
#     //gnulib/tests/compatibility/log1pf/... \
#     //gnulib/tests/compatibility/log1pl/... \
#     //gnulib/tests/compatibility/logp1/... \
#     //gnulib/tests/compatibility/malloc_h/... \
#     //gnulib/tests/compatibility/mbrtowc/... \
#     //gnulib/tests/compatibility/memchr/... \
#     //gnulib/tests/compatibility/mkfifo/... \
#     //gnulib/tests/compatibility/mktime/... \
#     //gnulib/tests/compatibility/msvc-inval/... \
#     //gnulib/tests/compatibility/open-slash/... \
#     //gnulib/tests/compatibility/openat/... \
#     //gnulib/tests/compatibility/printf/... \
#     //gnulib/tests/compatibility/pthread_h/... \
#     //gnulib/tests/compatibility/rename/... \
#     //gnulib/tests/compatibility/servent/... \
#     //gnulib/tests/compatibility/signal_h/... \
#     //gnulib/tests/compatibility/sockets/... \
#     //gnulib/tests/compatibility/spawn_h/... \
#     //gnulib/tests/compatibility/sqrtl/... \
#     //gnulib/tests/compatibility/sys_ioctl_h/... \
#     //gnulib/tests/compatibility/sys_stat_h/... \
#     //gnulib/tests/compatibility/sys_types_h/... \
#     //gnulib/tests/compatibility/systemd/... \
#     //gnulib/tests/compatibility/truncl/... \
#     //gnulib/tests/compatibility/uchar_h/... \
#     //gnulib/tests/compatibility/ungetc/... \
#     //gnulib/tests/compatibility/unlocked-io/... \
#     //gnulib/tests/compatibility/vasnprintf/... \
#     //gnulib/tests/compatibility/wctype_h/...

bazel test -- \
//gnulib/tests/compatibility/af_alg/... \
//gnulib/tests/compatibility/argp/... \
//gnulib/tests/compatibility/canonicalize/... \
//gnulib/tests/compatibility/cbrtf/... \
//gnulib/tests/compatibility/cbrtl/... \
//gnulib/tests/compatibility/closedir/... \
//gnulib/tests/compatibility/dup/... \
//gnulib/tests/compatibility/dup2/... \
//gnulib/tests/compatibility/duplocale/... \
//gnulib/tests/compatibility/euidaccess/... \
//gnulib/tests/compatibility/exp2f/... \
//gnulib/tests/compatibility/exp2l/... \
//gnulib/tests/compatibility/explicit_bzero/... \
//gnulib/tests/compatibility/expm1f/... \
//gnulib/tests/compatibility/fchmodat/... \
//gnulib/tests/compatibility/fcntl/... \
//gnulib/tests/compatibility/ffsl/... \
//gnulib/tests/compatibility/floorl/... \
//gnulib/tests/compatibility/fmodf/... \
//gnulib/tests/compatibility/fmodl/... \
//gnulib/tests/compatibility/fnmatch/... \
//gnulib/tests/compatibility/fprintf-posix/... \
//gnulib/tests/compatibility/fpurge/... \
//gnulib/tests/compatibility/fseeko/... \
//gnulib/tests/compatibility/fstatat/... \
//gnulib/tests/compatibility/ftello/... \
//gnulib/tests/compatibility/futimens/... \
//gnulib/tests/compatibility/getaddrinfo/... \
//gnulib/tests/compatibility/getcwd/... \
//gnulib/tests/compatibility/getdelim/... \
//gnulib/tests/compatibility/gethostname/... \
//gnulib/tests/compatibility/gethrxtime/... \
//gnulib/tests/compatibility/getline/... \
//gnulib/tests/compatibility/getloadavg/... \
//gnulib/tests/compatibility/getopt/... \
//gnulib/tests/compatibility/getpagesize/... \
//gnulib/tests/compatibility/getpass/... \
//gnulib/tests/compatibility/gettimeofday/... \
//gnulib/tests/compatibility/glob/... \
//gnulib/tests/compatibility/grantpt/... \
//gnulib/tests/compatibility/group-member/... \
//gnulib/tests/compatibility/hypotf/... \
//gnulib/tests/compatibility/hypotl/... \
//gnulib/tests/compatibility/iconv/... \
//gnulib/tests/compatibility/inet_pton/... \
//gnulib/tests/compatibility/ioctl/... \
//gnulib/tests/compatibility/isblank/... \
//gnulib/tests/compatibility/isfinite/... \
//gnulib/tests/compatibility/isinf/... \
//gnulib/tests/compatibility/iswblank/... \
//gnulib/tests/compatibility/lchmod/... \
//gnulib/tests/compatibility/lchown/... \
//gnulib/tests/compatibility/limits-h/... \
//gnulib/tests/compatibility/linkat/... \
//gnulib/tests/compatibility/lock/... \
//gnulib/tests/compatibility/logb/... \
//gnulib/tests/compatibility/logbf/... \
//gnulib/tests/compatibility/logp1f/... \
//gnulib/tests/compatibility/logp1l/... \
//gnulib/tests/compatibility/longlong/... \
//gnulib/tests/compatibility/mbrlen/... \
//gnulib/tests/compatibility/mbrtoc16/... \
//gnulib/tests/compatibility/mbsinit/... \
//gnulib/tests/compatibility/mbsrtowcs/... \
//gnulib/tests/compatibility/mbtowc/... \
//gnulib/tests/compatibility/mempcpy/... \
//gnulib/tests/compatibility/memrchr/... \
//gnulib/tests/compatibility/memset_explicit/... \
//gnulib/tests/compatibility/mkfifoat/... \
//gnulib/tests/compatibility/mknod/... \
//gnulib/tests/compatibility/mkostemp/... \
//gnulib/tests/compatibility/mkostemps/... \
//gnulib/tests/compatibility/monetary_h/... \
//gnulib/tests/compatibility/nanosleep/... \
//gnulib/tests/compatibility/net_if_h/... \
//gnulib/tests/compatibility/newlocale/... \
//gnulib/tests/compatibility/nl_langinfo/... \
//gnulib/tests/compatibility/open/... \
//gnulib/tests/compatibility/opendir/... \
//gnulib/tests/compatibility/pipe2/... \
//gnulib/tests/compatibility/poll_h/... \
//gnulib/tests/compatibility/poll/... \
//gnulib/tests/compatibility/posix_spawn/... \
//gnulib/tests/compatibility/pthread_mutex_timedlock/... \
//gnulib/tests/compatibility/raise/... \
//gnulib/tests/compatibility/rawmemchr/... \
//gnulib/tests/compatibility/readline/... \
//gnulib/tests/compatibility/readutmp/... \
//gnulib/tests/compatibility/reallocarray/... \
//gnulib/tests/compatibility/regex/... \
//gnulib/tests/compatibility/relocatable/... \
//gnulib/tests/compatibility/renameat/... \
//gnulib/tests/compatibility/rintf/... \
//gnulib/tests/compatibility/rpmatch/... \
//gnulib/tests/compatibility/setenv/... \
//gnulib/tests/compatibility/sigabbrev_np/... \
//gnulib/tests/compatibility/sigaltstack/... \
//gnulib/tests/compatibility/signalblocking/... \
//gnulib/tests/compatibility/snprintf/... \
//gnulib/tests/compatibility/stack-trace/... \
//gnulib/tests/compatibility/stpcpy/... \
//gnulib/tests/compatibility/strcasestr/... \
//gnulib/tests/compatibility/strerror_r/... \
//gnulib/tests/compatibility/strerror/... \
//gnulib/tests/compatibility/strnlen/... \
//gnulib/tests/compatibility/strptime/... \
//gnulib/tests/compatibility/strtod/... \
//gnulib/tests/compatibility/strtok_r/... \
//gnulib/tests/compatibility/strtoumax/... \
//gnulib/tests/compatibility/sys_select_h/... \
//gnulib/tests/compatibility/sys_un_h/... \
//gnulib/tests/compatibility/termios_h/... \
//gnulib/tests/compatibility/thread/... \
//gnulib/tests/compatibility/time_r/... \
//gnulib/tests/compatibility/timegm/... \
//gnulib/tests/compatibility/timespec_get/... \
//gnulib/tests/compatibility/tmpfile/... \
//gnulib/tests/compatibility/truncate/... \
//gnulib/tests/compatibility/truncf/... \
//gnulib/tests/compatibility/ttyname_r/... \
//gnulib/tests/compatibility/tzset/... \
//gnulib/tests/compatibility/usleep/... \
//gnulib/tests/compatibility/vdprintf/... \
//gnulib/tests/compatibility/vfprintf-posix/... \
//gnulib/tests/compatibility/vsnprintf/... \
//gnulib/tests/compatibility/wait-process/... \
//gnulib/tests/compatibility/wctob/... \
//gnulib/tests/compatibility/wctype/... \
//gnulib/tests/compatibility/wcwidth/... \
//gnulib/tests/compatibility/wmemchr/... \
//gnulib/tests/compatibility/wmempcpy/...
