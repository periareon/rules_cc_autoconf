"""Helpers for the inttypes port."""

load("//autoconf:checks.bzl", "checks")

def long_long_condition(name, cond, expr, fallback):
    """gl_INTTYPES_CHECK_LONG_LONG_INT_CONDITION(NAME, COND, EXPR, FALLBACK).

    Compiles upstream's probe and substitutes NAME with 1 or 0.

    Args:
        name (str): the AC_SUBST variable, also used for the cache variable.
        cond (str): preprocessor condition selecting `expr` over `fallback`.
        expr (str): the expression tested when `cond` holds.
        fallback (str): the expression tested otherwise.

    Returns:
        list: two checks (an AC_TRY_COMPILE and an AC_SUBST).
    """
    return [
        checks.AC_TRY_COMPILE(
            name = "gl_cv_test_" + name,
            code = """\
/* Work also in C++ mode.  */
#define __STDC_LIMIT_MACROS 1
/* Work if build is not clean.  */
#define _GL_JUST_INCLUDE_SYSTEM_STDINT_H
#include <limits.h>
#include <stdint.h>
#if {cond}
 #define CONDITION ({expr})
#else
 #define CONDITION ({fallback})
#endif
int test[CONDITION ? 1 : -1];
int main (void) {{ return 0; }}
""".format(cond = cond, expr = expr, fallback = fallback),
            language = "c",
        ),
        checks.AC_SUBST(
            name,
            condition = "gl_cv_test_" + name,
            if_false = 0,
            if_true = 1,
        ),
    ]
