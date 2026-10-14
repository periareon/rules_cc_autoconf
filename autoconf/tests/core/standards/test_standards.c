#include "autoconf/tests/core/standards/config.h"

#ifndef HAVE_C99_STD
#error "conlyopts did not reach the target's own check"
#endif
#ifdef DEP_SEES_C99
#error "the target's conlyopts leaked into its dependency's checks"
#endif
#if !defined STD_IS_EXACTLY_C99 || !defined STD_AT_LEAST_C89
#error "AC_LANG_STANDARD did not see the target's C99 mode"
#endif
#ifdef STD_AT_LEAST_C23
#error "AC_LANG_STANDARD took C99 for C23"
#endif
#ifndef HAVE_CXX11
#error "AX_CXX_COMPILE_STDCXX did not see a C++11 (or later) compiler"
#endif

int main(void) { return 0; }
