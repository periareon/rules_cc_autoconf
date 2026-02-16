#include <assert.h>

#include "autoconf/tests/core/compile_defines/config.h"

int main(void) {
// HAVE_FEATURE_A should be 1 because _ENABLE_FEATURE_A was defined
// and the compilation with compile_defines succeeded
#ifdef HAVE_FEATURE_A
    assert(HAVE_FEATURE_A == 1);
#else
    assert(0 && "HAVE_FEATURE_A should be defined");
#endif

// HAVE_FEATURE_B should be undefined because _ENABLE_FEATURE_B was not defined
// and the compilation with compile_defines failed
#ifdef HAVE_FEATURE_B
    assert(HAVE_FEATURE_B == 0);
#else
    // This is expected - when a check fails, the define is not present
    // (or is #undef, which means it's not defined)
#endif

    return 0;
}
