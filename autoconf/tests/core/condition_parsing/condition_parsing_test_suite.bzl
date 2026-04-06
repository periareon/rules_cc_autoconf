"""Tests for extract_condition_vars in condition_utils.bzl."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("//autoconf/private:condition_utils.bzl", "extract_condition_vars")

def _simple_var_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["REPLACE_X"], extract_condition_vars("REPLACE_X"))
    return unittest.end(env)

simple_var_test = unittest.make(_simple_var_test_impl)

def _negation_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["HAVE_X"], extract_condition_vars("!HAVE_X"))
    return unittest.end(env)

negation_test = unittest.make(_negation_test_impl)

def _double_negation_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["HAVE_X"], extract_condition_vars("!!HAVE_X"))
    return unittest.end(env)

double_negation_test = unittest.make(_double_negation_test_impl)

def _equality_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["HAVE_X"], extract_condition_vars("HAVE_X==0"))
    return unittest.end(env)

equality_test = unittest.make(_equality_test_impl)

def _inequality_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["HAVE_X"], extract_condition_vars("HAVE_X!=1"))
    return unittest.end(env)

inequality_test = unittest.make(_inequality_test_impl)

def _single_equals_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["HAVE_X"], extract_condition_vars("HAVE_X=1"))
    return unittest.end(env)

single_equals_test = unittest.make(_single_equals_test_impl)

def _or_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["HAVE_X", "REPLACE_X"],
        extract_condition_vars("HAVE_X==0 || REPLACE_X"),
    )
    return unittest.end(env)

or_test = unittest.make(_or_test_impl)

def _and_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["A", "B"], extract_condition_vars("A && B"))
    return unittest.end(env)

and_test = unittest.make(_and_test_impl)

def _nested_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["HAVE_ISWCNTRL", "REPLACE_ISWCNTRL", "REPLACE_ISWDIGIT"],
        extract_condition_vars("!(HAVE_ISWCNTRL==0 || REPLACE_ISWCNTRL) && REPLACE_ISWDIGIT"),
    )
    return unittest.end(env)

nested_test = unittest.make(_nested_test_impl)

def _numeric_rhs_filtered_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["FOO"], extract_condition_vars("FOO==0"))
    return unittest.end(env)

numeric_rhs_filtered_test = unittest.make(_numeric_rhs_filtered_test_impl)

def _relational_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(env, ["FOO"], extract_condition_vars("FOO<32"))
    asserts.equals(env, ["FOO"], extract_condition_vars("FOO>=10"))
    asserts.equals(env, ["FOO"], extract_condition_vars("FOO<=5"))
    asserts.equals(env, ["FOO"], extract_condition_vars("FOO>0"))
    return unittest.end(env)

relational_test = unittest.make(_relational_test_impl)

def _dedup_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["A"],
        extract_condition_vars("A || A"),
    )
    return unittest.end(env)

dedup_test = unittest.make(_dedup_test_impl)

def _multi_or_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["A", "B", "C"],
        extract_condition_vars("A || B || C"),
    )
    return unittest.end(env)

multi_or_test = unittest.make(_multi_or_test_impl)

def _multi_and_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["A", "B", "C"],
        extract_condition_vars("A && B && C"),
    )
    return unittest.end(env)

multi_and_test = unittest.make(_multi_and_test_impl)

def _gnulib_getmntent_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        ["ac_cv_func_getmntent", "ac_cv_func_getmntinfo"],
        extract_condition_vars("ac_cv_func_getmntent || ac_cv_func_getmntinfo"),
    )
    return unittest.end(env)

gnulib_getmntent_test = unittest.make(_gnulib_getmntent_test_impl)

def condition_parsing_test_suite(*, name, **kwargs):
    """Test suite for extract_condition_vars.

    Args:
        name: Name of the test suite.
        **kwargs: Additional keyword arguments passed to test_suite.
    """
    tests = []

    simple_var_test(name = name + "_simple_var")
    tests.append(":" + name + "_simple_var")

    negation_test(name = name + "_negation")
    tests.append(":" + name + "_negation")

    double_negation_test(name = name + "_double_negation")
    tests.append(":" + name + "_double_negation")

    equality_test(name = name + "_equality")
    tests.append(":" + name + "_equality")

    inequality_test(name = name + "_inequality")
    tests.append(":" + name + "_inequality")

    single_equals_test(name = name + "_single_equals")
    tests.append(":" + name + "_single_equals")

    or_test(name = name + "_or")
    tests.append(":" + name + "_or")

    and_test(name = name + "_and")
    tests.append(":" + name + "_and")

    nested_test(name = name + "_nested")
    tests.append(":" + name + "_nested")

    numeric_rhs_filtered_test(name = name + "_numeric_rhs_filtered")
    tests.append(":" + name + "_numeric_rhs_filtered")

    relational_test(name = name + "_relational")
    tests.append(":" + name + "_relational")

    dedup_test(name = name + "_dedup")
    tests.append(":" + name + "_dedup")

    multi_or_test(name = name + "_multi_or")
    tests.append(":" + name + "_multi_or")

    multi_and_test(name = name + "_multi_and")
    tests.append(":" + name + "_multi_and")

    gnulib_getmntent_test(name = name + "_gnulib_getmntent")
    tests.append(":" + name + "_gnulib_getmntent")

    native.test_suite(
        name = name,
        tests = tests,
        **kwargs
    )
