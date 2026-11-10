"""Unit tests for the lifted ports and their mirrors (`convert --inline-checks`)."""

from __future__ import annotations

import pytest

from tools.convert.emit import render_package
from tools.convert.ir import Package
from tools.convert.ports import LiftError, PortSnapshot, _is_label, lift_file, mirror

FOO = '''"""https://github.com/coreutils/gnulib/blob/abc/m4/foo.m4"""

load("@bazel_skylib//lib:selects.bzl", "selects")
load("//autoconf:autoconf_defaults.bzl", "autoconf_defaults")
load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks", "utils")
load("//gnulib:macros.bzl", "macros")

_PROLOGUE = [
    "#include <stdio.h>",
]

selects.config_setting_group(
    name = "_linux_x86_64",
    match_all = [
        "@platforms//os:linux",
        "@platforms//cpu:x86_64",
    ],
)

autoconf_defaults(
    name = "defaults",
    substs = {"HAVE_FOO": "1"},
    visibility = ["//visibility:private"],
)

# gl_FOO
autoconf(
    name = "foo",
    checks = [
        checks.AC_TRY_COMPILE(
            code = utils.AC_LANG_PROGRAM(_PROLOGUE, "return 0;"),
            define = "HAVE_FOO_WORKS",
        ),
    ] + select({
        ":_linux_x86_64": [checks.AC_DEFINE("FOO_X86", 1)],
        "//conditions:default": [],
    }) + macros.GL_CHECK_NEXT_HEADERS(["foo.h"]),
    default_deps = [":defaults"],
    visibility = ["//visibility:public"],
    deps = [
        "//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT",
        "//gnulib/m4/bar",
    ] + select({
        "@platforms//os:windows": [],
        "//conditions:default": ["//gnulib/m4/bar:gl_BAR_EXTRA"],
    }),
)
'''

BAR = '''"""https://github.com/coreutils/gnulib/blob/abc/m4/bar.m4"""

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")

autoconf(
    name = "bar",
    checks = [checks.AC_CHECK_HEADER("bar.h")],
    visibility = ["//visibility:public"],
)

autoconf(
    name = "gl_BAR_EXTRA",
    checks = [checks.AC_DEFINE("BAR_EXTRA", 1)],
    visibility = ["//visibility:private"],
    deps = [":bar"],
)
'''


def test_lift_reads_each_rule_as_text() -> None:
    """Every rule, attribute by attribute, verbatim; the loads and constants it uses noted."""
    file = lift_file("gnulib/m4/foo", FOO)
    assert file.loads["autoconf"] == ("//autoconf:autoconf_toolchain.bzl", "autoconf_cache")
    assert file.loads["macros"] == ("//gnulib:macros.bzl", "macros")
    assert file.constants == {"_PROLOGUE": '[\n    "#include <stdio.h>",\n]'}
    kinds = {rule.name: rule.kind for rule in file.rules}
    assert kinds == {
        "_linux_x86_64": "selects.config_setting_group",
        "defaults": "autoconf_defaults",
        "foo": "autoconf",
    }
    foo = next(rule for rule in file.rules if rule.name == "foo")
    assert foo.public
    assert foo.uses == ["_PROLOGUE", "checks", "macros", "utils"]
    assert foo.attrs["default_deps"] == '[":defaults"]'
    assert foo.attrs["checks"].startswith("[\n        checks.AC_TRY_COMPILE(")
    assert "visibility" not in foo.attrs


def test_lift_refuses_what_it_cannot_reproduce() -> None:
    with pytest.raises(LiftError, match="computed name"):
        lift_file(
            "gnulib/m4/x",
            'load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")\nautoconf(name = NAME)\n',
        )
    with pytest.raises(LiftError, match="positional"):
        lift_file(
            "gnulib/m4/x", 'load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")\nautoconf("x")\n'
        )
    with pytest.raises(LiftError, match="does not carry"):
        lift_file("gnulib/m4/x", "def f():\n    pass\n")
    with pytest.raises(LiftError, match="does not carry"):
        lift_file("gnulib/m4/x", 'cc_library(name = "x")\n')


def test_mirror_declares_the_closure_in_the_package() -> None:
    """From a project's dependency label: the port, the ports it reaches through plain deps and
    select arms, its defaults and its config setting, each a rule of the package with the ports'
    labels rewritten, the loads canonical, the constants qualified; `//autoconf/...` stays a
    dependency."""
    ports = PortSnapshot([lift_file("gnulib/m4/foo", FOO), lift_file("gnulib/m4/bar", BAR)])
    mirrored = mirror(ports, ["@rules_cc_autoconf//gnulib/m4/foo"], repo="@rules_cc_autoconf")
    assert sorted(mirrored.labels) == [
        "//gnulib/m4/bar:bar",
        "//gnulib/m4/bar:gl_BAR_EXTRA",
        "//gnulib/m4/foo:_linux_x86_64",
        "//gnulib/m4/foo:defaults",
        "//gnulib/m4/foo:foo",
    ]
    assert mirrored.labels["//gnulib/m4/foo:foo"] == ":gnulib/m4/foo/foo"
    package = Package(path="", docstring="x")
    package.before += mirrored.constants
    package.targets += mirrored.targets
    package.after += mirrored.rules
    package.loads += mirrored.loads
    text = render_package(package, repo="@rules_cc_autoconf", in_repo=False)
    assert 'load("@rules_cc_autoconf//gnulib:macros.bzl", gl_macros = "macros")' in text
    assert 'load("@rules_cc_autoconf//autoconf:checks.bzl", "checks", "utils")' in text
    assert 'load("@bazel_skylib//lib:selects.bzl", "selects")' in text
    assert "_GNULIB_M4_FOO_PROLOGUE = [" in text
    assert 'code = utils.AC_LANG_PROGRAM(_GNULIB_M4_FOO_PROLOGUE, "return 0;")' in text
    assert '":gnulib/m4/foo/_linux_x86_64": [checks.AC_DEFINE("FOO_X86", 1)]' in text
    assert 'gl_macros.GL_CHECK_NEXT_HEADERS(["foo.h"])' in text
    assert '"//conditions:default": [":gnulib/m4/bar/gl_BAR_EXTRA"]' in text
    assert '"@rules_cc_autoconf//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT"' in text
    assert 'default_deps = [":gnulib/m4/foo/defaults"]' in text
    assert 'name = "gnulib/m4/foo/defaults"' in text
    assert 'substs = {"HAVE_FOO": "1"}' in text
    assert 'name = "gnulib/m4/foo/_linux_x86_64"' in text
    assert "# @rules_cc_autoconf//gnulib/m4/foo:foo\nautoconf(" in text
    assert 'visibility = ["//visibility:private"]' in text
    assert "# gl_FOO" not in text  # a comment above the rule is the port file's, not the attribute's
    with pytest.raises(LiftError, match="no such port rule"):
        mirror(ports, ["@rules_cc_autoconf//gnulib/m4/baz"], repo="@rules_cc_autoconf")


def test_labels_are_told_from_text() -> None:
    assert _is_label(":x") and _is_label("//gnulib/m4/foo") and _is_label("@platforms//os:linux")
    assert _is_label("//conditions:default")
    assert not _is_label("@VAR@") and not _is_label("// a comment") and not _is_label("http://x")
