"""Tests for map_bin_dirs in autoconf_config.bzl."""

load("@bazel_skylib//lib:unittest.bzl", "asserts", "unittest")
load("//autoconf/private:autoconf_config.bzl", "map_bin_dirs")

_MAPPED_BIN = "bazel-out/cfg/bin"

def _target_bin_dir_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        "-Lbazel-out/cfg/bin/external/llvm+/runtimes",
        map_bin_dirs("-Lbazel-out/k8-fastbuild/bin/external/llvm+/runtimes", "bazel-out", _MAPPED_BIN),
    )
    return unittest.end(env)

target_bin_dir_test = unittest.make(_target_bin_dir_test_impl)

def _exec_bin_dir_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        "bazel-out/cfg/bin/external/llvm+/tools/link-wrapper",
        map_bin_dirs("bazel-out/k8-opt-exec/bin/external/llvm+/tools/link-wrapper", "bazel-out", _MAPPED_BIN),
    )
    return unittest.end(env)

exec_bin_dir_test = unittest.make(_exec_bin_dir_test_impl)

def _several_bin_dirs_test_impl(ctx):
    env = unittest.begin(ctx)
    asserts.equals(
        env,
        "--sysroot=bazel-out/cfg/bin/a:bazel-out/cfg/bin/b",
        map_bin_dirs("--sysroot=bazel-out/k8-fastbuild/bin/a:bazel-out/k8-opt-exec/bin/b", "bazel-out", _MAPPED_BIN),
    )
    return unittest.end(env)

several_bin_dirs_test = unittest.make(_several_bin_dirs_test_impl)

def _not_a_bin_dir_test_impl(ctx):
    env = unittest.begin(ctx)
    for value in [
        "external/llvm+/bin/clang",
        "-Wall",
        "bazel-out/k8-fastbuild/testlogs/x",
        "bazel-out/k8-fastbuild/binaries/x",
        "bazel-out/volatile-status.txt",
    ]:
        asserts.equals(env, value, map_bin_dirs(value, "bazel-out", _MAPPED_BIN))
    return unittest.end(env)

not_a_bin_dir_test = unittest.make(_not_a_bin_dir_test_impl)

def path_mapping_test_suite(name, **kwargs):
    """Test suite for map_bin_dirs.

    Args:
        name (str): The name of the test suite.
        **kwargs (dict): Additional keyword arguments for the test suite.
    """
    tests = []

    target_bin_dir_test(name = name + "_target_bin_dir")
    tests.append(":" + name + "_target_bin_dir")

    exec_bin_dir_test(name = name + "_exec_bin_dir")
    tests.append(":" + name + "_exec_bin_dir")

    several_bin_dirs_test(name = name + "_several_bin_dirs")
    tests.append(":" + name + "_several_bin_dirs")

    not_a_bin_dir_test(name = name + "_not_a_bin_dir")
    tests.append(":" + name + "_not_a_bin_dir")

    native.test_suite(
        name = name,
        tests = tests,
        **kwargs
    )
