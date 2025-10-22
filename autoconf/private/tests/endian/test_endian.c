#include <assert.h>
#include <stdint.h>

#include "autoconf/private/tests/endian/config.h"

int main(void) {
    // Verify endianness detection matches runtime check
    uint32_t x = 0x01020304;
    uint8_t* p = (uint8_t*)&x;
    int runtime_big_endian = (p[0] == 0x01);

    assert(WORDS_BIGENDIAN == runtime_big_endian);

    // On most modern systems, this should be little-endian
    // But we don't assert that since it's platform-dependent

    return 0;
}
