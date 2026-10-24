"""Lower dataflow conditions to the rules' `requires`/`condition` strings and
`select()` arms.

The condition grammar (autoconf/private/checker/condition_evaluator.cc):
`!`, `&&`, `||`, parentheses, `NAME`, `NAME==VALUE`, `NAME!=VALUE`.  A
bare `NAME` is true when the check succeeded and its value is non-empty and
not `0`; probe cache variables therefore map `yes`/`no` to truthiness.

Host OS atoms cannot be expressed in that grammar; they become `select()`
arms keyed by `@platforms//os:*`, following the migration guide's table.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from tools.convert import dataflow as df

# One prime implicant over an ordered list of atoms: True / False per atom, None for don't care.
Implicant = tuple[bool | None, ...]

# `case $host_os` patterns -> Bazel constraint, with a representative
# `$host_os` string per constraint for evaluating the patterns.
OS_CONSTRAINTS: list[tuple[str, tuple[str, ...]]] = [
    # (constraint label, representative `$host_os` strings config.guess yields)
    # Cygwin is not one of Windows': Bazel builds Windows with MSVC or MinGW,
    # and gnulib keeps `cygwin*` apart from native Windows on purpose.
    ("@platforms//os:windows", ("mingw32", "mingw64", "windows")),
    ("@platforms//os:macos", ("darwin23.0.0",)),
    ("@platforms//os:linux", ("linux-gnu", "linux-musl")),
    ("@platforms//os:freebsd", ("freebsd14.0",)),
    ("@platforms//os:openbsd", ("openbsd7.5",)),
    ("@platforms//os:netbsd", ("netbsd10.0",)),
    # Other constraints (android, ios, haiku, qnx...) are not modeled: their
    # host_os strings would also match `linux*` / `darwin*` patterns and the
    # rules' conformance tests do not run there.  Patterns only they match
    # are reported as unmatched.
]
CPU_CONSTRAINTS: list[tuple[str, tuple[str, ...]]] = [
    # (constraint label, representative `$host_cpu` strings config.guess yields)
    ("@platforms//cpu:x86_64", ("x86_64", "amd64")),
    ("@platforms//cpu:x86_32", ("i686", "i586", "i486", "i386")),
    ("@platforms//cpu:aarch64", ("aarch64", "arm64")),
    ("@platforms//cpu:armv7", ("armv7l", "arm")),
    ("@platforms//cpu:ppc64le", ("powerpc64le",)),
    ("@platforms//cpu:ppc", ("powerpc", "powerpc64")),
    ("@platforms//cpu:riscv64", ("riscv64",)),
    ("@platforms//cpu:s390x", ("s390x",)),
    ("@platforms//cpu:mips64", ("mips64", "mips64el")),
    ("@platforms//cpu:wasm32", ("wasm32",)),
]
# `$GCC = yes` holds for every compiler Bazel drives but the MSVC-style ones.
NON_GNU_COMPILERS = ("@rules_cc//cc/compiler:msvc-cl", "@rules_cc//cc/compiler:clang-cl")
DEFAULT_ARM = "//conditions:default"
_ALLCAPS_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_REPLACE_RE = re.compile(r"(?:ac_cv_subst_)?REPLACE_[A-Z0-9_]+")
_ALL_HOSTS = [host for _, hosts in OS_CONSTRAINTS for host in hosts]
_ALL_CPUS = [cpu for _, cpus in CPU_CONSTRAINTS for cpu in cpus]
# The representative `$host_os` / `$host_cpu` a select arm stands for (`residual`).
_OS_REPRESENTATIVE = {label: hosts[0] for label, hosts in OS_CONSTRAINTS}
_CPU_REPRESENTATIVE = {label: cpus[0] for label, cpus in CPU_CONSTRAINTS}


class OptionTable(Protocol):
    """What `flag_arms` asks about a project's configure options (`translate.ProjectState`)."""

    def label(self, name: str, value: str) -> str:
        """The `config_setting` label of option `name` with `value`."""

    def values(self, name: str) -> set[str]:
        """The values other than yes/no the project compares option `name` against."""

    def default(self, name: str) -> bool:
        """Whether the option's bool_flag is on by default."""


