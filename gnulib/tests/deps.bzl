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
        urls = ["https://github.com/coreutils/gnulib/archive/635dbdcf501d52d2e42daf6b44261af9ce2dfe38.zip"],
        integrity = "sha256-jdRTQZBSkiapmoHhQZTlzlKn7DgN+JY6mPJEb1we2cA=",
        strip_prefix = "gnulib-635dbdcf501d52d2e42daf6b44261af9ce2dfe38",
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
