#include "autoconf/private/tests/srcs/config.h"

int good_feature(void);
int use_incompatible_feature(void);

int main(void) {
    /* We only expect the good feature to be present. The incompatible source
     * should be completely stubbed out by autoconf_srcs because HAVE_BAD_SRC
     * is not set.
     */

    int value = good_feature();

    if (value != 42) {
        return 1;
    }

    /* Do not call use_incompatible_feature() here – it should not be
     * compiled in when HAVE_BAD_SRC is unset.
     */

    return 0;
}
