#include <stdio.h>
#include <stdlib.h>

#include "autoconf/tests/core/requires/config.h"

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

// NEW: Test negated require with ! syntax
// NEGATED_REQUIRE_TEST should be 1 because !HAVE_NONEXISTENT_HEADER_XYZ_H
// succeeds
#if NEGATED_REQUIRE_TEST != 1
#error \
    "NEGATED_REQUIRE_TEST should be 1 (requires !HAVE_NONEXISTENT_HEADER_XYZ_H)"
#endif

// NEGATED_REQUIRE_FAIL should NOT be defined because !HAVE_STDIO_H fails
// (stdio.h exists)
#ifdef NEGATED_REQUIRE_FAIL
#error \
    "NEGATED_REQUIRE_FAIL should NOT be defined (requires !HAVE_STDIO_H but stdio.h exists)"
#endif

// NEW: Test == syntax
// DOUBLE_EQUALS_TEST should be 1 because REPLACE_FEATURE==1 matches
#if DOUBLE_EQUALS_TEST != 1
#error "DOUBLE_EQUALS_TEST should be 1 (requires REPLACE_FEATURE==1)"
#endif

// NEW: Test != syntax
// NOT_EQUALS_TEST should be 1 because REPLACE_OTHER!=1 succeeds (it's 0)
#if NOT_EQUALS_TEST != 1
#error "NOT_EQUALS_TEST should be 1 (requires REPLACE_OTHER!=1)"
#endif

// NOT_EQUALS_FAIL should NOT be defined because REPLACE_FEATURE!=1 fails (it's
// 1)
#ifdef NOT_EQUALS_FAIL
#error \
    "NOT_EQUALS_FAIL should NOT be defined (requires REPLACE_FEATURE!=1 but it's 1)"
#endif

// NEW: Test if_true/if_false
// STRING_H_IF_TRUE should be 1 because string.h exists (if_true runs)
#if STRING_H_IF_TRUE != 1
#error "STRING_H_IF_TRUE should be 1 (from if_true when string.h exists)"
#endif

// STRING_H_IF_FALSE should be defined with value 1 (if_false is used when
// HAVE_STRING_H is true, since condition selects which value to use)
#ifndef STRING_H_IF_FALSE
#error \
    "STRING_H_IF_FALSE should be defined (if_false value when condition is true)"
#endif
#if STRING_H_IF_FALSE != 1
#error "STRING_H_IF_FALSE should be 1"
#endif

// NONEXISTENT2_IF_TRUE should NOT be defined (condition is false, so if_false
// is used, but if_false is None/not provided)
#ifdef NONEXISTENT2_IF_TRUE
#error \
    "NONEXISTENT2_IF_TRUE should NOT be defined (if_false not provided when condition is false)"
#endif

// NONEXISTENT2_IF_FALSE should be 1 because header doesn't exist (if_false
// runs)
#if NONEXISTENT2_IF_FALSE != 1
#error "NONEXISTENT2_IF_FALSE should be 1 (from if_false when header missing)"
#endif

    printf("All requires tests passed!\n");
    return 0;
}
