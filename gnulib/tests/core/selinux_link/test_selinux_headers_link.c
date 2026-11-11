/* As gnulib's consumers of selinux/context.h and selinux/label.h: the system
 * headers, or the replacements the port asks to generate. */
#include <selinux/context.h>
#include <selinux/label.h>
#include <stddef.h>

int main(void) {
    struct selabel_handle* h = selabel_open(SELABEL_CTX_FILE, NULL, 0);
    if (h) selabel_close(h);
    context_t c = context_new("u:r:t:s0");
    if (c) context_free(c);
    return 0;
}
