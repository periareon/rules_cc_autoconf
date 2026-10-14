"""Intermediate representation: what gets emitted as Starlark.

Deliberately close to the output: a `Call` is a Starlark call expression
with positional and keyword arguments (`checks.AC_SUBST("X", 1)`), a
`Target` is one `autoconf(...)` rule, a `Package` is one BUILD file.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class Loc:
    file: str
    line: int | None = None
    approx: bool = False

    def __str__(self) -> str:
        if self.line is None:
            return self.file
        return f"{self.file}:{'~' if self.approx else ''}{self.line}"


@dataclass
class Raw:
    """Starlark source text to emit verbatim as a value."""

    text: str


@dataclass
class Call:
    """A Starlark call: `fn(*args, **kwargs)`."""

    fn: str
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)  # emitted above the call
    returns_list: bool = False  # plural macros: concatenated with `+`

    def arg(self, name: str) -> Any:
        return self.kwargs.get(name)


@dataclass
class Dep:
    label: str
    comment: str | None = None


CheckItem = Union[Call, Raw]


DEFAULT_ARM = "//conditions:default"
OS_FAMILY = "@platforms//os:"
CPU_FAMILY = "@platforms//cpu:"
COMPILER_FAMILY = "@rules_cc//cc/compiler:"
PLATFORM_FAMILIES = (OS_FAMILY, CPU_FAMILY, COMPILER_FAMILY)  # mutually exclusive arms, one select each


def select_family(label: str) -> str:
    """The `select()` a configuration label belongs to.

    Bazel rejects a `select()` whose arms can match together (two configure
    options both enabled), so the host-OS constraints share one select, the
    CPU constraints another, the compilers a third, and every configure
    option gets its own (keyed by the option name), each with its own
    default arm.
    """
    for family in PLATFORM_FAMILIES:
        if label.startswith(family):
            return family
    if label == DEFAULT_ARM:
        return OS_FAMILY
    return option_family(label)


def option_family(label: str) -> str:
    """`:x_enabled`, `:x_openssl` -> the option's family (`:x`)."""
    name = label.lstrip(":")
    if name.endswith("_enabled"):
        return ":" + name[:-len("_enabled")]
    return ":" + name.rsplit("_", 1)[0] if "_" in name else label


def family_of_labels(labels) -> str:
    """The family of a decided `select({...})`: that of its first non-default label, or the OS."""
    others = [label for label in labels if label != DEFAULT_ARM]
    return select_family(others[0]) if others else OS_FAMILY


@dataclass
class Target:
    name: str
    checks: list[CheckItem] = field(default_factory=list)
    # Conditional checks, one `select({label: [...], "//conditions:default": [...]})`
    # per family (see `select_family`).
    selects: dict[str, dict[str, list[CheckItem]]] = field(default_factory=dict)
    deps: list[Dep] = field(default_factory=list)
    deps_suffix: list[str] = field(default_factory=list)  # `+ NAME` list variables appended to deps
    comments: list[str] = field(default_factory=list)  # above the rule
    visibility: str = "//visibility:public"
    loc: Loc | None = None
    helper: bool = False  # a shared-probe helper target; not in the aggregator
    attrs: dict[str, Any] = field(default_factory=dict)  # other attributes (copts, visibility, ...)
    # "autoconf", or "autoconf_defaults" for a `_DEFAULTS` macro: literal values a probe may
    # override, rendered as `substs` / `variables` / `defines` dicts instead of checks.
    kind: str = "autoconf"
    # Defaults targets read beneath the results (`default_deps`; the `deps` of an
    # autoconf_defaults target, which composes defaults).
    default_deps: list[Dep] = field(default_factory=list)
    default_deps_suffix: list[str] = field(default_factory=list)  # `+ NAME` list variables

    def add_dep(self, label: str, comment: str | None = None) -> None:
        _add_dep(self.deps, label, comment)

    def add_default_dep(self, label: str, comment: str | None = None) -> None:
        _add_dep(self.default_deps, label, comment)

    @property
    def is_empty(self) -> bool:
        return not self.checks and not self.selects and not self.deps and not self.default_deps

    def arm(self, label: str, family: str | None = None) -> list[CheckItem]:
        """The checks list of a `select()` arm.

        A new host-OS arm starts as a copy of the default arm: the default is
        "every other platform", and what was placed there without naming this
        OS holds on it as well.
        """
        family = family or select_family(label)
        arms = self.selects.setdefault(family, {})
        if label not in arms:
            seed = arms.get(DEFAULT_ARM, []) if family in PLATFORM_FAMILIES and label != DEFAULT_ARM else []
            arms[label] = [copy.deepcopy(item) for item in seed]
        return arms[label]

    @property
    def arm_lists(self) -> list[list[CheckItem]]:
        return [items for arms in self.selects.values() for items in arms.values()]


def _add_dep(deps: list[Dep], label: str, comment: str | None) -> None:
    for dep in deps:
        if dep.label == label:
            if comment and dep.comment and comment not in dep.comment:
                dep.comment = f"{dep.comment}, {comment}"
            elif comment and not dep.comment:
                dep.comment = comment
            return
    deps.append(Dep(label, comment))


@dataclass
class Rule:
    """Any other rule call: `kind(name = ..., attr = value, ...)`.

    `load_from` names the .bzl file (repo-relative, e.g.
    "//autoconf:autoconf_hdr.bzl") the kind is loaded from; None for natives.
    """

    kind: str
    name: str
    attrs: dict[str, Any] = field(default_factory=dict)  # rendered in attribute order
    comments: list[str] = field(default_factory=list)
    load_from: str | None = None
    load_symbol: str | None = None  # when the loaded symbol differs from `kind`


@dataclass
class Assign:
    """A top-level `NAME = value` (lists shared by several rules)."""

    name: str
    value: Any
    comments: list[str] = field(default_factory=list)


Statement = Union[Target, Rule, Assign, Raw]


@dataclass
class Package:
    """One emitted BUILD file."""

    path: str  # directory relative to the output root ("" for the root)
    docstring: str
    targets: list[Target] = field(default_factory=list)
    # Statements emitted after the `autoconf` targets (project mode).
    before: list[Statement] = field(default_factory=list)
    after: list[Statement] = field(default_factory=list)
    loads: list[str] = field(default_factory=list)  # extra `load(...)` lines (rules decisions bring in)
    notes: list[str] = field(default_factory=list)  # report lines

    def target(self, name: str) -> Target | None:
        for target in self.targets:
            if target.name == name:
                return target
        return None
