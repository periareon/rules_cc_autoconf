#include <stdio.h>
#include <stdlib.h>

#include "autoconf/private/tests/requires/config.h"

int main(void) {
// Test that required checks work correctly
// HAVE_PRINTF should be 1 because HAVE_STDIO_H is 1
#if HAVE_PRINTF != 1
#error "HAVE_PRINTF should be 1 (required by HAVE_STDIO_H)"
#endif

// HAVE_MALLOC should be 1 because HAVE_STDLIB_H is 1
#if HAVE_MALLOC != 1
#error "HAVE_MALLOC should be 1 (required by HAVE_STDLIB_H)"
#endif

// HAVE_SOME_FUNC should be 0 because HAVE_NONEXISTENT_HEADER_XYZ_H is 0
#if HAVE_SOME_FUNC != 0
#error \
    "HAVE_SOME_FUNC should be 0 (required by HAVE_NONEXISTENT_HEADER_XYZ_H which is 0)"
#endif

// HAVE_FILE should be 1 because HAVE_STDIO_H is 1
#if HAVE_FILE != 1
#error "HAVE_FILE should be 1 (required by HAVE_STDIO_H)"
#endif

// HAVE_NULL should be 1 because both HAVE_STDIO_H and HAVE_STDLIB_H are 1
#if HAVE_NULL != 1
#error "HAVE_NULL should be 1 (required by both HAVE_STDIO_H and HAVE_STDLIB_H)"
#endif

// Test value-based requirements
// REPLACE_FEATURE should be 1
#if REPLACE_FEATURE != 1
#error "REPLACE_FEATURE should be 1"
#endif

// REPLACE_OTHER should be 0
#if REPLACE_OTHER != 0
#error "REPLACE_OTHER should be 0"
#endif

// HAVE_EXIT_WITH_FEATURE should be 1 because REPLACE_FEATURE=1 matches
#if HAVE_EXIT_WITH_FEATURE != 1
#error "HAVE_EXIT_WITH_FEATURE should be 1 (requires REPLACE_FEATURE=1)"
#endif

// HAVE_EXIT_WITH_OTHER should be 0 because REPLACE_OTHER=0, not 1
#ifdef HAVE_EXIT_WITH_OTHER
#if HAVE_EXIT_WITH_OTHER != 0
#error "HAVE_EXIT_WITH_OTHER should be 0 (requires REPLACE_OTHER=1 but it's 0)"
#endif
#endif

// HAVE_EXIT_WITH_OTHER_ZERO should be 1 because REPLACE_OTHER=0 matches
#if HAVE_EXIT_WITH_OTHER_ZERO != 1
#error "HAVE_EXIT_WITH_OTHER_ZERO should be 1 (requires REPLACE_OTHER=0)"
#endif

    printf("All requires tests passed!\n");
    return 0;
}
