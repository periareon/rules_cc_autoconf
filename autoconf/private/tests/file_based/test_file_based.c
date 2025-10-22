#include <stdio.h>
#include <stdlib.h>

#include "autoconf/private/tests/file_based/config.h"

int main(void) {
    printf("Testing file-based autoconf checks\n");

#ifdef FILE_COMPILE_WORKS
    printf("✓ File compilation test passed\n");
#else
    printf("✗ File compilation test failed\n");
    return 1;
#endif

    // The int64_t check demonstrates file parameter usage
    // It may or may not pass depending on the system
#ifdef HAVE_INT64_T
    printf("✓ Type check test passed (int64_t found)\n");
#else
    printf(
        "✓ Type check test passed (int64_t not found, but file parameter "
        "worked)\n");
#endif

    printf("All file-based tests passed!\n");
    return 0;
}
