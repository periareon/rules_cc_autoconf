#include "autoconf/tests/core/werror/config.h"

#ifndef WARNING_PROBE_COMPILES
#error "the warning probe failed without -Werror"
#endif
#ifndef WARNING_PROBE_COMPILES_CXX
#error "the C++ warning probe failed without -Werror"
#endif
#ifdef CHECK_WERROR_COMPILES
#error "a check's own -Werror did not reach its compile"
#endif
#ifndef COMMAND_LINE_WERROR_COMPILES
#error "--copt=-Werror reached a probe, or --copt did not reach it at all"
#endif
#ifdef TARGET_COPTS_WERROR_COMPILES
#error "the target's copts -Werror did not reach its C checks"
#endif
#ifdef TARGET_COPTS_WERROR_COMPILES_CXX
#error "the target's copts -Werror did not reach its C++ checks"
#endif
#ifndef TARGET_COPTS_CHECK_NO_ERROR_COMPILES
#error "a check's own copts did not follow the target's"
#endif
#ifdef TARGET_COPTS_NAMED_WERROR_COMPILES
#error "the target's copts -Werror=cpp did not reach its C checks"
#endif
#ifdef TARGET_CONLYOPTS_WERROR_COMPILES
#error "the target's conlyopts -Werror did not reach its C checks"
#endif
#ifndef TARGET_CONLYOPTS_WERROR_COMPILES_CXX
#error "the target's conlyopts reached its C++ checks"
#endif
#ifndef TARGET_CXXOPTS_WERROR_COMPILES
#error "the target's cxxopts reached its C checks"
#endif
#ifdef TARGET_CXXOPTS_WERROR_COMPILES_CXX
#error "the target's cxxopts -Werror did not reach its C++ checks"
#endif

int main(void) { return 0; }
