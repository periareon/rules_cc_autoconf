#include <assert.h>
#include <stddef.h>
#include <stdlib.h>

#include "autoconf/tests/core/functions/config.h"

int main(void) {
    // These functions should exist and be linkable
    assert(HAVE_MALLOC == 1);
    assert(HAVE_FREE == 1);
    assert(HAVE_PRINTF == 1);
    assert(HAVE_STRLEN == 1);

    // Verify we can actually use them (this tests linkage)
    void* ptr = malloc(10);
    assert(ptr != NULL);
    free(ptr);

    return 0;
}
