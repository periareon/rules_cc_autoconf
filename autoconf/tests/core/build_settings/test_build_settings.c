#include <stdbool.h>
#include <string.h>

#include "autoconf/tests/core/build_settings/config.h"

int main(void) {
    if (strcmp(PACKAGE_NAME, "myproj") != 0) {
        return 1;
    }
    if (!ENABLE_THREADS) {
        return 2;
    }
    if (LOG_LEVEL != 2) {
        return 3;
    }
    return 0;
}
