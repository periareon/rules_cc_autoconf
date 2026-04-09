"""Gnulib test deps"""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_BUILD_FILE = """\
exports_files(glob(["m4/*.m4"]))

filegroup(
    name = "all_m4",
    srcs = glob(["m4/*.m4"]),
    visibility = ["//visibility:public"],
)

filegroup(
    name = "build-aux-config",
    srcs = glob(["build-aux/config.*"]),
    visibility = ["//visibility:public"],
)
"""

def _gnulib_impl(module_ctx):
    http_archive(
        name = "gnulib",
        urls = ["https://github.com/coreutils/gnulib/archive/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0.zip"],
        integrity = "sha256-m+oVoY9VOHLmFjjV3zmzlCS+Mnp9mqso0YNq3vTHodc=",
        strip_prefix = "gnulib-1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0",
        build_file_content = _BUILD_FILE,
    )

    return module_ctx.extension_metadata(
        reproducible = True,
        root_module_direct_deps = [],
        root_module_direct_dev_deps = ["gnulib"],
    )

gnulib = module_extension(
    doc = "rules_cc_autoconf gnulib test dependencies.",
    implementation = _gnulib_impl,
)
