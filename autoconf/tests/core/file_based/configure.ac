AC_INIT([test_file_based], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# Test compilation with file-based check
AC_MSG_CHECKING([if test_compile.c compiles])
AC_COMPILE_IFELSE([AC_LANG_SOURCE([[
#include <stdio.h>
#include <stdlib.h>

int main() {
    printf("Testing file-based compilation\n");
    return 0;
}
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([FILE_COMPILE_WORKS], [1], [Define to 1 if test_compile.c compiles])],
[AC_MSG_RESULT([no])])

# Test type check with file-based content
AC_MSG_CHECKING([for int64_t in test_type.c])
AC_COMPILE_IFELSE([AC_LANG_SOURCE([[
#include <stdint.h>
#include <stddef.h>

typedef struct {
    int x;
    int y;
} point_t;

int main() {
    point_t p = {1, 2};
    int64_t val = p.x + p.y;
    return val;
}
]])],
[AC_MSG_RESULT([yes])
 AC_DEFINE([HAVE_INT64_T], [1], [Define to 1 if int64_t is available])],
[AC_MSG_RESULT([no])])

AC_OUTPUT
