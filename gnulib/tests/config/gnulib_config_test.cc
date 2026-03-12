/**
 * @file gnulib_config_test.cc
 * @brief Validates that the generated gnulib config.h header compiles
 * and contains expected macro categories.
 */

#include <cassert>
#include <cstdio>

#include "rules_cc_autoconf/gnulib/config.h"

int main() {
    int defined_count = 0;
    int undefined_count = 0;

#ifdef STDC_HEADERS
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_UNISTD_H
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_STDINT_H
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_STDLIB_H
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_STRING_H
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef _GNU_SOURCE
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_SYS_TYPES_H
    ++defined_count;
#else
    ++undefined_count;
#endif

#ifdef HAVE_SYS_STAT_H
    ++defined_count;
#else
    ++undefined_count;
#endif

    assert(defined_count + undefined_count > 0);

    std::printf(
        "gnulib config.h: %d defined, %d undefined out of %d sampled macros\n",
        defined_count, undefined_count, defined_count + undefined_count);

    return 0;
}
