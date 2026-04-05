/* Test that autoconf_linkopts resolves AC_SEARCH_LIBS results correctly. */
#include <math.h>
#include <stdio.h>

int main(void) {
    /* Use cos() which requires -lm on most systems */
    double result = cos(0.0);
    if (result != 1.0) {
        fprintf(stderr, "Expected cos(0.0) == 1.0, got %f\n", result);
        return 1;
    }
    printf("cos(0.0) = %f (OK)\n", result);
    return 0;
}
