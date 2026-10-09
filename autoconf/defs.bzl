"""# rules_cc_autoconf
"""

load(
    ":autoconf.bzl",
    _autoconf = "autoconf",
)
load(
    ":autoconf_hdr.bzl",
    _autoconf_hdr = "autoconf_hdr",
)
load(
    ":autoconf_linkopts.bzl",
    _autoconf_linkopts = "autoconf_linkopts",
)
load(
    ":autoconf_package_info.bzl",
    _autoconf_package_info = "autoconf_package_info",
)
load(
    ":autoconf_srcs.bzl",
    _autoconf_srcs = "autoconf_srcs",
)
load(
    ":autoconf_toolchain.bzl",
    _autoconf_toolchain = "autoconf_toolchain",
)
load(
    ":checks.bzl",
    _checks = "checks",
    _macros = "macros",
)

autoconf = _autoconf

autoconf_hdr = _autoconf_hdr
autoconf_linkopts = _autoconf_linkopts
autoconf_package_info = _autoconf_package_info
autoconf_srcs = _autoconf_srcs
autoconf_toolchain = _autoconf_toolchain
checks = _checks
macros = _macros

# Deprecated: use `autoconf_package_info` instead.
package_info = _autoconf_package_info