@dataclass
class Lowered:
    """A condition as the rules see it."""

    expr: str | None
    """The condition in the rules' grammar; None when it is unconditional (TRUE) or could not be
    expressed (see `unknown`)."""
    unknown: str | None = None
    """Why the condition could not be expressed."""

    @property
    def ok(self) -> bool:
        return self.unknown is None


def expression(cond: df.Cond) -> Lowered:
    """Render a host-independent condition in the rules' grammar.

    Compound conditions are minimized first (the path conditions an `if`/
    `case` tree produces are usually far from their simplest form), and the
    shorter of the positive and the negated rendering is used.
    """
    if isinstance(cond, (df.And, df.Or, df.Not)) and not df.has_unknown(cond):
        atoms = df.atoms(cond)
        if 0 < len(atoms) <= 10:
            positive = _render_dnf(minimize(cond, atoms, True), atoms)
            negative = _render_dnf(minimize(cond, atoms, False), atoms)
            if positive is None:
                return Lowered(None, "never true")
            if negative is None:
                return Lowered(None)  # always true
            if positive.ok and negative.ok and negative.expr is not None:
                negated = f"!({negative.expr})" if not _atomic(negative.expr) else f"!{negative.expr}"
                if len(negated) < len(positive.expr or ""):
                    return Lowered(negated)
            if positive.ok:
                return positive
    return _expression(cond)


def minimize(cond: df.Cond, atoms: list[df.Cond], polarity: bool) -> list[Implicant] | None:
    """Prime-implicant cover (Quine-McCluskey) of `cond` (or its negation).

    Returns a list of implicants, each a tuple over the atoms with values
    True / False / None (don't care); `[]` means always false, None means
    always true.
    """
    n = len(atoms)
    minterms: list[Implicant] = []
    for bits in range(1 << n):
        values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
        if df.evaluate(cond, values) == polarity:
            minterms.append(tuple(bool(bits >> i & 1) for i in range(n)))
    if not minterms:
        return []
    if len(minterms) == 1 << n:
        return None
    # Combine terms that differ in one position until nothing combines.  Two
    # terms combine exactly when blanking that position makes them equal, so
    # each round buckets the terms by position and blanked form instead of
    # comparing every pair (1024 minterms of ten atoms made half a million
    # pairs a round).
    current = set(minterms)
    primes: set[Implicant] = set()
    while current:
        buckets: dict[tuple[int, Implicant], list[Implicant]] = {}
        for term in current:
            for k in range(n):
                if term[k] is not None:
                    buckets.setdefault((k, term[:k] + (None,) + term[k + 1 :]), []).append(term)
        combined: set[Implicant] = set()
        used: set[Implicant] = set()
        for (_, merged), terms in buckets.items():
            if len(terms) == 2:
                combined.add(merged)
                used.update(terms)
        primes |= current - used
        current = combined

    # Greedy cover of the minterms by the prime implicants.
    def covers(prime: Implicant, term: Implicant) -> bool:
        return all(p is None or p == t for p, t in zip(prime, term))

    remaining = set(minterms)
    chosen: list[Implicant] = []
    ordered = sorted(primes, key=lambda p: (-sum(1 for v in p if v is None), [2 if v is None else int(v) for v in p]))
    while remaining:
        best = max(ordered, key=lambda p: sum(1 for t in remaining if covers(p, t)))
        if not any(covers(best, t) for t in remaining):
            break
        chosen.append(best)
        remaining = {t for t in remaining if not covers(best, t)}
    return chosen


