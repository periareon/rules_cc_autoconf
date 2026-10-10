#include <assert.h>
#include <stddef.h>
#include <string.h>

#include "gnulib/tests/core/check_func_android/config.h"
#include "gnulib/tests/core/check_func_android/subst.h"

int main(void) {
// CHECK_FUNC_ANDROID for puts - should be defined in config.h (function
// exists)
#ifdef HAVE_PUTS
    assert(HAVE_PUTS == 1);
#else
    assert(0 && "HAVE_PUTS should be defined in config.h");
#endif

// CHECK_FUNC_ANDROID for dup3 - should NOT be defined in config.h on macOS
// (function doesn't exist)
#ifndef HAVE_DOESNTEXIST
#else
    assert(0 && "HAVE_DOESNTEXIST should be defined in config.h");
#endif

    return 0;
}
