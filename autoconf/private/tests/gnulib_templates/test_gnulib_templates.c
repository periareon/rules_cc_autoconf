#include <assert.h>
#include <stddef.h>
#include <string.h>

#include "autoconf/private/tests/gnulib_templates/config.h"

int main(void) {
    // Verify package info
    assert(strcmp(PACKAGE_NAME, "test_gnulib_templates") == 0);
    assert(strcmp(PACKAGE_VERSION, "2.3.4") == 0);

    // Verify gnulib header defines (from inline template with @VAR@
    // replacements)
    assert(strcmp(GNULIB_PACKAGE_NAME, "test_gnulib_templates") == 0);
    assert(strcmp(GNULIB_PACKAGE_VERSION, "2.3.4") == 0);

    // Verify gnulib feature detection (from inline template with @VAR@
    // replacements)
    assert(GNULIB_HAVE_STDIO == 1);
    assert(GNULIB_HAVE_STDLIB == 1);

    // Verify original checks still work
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);

    return 0;
}