def equivalent(a: df.Cond, b: df.Cond) -> bool:
    """True when the two conditions agree on every valuation of their atoms."""
    if df.has_unknown(a) or df.has_unknown(b):
        return a == b
    atoms = df.atoms(df.And((a, b)))
    if len(atoms) > 12:
        return a == b
    for bits in range(1 << len(atoms)):
        values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
        if df.evaluate(a, values) != df.evaluate(b, values):
            return False
    return True


def _render_dnf(implicants: list[Implicant] | None, atoms: list[df.Cond]) -> Lowered | None:
    """The implicants as a disjunction of conjunctions: None when there are no implicants (the
    condition is always false), `Lowered(None)` for an always-true condition (`minimize` gave
    None), else the rendering, or the first atom's `Lowered` that could not be expressed."""
    if implicants is None:
        return Lowered(None)
    if not implicants:
        return None
    products: list[str] = []
    for implicant in implicants:
        factors: list[str] = []
        for atom, value in zip(atoms, implicant):
            if value is None:
                continue
            lowered = _expression(atom if value else df.Not(atom))
            if not lowered.ok:
                return lowered
            if lowered.expr is not None:  # an always-true factor adds nothing to the conjunction
                factors.append(lowered.expr)
        products.append(" && ".join(factors) if factors else "")
    if len(products) == 1:
        return Lowered(products[0])
    return Lowered(" || ".join(p if " && " not in p else f"({p})" for p in products))


def _expression(cond: df.Cond) -> Lowered:
    """Render a condition structurally (no minimization)."""
    if cond is df.TRUE:
        return Lowered(None)
    if cond is df.FALSE:
        return Lowered(None, "never true")
    if isinstance(cond, df.Unknown):
        return Lowered(None, cond.text)
    if isinstance(cond, df.HostOs):
        return Lowered(None, f"host_os in {'|'.join(cond.patterns)}")
    if isinstance(cond, df.HostCpu):
        return Lowered(None, f"host_cpu in {'|'.join(cond.patterns)}")
    if isinstance(cond, df.Gnuc):
        return Lowered(None, "GCC = yes")
    if isinstance(cond, df.Flag):
        return Lowered(None, f"--enable-{cond.name} given")
    if isinstance(cond, df.FlagValue):
        return Lowered(
            None, f"--enable-{cond.name}={cond.value}" if cond.value != "no" else f"--enable-{cond.name} not given"
        )
    if isinstance(cond, df.Truthy):
        return Lowered(cond.name)
    if isinstance(cond, df.Eq):
        # A probe's yes/no is its success (`test $ac_cv_func_x = yes`, `test $has_a_format = yes`
        # for a guessed M4_VARIABLE 1/0); an ALLCAPS substitution holds the words themselves
        # (gnulib's `test $HAVE_LIBGMP != yes`, where the port substitutes "no").
        by_value = cond.by_value or _ALLCAPS_RE.fullmatch(cond.name) is not None
        if cond.value in df.YES and not by_value:
            return Lowered(cond.name)
        if cond.value in df.NO and not by_value:
            return Lowered(f"!{cond.name}")
        if " " in cond.value or not cond.value:
            return Lowered(None, f"{cond.name} == {cond.value!r}")
        if cond.value == "1" and _REPLACE_RE.fullmatch(cond.name):
            # gnulib's REPLACE_* are "0 or non-zero": a newer gnulib answers 2 where an older
            # gnulib.mk tests `= 1` (REPLACE_REALLOC_FOR_REALLOC_POSIX), and the ports follow the
            # newer one.  `!= 0` holds across that skew; `== 1` would leave realloc.c out.
            return Lowered(f"{cond.name}!=0")
        return Lowered(f"{cond.name}=={cond.value}")
    if isinstance(cond, df.Glob):
        return Lowered(None, f"{cond.name} matches {cond.pattern}")
    if isinstance(cond, df.Not):
        inner = _expression(cond.cond)
        if not inner.ok:
            return inner
        if inner.expr is None:
            return Lowered(None, "never true")
        if _atomic(inner.expr):
            if inner.expr.startswith("!"):
                return Lowered(inner.expr[1:])
            if inner.expr.endswith("!=0"):
                return Lowered(inner.expr[: -len("!=0")] + "==0")
            return Lowered(f"!{inner.expr}")
        return Lowered(f"!({inner.expr})")
    if isinstance(cond, (df.And, df.Or)):
        parts: list[str] = []
        for sub in cond.conds:
            lowered = _expression(sub)
            if not lowered.ok:
                return lowered
            if lowered.expr is None:
                continue
            parts.append(lowered.expr)
        if not parts:
            return Lowered(None)
        joiner = " && " if isinstance(cond, df.And) else " || "
        wrapped = [p if _atomic(p) or _same_level(p, joiner) else f"({p})" for p in parts]
        return Lowered(joiner.join(wrapped))
    return Lowered(None, repr(cond))


