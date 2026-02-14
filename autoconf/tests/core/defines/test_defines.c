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
// Note: We can't easily test empty values in C, but we can verify non-empty
// values The empty value test is verified by the diff_test comparing with
// golden file
#ifdef UNQUOTED_VALUE
// UNQUOTED_VALUE should be defined (but we can't easily test its value as a
// string in C)
#endif

    return 0;
}
