#include <assert.h>
#include <stddef.h>
#include <sys/types.h>

#include "autoconf/tests/core/sizeof/config.h"

int main(void) {
    // Verify sizeof values match reality
    assert(SIZEOF_CHAR == sizeof(char));
    assert(SIZEOF_SHORT == sizeof(short));
    assert(SIZEOF_INT == sizeof(int));
    assert(SIZEOF_LONG == sizeof(long));
    assert(SIZEOF_VOIDP == sizeof(void*));
    assert(SIZEOF_OFF_T == sizeof(off_t));
    assert(SIZEOF_LONG_COMPUTED == sizeof(long));

    // Basic sanity checks
    assert(SIZEOF_CHAR == 1);
    assert(SIZEOF_INT >= 2);
    assert(SIZEOF_LONG >= 4);

    return 0;
}
