/* Test that autoconf_linkopts resolves AC_SEARCH_LIBS on MSVC via
   #pragma comment(lib, ...) directives compiled into the object file.
   htons() requires ws2_32.lib which is NOT linked by default. */
#include <stdio.h>
#include <winsock2.h>

int main(void) {
    unsigned short result = htons(0x1234);
    if (result != 0x3412) {
        fprintf(stderr, "Expected htons(0x1234) == 0x3412, got 0x%04x\n",
                result);
        return 1;
    }
    printf("htons(0x1234) = 0x%04x (OK)\n", result);
    return 0;
}
