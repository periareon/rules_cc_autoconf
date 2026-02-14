#include <assert.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

#include "autoconf/tests/core/hdr/config.h"

int main(void) {
    // Verify package info
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    // Verify custom defines
    assert(CUSTOM_VALUE == 42);
    assert(ENABLE_FEATURE == 1);

    // Verify headers (cross-platform friendly)
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);

    // Verify functions (cross-platform friendly)
    assert(HAVE_MALLOC == 1);
    assert(HAVE_PRINTF == 1);

    // Verify we can actually use them (this tests linkage)
    void* ptr = malloc(10);
    assert(ptr != NULL);
    free(ptr);

    // Verify printf works
    printf("Package: %s %s\n", PACKAGE_NAME, PACKAGE_VERSION);
    printf("Custom value: %d\n", CUSTOM_VALUE);
    printf("Feature enabled: %d\n", ENABLE_FEATURE);

    return 0;
}
