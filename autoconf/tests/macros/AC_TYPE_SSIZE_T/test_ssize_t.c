#include <sys/types.h>

#include "autoconf/tests/macros/AC_TYPE_SSIZE_T/config.h"
#include "autoconf/tests/macros/AC_TYPE_SSIZE_T/subst.h"

int main(void) {
    ssize_t n = -1;
    return n < 0 ? 0 : 1;
}
