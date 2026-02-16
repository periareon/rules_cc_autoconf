#include <assert.h>
#include <stddef.h>
#include <string.h>

#include "autoconf/tests/core/template/config.h"

int main(void) {
    // Verify template substitution
    assert(strcmp(PACKAGE_NAME, "test_template") == 0);
    assert(strcmp(PACKAGE_VERSION, "1.0.0") == 0);

    // Headers should be detected
    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);

    return 0;
}
