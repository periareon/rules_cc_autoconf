/* Stand-in for gnulib's se-context.in.h: the API as failing stubs. */
#ifndef SELINUX_CONTEXT_H
#define SELINUX_CONTEXT_H
#include <stddef.h>
typedef int context_t;
static inline context_t context_new(char const* s) {
    (void)s;
    return 0;
}
static inline void context_free(context_t c) { (void)c; }
#endif
