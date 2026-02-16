#include "autoconf/tests/core/srcs/config.h"

int good_feature(void) {
#ifdef HAVE_GOOD_SRC
    return 42;
#else
    return -1;
#endif
}
