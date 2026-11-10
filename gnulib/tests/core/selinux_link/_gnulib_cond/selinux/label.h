/* Stand-in for gnulib's se-label.in.h: the API as failing stubs. */
#ifndef SELINUX_LABEL_H
#define SELINUX_LABEL_H
#include <stddef.h>
#define SELABEL_CTX_FILE 0
struct selabel_handle;
static inline struct selabel_handle* selabel_open(unsigned int backend,
                                                  void const* opts,
                                                  unsigned int nopts) {
    (void)backend;
    (void)opts;
    (void)nopts;
    return NULL;
}
static inline void selabel_close(struct selabel_handle* h) { (void)h; }
#endif
