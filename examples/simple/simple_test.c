#include <assert.h>
#include <stddef.h>
#include <string.h>

// Include the generated config.h
#include "config.h"

// Include standard headers based on autoconf checks
#ifdef HAVE_STDLIB_H
#include <stdlib.h>
#endif

#ifdef HAVE_STDIO_H
#include <stdio.h>
#endif

int main(void) {
    // Verify package name and version from MODULE.bazel
    assert(strcmp(PACKAGE_NAME, "rules_cc_autoconf_example") == 0);
    assert(strcmp(PACKAGE_VERSION, "0.0.0") == 0);

    // Verify that the autoconf checks detected the standard library
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);
    assert(HAVE_STRING_H == 1);

    // Verify that standard functions were detected
    assert(HAVE_MALLOC == 1);
    assert(HAVE_FREE == 1);
    assert(HAVE_PRINTF == 1);

// Test that we can actually use these functions
#ifdef HAVE_STDLIB_H
    void* ptr = malloc(10);
    assert(ptr != NULL);
    free(ptr);
#endif

#ifdef HAVE_STDIO_H
    printf("Package: %s version %s\n", PACKAGE_NAME, PACKAGE_VERSION);
    printf("All autoconf checks passed!\n");
#endif

#ifdef HAVE_STRING_H
    const char* test = "hello";
    assert(strlen(test) == 5);
#endif

    return 0;
}