def _atomic(expr: str) -> bool:
    return " " not in expr and "(" not in expr


def _same_level(expr: str, joiner: str) -> bool:
    other = " || " if joiner == " && " else " && "
    return other not in expr


def requires_list(cond: df.Cond) -> list[str] | None:
    """`requires = [...]`: one entry per top-level conjunct."""
    if cond is df.TRUE:
        return []
    if isinstance(cond, df.And):
        out: list[str] = []
        for sub in cond.conds:
            lowered = expression(sub)
            if not lowered.ok:
                return None
            if lowered.expr is not None and lowered.expr not in out:
                out.append(lowered.expr)
        return out
    lowered = expression(cond)
    if not lowered.ok:
        return None
    return [lowered.expr] if lowered.expr is not None else []


def os_arms(cond: df.Cond) -> dict[str, df.Cond]:
    """Split a condition with host OS atoms into `select()` arms.

    Returns `{constraint_label: residual condition}` for every constraint one
    of the patterns matches, plus the default arm with all atoms false.
    Conditions without OS atoms come back as `{DEFAULT_ARM: cond}`.
    """
    return _platform_arms(cond, df.host_os_atoms(cond), OS_CONSTRAINTS, df.substitute_host_os)


def cpu_arms(cond: df.Cond) -> dict[str, df.Cond]:
    """Split a condition with host CPU atoms into `select()` arms (see `os_arms`)."""
    return _platform_arms(cond, df.host_cpu_atoms(cond), CPU_CONSTRAINTS, df.substitute_host_cpu)


def _platform_arms(
    cond: df.Cond,
    atoms: Sequence[df.Cond],
    constraints: list[tuple[str, tuple[str, ...]]],
    substitute: Callable[[df.Cond, str | None], df.Cond],
) -> dict[str, df.Cond]:
    """`os_arms` / `cpu_arms` over `atoms` (the condition's atoms of that kind), `constraints` (the
    table of constraint labels and representative strings) and `substitute` (the evaluation of
    the atoms for one representative, None for none)."""
    if not atoms:
        return {DEFAULT_ARM: cond}
    arms: dict[str, df.Cond] = {}
    for label, representatives in constraints:
        # The first representative any atom matches decides the arm.
        for representative in representatives:
            if any(substitute(atom, representative) is df.TRUE for atom in atoms):
                arms[label] = substitute(cond, representative)
                break
    arms[DEFAULT_ARM] = substitute(cond, None)
    return arms


def compiler_arms(cond: df.Cond) -> dict[str, df.Cond]:
    """`$GCC = yes`: false for the MSVC-style compilers, true for every other."""
    arms = {label: df.substitute_gnuc(cond, False) for label in NON_GNU_COMPILERS}
    arms[DEFAULT_ARM] = df.substitute_gnuc(cond, True)
    return arms


