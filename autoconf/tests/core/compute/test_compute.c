#include <assert.h>

#include "autoconf/tests/core/compute/config.h"

int main(void) {
    // Verify computed values
    assert(TWO == 2);
    assert(ANSWER == 42);
    assert(HUNDRED == 100);

    return 0;
}
