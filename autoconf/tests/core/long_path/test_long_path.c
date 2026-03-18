#include <assert.h>
#include <stdlib.h>

#include "autoconf/tests/core/long_path/config.h"

int main(void) {
    assert(HAVE_MALLOC_LONG_PATH_TEST == 1);

    void* ptr = malloc(10);
    assert(ptr != NULL);
    free(ptr);

    return 0;
}
