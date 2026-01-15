#include <assert.h>
#include <stddef.h>
#include <stdint.h>

#include "autoconf/tests/core/types/config.h"

int main(void) {
    // size_t with default headers should be found (matches GNU Autoconf
    // behavior)
#ifndef HAVE_SIZE_T
    assert(0 && "size_t should be found with default headers");
#endif

    // These types with headers should exist
    assert(HAVE_INT8_T == 1);
    assert(HAVE_INT64_T == 1);

    // Verify we can actually use them
    size_t sz = 0;
    int8_t i8 = 0;
    int64_t i64 = 0;
    (void)sz;
    (void)i8;
    (void)i64;

    // This type should not exist
#ifdef HAVE_NONEXISTENT_TYPE_XYZ
    assert(0 && "nonexistent type should not be found");
#endif

    return 0;
}
