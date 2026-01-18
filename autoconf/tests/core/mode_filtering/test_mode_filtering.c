#include <stdio.h>
#include "config.h"

int main(void) {
    // Test that defines work
    if (!HAVE_STDIO_H) {
        return 1;
    }
    if (!HAVE_FEATURE) {
        return 1;
    }
    return 0;
}
