#include <assert.h>

#include "autoconf/tests/core/compiler/config.h"

int main(void) {
    // Compiler should be available (otherwise we wouldn't be running)
    assert(HAVE_C_COMPILER == 1);

    return 0;
}
