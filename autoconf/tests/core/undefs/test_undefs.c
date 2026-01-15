#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/undefs/config.h"

int main(void) {
    // Verify PACKAGE info
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    // These headers should exist
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);

    // These headers should not exist - the #undef should be commented out
    // so the defines should not be present
#ifdef HAVE_NONEXISTENT_HEADER_ABC_H
    assert(0 && "nonexistent_header_abc.h should not be found");
#endif

#ifdef HAVE_NONEXISTENT_HEADER_DEF_H
    assert(0 && "nonexistent_header_def.h should not be found");
#endif

    return 0;
}
