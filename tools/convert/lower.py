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

from dataclasses import dataclass

from tools.convert import dataflow as df

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
_ALL_HOSTS = [host for _, hosts in OS_CONSTRAINTS for host in hosts]
_ALL_CPUS = [cpu for _, cpus in CPU_CONSTRAINTS for cpu in cpus]


@dataclass
class Lowered:
    """A condition as the rules see it."""

    expr: str | None  # None: unconditional (TRUE) or inexpressible (see `unknown`)
    unknown: str | None = None  # why it could not be expressed

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
        atoms = _atoms(cond)
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


def _atoms(cond: df.Cond) -> list[df.Cond]:
    """The distinct atoms of a condition, constants excluded."""
    out: list[df.Cond] = []

    def visit(c: df.Cond) -> None:
        if isinstance(c, df.Not):
            visit(c.cond)
        elif isinstance(c, (df.And, df.Or)):
            for sub in c.conds:
                visit(sub)
        elif c is not df.TRUE and c is not df.FALSE and c not in out:
            out.append(c)

    visit(cond)
    return out


def _evaluate(cond: df.Cond, values: dict) -> bool:
    if cond is df.TRUE:
        return True
    if cond is df.FALSE:
        return False
    if isinstance(cond, df.Not):
        return not _evaluate(cond.cond, values)
    if isinstance(cond, df.And):
        return all(_evaluate(c, values) for c in cond.conds)
    if isinstance(cond, df.Or):
        return any(_evaluate(c, values) for c in cond.conds)
    return values[cond]


def minimize(cond: df.Cond, atoms: list[df.Cond], polarity: bool) -> list[tuple] | None:
    """Prime-implicant cover (Quine-McCluskey) of `cond` (or its negation).

    Returns a list of implicants, each a tuple over the atoms with values
    True / False / None (don't care); `[]` means always false, None means
    always true.
    """
    n = len(atoms)
    minterms = []
    for bits in range(1 << n):
        values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
        if _evaluate(cond, values) == polarity:
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
    primes: set[tuple] = set()
    while current:
        buckets: dict[tuple[int, tuple], list[tuple]] = {}
        for term in current:
            for k in range(n):
                if term[k] is not None:
                    buckets.setdefault((k, term[:k] + (None,) + term[k + 1:]), []).append(term)
        combined: set[tuple] = set()
        used: set[tuple] = set()
        for (_, merged), terms in buckets.items():
            if len(terms) == 2:
                combined.add(merged)
                used.update(terms)
        primes |= current - used
        current = combined
    # Greedy cover of the minterms by the prime implicants.
    def covers(prime: tuple, term: tuple) -> bool:
        return all(p is None or p == t for p, t in zip(prime, term))

    remaining = set(minterms)
    chosen: list[tuple] = []
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
    atoms = _atoms(df.And((a, b)))
    if len(atoms) > 12:
        return a == b
    for bits in range(1 << len(atoms)):
        values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
        if _evaluate(a, values) != _evaluate(b, values):
            return False
    return True


def _render_dnf(implicants: list[tuple] | None, atoms: list[df.Cond]) -> Lowered | None:
    """`None` for always-true; `Lowered(None, ...)`... see callers."""
    if implicants is None:
        return Lowered(None)
    if not implicants:
        return None
    products = []
    for implicant in implicants:
        factors = []
        for atom, value in zip(atoms, implicant):
            if value is None:
                continue
            lowered = _expression(atom if value else df.Not(atom))
            if not lowered.ok:
                return lowered
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
        return Lowered(None, f"--enable-{cond.name}={cond.value}" if cond.value != "no" else f"--enable-{cond.name} not given")
    if isinstance(cond, df.Truthy):
        return Lowered(cond.name)
    if isinstance(cond, df.Eq):
        if cond.value in df.YES:
            return Lowered(cond.name)
        if cond.value in df.NO:
            return Lowered(f"!{cond.name}")
        if " " in cond.value or not cond.value:
            return Lowered(None, f"{cond.name} == {cond.value!r}")
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
            return Lowered(f"!{inner.expr}")
        return Lowered(f"!({inner.expr})")
    if isinstance(cond, (df.And, df.Or)):
        parts = []
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
        out = []
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
    atoms = df.host_os_atoms(cond)
    if not atoms:
        return {DEFAULT_ARM: cond}
    arms: dict[str, df.Cond] = {}
    for label, hosts in OS_CONSTRAINTS:
        # The first representative any atom matches decides the arm.
        for host in hosts:
            if any(df.substitute_host_os(atom, host) is df.TRUE for atom in atoms):
                arms[label] = df.substitute_host_os(cond, host)
                break
    arms[DEFAULT_ARM] = df.substitute_host_os(cond, None)
    return arms


