#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/has_feature_override/config.h"

int main(void) {
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);
    assert(HAVE_STRING_H == 1);

    assert(HAVE_COMPILE_COPTS == 1);
    assert(HAVE_LINK_COPTS == 1);
    assert(HAVE_COPTS_VERIFIED == 1);

    return 0;
}
