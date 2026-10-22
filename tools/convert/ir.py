"""Intermediate representation: what gets emitted as Starlark.

Deliberately close to the output: a `Call` is a Starlark call expression
with positional and keyword arguments (`checks.AC_SUBST("X", 1)`), a
`Target` is one `autoconf(...)` rule, a `Package` is one BUILD file.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field


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
    """The callee, namespace included (`checks.AC_SUBST`)."""
    args: list[AttrValue] = field(default_factory=list)
    """Positional arguments: scalars, lists, `Raw` text or nested calls."""
    kwargs: dict[str, AttrValue] = field(default_factory=dict)
    """Keyword arguments, rendered in `ordered_keys` order."""
    comments: list[str] = field(default_factory=list)
    """Emitted above the call."""
    returns_list: bool = False
    """Plural macros: concatenated with `+`."""
    from_decision: bool = False
    """Written in the decisions file, not translated from configure.ac."""


AttrValue = str | int | bool | None | Call | Raw | Sequence["AttrValue"] | Mapping[str, "AttrValue"]
"""What an attribute, a call argument or a top-level assignment holds: a Starlark literal (a
string, an int, a bool, `None`), a `Raw` expression, a nested `Call`, or a list or string-keyed
dict of these.  Closed: `emit.fmt` renders every kind and nothing else.  The containers are
spelled as the covariant `Sequence` / `Mapping` so a builder's `list[str]` or `dict[str, str]`
is one without a copy; what is built is always a `list` or a `dict`."""


def strings(value: AttrValue) -> list[str]:
    """The string items of a list value a builder reads back (a plural check's names, `requires`,
    `srcs`, `textual_hdrs`); an absent keyword (`None`) reads as the empty list.  The narrowing of
    `AttrValue` where the code needs the list's strings."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected a list of strings, got {value!r}")
    return [item for item in value if isinstance(item, str)]


@dataclass
class Dep:
    label: str
    comment: str | None = None


CheckItem = Call | Raw


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
        return ":" + name[: -len("_enabled")]
    return ":" + name.rsplit("_", 1)[0] if "_" in name else label


def family_of_labels(labels: Iterable[str]) -> str:
    """The family of a decided `select({...})`: that of its first non-default label, or the OS."""
    others = [label for label in labels if label != DEFAULT_ARM]
    return select_family(others[0]) if others else OS_FAMILY


@dataclass
class Target:
    name: str
    """The rule's `name`."""
    checks: list[CheckItem] = field(default_factory=list)
    """The unconditional checks, in order."""
    selects: dict[str, dict[str, list[CheckItem]]] = field(default_factory=dict)
    """Conditional checks, one `select({label: [...], "//conditions:default": [...]})` per
    family (see `select_family`)."""
    deps: list[Dep] = field(default_factory=list)
    """The `deps` labels, each with an optional trailing comment."""
    deps_suffix: list[str] = field(default_factory=list)
    """`+ NAME` list variables appended to deps."""
    comments: list[str] = field(default_factory=list)
    """Above the rule."""
    visibility: str = "//visibility:public"
    """The one `visibility` label."""
    loc: Loc | None = None
    """Where in configure.ac the target comes from, rendered as a comment with `--annotate`."""
    helper: bool = False
    """A shared-probe helper target; not in the aggregator."""
    attrs: dict[str, AttrValue] = field(default_factory=dict)
    """Other attributes (`cc_deps`, ...), rendered after the checks and deps."""
    kind: str = "autoconf"
    """"autoconf", or "autoconf_defaults" for a `_DEFAULTS` macro: literal values a probe may
    override, rendered as `substs` / `variables` / `defines` dicts instead of checks."""
    default_deps: list[Dep] = field(default_factory=list)
    """Defaults targets read beneath the results (`default_deps`; the `deps` of an
    autoconf_defaults target, which composes defaults)."""
    default_deps_suffix: list[str] = field(default_factory=list)
    """`+ NAME` list variables appended to default_deps."""

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
    """The rule function called (`cc_library`, `selects.config_setting_group`)."""
    name: str
    """The rule's `name`."""
    attrs: dict[str, AttrValue] = field(default_factory=dict)
    """Rendered in attribute order."""
    comments: list[str] = field(default_factory=list)
    """Above the rule."""
    load_from: str | None = None
    """The .bzl file the kind is loaded from; None for natives."""
    load_symbol: str | None = None
    """When the loaded symbol differs from `kind`."""


@dataclass
class Assign:
    """A top-level `NAME = value` (lists shared by several rules)."""

    name: str
    """The variable assigned."""
    value: AttrValue
    """The value, rendered by `emit.fmt`."""
    comments: list[str] = field(default_factory=list)
    """Above the assignment."""


@dataclass
class Each:
    """One rule call for every row of a table, as a list comprehension:
    `[kind(name = ..., ...) for <over>]`.  Attribute values are literals or
    `Raw` expressions over the loop variables."""

    kind: str
    """The rule function called for every row."""
    attrs: dict[str, AttrValue]
    """Rendered in attribute order; "name" among them."""
    over: str
    """The `for` clause, e.g. `name, default in BOOL_OPTIONS.items()`."""
    comments: list[str] = field(default_factory=list)
    """Above the comprehension."""
    load_from: str | None = None
    """The .bzl file the kind is loaded from; None for natives."""
    load_symbol: str | None = None
    """When the loaded symbol differs from `kind`."""


Statement = Target | Rule | Assign | Raw | Each


@dataclass
class Package:
    """One emitted BUILD file."""

    path: str
    """Directory relative to the output root ("" for the root)."""
    docstring: str
    """The file's opening docstring, or its `#` banner when it starts with one."""
    targets: list[Target] = field(default_factory=list)
    """The `autoconf` / `autoconf_defaults` targets."""
    before: list[Statement] = field(default_factory=list)
    """Statements emitted before the `autoconf` targets (project mode)."""
    after: list[Statement] = field(default_factory=list)
    """Statements emitted after the `autoconf` targets (project mode)."""
    loads: list[str] = field(default_factory=list)
    """Extra `load(...)` lines (rules decisions bring in)."""
    notes: list[str] = field(default_factory=list)
    """Report lines."""

    def target(self, name: str) -> Target | None:
        for target in self.targets:
            if target.name == name:
                return target
        return None
