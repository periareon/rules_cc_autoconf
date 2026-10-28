/* The make variables a cc target reads through `toolchains` carry the values
   the substitution and the define rendered into the headers. */
#include <stdio.h>
#include <string.h>

#include "autoconf/tests/core/build_settings/config.h"
#include "autoconf/tests/core/build_settings/subst.h"

int main(void) {
    if (strcmp(PLUGIN_DIR, SUBST_PLUGIN_DIR) != 0) {
        printf("PLUGIN_DIR: define %s, substitution %s\n", PLUGIN_DIR,
               SUBST_PLUGIN_DIR);
        return 1;
    }
    if (strcmp(PLUGIN_DIRECTORY, SUBST_PLUGIN_DIR) != 0) {
        printf("PLUGIN_DIRECTORY: define %s, substitution %s\n",
               PLUGIN_DIRECTORY, SUBST_PLUGIN_DIR);
        return 1;
    }
    if (strcmp(MAKE_PACKAGE_NAME, PACKAGE_NAME) != 0) {
        printf("PACKAGE_NAME: make variable %s, header %s\n", MAKE_PACKAGE_NAME,
               PACKAGE_NAME);
        return 1;
    }
    return 0;
}
