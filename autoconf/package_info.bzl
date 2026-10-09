"""# package_info

Deprecated: `package_info` has been renamed to `autoconf_package_info`. Load it from
`@rules_cc_autoconf//autoconf:autoconf_package_info.bzl` instead. This file remains as a
compatibility alias and will be removed in a future release.
"""

load(":autoconf_package_info.bzl", _autoconf_package_info = "autoconf_package_info")

package_info = _autoconf_package_info
