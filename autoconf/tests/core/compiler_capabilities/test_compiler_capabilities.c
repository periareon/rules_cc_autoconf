#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "autoconf/tests/core/compiler_capabilities/config.h"

int main(void) {
    printf("Testing compiler capabilities\n");

    // Test const keyword
#ifdef const
    printf("✓ const keyword supported\n");
#else
    printf("✗ const keyword not supported\n");
#endif

    // Test restrict keyword
#ifdef restrict
    printf("✓ restrict keyword supported\n");
#else
    printf("✗ restrict keyword not supported\n");
#endif

    // Test volatile keyword
#ifdef volatile
    printf("✓ volatile keyword supported\n");
#else
    printf("✗ volatile keyword not supported\n");
#endif

    // Test backslash-a escape sequence
#ifdef HAVE_C_BACKSLASH_A
    printf("✓ \\a escape sequence supported\n");
#else
    printf("✗ \\a escape sequence not supported\n");
#endif

    // Test function prototypes
#ifdef HAVE_PROTOTYPES
    printf("✓ function prototypes supported\n");
#else
    printf("✗ function prototypes not supported\n");
#endif

    // Test inline keyword
#ifdef inline
    printf("✓ inline keyword supported\n");
#else
    printf("✗ inline keyword not supported\n");
#endif

    // Test compiler flags
#ifdef HAVE_FLAG__WALL
    printf("✓ -Wall flag supported\n");
#else
    printf("✗ -Wall flag not supported\n");
#endif

#ifdef HAVE_FLAG__STD_C99
    printf("✓ -std=c99 flag supported\n");
#else
    printf("✗ -std=c99 flag not supported\n");
#endif

#ifdef HAVE_FLAG__STD_C__17
    printf("✓ -std=c++17 flag supported\n");
#else
    printf("✗ -std=c++17 flag not supported\n");
#endif

    // Test -c and -o flag support
#ifdef NO_MINUS_C_MINUS_O
    printf("✗ -c and -o flags not supported together\n");
#else
    printf("✓ -c and -o flags supported together\n");
#endif

    printf("Compiler capability tests completed!\n");
    return 0;
}
