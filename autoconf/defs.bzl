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
    ":macros.bzl",
    _macros = "macros",
)
load(
    ":module_info.bzl",
    _module_info = "module_info",
)

autoconf = _autoconf
autoconf_hdr = _autoconf_hdr
autoconf_srcs = _autoconf_srcs
macros = _macros
module_info = _module_info