def residual(family: str, label: str, cond: df.Cond) -> df.Cond:
    """`cond` on the platform a select arm stands for (an arm another condition opened)."""
    if label in _OS_REPRESENTATIVE:
        return df.substitute_host_os(cond, _OS_REPRESENTATIVE[label])
    if label in _CPU_REPRESENTATIVE:
        return df.substitute_host_cpu(cond, _CPU_REPRESENTATIVE[label])
    if label.startswith("@rules_cc//cc/compiler:"):
        return df.substitute_gnuc(cond, label not in NON_GNU_COMPILERS)
    return cond


def default_residual(cond: df.Cond) -> df.Cond:
    """`cond` on the default arm: no modeled OS or CPU matches, the compiler is GNU-like."""
    return df.substitute_gnuc(df.substitute_host_cpu(df.substitute_host_os(cond, None), None), True)


def platform_atoms(cond: df.Cond) -> bool:
    """Whether the condition tests the host OS, the host CPU or the compiler."""
    return bool(df.host_os_atoms(cond) or df.host_cpu_atoms(cond) or df.gnuc_atoms(cond))


def flag_arms(cond: df.Cond, options: OptionTable, conds: Sequence[df.Cond] = ()) -> dict[str, df.Cond] | None:
    """Split a condition on one configure option into `select()` arms.

    `options.label(name, value)` names the `config_setting` of the option with that value,
    `options.values(name)` the other values the project compares the option against, and
    `options.default(name)` whether the bool_flag is on by default.  Each arm is one configure
    invocation, the one that puts the option in that state by default: for an option off by
    default the enabled arm is `--enable-x` and the default arm the option not given; for one
    on by default the enabled arm is the option not given (`enable_x=yes` in its own not-given
    action) and the default arm `--disable-x`.  Conditions over two different options, or
    mixing options and platform atoms, are not split (None).  A condition the invocations all
    agree on (an option off by default compared against `no`: `--enable-x` and the option not
    given both differ from it) is no split: the one default arm, the option not registered.
    `conds` are the conditions the arms will carry when they are not `cond` itself (a decision
    list's, `cond` their disjunction): the invocations must agree on every one of them.
    """
    names = df.option_names(cond)
    if not names:
        return {DEFAULT_ARM: cond}
    if len(names) > 1 or platform_atoms(cond):
        return None
    (name,) = names
    on_by_default = bool(options.default(name))
    values = sorted(
        (set(options.values(name)) | {a.value for a in df.flag_atoms(cond) if isinstance(a, df.FlagValue)})
        - {"yes", "no"}
    )
    invocations: list[tuple[str, str | None]] = [
        ("yes", None if on_by_default else "yes"),
        *((value, value) for value in values),
        (DEFAULT_ARM, "no" if on_by_default else None),
    ]
    readings = [[df.substitute_option(c, name, given) for c in (conds or [cond])] for _, given in invocations]
    if all(reading == readings[0] for reading in readings[1:]):
        return {DEFAULT_ARM: df.substitute_option(cond, name, invocations[-1][1])}
    return {
        (DEFAULT_ARM if value == DEFAULT_ARM else options.label(name, value)): df.substitute_option(cond, name, given)
        for value, given in invocations
    }


def unmatched_patterns(cond: df.Cond) -> list[str]:
    """Patterns of the OS atoms that no constraint's representative matches."""
    out: list[str] = []
    for os_atom in df.host_os_atoms(cond):
        for pattern in os_atom.patterns:
            if not any(df.substitute_host_os(df.HostOs((pattern,)), host) is df.TRUE for host in _ALL_HOSTS):
                out.append(pattern)
    for cpu_atom in df.host_cpu_atoms(cond):
        for pattern in cpu_atom.patterns:
            if not any(df.substitute_host_cpu(df.HostCpu((pattern,)), cpu) is df.TRUE for cpu in _ALL_CPUS):
                out.append(pattern)
    return out


def guess_value(value: df.Value) -> str | None:
    """`yes`/`no` of a cross-compiling guess, or None."""
    if isinstance(value, df.Lit):
        if value.text in df.YES:
            return "yes"
        if value.text in df.NO:
            return "no"
    return None
