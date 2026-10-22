"""Source archives the converter's project-mode golden tests convert."""

load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_BUILD_FILE = """\
filegroup(
    name = "all_files",
    srcs = glob(
        include = ["**"],
        exclude = [
            "BUILD",
            "*.bazel",
        ],
    ),
    visibility = ["//visibility:public"],
)
"""

def _gnu_urls(package, filename):
    return [
        "https://ftpmirror.gnu.org/{}/{}".format(package, filename),
        "https://ftp.gnu.org/gnu/{}/{}".format(package, filename),
        "https://mirror.bazel.build/ftp.gnu.org/gnu/{}/{}".format(package, filename),
    ]

def _convert_test_sources_impl(module_ctx):
    http_archive(
        name = "gzip_src",
        urls = _gnu_urls("gzip", "gzip-1.15.tar.xz"),
        integrity = "sha256-mqDMeA3sFWuCgoRIM7NCq3ywjCXSzZoYac3Q3zHe/0g=",
        strip_prefix = "gzip-1.15",
        build_file_content = _BUILD_FILE,
    )

    return module_ctx.extension_metadata(reproducible = True)

convert_test_sources = module_extension(
    doc = "Release tarballs converted by `//tools/convert`'s golden tests.",
    implementation = _convert_test_sources_impl,
)
