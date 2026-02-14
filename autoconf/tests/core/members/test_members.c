#include <assert.h>
#include <stddef.h>
#include <time.h>

#include "autoconf/tests/core/members/config.h"

int main(void) {
    // These members should exist on all platforms
    assert(HAVE_STRUCT_TM_TM_SEC == 1);
    assert(HAVE_STRUCT_TM_TM_YEAR == 1);

    // Verify we can access them
    struct tm t;
    (void)t.tm_sec;
    (void)t.tm_year;

    // This member should not exist
#ifdef HAVE_STRUCT_TM_TM_NONEXISTENT
    assert(0 && "nonexistent member should not be found");
#endif

    return 0;
}
