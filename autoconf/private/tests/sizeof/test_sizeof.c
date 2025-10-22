#include <assert.h>
#include <stddef.h>

#include "autoconf/private/tests/sizeof/config.h"

int main(void) {
    // Verify sizeof values match reality
    assert(SIZEOF_CHAR == sizeof(char));
    assert(SIZEOF_SHORT == sizeof(short));
    assert(SIZEOF_INT == sizeof(int));
    assert(SIZEOF_LONG == sizeof(long));
    assert(SIZEOF_VOIDP == sizeof(void*));

    // Basic sanity checks
    assert(SIZEOF_CHAR == 1);
    assert(SIZEOF_INT >= 2);
    assert(SIZEOF_LONG >= 4);

    return 0;
}
