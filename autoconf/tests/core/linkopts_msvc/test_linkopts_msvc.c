/* Regression test for per-check `linkopts` being dropped on MSVC; see
   BUILD.bazel for the probe setup.

   These are #error rather than assert because the config header is the whole
   result -- the checker's answer is fixed at generation time, so there is
   nothing to defer to runtime. */
#include <assert.h>

/* windows.h must preceed shellapi.h */
/* clang-format off */
#include <windows.h>
#include <shellapi.h>
/* clang-format on */

#include "autoconf/tests/core/linkopts_msvc/config.h"

#if !defined(HAVE_COMMANDLINETOARGVW_WITH_LINKOPTS) || \
    HAVE_COMMANDLINETOARGVW_WITH_LINKOPTS != 1
#error "linkopts did not reach the linker: AC_TRY_LINK probe resolved to 0"
#endif

/* The control must fail, otherwise the check above proves nothing. */
#if defined(HAVE_COMMANDLINETOARGVW_NO_LINKOPTS) && \
    HAVE_COMMANDLINETOARGVW_NO_LINKOPTS
#error \
    "shell32 linked without linkopts; the positive probe is not discriminating"
#endif

#if !defined(HAVE_LIBSHELL32_COMMANDLINETOARGVW) || \
    HAVE_LIBSHELL32_COMMANDLINETOARGVW != 1
#error "AC_CHECK_LIB regressed: bare positional .lib no longer reaches linker"
#endif

int main(void) {
    /* Confirm the detected symbol is genuinely callable, so the config header
       agrees with what actually links in a real cc_test. */
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(L"probe.exe one two", &argc);
    assert(argv != NULL);
    assert(argc == 3);
    LocalFree(argv);
    return 0;
}
