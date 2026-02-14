#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/link/config.h"

int main(void) {
    // Test that AC_TRY_LINK correctly detects linkable functions
    assert(HAVE_PRINTF_LINK == 1);

    // Test extern declaration pattern (AC_CHECK_FUNC default pattern)
    assert(HAVE_MALLOC_EXTERN_PATTERN == 1);

#if defined(HAVE_LANGINFO_CODESET) && HAVE_LANGINFO_CODESET
// If nl_langinfo(CODESET) links, verify we can use it
#include <langinfo.h>
    char* cs = nl_langinfo(CODESET);
    (void)cs;
    assert(HAVE_LANGINFO_CODESET == 1);
#endif
    // On systems where nl_langinfo(CODESET) doesn't link (e.g. Windows),
    // the define is either 0 or #undef'd, both are acceptable.

    return 0;
}
