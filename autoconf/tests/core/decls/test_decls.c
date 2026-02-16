#include <assert.h>
#include <stddef.h>
#include <stdio.h>

#include "autoconf/tests/core/decls/config.h"

int main(void) {
    // These symbols should be declared
    assert(HAVE_DECL_NULL == 1);
    assert(HAVE_DECL_STDOUT == 1);

    // Verify we can use them
    void* p = NULL;
    FILE* f = stdout;
    (void)p;
    (void)f;

    // This symbol should not be declared (value should be 0)
    assert(HAVE_DECL_NONEXISTENT_SYMBOL == 0);

    return 0;
}
