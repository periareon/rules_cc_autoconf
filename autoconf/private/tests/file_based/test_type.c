#include <stddef.h>
#include <stdint.h>

typedef struct {
    int x;
    int y;
} point_t;

int main(void) {
    point_t p = {1, 2};
    return p.x + p.y;
}
