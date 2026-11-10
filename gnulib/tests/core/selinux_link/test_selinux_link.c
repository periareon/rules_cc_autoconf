/* As gnulib's se-selinux.in.h: the real API or stubs. */
#include "gnulib/tests/core/selinux_link/config.h"

#if USE_SELINUX_SELINUX_H
#include <selinux/selinux.h>
#else
#define is_selinux_enabled() 0
#endif

int main(void) { return is_selinux_enabled() < 0; }
