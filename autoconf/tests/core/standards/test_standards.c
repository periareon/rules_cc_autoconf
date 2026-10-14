#include "autoconf/tests/core/standards/config.h"

#ifndef HAVE_C99_STD
#error "std_copts did not reach the target's own check"
#endif
#ifdef DEP_SEES_C99
#error "the target's conlyopts leaked into its dependency's checks"
#endif

int main(void) { return 0; }
