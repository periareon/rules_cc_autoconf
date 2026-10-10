"""constant_results_test_suite

Literal `AC_DEFINE` / `AC_SUBST` / `M4_VARIABLE` checks with no `condition`
or `requires` are written at analysis time instead of running the checker.
These tests pin three properties of that fast path:

1. No `CcAutoconfCheck` action is registered for such checks, while an
   otherwise identical check that carries `requires` still runs the checker.
2. The `autoconf_checks` output group still carries a `<name>.check.json`
   spec for every check resolved by the target, whichever path it took.
3. The written result file matches the one the checker produces for the same
   value, so nothing downstream can tell the paths apart.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:checks.bzl", "checks")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load("//autoconf/tests:diff_test.bzl", "diff_test")

# (suffix, value) pairs covering every value shape the fast path accepts.
_VALUES = [
    ("INT", 42),
    ("NEGATIVE_INT", -7),
    ("NUMERIC_STR", "42"),
    ("QUOTED_STR", "\"hello world\""),
    ("ESCAPED_STR", "\"a\\\\b\""),
    ("BARE_STR", "GL"),
    ("PUNCT_STR", "-O2 -D_GNU_SOURCE=1 ~/x [y] {z} <w> |`'"),
    ("EMPTY_STR", ""),
    ("NONE", None),
    ("TRUE", True),
    ("FALSE", False),
]

def _result_file_impl(ctx):
    info = ctx.attr.autoconf[CcAutoconfInfo]
    if ctx.attr.cache_var not in info.cache_results:
        fail("`{}` has no result for `{}`. Available: {}".format(
            ctx.attr.autoconf.label,
            ctx.attr.cache_var,
            sorted(info.cache_results.keys()),
        ))
    return [DefaultInfo(files = depset([info.cache_results[ctx.attr.cache_var]]))]

_result_file = rule(
    doc = "Expose a single check result file from an `autoconf` target.",
    implementation = _result_file_impl,
    attrs = {
        "autoconf": attr.label(providers = [CcAutoconfInfo], mandatory = True),
        "cache_var": attr.string(mandatory = True),
    },
)

def _checker_outputs(env):
    outputs = {}
    for action in analysistest.target_actions(env):
        if action.mnemonic == "CcAutoconfCheck":
            for out in action.outputs.to_list():
                outputs[out.path] = True
    return outputs

def _assert_spec_tracked(env, target, name, result):
    """Assert the `autoconf_checks` output group carries `name`'s check spec.

    Results served from the toolchain or a dependency are owned by another
    target and have no spec here, so those are skipped.
    """
    if result.owner != target.label:
        return
    specs = [spec.basename for spec in target[OutputGroupInfo].autoconf_checks.to_list()]
    asserts.true(
        env,
        "{}.check.json".format(name) in specs,
        "`{}` is missing from the `autoconf_checks` output group".format(name),
    )

def _constants_skip_checker_test_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    info = target[CcAutoconfInfo]
    checker_outputs = _checker_outputs(env)
    for name, result in info.cache_results.items():
        asserts.false(
            env,
            result.path in checker_outputs,
            "`{}` is a literal constant but was given a CcAutoconfCheck action".format(name),
        )
        _assert_spec_tracked(env, target, name, result)
    return analysistest.end(env)

_constants_skip_checker_test = analysistest.make(_constants_skip_checker_test_impl)

def _requires_use_checker_test_impl(ctx):
    env = analysistest.begin(ctx)
    target = analysistest.target_under_test(env)
    info = target[CcAutoconfInfo]
    checker_outputs = _checker_outputs(env)
    for name, result in info.cache_results.items():
        _assert_spec_tracked(env, target, name, result)
        if name == "ac_cv_define_ALWAYS":
            # The anchor the other checks `require` is itself a literal.
            continue
        asserts.true(
            env,
            result.path in checker_outputs,
            "`{}` carries `requires` and must run through the checker".format(name),
        )
    return analysistest.end(env)

_requires_use_checker_test = analysistest.make(_requires_use_checker_test_impl)

def constant_results_test_suite(name):
    """Instantiate the constant-result tests.

    Args:
        name: Name of the resulting `test_suite`.
    """
    constant_checks = []
    checker_checks = [checks.AC_DEFINE("ALWAYS", 1)]
    for suffix, value in _VALUES:
        constant_checks.append(checks.AC_DEFINE("CONST_DEFINE_" + suffix, value))
        constant_checks.append(checks.AC_SUBST("CONST_SUBST_" + suffix, value))
        constant_checks.append(checks.M4_VARIABLE("CONST_M4_" + suffix, value))

        # `requires` on an always-true define forces the checker path while
        # leaving the value handling identical to the unconditional case.
        checker_checks.append(checks.AC_DEFINE("CHECKED_DEFINE_" + suffix, value, requires = ["ALWAYS"]))
        checker_checks.append(checks.AC_SUBST("CHECKED_SUBST_" + suffix, value, requires = ["ALWAYS"]))
        checker_checks.append(checks.M4_VARIABLE("CHECKED_M4_" + suffix, value, requires = ["ALWAYS"]))

    autoconf(
        name = name + "_constants",
        checks = constant_checks,
    )

    autoconf(
        name = name + "_checked",
        checks = checker_checks,
    )

    tests = [
        name + "_constants_skip_checker_test",
        name + "_requires_use_checker_test",
    ]

    _constants_skip_checker_test(
        name = name + "_constants_skip_checker_test",
        target_under_test = name + "_constants",
    )

    _requires_use_checker_test(
        name = name + "_requires_use_checker_test",
        target_under_test = name + "_checked",
    )

    for suffix, _ in _VALUES:
        # `M4_VARIABLE` uses the bare name as its cache variable.
        for kind, prefix in (("define", "ac_cv_define_"), ("subst", "ac_cv_subst_"), ("m4", "")):
            tag = "{}_{}".format(kind, suffix.lower())
            _result_file(
                name = "{}_{}_constant_result".format(name, tag),
                autoconf = name + "_constants",
                cache_var = "{}CONST_{}_{}".format(prefix, kind.upper(), suffix),
            )
            _result_file(
                name = "{}_{}_checked_result".format(name, tag),
                autoconf = name + "_checked",
                cache_var = "{}CHECKED_{}_{}".format(prefix, kind.upper(), suffix),
            )
            diff_test(
                name = "{}_{}_identical_test".format(name, tag),
                file1 = "{}_{}_constant_result".format(name, tag),
                file2 = "{}_{}_checked_result".format(name, tag),
            )
            tests.append("{}_{}_identical_test".format(name, tag))

    native.test_suite(
        name = name,
        tests = tests,
    )
