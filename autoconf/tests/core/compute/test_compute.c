#undef NDEBUG
#include <assert.h>

#include "autoconf/tests/core/compute/config.h"

int main(void) {
    // Verify computed values
    assert(TWO == 2);
    assert(ANSWER == 42);
    assert(HUNDRED == 100);
    assert(SEARCH_BEGIN == -1024);
    assert(SEARCH_END == 1024);

    return 0;
}
