#include <assert.h>
#include <stddef.h>

#include "autoconf/private/tests/alignof/config.h"

int main(void) {
    // Verify alignof values match reality
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
    // C11 has _Alignof
    assert(ALIGNOF_CHAR == _Alignof(char));
    assert(ALIGNOF_INT == _Alignof(int));
    assert(ALIGNOF_DOUBLE == _Alignof(double));
#endif

    // Basic sanity checks
    assert(ALIGNOF_CHAR >= 1);
    assert(ALIGNOF_INT >= 1);
    assert(ALIGNOF_DOUBLE >= 1);
    assert(ALIGNOF_INT <= sizeof(int));
    assert(ALIGNOF_DOUBLE <= sizeof(double));

    return 0;
}
