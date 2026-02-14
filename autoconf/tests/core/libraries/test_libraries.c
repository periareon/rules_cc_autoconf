#include <assert.h>
#include <stdio.h>

#include "autoconf/tests/core/libraries/config.h"

#ifdef HAVE_LIBM
#include <math.h>
#endif

#ifdef HAVE_LIBPTHREAD
#include <pthread.h>
#endif

#ifdef HAVE_LIBKERNEL32
#include <windows.h>
#endif

int main(void) {
#ifdef HAVE_LIBM
    // The math library should be available on Unix
    assert(HAVE_LIBM == 1);

    // Verify we can actually use math functions
    double x = cos(0.0);
    assert(x > 0.99 && x < 1.01);  // cos(0) should be approximately 1
#else
    // Math library should not be available on Windows
    // (macro is not defined)
#endif

#ifdef HAVE_LIBPTHREAD
    // The pthread library should be available on Unix
    assert(HAVE_LIBPTHREAD == 1);
#else
    // Pthread library should not be available on Windows
    // (macro is not defined)
#endif

#ifdef HAVE_LIBKERNEL32
    // The Windows kernel library should be available on Windows
    assert(HAVE_LIBKERNEL32 == 1);
#else
    // Windows kernel library should not be available on Unix
    // (macro is not defined)
#endif

    // The nonexistent library should not be available
#ifdef HAVE_LIBNONEXISTENT
    assert(0 && "HAVE_LIBNONEXISTENT should not be defined");
#endif

    printf("Library checks passed!\n");
    return 0;
}
