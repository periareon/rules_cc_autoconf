/* Regression test for AC_CHECK_FUNC reporting UCRT header-inline functions
   (vfprintf, snprintf, ...) as absent on MSVC; see BUILD.bazel for the setup.

   These are #error rather than assert because the config header is the whole
   result -- the checker's answer is fixed at generation time, so there is
   nothing to defer to runtime. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "autoconf/tests/core/inline_funcs_msvc/config.h"

/* The callable answer: what a port of `AC_CHECK_FUNCS([vfprintf ...])` or
   `AC_FUNC_VPRINTF` gates on. */
#if !defined(HAVE_SNPRINTF) || !defined(HAVE_VFPRINTF) || \
    !defined(HAVE_VPRINTF) || !defined(HAVE_VSNPRINTF)
#error "a header-inline printf function probed as absent on MSVC"
#endif

#if !defined(HAVE_VFPRINTF_O2) || HAVE_VFPRINTF_O2 != 1
#error "vfprintf probed as absent under /O2: the address was optimised away"
#endif

#if !defined(HAVE_VFPRINTF_CXX) || HAVE_VFPRINTF_CXX != 1
#error "vfprintf probed as absent from C++"
#endif

/* The GNU-literal answer, which gnulib's gl_REPLACE_* logic is built on.
   Passing GNU's program alone as `code` must replace the default list. */
#if defined(HAVE_VFPRINTF_GNU)
#error \
    "GNU's program alone reported a header-inline function present; GNU says no"
#endif

#if !defined(HAVE_PUTS_GNU) || HAVE_PUTS_GNU != 1
#error "GNU's program stopped finding an exported function"
#endif

/* The controls must stay absent, otherwise the checks above prove nothing. */
#if defined(HAVE__CPRINTF)
#error \
    "_cprintf probed as present; the default probes are looking beyond AC_INCLUDES_DEFAULT"
#endif
#if !defined(HAVE__CPRINTF_CONIO) || HAVE__CPRINTF_CONIO != 1
#error "a caller-supplied code list did not fall through to a later program"
#endif
#if defined(HAVE_RULES_CC_AUTOCONF_NO_SUCH_FUNCTION)
#error "a nonexistent function probed as present; probes are not discriminating"
#endif

static int log_to(FILE* stream, const char* fmt, ...) {
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vfprintf(stream, fmt, ap);
    va_end(ap);
    return n;
}

static int format_to(char* buf, size_t size, const char* fmt, ...) {
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(buf, size, fmt, ap);
    va_end(ap);
    return n;
}

int main(void) {
    /* Confirm the detected functions are genuinely usable, so the config
       header agrees with what actually compiles and links in a real
       cc_test. */
    char buf[32];
    assert(snprintf(buf, sizeof(buf), "%d-%s", 42, "ok") == 5);
    assert(strcmp(buf, "42-ok") == 0);
    assert(format_to(buf, sizeof(buf), "%s", "inline") == 6);
    assert(strcmp(buf, "inline") == 0);
    assert(log_to(stdout, "vfprintf %s\n", "works") == 15);
    return 0;
}
