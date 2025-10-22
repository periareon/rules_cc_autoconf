#include <assert.h>
#include <stddef.h>

#include "autoconf/private/tests/defines/config.h"

int main(void) {
    // Verify custom defines
    assert(CUSTOM_VALUE == 42);
    assert(ENABLE_FEATURE == 1);
    assert(PROJECT_YEAR == 2025);

    // Verify defines with matching prefixes are correctly replaced
    // This tests that HAVE_FOO doesn't break HAVE_FOO_BAR
    assert(HAVE_FOO == 1);
    assert(HAVE_FOO_BAR == 1);

    return 0;
}
