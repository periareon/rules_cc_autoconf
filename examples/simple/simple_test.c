#include <assert.h>
#include <stddef.h>
#include <string.h>

/* Include the generated config.h */
#include "config.h"

/* Include standard headers based on autoconf checks */
#ifdef HAVE_STDLIB_H
#include <stdlib.h>
#endif

#ifdef HAVE_STDIO_H
#include <stdio.h>
#endif

int main(void) {
    /* Verify package name and version from MODULE.bazel */
    assert(strcmp(PACKAGE_NAME, "rules_cc_autoconf_example") == 0);
    assert(strcmp(PACKAGE_VERSION, "0.0.0") == 0);

    /* Use standard headers and functions guarded by autoconf checks */
#ifdef HAVE_STDLIB_H
#ifdef HAVE_MALLOC
    {
        void* ptr = malloc(10);
        assert(ptr != NULL);
#ifdef HAVE_FREE
        free(ptr);
#endif
    }
#endif
#endif

#ifdef HAVE_STDIO_H
    printf("Package: %s version %s\n", PACKAGE_NAME, PACKAGE_VERSION);
    printf("All autoconf checks passed!\n");
#endif

#ifdef HAVE_STRING_H
    {
        const char* test = "hello";
        assert(strlen(test) == 5);
    }
#endif

    return 0;
}
