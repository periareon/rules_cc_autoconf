/* Regression test for AC_CHECK_FUNC misreporting MSVC intrinsics as absent
   when the probe is compiled with /O2; see BUILD.bazel for the setup.

   These are #error rather than assert because the config header is the whole
   result -- the checker's answer is fixed at generation time, so there is
   nothing to defer to runtime. */
#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "autoconf/tests/core/intrinsics_msvc/config.h"

#if !defined(HAVE_MEMCMP_O2) || HAVE_MEMCMP_O2 != 1
#error "memcmp probed as absent under /O2: intrinsic defeated AC_CHECK_FUNC"
#endif
#if !defined(HAVE_MEMCPY_O2) || HAVE_MEMCPY_O2 != 1
#error "memcpy probed as absent under /O2: intrinsic defeated AC_CHECK_FUNC"
#endif
#if !defined(HAVE_FABS_O2) || HAVE_FABS_O2 != 1
#error "fabs probed as absent under /O2: intrinsic defeated AC_CHECK_FUNC"
#endif
#if !defined(HAVE_LABS_O2) || HAVE_LABS_O2 != 1
#error "labs probed as absent under /O2: intrinsic defeated AC_CHECK_FUNC"
#endif

#if !defined(HAVE_MEMCMP) || HAVE_MEMCMP != 1
#error "plain memcmp probe regressed"
#endif

/* The negative control must stay absent, otherwise the checks above prove
   nothing. */
#if defined(HAVE_RULES_CC_AUTOCONF_NO_SUCH_FUNCTION)
#if HAVE_RULES_CC_AUTOCONF_NO_SUCH_FUNCTION
#error "a nonexistent function probed as present; probes are not discriminating"
#endif
#endif

int main(void) {
    /* Confirm the detected symbols are genuinely usable, so the config header
       agrees with what actually compiles and links in a real cc_test. */
    char a[4] = "abc";
    char b[4];
    memcpy(b, a, sizeof(a));
    assert(memcmp(a, b, sizeof(a)) == 0);
    assert(fabs(-1.5) == 1.5);
    assert(labs(-7L) == 7L);
    return 0;
}
