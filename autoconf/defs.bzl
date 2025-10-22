"""Autoconf-like configuration checks for Bazel.

This module provides a rule to mimic autoconf functionality, allowing you to
generate config.h files by running compilation checks against a cc_toolchain.
"""

load(
    ":autoconf.bzl",
    _autoconf = "autoconf",
)
load(
    ":macros.bzl",
    _macros = "macros",
)

autoconf = _autoconf
macros = _macros
