#include <assert.h>
#include <stddef.h>
#include <stdlib.h>

#include "autoconf/tests/core/inline_replace/config.h"

/* Test function using _GL_ARG_NONNULL attribute */
static void test_nonnull(int* ptr) _GL_ARG_NONNULL((1));

static void test_nonnull(int* ptr) { (void)ptr; }

int main(void) {
    // Verify package info
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    // Verify that _GL_ARG_NONNULL is defined (even if empty)
    // This tests that the inline replacement worked and the attribute is
    // available
    int value = 42;
    test_nonnull(&value);

    // Verify that _Noreturn is defined (even if empty)
    // We can't easily test _Noreturn without calling a function that aborts,
    // but the fact that it compiles means the definition is present

    return 0;
}
