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
    ":autoconf_srcs.bzl",
    _autoconf_srcs = "autoconf_srcs",
)
load(
    ":checks.bzl",
    _checks = "checks",
    _macros = "macros",
)
load(
    ":package_info.bzl",
    _package_info = "package_info",
)

autoconf = _autoconf
autoconf_hdr = _autoconf_hdr
autoconf_srcs = _autoconf_srcs
checks = _checks
macros = _macros
package_info = _package_info
