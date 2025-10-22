#include <assert.h>
#include <stddef.h>

#include "autoconf/private/tests/symbols/config.h"

int main(void) {
    // NULL should be defined
    assert(HAVE_NULL == 1);

    // Platform-specific symbols are tested but not asserted
    // since they vary by platform

    return 0;
}
