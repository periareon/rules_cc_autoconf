AC_INIT([test_subst_templates], [2.3.4])
AC_CONFIG_HEADERS([config.h])

AC_CONFIG_FILES([subst.h:subst.h.in])

# Basic defines
AC_DEFINE([SPECIAL_DEFINE])
# Set shell variable to track that SPECIAL_DEFINE is defined (value 1)
special_define=1

AC_DEFINE([DEFINE_NONE], [])
# For Bazel test compatibility: our BUILD uses subst=True on this AC_DEFINE
# so @DEFINE_NONE@ is replaced in subst.h. Adding AC_SUBST so GNU autoconf
# produces the same output (empty replacement).
AC_SUBST([DEFINE_NONE], [])

# Basic substitutions
SUBST_VAR=1
AC_SUBST([SUBST_VAR])
AC_SUBST([SUBST_TEXT], ["Hello"])
AC_SUBST([SUBST_TEXT_DOUBLE_QUOTE], ["\"Hello\""])
AC_SUBST([SUBST_EMPTY_STRING], [])

# Conditional AC_SUBST: SUBST_CONDITIONAL_TRUE
# Condition: SPECIAL_DEFINE (which is defined, so true)
if test "$special_define" = "1"; then
    SUBST_CONDITIONAL_TRUE=1
else
    SUBST_CONDITIONAL_TRUE=0
fi
AC_SUBST([SUBST_CONDITIONAL_TRUE])

# Conditional AC_SUBST: SUBST_CONDITIONAL_FALSE
# Condition: SPECIAL_DEFINE==0 (which is false since SPECIAL_DEFINE is 1)
if test "$special_define" = "0"; then
    SUBST_CONDITIONAL_FALSE=1
else
    SUBST_CONDITIONAL_FALSE=0
fi
AC_SUBST([SUBST_CONDITIONAL_FALSE])

# Test conditional AC_SUBST based on check result (regression test for bug)
# This checks that when a condition based on a check result is false,
# the subst variable is still created with the if_false value
AC_CHECK_DECL([nonexistent_function_xyz],
    [AC_DEFINE([_test_cv_have_decl_nonexistent], [1], [Have declaration])
     _test_cv_have_decl_nonexistent_val=1],
    [AC_DEFINE([_test_cv_have_decl_nonexistent], [0], [No declaration])
     _test_cv_have_decl_nonexistent_val=0],
    [#include <stdio.h>])

# Set substitution based on check result
# Condition checks if _test_cv_have_decl_nonexistent is true (non-zero)
# Since the check fails, _test_cv_have_decl_nonexistent_val will be 0
if test "$_test_cv_have_decl_nonexistent_val" = "1"; then
    SUBST_CONDITIONAL_CHECK_FALSE=1
else
    SUBST_CONDITIONAL_CHECK_FALSE=0
fi
AC_SUBST([SUBST_CONDITIONAL_CHECK_FALSE])

# Test conditional AC_SUBST with check result - if_true case
# Use a function that exists (printf) so the check succeeds
AC_CHECK_DECL([printf],
    [AC_DEFINE([_test_cv_have_decl_printf], [1], [Have declaration])
     _test_cv_have_decl_printf_val=1],
    [AC_DEFINE([_test_cv_have_decl_printf], [0], [No declaration])
     _test_cv_have_decl_printf_val=0],
    [#include <stdio.h>])

# Set substitution based on check result
# Condition checks if _test_cv_have_decl_printf is true (non-zero)
# Since the check succeeds, _test_cv_have_decl_printf_val will be 1
if test "$_test_cv_have_decl_printf_val" = "1"; then
    SUBST_CONDITIONAL_CHECK_TRUE=1
else
    SUBST_CONDITIONAL_CHECK_TRUE=0
fi
AC_SUBST([SUBST_CONDITIONAL_CHECK_TRUE])

# Simpler test: Use AC_DEFINE to create a condition result, then test both if_true and if_false
AC_DEFINE([TEST_CONDITION_DEFINE])
# Set shell variable to track that TEST_CONDITION_DEFINE is defined (value 1)
test_condition_define=1

# Conditional AC_SUBST: SUBST_CONDITIONAL_DEFINE_TRUE
# Condition: TEST_CONDITION_DEFINE (which is defined, so true)
if test "$test_condition_define" = "1"; then
    SUBST_CONDITIONAL_DEFINE_TRUE=1
else
    SUBST_CONDITIONAL_DEFINE_TRUE=0
fi
AC_SUBST([SUBST_CONDITIONAL_DEFINE_TRUE])

# Conditional AC_SUBST: SUBST_CONDITIONAL_DEFINE_FALSE
# Condition: TEST_CONDITION_DEFINE==0 (which is false since TEST_CONDITION_DEFINE is 1)
if test "$test_condition_define" = "0"; then
    SUBST_CONDITIONAL_DEFINE_FALSE=0
else
    SUBST_CONDITIONAL_DEFINE_FALSE=1
fi
AC_SUBST([SUBST_CONDITIONAL_DEFINE_FALSE])

# =================================================================
# Direct substitutions (via substitutions attribute in Bazel)
# =================================================================
# For Bazel test compatibility: our BUILD uses the substitutions= parameter
# on autoconf_hdr for these. Adding AC_SUBST here so GNU autoconf produces
# the same subst.h output as our implementation (same golden file).
AC_SUBST([DIRECT_SUBST_VAR], [direct_value])
AC_SUBST([DIRECT_SUBST_NUMBER], [42])

AC_CHECK_HEADERS([doesntexist.h])

AC_OUTPUT
