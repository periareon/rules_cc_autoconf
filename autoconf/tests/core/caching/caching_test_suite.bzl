"""caching_test_suite

Test suite for verifying autoconf dep-level check caching.

When an autoconf target lists a check whose cache variable name already exists
in its deps, the check action is skipped and the dep's result file is reused.
"""

load("@bazel_skylib//lib:unittest.bzl", "analysistest", "asserts")
load("@bazel_skylib//rules:write_file.bzl", "write_file")
load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("//autoconf:autoconf_toolchain.bzl", "autoconf_cache", "autoconf_toolchain")
load("//autoconf:checks.bzl", "checks")
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")
load("//autoconf/tests:diff_test.bzl", "diff_test")

def _toolchain_transition_impl(_settings, attr):
    return {
        "//command_line_option:extra_toolchains": [attr.toolchain],
    }

_toolchain_transition = transition(
    implementation = _toolchain_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:extra_toolchains"],
)

def _autoconf_with_toolchain_impl(ctx):
    target = ctx.attr.autoconf_target[0]
    return [target[DefaultInfo], target[CcAutoconfInfo]]

_autoconf_with_toolchain = rule(
    implementation = _autoconf_with_toolchain_impl,
    attrs = {
        "autoconf_target": attr.label(
            cfg = _toolchain_transition,
            mandatory = True,
        ),
        "toolchain": attr.string(mandatory = True),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)

def _autoconf_hdr_with_toolchain_impl(ctx):
    target = ctx.attr.autoconf_hdr[0]
    return [
        DefaultInfo(
            files = depset([target[DefaultInfo].files.to_list()[0]]),
        ),
    ]

_autoconf_hdr_with_toolchain = rule(
    implementation = _autoconf_hdr_with_toolchain_impl,
    attrs = {
        "autoconf_hdr": attr.label(
            cfg = _toolchain_transition,
            mandatory = True,
        ),
        "toolchain": attr.string(mandatory = True),
        "_allowlist_function_transition": attr.label(
            default = "@bazel_tools//tools/allowlists/function_transition_allowlist",
        ),
    },
)

def _content_dedup_same_file_test_impl(ctx):
    env = analysistest.begin(ctx)
    info = analysistest.target_under_test(env)[CcAutoconfInfo]
    asserts.equals(
        env,
        info.define_results["CONTENT_A"].path,
        info.define_results["CONTENT_B"].path,
    )
    asserts.true(
        env,
        len(info.content_cache) == 1,
        "Expected 1 content_cache entry, got %d" % len(info.content_cache),
    )
    return analysistest.end(env)

content_dedup_same_file_test = analysistest.make(_content_dedup_same_file_test_impl)

def _content_diff_val_different_files_test_impl(ctx):
    env = analysistest.begin(ctx)
    info = analysistest.target_under_test(env)[CcAutoconfInfo]
    asserts.true(
        env,
        info.define_results["VAL_LOW"].path != info.define_results["VAL_HIGH"].path,
        "Expected different files for different values",
    )
    asserts.true(
        env,
        len(info.content_cache) == 2,
        "Expected 2 content_cache entries, got %d" % len(info.content_cache),
    )
    return analysistest.end(env)

content_diff_val_different_files_test = analysistest.make(_content_diff_val_different_files_test_impl)

def _content_dep_dedup_reuses_file_test_impl(ctx):
    env = analysistest.begin(ctx)
    info = analysistest.target_under_test(env)[CcAutoconfInfo]
    header_b_path = info.define_results["HEADER_B"].path
    asserts.true(
        env,
        "content_dep_provider" in header_b_path,
        "Expected HEADER_B to reuse dep's file, got: " + header_b_path,
    )
    return analysistest.end(env)

content_dep_dedup_reuses_file_test = analysistest.make(_content_dep_dedup_reuses_file_test_impl)

def _tc_content_dedup_reuses_file_test_impl(ctx):
    env = analysistest.begin(ctx)
    info = analysistest.target_under_test(env)[CcAutoconfInfo]
    y_path = info.define_results["TC_CONTENT_Y"].path
    asserts.true(
        env,
        "tc_content_provider" in y_path,
        "Expected TC_CONTENT_Y to reuse toolchain's file, got: " + y_path,
    )
    return analysistest.end(env)

tc_content_dedup_reuses_file_test = analysistest.make(_tc_content_dedup_reuses_file_test_impl)

def caching_test_suite(*, name, **kwargs):
    """Test suite for autoconf dep-level check caching.

    Args:
        name (str): The name of the test suite.
        **kwargs (dict): Additional keyword arguments.
    """
    tests = []

    # ============================================================================
    # Test 1: dep cache hit (defines mode)
    #
    # base_autoconf defines SHARED_DEFINE.  app_autoconf lists the same check
    # AND adds APP_ONLY.  SHARED_DEFINE should be a cache hit (no action), while
    # APP_ONLY runs locally.  The rendered header must contain both.
    # ============================================================================

    autoconf(
        name = "base_autoconf",
        checks = [
            checks.AC_DEFINE("SHARED_DEFINE", 1),
        ],
        tags = ["manual"],
    )

    autoconf(
        name = "app_autoconf",
        checks = [
            checks.AC_DEFINE("SHARED_DEFINE", 1),
            checks.AC_DEFINE("APP_ONLY", 42),
        ],
        deps = [":base_autoconf"],
    )

    write_file(
        name = "dep_cache_hit_template",
        out = "dep_cache_hit.h.in",
        content = [
            "#undef SHARED_DEFINE",
            "#undef APP_ONLY",
            "",
        ],
    )

    autoconf_hdr(
        name = "dep_cache_hit_hdr",
        out = "dep_cache_hit.h",
        mode = "defines",
        template = ":dep_cache_hit_template",
        deps = [":app_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_dep_cache_hit",
        out = "golden_dep_cache_hit.h.in",
        content = [
            "#define SHARED_DEFINE 1",
            "#define APP_ONLY 42",
            "",
        ],
    )

    diff_test(
        name = "test_dep_cache_hit",
        file1 = ":golden_dep_cache_hit",
        file2 = ":dep_cache_hit_hdr",
    )
    tests.append(":test_dep_cache_hit")

    # ============================================================================
    # Test 2: dep cache hit (subst mode)
    #
    # Same pattern with AC_SUBST.
    # ============================================================================

    autoconf(
        name = "base_subst_autoconf",
        checks = [
            checks.AC_SUBST("SHARED_SUBST", "base_value"),
        ],
        tags = ["manual"],
    )

    autoconf(
        name = "app_subst_autoconf",
        checks = [
            checks.AC_SUBST("SHARED_SUBST", "base_value"),
            checks.AC_SUBST("APP_SUBST", "app_value"),
        ],
        deps = [":base_subst_autoconf"],
    )

    write_file(
        name = "dep_cache_hit_subst_template",
        out = "dep_cache_hit_subst.h.in",
        content = [
            "@SHARED_SUBST@",
            "@APP_SUBST@",
            "",
        ],
    )

    autoconf_hdr(
        name = "dep_cache_hit_subst_hdr",
        out = "dep_cache_hit_subst.h",
        mode = "subst",
        template = ":dep_cache_hit_subst_template",
        deps = [":app_subst_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_dep_cache_hit_subst",
        out = "golden_dep_cache_hit_subst.h.in",
        content = [
            "base_value",
            "app_value",
            "",
        ],
    )

    diff_test(
        name = "test_dep_cache_hit_subst",
        file1 = ":golden_dep_cache_hit_subst",
        file2 = ":dep_cache_hit_subst_hdr",
    )
    tests.append(":test_dep_cache_hit_subst")

    # ============================================================================
    # Test 3: cached check referenced by requires
    #
    # BASE_FLAG is a cache hit from the dep.  DERIVED_FLAG has
    # requires=["BASE_FLAG"] and runs locally.  The dep resolution must find
    # BASE_FLAG in the cached results so the checker can evaluate the requirement.
    # ============================================================================

    autoconf(
        name = "base_requires_autoconf",
        checks = [
            checks.AC_DEFINE("BASE_FLAG", 1),
        ],
        tags = ["manual"],
    )

    autoconf(
        name = "app_requires_autoconf",
        checks = [
            checks.AC_DEFINE("BASE_FLAG", 1),
            checks.AC_DEFINE("DERIVED_FLAG", 1, requires = ["BASE_FLAG"]),
        ],
        deps = [":base_requires_autoconf"],
    )

    write_file(
        name = "dep_requires_template",
        out = "dep_requires.h.in",
        content = [
            "#undef BASE_FLAG",
            "#undef DERIVED_FLAG",
            "",
        ],
    )

    autoconf_hdr(
        name = "dep_requires_hdr",
        out = "dep_requires.h",
        mode = "defines",
        template = ":dep_requires_template",
        deps = [":app_requires_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_dep_requires",
        out = "golden_dep_requires.h.in",
        content = [
            "#define BASE_FLAG 1",
            "#define DERIVED_FLAG 1",
            "",
        ],
    )

    diff_test(
        name = "test_dep_requires",
        file1 = ":golden_dep_requires",
        file2 = ":dep_requires_hdr",
    )
    tests.append(":test_dep_requires")

    # ============================================================================
    # Test 4: idempotent duplicate within the same target
    #
    # The same check listed twice in a single target's checks list.  The second
    # occurrence should be silently skipped (idempotent).
    # ============================================================================

    autoconf(
        name = "dup_within_target_autoconf",
        checks = [
            checks.AC_DEFINE("DUP_VAR", 1),
            checks.AC_DEFINE("DUP_VAR", 1),
            checks.AC_DEFINE("UNIQUE_VAR", 2),
        ],
    )

    write_file(
        name = "dup_within_target_template",
        out = "dup_within_target.h.in",
        content = [
            "#undef DUP_VAR",
            "#undef UNIQUE_VAR",
            "",
        ],
    )

    autoconf_hdr(
        name = "dup_within_target_hdr",
        out = "dup_within_target.h",
        mode = "defines",
        template = ":dup_within_target_template",
        deps = [":dup_within_target_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_dup_within_target",
        out = "golden_dup_within_target.h.in",
        content = [
            "#define DUP_VAR 1",
            "#define UNIQUE_VAR 2",
            "",
        ],
    )

    diff_test(
        name = "test_dup_within_target",
        file1 = ":golden_dup_within_target",
        file2 = ":dup_within_target_hdr",
    )
    tests.append(":test_dup_within_target")

    # ============================================================================
    # Test 5: toolchain cache_deps hit
    #
    # A check is provided by the toolchain's cache_deps.  An autoconf target
    # listing the same check should reuse the toolchain's result (cache hit).
    # ============================================================================

    autoconf_cache(
        name = "tc_cache_provider",
        checks = [
            checks.AC_DEFINE("TC_CACHED_DEFINE", 77),
        ],
        tags = ["manual"],
    )

    autoconf_toolchain(
        name = "tc_cache_toolchain_impl",
        cache_deps = [":tc_cache_provider"],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    native.toolchain(
        name = "tc_cache_toolchain",
        toolchain = ":tc_cache_toolchain_impl",
        toolchain_type = "//autoconf:toolchain_type",
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    _autoconf_with_toolchain(
        name = "tc_cache_consumer_wrapper",
        autoconf_target = ":tc_cache_consumer_inner",
        toolchain = "//autoconf/tests/core/caching:tc_cache_toolchain",
    )

    autoconf(
        name = "tc_cache_consumer_inner",
        checks = [
            checks.AC_DEFINE("TC_CACHED_DEFINE", 77),
            checks.AC_DEFINE("TC_LOCAL_DEFINE", 88),
        ],
        tags = ["manual"],
    )

    write_file(
        name = "tc_cache_hit_template",
        out = "tc_cache_hit.h.in",
        content = [
            "#undef TC_CACHED_DEFINE",
            "#undef TC_LOCAL_DEFINE",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "tc_cache_hit_hdr_wrapper",
        autoconf_hdr = ":tc_cache_hit_hdr_inner",
        toolchain = "//autoconf/tests/core/caching:tc_cache_toolchain",
    )

    autoconf_hdr(
        name = "tc_cache_hit_hdr_inner",
        out = "tc_cache_hit.h",
        mode = "defines",
        template = ":tc_cache_hit_template",
        deps = [":tc_cache_consumer_inner"],
        defaults = False,
        tags = ["manual"],
    )

    write_file(
        name = "golden_tc_cache_hit",
        out = "golden_tc_cache_hit.h.in",
        content = [
            "#define TC_CACHED_DEFINE 77",
            "#define TC_LOCAL_DEFINE 88",
            "",
        ],
    )

    diff_test(
        name = "test_tc_cache_hit",
        file1 = ":golden_tc_cache_hit",
        file2 = ":tc_cache_hit_hdr_wrapper",
    )
    tests.append(":test_tc_cache_hit")

    # ============================================================================
    # Test 6: toolchain defaults also cached
    #
    # A check provided via the toolchain's defaults attribute should also be
    # available as a cache hit for autoconf targets.
    # ============================================================================

    autoconf_cache(
        name = "tc_defaults_provider",
        checks = [
            checks.AC_DEFINE("TC_DEFAULTS_DEFINE", 55),
        ],
        tags = ["manual"],
    )

    autoconf_toolchain(
        name = "tc_defaults_cache_toolchain_impl",
        defaults = [":tc_defaults_provider"],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    native.toolchain(
        name = "tc_defaults_cache_toolchain",
        toolchain = ":tc_defaults_cache_toolchain_impl",
        toolchain_type = "//autoconf:toolchain_type",
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    _autoconf_with_toolchain(
        name = "tc_defaults_cache_consumer_wrapper",
        autoconf_target = ":tc_defaults_cache_consumer_inner",
        toolchain = "//autoconf/tests/core/caching:tc_defaults_cache_toolchain",
    )

    autoconf(
        name = "tc_defaults_cache_consumer_inner",
        checks = [
            checks.AC_DEFINE("TC_DEFAULTS_DEFINE", 55),
            checks.AC_DEFINE("TC_DEFAULTS_LOCAL", 66),
        ],
        tags = ["manual"],
    )

    write_file(
        name = "tc_defaults_cache_template",
        out = "tc_defaults_cache.h.in",
        content = [
            "#undef TC_DEFAULTS_DEFINE",
            "#undef TC_DEFAULTS_LOCAL",
            "",
        ],
    )

    _autoconf_hdr_with_toolchain(
        name = "tc_defaults_cache_hdr_wrapper",
        autoconf_hdr = ":tc_defaults_cache_hdr_inner",
        toolchain = "//autoconf/tests/core/caching:tc_defaults_cache_toolchain",
    )

    autoconf_hdr(
        name = "tc_defaults_cache_hdr_inner",
        out = "tc_defaults_cache.h",
        mode = "defines",
        template = ":tc_defaults_cache_template",
        deps = [":tc_defaults_cache_consumer_inner"],
        defaults = False,
        tags = ["manual"],
    )

    write_file(
        name = "golden_tc_defaults_cache",
        out = "golden_tc_defaults_cache.h.in",
        content = [
            "#define TC_DEFAULTS_DEFINE 55",
            "#define TC_DEFAULTS_LOCAL 66",
            "",
        ],
    )

    diff_test(
        name = "test_tc_defaults_cache",
        file1 = ":golden_tc_defaults_cache",
        file2 = ":tc_defaults_cache_hdr_wrapper",
    )
    tests.append(":test_tc_defaults_cache")

    # ============================================================================
    # Test 7: content-based dedup — same check, different define names
    #
    # Two defines backed by the same check implementation (AC_DEFINE with
    # identical value).  The second should get a content cache hit and reuse
    # the first's result file, but both define names must appear in output.
    # ============================================================================

    autoconf(
        name = "content_dedup_autoconf",
        checks = [
            checks.AC_DEFINE("CONTENT_A", 99),
            checks.AC_DEFINE("CONTENT_B", 99),
        ],
    )

    write_file(
        name = "content_dedup_template",
        out = "content_dedup.h.in",
        content = [
            "#undef CONTENT_A",
            "#undef CONTENT_B",
            "",
        ],
    )

    autoconf_hdr(
        name = "content_dedup_hdr",
        out = "content_dedup.h",
        mode = "defines",
        template = ":content_dedup_template",
        deps = [":content_dedup_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_content_dedup",
        out = "golden_content_dedup.h.in",
        content = [
            "#define CONTENT_A 99",
            "#define CONTENT_B 99",
            "",
        ],
    )

    diff_test(
        name = "test_content_dedup",
        file1 = ":golden_content_dedup",
        file2 = ":content_dedup_hdr",
    )
    tests.append(":test_content_dedup")

    # ============================================================================
    # Test 8: content-based dedup — different values = different content keys
    #
    # Two AC_DEFINE checks with the SAME base implementation but DIFFERENT
    # values must produce different content keys and NOT dedup.  Both must
    # run their own action and produce their respective values.
    # ============================================================================

    autoconf(
        name = "content_diff_val_autoconf",
        checks = [
            checks.AC_DEFINE("VAL_LOW", 10),
            checks.AC_DEFINE("VAL_HIGH", 20),
        ],
    )

    write_file(
        name = "content_diff_val_template",
        out = "content_diff_val.h.in",
        content = [
            "#undef VAL_LOW",
            "#undef VAL_HIGH",
            "",
        ],
    )

    autoconf_hdr(
        name = "content_diff_val_hdr",
        out = "content_diff_val.h",
        mode = "defines",
        template = ":content_diff_val_template",
        deps = [":content_diff_val_autoconf"],
        defaults = False,
    )

    write_file(
        name = "golden_content_diff_val",
        out = "golden_content_diff_val.h.in",
        content = [
            "#define VAL_LOW 10",
            "#define VAL_HIGH 20",
            "",
        ],
    )

    diff_test(
        name = "test_content_diff_val",
        file1 = ":golden_content_diff_val",
        file2 = ":content_diff_val_hdr",
    )
    tests.append(":test_content_diff_val")

    # ============================================================================
    # Test 9: content-based dedup across deps — same check in dep and consumer
    #         with different define names
    #
    # dep defines HEADER_A backed by AC_DEFINE(_, 123).  consumer lists the
    # same implementation but with define name HEADER_B.  Content key matches
    # so the dep's result file is reused.  Both define names must render.
    # ============================================================================

    autoconf(
        name = "content_dep_provider",
        checks = [
            checks.AC_DEFINE("HEADER_A", 123),
        ],
        tags = ["manual"],
    )

    autoconf(
        name = "content_dep_consumer",
        checks = [
            checks.AC_DEFINE("HEADER_B", 123),
            checks.AC_DEFINE("LOCAL_ONLY", 456),
        ],
        deps = [":content_dep_provider"],
    )

    write_file(
        name = "content_dep_dedup_template",
        out = "content_dep_dedup.h.in",
        content = [
            "#undef HEADER_A",
            "#undef HEADER_B",
            "#undef LOCAL_ONLY",
            "",
        ],
    )

    autoconf_hdr(
        name = "content_dep_dedup_hdr",
        out = "content_dep_dedup.h",
        mode = "defines",
        template = ":content_dep_dedup_template",
        deps = [":content_dep_consumer"],
        defaults = False,
    )

    write_file(
        name = "golden_content_dep_dedup",
        out = "golden_content_dep_dedup.h.in",
        content = [
            "#define HEADER_A 123",
            "#define HEADER_B 123",
            "#define LOCAL_ONLY 456",
            "",
        ],
    )

    diff_test(
        name = "test_content_dep_dedup",
        file1 = ":golden_content_dep_dedup",
        file2 = ":content_dep_dedup_hdr",
    )
    tests.append(":test_content_dep_dedup")

    # ============================================================================
    # Test 10: toolchain content cache hit with different define name
    #
    # Toolchain's cache_deps provides AC_DEFINE("TC_CONTENT_X", 200).
    # Consumer lists AC_DEFINE("TC_CONTENT_Y", 200) — same implementation,
    # different define name.  Should get a content cache hit from the toolchain.
    # ============================================================================

    autoconf_cache(
        name = "tc_content_provider",
        checks = [
            checks.AC_DEFINE("TC_CONTENT_X", 200),
        ],
        tags = ["manual"],
    )

    autoconf_toolchain(
        name = "tc_content_toolchain_impl",
        cache_deps = [":tc_content_provider"],
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    native.toolchain(
        name = "tc_content_toolchain",
        toolchain = ":tc_content_toolchain_impl",
        toolchain_type = "//autoconf:toolchain_type",
        visibility = ["//visibility:public"],
        tags = ["manual"],
    )

    autoconf(
        name = "tc_content_consumer_inner",
        checks = [
            checks.AC_DEFINE("TC_CONTENT_Y", 200),
            checks.AC_DEFINE("TC_CONTENT_LOCAL", 300),
        ],
        tags = ["manual"],
    )

    _autoconf_with_toolchain(
        name = "tc_content_consumer_wrapper",
        autoconf_target = ":tc_content_consumer_inner",
        toolchain = "//autoconf/tests/core/caching:tc_content_toolchain",
    )

    write_file(
        name = "tc_content_dedup_template",
        out = "tc_content_dedup.h.in",
        content = [
            "#undef TC_CONTENT_Y",
            "#undef TC_CONTENT_LOCAL",
            "",
        ],
    )

    autoconf_hdr(
        name = "tc_content_dedup_hdr_inner",
        out = "tc_content_dedup.h",
        mode = "defines",
        template = ":tc_content_dedup_template",
        deps = [":tc_content_consumer_inner"],
        defaults = False,
        tags = ["manual"],
    )

    _autoconf_hdr_with_toolchain(
        name = "tc_content_dedup_hdr_wrapper",
        autoconf_hdr = ":tc_content_dedup_hdr_inner",
        toolchain = "//autoconf/tests/core/caching:tc_content_toolchain",
    )

    write_file(
        name = "golden_tc_content_dedup",
        out = "golden_tc_content_dedup.h.in",
        content = [
            "#define TC_CONTENT_Y 200",
            "#define TC_CONTENT_LOCAL 300",
            "",
        ],
    )

    diff_test(
        name = "test_tc_content_dedup",
        file1 = ":golden_tc_content_dedup",
        file2 = ":tc_content_dedup_hdr_wrapper",
    )
    tests.append(":test_tc_content_dedup")

    # ============================================================================
    # Starlark unit tests — provider-level cache hit verification
    # ============================================================================

    content_dedup_same_file_test(
        name = "test_content_dedup_same_file",
        target_under_test = ":content_dedup_autoconf",
    )
    tests.append(":test_content_dedup_same_file")

    content_diff_val_different_files_test(
        name = "test_content_diff_val_different_files",
        target_under_test = ":content_diff_val_autoconf",
    )
    tests.append(":test_content_diff_val_different_files")

    content_dep_dedup_reuses_file_test(
        name = "test_content_dep_dedup_reuses_file",
        target_under_test = ":content_dep_consumer",
    )
    tests.append(":test_content_dep_dedup_reuses_file")

    tc_content_dedup_reuses_file_test(
        name = "test_tc_content_dedup_reuses_file",
        target_under_test = ":tc_content_consumer_wrapper",
    )
    tests.append(":test_tc_content_dedup_reuses_file")

    # ============================================================================
    # Test Suite
    # ============================================================================

    native.test_suite(
        name = name,
        tests = tests,
        **kwargs
    )
