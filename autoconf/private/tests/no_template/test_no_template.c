#include <stdio.h>

#include "autoconf/private/tests/no_template/config.h"

int main(void) {
    // Test that config.h was generated correctly

#ifndef PACKAGE_NAME
#error "PACKAGE_NAME not defined"
#endif

#ifndef PACKAGE_VERSION
#error "PACKAGE_VERSION not defined"
#endif

#ifndef HAVE_STDIO_H
#error "HAVE_STDIO_H not defined"
#endif

#ifndef HAVE_STDLIB_H
#error "HAVE_STDLIB_H not defined"
#endif

#ifndef HAVE_MALLOC
#error "HAVE_MALLOC not defined"
#endif

#ifndef HAVE_FREE
#error "HAVE_FREE not defined"
#endif

#ifndef CUSTOM_VALUE
#error "CUSTOM_VALUE not defined"
#endif

#ifndef ENABLE_FEATURE
#error "ENABLE_FEATURE not defined"
#endif

// Verify values
#if CUSTOM_VALUE != 42
#error "CUSTOM_VALUE has wrong value"
#endif

#if ENABLE_FEATURE != 1
#error "ENABLE_FEATURE has wrong value"
#endif

    printf("All config checks passed!\n");
    printf("Package: %s %s\n", PACKAGE_NAME, PACKAGE_VERSION);
    printf("Custom value: %d\n", CUSTOM_VALUE);

    return 0;
}
