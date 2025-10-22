#include <assert.h>

#include "autoconf/private/tests/custom/config.h"

int main(void) {
    // Basic compilation should always work
    assert(BASIC_COMPILE_WORKS == 1);

    // Atomics are platform-dependent
#ifdef HAVE_STDATOMIC
// If we have atomics, try to use them
#include <stdatomic.h>
    atomic_int x = 0;
    (void)x;
#endif

    return 0;
}
