#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/headers/config.h"

int main(void) {
    // Verify PACKAGE info
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    // These headers should exist on all platforms
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);
    assert(HAVE_STRING_H == 1);

    // unistd.h is POSIX-only, not available on Windows
#ifndef _WIN32
    assert(HAVE_UNISTD_H == 1);
#else
    // On Windows, unistd.h should not be found
#ifdef HAVE_UNISTD_H
    assert(HAVE_UNISTD_H == 0);
#else
    // HAVE_UNISTD_H is undefined on Windows, which is correct
#endif
#endif

    // This header should not exist
#ifdef HAVE_NONEXISTENT_HEADER_XYZ_H
    assert(0 && "nonexistent header should not be found");
#endif

    return 0;
}
