#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/undefs_trailing_ws/config.h"

int main(void) {
    assert(PACKAGE_NAME != NULL);
    assert(PACKAGE_VERSION != NULL);

    assert(HAVE_STDIO_H == 1);
    assert(HAVE_STDLIB_H == 1);

#ifdef HAVE_NONEXISTENT_HEADER_ABC_H
    assert(0 && "nonexistent_header_abc.h should not be found");
#endif

#ifdef HAVE_NONEXISTENT_HEADER_DEF_H
    assert(0 && "nonexistent_header_def.h should not be found");
#endif

    return 0;
}
