/* The make variable a cc target reads through `toolchains` carries the same
   value the substitution rendered into the header. */
#include <stdio.h>
#include <string.h>

#include "autoconf/tests/core/template_vars/subst.h"

int main(void) {
    if (strcmp(PPPD_RUNTIME_DIR, SUBST_PPPD_RUNTIME_DIR) != 0) {
        printf("PPPD_RUNTIME_DIR: define %s, substitution %s\n",
               PPPD_RUNTIME_DIR, SUBST_PPPD_RUNTIME_DIR);
        return 1;
    }
    if (MAX_LINKS != SUBST_MAX_LINKS) {
        printf("MAX_LINKS: define %d, substitution %d\n", MAX_LINKS,
               SUBST_MAX_LINKS);
        return 1;
    }
    return 0;
}
