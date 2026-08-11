#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/defines/config.h"

int main(void) {
    // Verify custom defines
    assert(CUSTOM_VALUE == 42);
    assert(ENABLE_FEATURE == 1);
    assert(PROJECT_YEAR == 2025);

    // Verify defines with matching prefixes are correctly replaced
    // This tests that HAVE_FOO doesn't break HAVE_FOO_BAR
    assert(HAVE_FOO == 1);

    // Verify AC_DEFINE_UNQUOTED values
    assert(UNQUOTED_HEX == 0x1000);
    assert(UNQUOTED_NUM == 42);

    // Verify function-like macro expansion. If MY_MULT is left as a bare
    // identifier (bug), the `#define` is dropped and this call to an
    // undefined identifier fails to compile.
    assert(MY_MULT(3, 4) == 12);

    return 0;
}
