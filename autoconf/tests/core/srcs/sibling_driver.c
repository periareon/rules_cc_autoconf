#include <stdio.h>

int sibling_value(void);

int main(void) {
    if (sibling_value() != 42) {
        printf("sibling_value() returned the wrong value\n");
        return 1;
    }
    printf("ok\n");
    return 0;
}
