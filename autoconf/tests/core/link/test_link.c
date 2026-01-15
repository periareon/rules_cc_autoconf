#include <assert.h>
#include <stddef.h>

#include "autoconf/tests/core/link/config.h"

int main(void) {
    // Test that AC_TRY_LINK correctly detects linkable functions
    assert(HAVE_PRINTF_LINK == 1);

#ifdef HAVE_LANGINFO_CODESET
    // If nl_langinfo(CODESET) links, verify we can use it
    #include <langinfo.h>
    char* cs = nl_langinfo(CODESET);
    (void)cs;
    assert(HAVE_LANGINFO_CODESET == 1);
#else
    // On systems where nl_langinfo(CODESET) doesn't link, it should be 0
    assert(HAVE_LANGINFO_CODESET == 0);
#endif

    return 0;
}
