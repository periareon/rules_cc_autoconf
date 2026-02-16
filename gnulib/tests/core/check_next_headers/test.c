#include <assert.h>
#include <stddef.h>
#include <string.h>

#include "gnulib/tests/core/check_next_headers/subst.h"

int main(void) {
    // Verify NEXT_* variables are set correctly
    // The values are replaced as raw strings in the template
    // They are meant to be used in #include_next directives
    // The template file verifies they are replaced correctly via the #define
    // statements

    // Just verify the file compiles (the actual values are checked in the
    // template)
    return 0;
}
