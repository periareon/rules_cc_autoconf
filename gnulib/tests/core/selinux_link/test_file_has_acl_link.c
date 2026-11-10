/* As gnulib's file-has-acl.c: the SELinux API under its xattr condition. */
#include "gnulib/tests/core/selinux_link/config.h"

#if HAVE_LINUX_XATTR_H && HAVE_LISTXATTR && USE_SELINUX_SELINUX_H
#include <selinux/selinux.h>
#else
#define lgetfilecon(path, con) (-1)
#endif

int main(void) {
    char* con = 0;
    return lgetfilecon("/", &con) < -1;
}
