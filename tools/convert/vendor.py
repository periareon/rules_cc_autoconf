"""Vendor the hand ports a converted project uses into the project itself.

With `--vendor-ports` the generated project depends on rules_cc_autoconf for
its rules only.  Every `@rules_cc_autoconf//gnulib/m4/...` (and
`//autoconf/macros/...`, `//gnulib/macros/...`) port the conversion resolved
is copied, with the ports it depends on in turn, into
`third_party/rules_cc_autoconf/<package>/BUILD.bazel` of the project, labels
rewritten, and the project's references point at the copies.  The copies are
pinned to the gnulib snapshot the project was converted against: later
changes to the ports in rules_cc_autoconf do not reach it.

What stays with rules_cc_autoconf: the rules and two ports that are rule
implementations rather than check data (AC_C_INLINE, AC_C_RESTRICT); the
report says so.  The headers' `:defaults` targets are ports like the rest:
the `default_deps` that name them are rewritten and the packages copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert.index import PortIndex
from tools.convert.ir import Assign, Call, Package, Raw, Rule
from tools.convert.translate import ProjectState

VENDOR_ROOT = "third_party/rules_cc_autoconf"
PORT_ROOTS = ("gnulib/m4", "autoconf/macros", "gnulib/macros")
# Ports that are rule implementations (a resolver binary, private rule
# machinery), not check data: they stay references to rules_cc_autoconf.
NOT_VENDORED = {"autoconf/macros/AC_C_INLINE", "autoconf/macros/AC_C_RESTRICT"}

# A port label in the generated project (`@rules_cc_autoconf//gnulib/m4/chown:gl_FUNC_CHOWN`)
# or in a port's own BUILD file (`//gnulib/m4/chown`).
_PORT_LABEL_RE = re.compile(
    r'"(?:@rules_cc_autoconf)?//((?:gnulib/m4|autoconf/macros|gnulib/macros)/[A-Za-z0-9_.+-]+)(:[A-Za-z0-9_.+-]+)?"')
_PORT_PACKAGE_IN_TEXT_RE = re.compile(r'"//((?:gnulib/m4|autoconf/macros|gnulib/macros)/[A-Za-z0-9_.+-]+)(?::[^"]*)?"')
_LOAD_CACHE_RULE = 'load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")'
_LOAD_RULE = 'load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")'


@dataclass
class Vendored:
    files: dict[str, str] = field(default_factory=dict)  # output path -> content
    packages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def vendor_ports(package: Package, state: ProjectState | None, index: PortIndex, workspace: Path,
                 rules_version: str) -> Vendored:
    """Rewrite `package` to use copies of its ports and return the copies."""
    out = Vendored()
    referenced: set[str] = set()
    _rewrite_package(package, referenced)
    closure = _closure(referenced, workspace, out.notes)
    out.packages = sorted(closure)
    for pkg in out.packages:
        out.files.update(_copy_package(pkg, workspace, rules_version))
    out.notes.insert(0, f"ports vendored: {len(out.packages)} packages under {VENDOR_ROOT}/ (no dependency on "
                        "rules_cc_autoconf's gnulib ports; the rules themselves still come from it)")
    kept = sorted(NOT_VENDORED & _referenced_anywhere(package, out.files))
    if kept:
        out.notes.append("still referenced from rules_cc_autoconf (rule implementations, not check data): "
                         + ", ".join(kept))
    return out


def _referenced_anywhere(package: Package, files: dict[str, str]) -> set[str]:
    texts = list(files.values())
    for target in package.targets:
        texts += [dep.label for dep in target.deps + target.default_deps]
    return {m.group(1) for text in texts for m in _PORT_LABEL_RE.finditer(" ".join(f'"{t}"' for t in [text]))}


# -- references in the generated package -------------------------------------

def _vendored_label(match: re.Match) -> str:
    pkg, target = match.group(1), match.group(2) or ""
    if pkg in NOT_VENDORED:
        return f'"@rules_cc_autoconf//{pkg}{target}"'
    return f'"//{VENDOR_ROOT}/{pkg}{target}"'


def _rewrite_text(text: str, referenced: set[str]) -> str:
    for match in _PORT_LABEL_RE.finditer(text):
        if match.group(1) not in NOT_VENDORED:
            referenced.add(match.group(1))
    return _PORT_LABEL_RE.sub(_vendored_label, text)


def _rewrite_value(value, referenced: set[str]):
    if isinstance(value, str):
        return _rewrite_text(f'"{value}"', referenced)[1:-1] if "//" in value else value
    if isinstance(value, list):
        return [_rewrite_value(v, referenced) for v in value]
    if isinstance(value, dict):
        return {_rewrite_value(k, referenced): _rewrite_value(v, referenced) for k, v in value.items()}
    if isinstance(value, Raw):
        value.text = _rewrite_text(value.text, referenced)
        return value
    if isinstance(value, Call):
        value.args = [_rewrite_value(a, referenced) for a in value.args]
        value.kwargs = {k: _rewrite_value(v, referenced) for k, v in value.kwargs.items()}
        return value
    return value


def _rewrite_package(package: Package, referenced: set[str]) -> None:
    for target in package.targets:
        for dep in target.deps + target.default_deps:
            dep.label = _rewrite_value(dep.label, referenced)
        target.checks = _rewrite_value(target.checks, referenced)
        for family, arms in target.selects.items():
            target.selects[family] = _rewrite_value(arms, referenced)
        target.attrs = _rewrite_value(target.attrs, referenced)
    for statement in [*package.before, *package.after]:
        if isinstance(statement, Assign):
            statement.value = _rewrite_value(statement.value, referenced)
        elif isinstance(statement, Rule):
            statement.attrs = _rewrite_value(statement.attrs, referenced)
        elif isinstance(statement, Raw):
            _rewrite_value(statement, referenced)


# -- the ports' closure and copies ---------------------------------------------

def _package_files(pkg: str, workspace: Path) -> list[Path]:
    directory = workspace / pkg
    return sorted(p for p in directory.iterdir() if p.is_file()) if directory.is_dir() else []


def _closure(referenced: set[str], workspace: Path, notes: list[str]) -> set[str]:
    """The referenced port packages and, transitively, the ones their BUILD files name."""
    seen: set[str] = set()
    queue = sorted(referenced)
    while queue:
        pkg = queue.pop(0)
        if pkg in seen or pkg in NOT_VENDORED:
            continue
        files = _package_files(pkg, workspace)
        if not files:
            notes.append(f"{pkg}: no such port package in {workspace}; the reference is left as is")
            continue
        seen.add(pkg)
        for path in files:
            if path.suffix in (".bazel", ".bzl"):
                text = path.read_text(encoding="utf-8", errors="replace")
                queue += [m.group(1) for m in _PORT_PACKAGE_IN_TEXT_RE.finditer(text) if m.group(1) not in seen]
    return seen


def _rewrite_port_text(text: str) -> str:
    """A port BUILD or .bzl file as it reads inside the project."""
    text = text.replace(_LOAD_CACHE_RULE, _LOAD_RULE)
    text = re.sub(r'load\("//', 'load("@rules_cc_autoconf//', text)
    text = _PORT_PACKAGE_IN_TEXT_RE.sub(lambda m: _vendored_label(_PORT_LABEL_RE.match(m.group(0))), text)
    # The ports' package-group visibilities (`//gnulib:__subpackages__`) kept
    # the defaults targets internal; inside the project they are public, as
    # the project's root package reads them.
    text = re.sub(r'"//gnulib(/m4)?:__subpackages__"', '"//visibility:public"', text)
    text = re.sub(r'"//gnulib/tests/[^"]*"', '"//visibility:public"', text)
    text = re.sub(r'"//tools/', '"@rules_cc_autoconf//tools/', text)
    return text


def _target_suffix(label: str) -> str:
    inner = label.strip('"')
    return inner[inner.index(":"):] if ":" in inner else ""


def _copy_package(pkg: str, workspace: Path, rules_version: str) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in _package_files(pkg, workspace):
        out_path = f"{VENDOR_ROOT}/{pkg}/{path.name}"
        if path.suffix in (".bazel", ".bzl"):
            text = _rewrite_port_text(path.read_text(encoding="utf-8", errors="replace"))
            banner = (f"# @generated: vendored from rules_cc_autoconf {rules_version}, {pkg}/{path.name}.\n"
                      "# Regenerate with the converter (--vendor-ports); do not edit.\n")
            files[out_path] = banner + text
        else:
            files[out_path] = path.read_text(encoding="utf-8", errors="replace")
    return files


def ensure_module_deps(module_text: str, files: dict[str, str]) -> str:
    """MODULE.bazel with the dependencies the vendored files load (a few ports use
    `@bazel_skylib//lib:selects.bzl`) that the project did not already declare."""
    needed = []
    if any("@bazel_skylib//" in content for content in files.values()) and "bazel_skylib" not in module_text:
        needed.append('bazel_dep(name = "bazel_skylib", version = "1.8.2")')
    if not needed:
        return module_text
    anchor = 'bazel_dep(name = "platforms"'
    if anchor in module_text:
        return module_text.replace(anchor, "\n".join(needed) + "\n" + anchor, 1)
    return module_text.rstrip("\n") + "\n" + "\n".join(needed) + "\n"
