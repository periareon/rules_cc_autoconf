AC_INIT([test_conditions], [1.0.0])
AC_CONFIG_HEADERS([config.h])

# =================================================================
# Base condition checks
# =================================================================

# Compile check that succeeds
AC_MSG_CHECKING([for CONDITION_SUCCEEDS])
AC_COMPILE_IFELSE(
    [AC_LANG_SOURCE([[
int main(void) { return 0; }
]])],
    [condition_succeeds=yes
     AC_DEFINE([CONDITION_SUCCEEDS], [1], [Compile check that succeeds])],
    [condition_succeeds=no])
AC_MSG_RESULT([$condition_succeeds])

# Compile check that fails
AC_MSG_CHECKING([for CONDITION_FAILS])
AC_COMPILE_IFELSE(
    [AC_LANG_SOURCE([[
#error "This compile check intentionally fails"
int main(void) { return 0; }
]])],
    [condition_fails=yes
     AC_DEFINE([CONDITION_FAILS], [1], [Compile check that fails])],
    [condition_fails=no])
AC_MSG_RESULT([$condition_fails])

# Header checks
AC_CHECK_HEADERS([stdio.h nonexistent_condition_test.h])

# =================================================================
# AC_SUBST with condition tests
# =================================================================

# Test 1: condition succeeds -> if_true value
if test "$condition_succeeds" = yes; then
    SUBST_COND_SUCCESS=yes_it_worked
else
    SUBST_COND_SUCCESS=no_it_failed
fi
AC_DEFINE_UNQUOTED([SUBST_COND_SUCCESS], [$SUBST_COND_SUCCESS],
    [Test 1: condition succeeds -> if_true value])

# Test 2: condition fails -> if_false value
if test "$condition_fails" = yes; then
    SUBST_COND_FAILURE=yes_it_worked
else
    SUBST_COND_FAILURE=no_it_failed
fi
AC_DEFINE_UNQUOTED([SUBST_COND_FAILURE], [$SUBST_COND_FAILURE],
    [Test 2: condition fails -> if_false value])

# Test 3: numeric value when condition succeeds
if test "$condition_succeeds" = yes; then
    REPLACE_WHEN_SUCCESS=0
else
    REPLACE_WHEN_SUCCESS=1
fi
AC_DEFINE_UNQUOTED([REPLACE_WHEN_SUCCESS], [$REPLACE_WHEN_SUCCESS],
    [Test 3: numeric value when condition succeeds])

# Test 4: numeric value when condition fails
if test "$condition_fails" = yes; then
    REPLACE_WHEN_FAILURE=0
else
    REPLACE_WHEN_FAILURE=1
fi
AC_DEFINE_UNQUOTED([REPLACE_WHEN_FAILURE], [$REPLACE_WHEN_FAILURE],
    [Test 4: numeric value when condition fails])

# Test 5: header exists as condition
if test "$ac_cv_header_stdio_h" = yes; then
    SUBST_HEADER_EXISTS=found
else
    SUBST_HEADER_EXISTS=missing
fi
AC_DEFINE_UNQUOTED([SUBST_HEADER_EXISTS], [$SUBST_HEADER_EXISTS],
    [Test 5: header exists as condition])

# Test 6: header missing as condition
if test "$ac_cv_header_nonexistent_condition_test_h" = yes; then
    SUBST_HEADER_MISSING=found
else
    SUBST_HEADER_MISSING=missing
fi
AC_DEFINE_UNQUOTED([SUBST_HEADER_MISSING], [$SUBST_HEADER_MISSING],
    [Test 6: header missing as condition])

# =================================================================
# AC_DEFINE with condition tests
# =================================================================

# Test 7: condition succeeds -> if_true value
if test "$condition_succeeds" = yes; then
    AC_DEFINE([DEFINE_COND_SUCCESS], [SUCCESS_VALUE],
        [Test 7: condition succeeds -> if_true value])
else
    AC_DEFINE([DEFINE_COND_SUCCESS], [FAILURE_VALUE],
        [Test 7: condition succeeds -> if_true value])
fi

# Test 8: condition fails -> if_false value
if test "$condition_fails" = yes; then
    AC_DEFINE([DEFINE_COND_FAILURE], [SUCCESS_VALUE],
        [Test 8: condition fails -> if_false value])
else
    AC_DEFINE([DEFINE_COND_FAILURE], [FAILURE_VALUE],
        [Test 8: condition fails -> if_false value])
fi

# Test 9: condition succeeds, no if_false -> defined
if test "$condition_succeeds" = yes; then
    AC_DEFINE([DEFINE_ONLY_IF_TRUE], [1],
        [Test 9: condition succeeds, no if_false -> defined])
fi

# Test 10: condition fails, no if_false -> not defined
if test "$condition_fails" = yes; then
    AC_DEFINE([DEFINE_SKIP_IF_FALSE], [1],
        [Test 10: condition fails, no if_false -> not defined])
fi

# Test 11: condition fails, if_false = 0 -> defined as 0
if test "$condition_fails" = yes; then
    AC_DEFINE([DEFINE_ZERO_IF_FALSE], [1],
        [Test 11: condition fails, if_false = 0 -> defined as 0])
else
    AC_DEFINE([DEFINE_ZERO_IF_FALSE], [0],
        [Test 11: condition fails, if_false = 0 -> defined as 0])
fi
AC_SUBST([DEFINE_ZERO_IF_FALSE])

AC_OUTPUT

