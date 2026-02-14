#include <assert.h>
#include <stddef.h>
#include <string.h>

#include "autoconf/tests/core/subst/config.h"
// Note: subst.h contains direct substitutions that are not valid C code,
// so we don't include it in the test
// #include "autoconf/tests/core/subst/subst.h"
#include "autoconf/tests/core/subst/subst_inline.h"

int main(void) {
    // Verify package info
    assert(strcmp(PACKAGE_NAME, "test_subst_templates") == 0);
    assert(strcmp(PACKAGE_VERSION, "2.3.4") == 0);

    // AC_DEFINE check
    assert(SPECIAL_DEFINE == 1);

    // inline check
    assert(FROM_SOURCE == 1);
    assert(FROM_GENERATED == 1);

    return 0;
}