def cpu_arms(cond: df.Cond) -> dict[str, df.Cond]:
    """Split a condition with host CPU atoms into `select()` arms (see `os_arms`)."""
    atoms = df.host_cpu_atoms(cond)
    if not atoms:
        return {DEFAULT_ARM: cond}
    arms: dict[str, df.Cond] = {}
    for label, cpus in CPU_CONSTRAINTS:
        for cpu in cpus:
            if any(df.substitute_host_cpu(atom, cpu) is df.TRUE for atom in atoms):
                arms[label] = df.substitute_host_cpu(cond, cpu)
                break
    arms[DEFAULT_ARM] = df.substitute_host_cpu(cond, None)
    return arms


def compiler_arms(cond: df.Cond) -> dict[str, df.Cond]:
    """`$GCC = yes`: false for the MSVC-style compilers, true for every other."""
    arms = {label: df.substitute_gnuc(cond, False) for label in NON_GNU_COMPILERS}
    arms[DEFAULT_ARM] = df.substitute_gnuc(cond, True)
    return arms


def residual(family: str, label: str, cond: df.Cond) -> df.Cond:
    """`cond` on the platform a select arm stands for (an arm another condition opened)."""
    for constraint, hosts in OS_CONSTRAINTS:
        if constraint == label:
            return df.substitute_host_os(cond, hosts[0])
    for constraint, cpus in CPU_CONSTRAINTS:
        if constraint == label:
            return df.substitute_host_cpu(cond, cpus[0])
    if label.startswith("@rules_cc//cc/compiler:"):
        return df.substitute_gnuc(cond, label not in NON_GNU_COMPILERS)
    return cond


def default_residual(cond: df.Cond) -> df.Cond:
    """`cond` on the default arm: no modeled OS or CPU matches, the compiler is GNU-like."""
    return df.substitute_gnuc(df.substitute_host_cpu(df.substitute_host_os(cond, None), None), True)


def platform_atoms(cond: df.Cond) -> bool:
    """Whether the condition tests the host OS, the host CPU or the compiler."""
    return bool(df.host_os_atoms(cond) or df.host_cpu_atoms(cond) or df.gnuc_atoms(cond))


def flag_arms(cond: df.Cond, options) -> dict[str, df.Cond] | None:
    """Split a condition on one configure option into `select()` arms.

    `options.label(name, value)` names the `config_setting` of the option
    with that value ("yes": given, `--enable-x`), and `options.values(name)`
    the other values the project compares the option against; the default
    arm has the option not given.  Conditions over two different options, or
    mixing options and platform atoms, are not split (None).
    """
    names = df.option_names(cond)
    if not names:
        return {DEFAULT_ARM: cond}
    if len(names) > 1 or platform_atoms(cond):
        return None
    (name,) = names
    values = ["yes"] + sorted((set(options.values(name)) | {a.value for a in df.flag_atoms(cond)
                                                           if isinstance(a, df.FlagValue)}) - {"yes", "no"})
    arms = {options.label(name, value): df.substitute_option(cond, name, value) for value in values}
    arms[DEFAULT_ARM] = df.substitute_option(cond, name, None)
    return arms


def unmatched_patterns(cond: df.Cond) -> list[str]:
    """Patterns of the OS atoms that no constraint's representative matches."""
    out = []
    for atom in df.host_os_atoms(cond):
        for pattern in atom.patterns:
            if not any(df.substitute_host_os(df.HostOs((pattern,)), host) is df.TRUE
                       for host in _ALL_HOSTS):
                out.append(pattern)
    for atom in df.host_cpu_atoms(cond):
        for pattern in atom.patterns:
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
