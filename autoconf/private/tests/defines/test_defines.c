#include <assert.h>
#include <stddef.h>

#include "autoconf/private/tests/defines/config.h"

int main(void) {
    // Verify custom defines
    assert(CUSTOM_VALUE == 42);
    assert(ENABLE_FEATURE == 1);
    assert(PROJECT_YEAR == 2025);

    return 0;
}
