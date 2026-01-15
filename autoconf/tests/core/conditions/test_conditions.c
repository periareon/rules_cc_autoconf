/**
 * Test file to verify conditional AC_SUBST and AC_DEFINE work correctly.
 *
 * This tests the `condition` parameter which selects between if_true/if_false
 * values based on whether another check succeeded or failed.
 */

#include <stdio.h>
#include <string.h>

#include "autoconf/tests/core/conditions/config.h"

int main(void) {
    int errors = 0;

    /* =================================================================
     * Verify base condition checks
     * ================================================================= */

    /* CONDITION_SUCCEEDS should be defined (compile check passed) */
#ifndef CONDITION_SUCCEEDS
#error "CONDITION_SUCCEEDS should be defined (compile check passed)"
#endif

    /* CONDITION_FAILS should NOT be defined (compile check failed) */
#ifdef CONDITION_FAILS
#error "CONDITION_FAILS should NOT be defined (compile check failed)"
#endif

    /* HAVE_STDIO_H should be defined */
#ifndef HAVE_STDIO_H
#error "HAVE_STDIO_H should be defined"
#endif

    /* HAVE_NONEXISTENT_CONDITION_TEST_H should NOT be defined */
#ifdef HAVE_NONEXISTENT_CONDITION_TEST_H
#error "HAVE_NONEXISTENT_CONDITION_TEST_H should NOT be defined"
#endif

    /* =================================================================
     * Verify AC_SUBST with condition tests
     * ================================================================= */

    /* Test 1: SUBST_COND_SUCCESS - condition succeeded, should use if_true */
#ifndef SUBST_COND_SUCCESS
#error "SUBST_COND_SUCCESS should be defined"
#endif
#define STRINGIFY(x) STRINGIFY2(x)
#define STRINGIFY2(x) #x
    if (strcmp(STRINGIFY(SUBST_COND_SUCCESS), "yes_it_worked") != 0) {
        printf("FAIL: SUBST_COND_SUCCESS = '%s', expected 'yes_it_worked'\n",
               STRINGIFY(SUBST_COND_SUCCESS));
        errors++;
    }

    /* Test 2: SUBST_COND_FAILURE - condition failed, should use if_false */
#ifndef SUBST_COND_FAILURE
#error "SUBST_COND_FAILURE should be defined"
#endif
    if (strcmp(STRINGIFY(SUBST_COND_FAILURE), "no_it_failed") != 0) {
        printf("FAIL: SUBST_COND_FAILURE = '%s', expected 'no_it_failed'\n",
               STRINGIFY(SUBST_COND_FAILURE));
        errors++;
    }

    /* Test 3: REPLACE_WHEN_SUCCESS - condition succeeded, should be 0 */
#ifndef REPLACE_WHEN_SUCCESS
#error "REPLACE_WHEN_SUCCESS should be defined"
#endif
    if (REPLACE_WHEN_SUCCESS != 0) {
        printf("FAIL: REPLACE_WHEN_SUCCESS = %d, expected 0\n",
               REPLACE_WHEN_SUCCESS);
        errors++;
    }

    /* Test 4: REPLACE_WHEN_FAILURE - condition failed, should be 1 */
#ifndef REPLACE_WHEN_FAILURE
#error "REPLACE_WHEN_FAILURE should be defined"
#endif
    if (REPLACE_WHEN_FAILURE != 1) {
        printf("FAIL: REPLACE_WHEN_FAILURE = %d, expected 1\n",
               REPLACE_WHEN_FAILURE);
        errors++;
    }

    /* Test 5: SUBST_HEADER_EXISTS - header exists, should be "found" */
#ifndef SUBST_HEADER_EXISTS
#error "SUBST_HEADER_EXISTS should be defined"
#endif
    if (strcmp(STRINGIFY(SUBST_HEADER_EXISTS), "found") != 0) {
        printf("FAIL: SUBST_HEADER_EXISTS = '%s', expected 'found'\n",
               STRINGIFY(SUBST_HEADER_EXISTS));
        errors++;
    }

    /* Test 6: SUBST_HEADER_MISSING - header missing, should be "missing" */
#ifndef SUBST_HEADER_MISSING
#error "SUBST_HEADER_MISSING should be defined"
#endif
    if (strcmp(STRINGIFY(SUBST_HEADER_MISSING), "missing") != 0) {
        printf("FAIL: SUBST_HEADER_MISSING = '%s', expected 'missing'\n",
               STRINGIFY(SUBST_HEADER_MISSING));
        errors++;
    }

    /* =================================================================
     * Verify AC_DEFINE with condition tests
     * ================================================================= */

    /* Test 7: DEFINE_COND_SUCCESS - condition succeeded, should be
     * SUCCESS_VALUE */
#ifndef DEFINE_COND_SUCCESS
#error "DEFINE_COND_SUCCESS should be defined"
#endif
    if (strcmp(STRINGIFY(DEFINE_COND_SUCCESS), "SUCCESS_VALUE") != 0) {
        printf("FAIL: DEFINE_COND_SUCCESS = '%s', expected 'SUCCESS_VALUE'\n",
               STRINGIFY(DEFINE_COND_SUCCESS));
        errors++;
    }

    /* Test 8: DEFINE_COND_FAILURE - condition failed, should be FAILURE_VALUE
     */
#ifndef DEFINE_COND_FAILURE
#error "DEFINE_COND_FAILURE should be defined"
#endif
    if (strcmp(STRINGIFY(DEFINE_COND_FAILURE), "FAILURE_VALUE") != 0) {
        printf("FAIL: DEFINE_COND_FAILURE = '%s', expected 'FAILURE_VALUE'\n",
               STRINGIFY(DEFINE_COND_FAILURE));
        errors++;
    }

    /* Test 9: DEFINE_ONLY_IF_TRUE - condition succeeded, no if_false, should be
     * 1 */
#ifndef DEFINE_ONLY_IF_TRUE
#error "DEFINE_ONLY_IF_TRUE should be defined (condition succeeded)"
#endif
    if (DEFINE_ONLY_IF_TRUE != 1) {
        printf("FAIL: DEFINE_ONLY_IF_TRUE = %d, expected 1\n",
               DEFINE_ONLY_IF_TRUE);
        errors++;
    }

    /* Test 10: DEFINE_SKIP_IF_FALSE - condition failed, no if_false, should NOT
     * be defined */
#ifdef DEFINE_SKIP_IF_FALSE
#error \
    "DEFINE_SKIP_IF_FALSE should NOT be defined (condition failed, no if_false)"
#endif

    /* =================================================================
     * Verify direct substitutions (via substitutions attribute)
     * ================================================================= */

    /* DIRECT_SUBST_VALUE should be "hello_world" */
#ifndef DIRECT_SUBST_VALUE
#error "DIRECT_SUBST_VALUE should be defined"
#endif
    if (strcmp(STRINGIFY(DIRECT_SUBST_VALUE), "hello_world") != 0) {
        printf("FAIL: DIRECT_SUBST_VALUE = '%s', expected 'hello_world'\n",
               STRINGIFY(DIRECT_SUBST_VALUE));
        errors++;
    }

    /* DIRECT_SUBST_NUMBER should be 42 */
#ifndef DIRECT_SUBST_NUMBER
#error "DIRECT_SUBST_NUMBER should be defined"
#endif
    if (DIRECT_SUBST_NUMBER != 42) {
        printf("FAIL: DIRECT_SUBST_NUMBER = %d, expected 42\n",
               DIRECT_SUBST_NUMBER);
        errors++;
    }

    if (errors == 0) {
        printf("All condition tests passed!\n");
        return 0;
    } else {
        printf("FAILED: %d condition tests failed\n", errors);
        return 1;
    }
}
