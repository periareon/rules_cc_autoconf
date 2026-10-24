"""Symbolic evaluation of a DEFUN body's shell.

Walks the `shparse` AST once, tracking a path condition and a symbolic
value per shell variable, and records

- the condition under which every macro-call placeholder executes (what
  becomes `requires = [...]` or a `select()` arm), and
- for every variable, the ordered list of `(condition, value)` assignments
  (what becomes an `AC_SUBST` / `M4_VARIABLE` with `condition =`).

Conditions are over *names*: a probe's cache variable, a shell variable
set by another module (`HAVE_FCHDIR`), or the host OS (`HostOs`).  Anything
the pass cannot follow turns into `Unknown`, never into a guess.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeGuard

from tools.convert import shparse as sh

# `test "x$var" = xyes` and `test x"$var" = xyes`: a literal prefix guards against empty values.
_PREFIXED_VAR_RE = re.compile(r'^"?([A-Za-z0-9_.-]+)"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?$')

# -- conditions ----------------------------------------------------------------


@dataclass(frozen=True)
class TRUE_T:
    def __repr__(self) -> str:
        return "TRUE"


@dataclass(frozen=True)
class FALSE_T:
    def __repr__(self) -> str:
        return "FALSE"


TRUE = TRUE_T()
FALSE = FALSE_T()


@dataclass(frozen=True)
class Truthy:
    """A probe or variable is "yes"/non-zero."""

    name: str


@dataclass(frozen=True)
class Eq:
    """A variable equals a literal.  `by_value`: the variable holds a literal a decisions file gave
    it, so a yes/no comparison stays a comparison (a probe's yes/no is its success)."""

    name: str
    value: str
    by_value: bool = False


@dataclass(frozen=True)
class Glob:
    """A variable matches a `case` pattern."""

    name: str
    pattern: str


@dataclass(frozen=True)
class HostOs:
    """`$host_os` (or `$host`) matches one of the patterns."""

    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Flag:
    """A configure option (`--enable-x` / `--with-x`) was given."""

    name: str


@dataclass(frozen=True)
class FlagValue:
    """A configure option has this value: "yes" for `--enable-x` / `--with-x`
    (or `=yes`), "no" when it was not given, any other text for `--with-x=text`."""

    name: str
    value: str


@dataclass(frozen=True)
class HostCpu:
    """`$host_cpu` (or the CPU of `$host`) matches one of the patterns."""

    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Gnuc:
    """The C compiler is GCC-compatible (`$GCC = yes`: gcc, clang, mingw)."""


_ALLCAPS_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_WORD_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
OPTVAL_PREFIX = "__rccac_optval_"  # the shell variable standing for an option's value


FLAG_COMMAND_RE = re.compile(r"^__RCCAC_FLAG_([A-Za-z0-9_]+)__$")
# AC_CACHE_VAL's cache-hit test: `if ${var+y}` (2.70+) or `test "${var+set}" = set`.
# A fresh configure run has no cache, so the commands always run.
_FATAL_CMD_RE = re.compile(r"^\s*(?:as_fn_error\b|exit\s+[1-9])")
_CACHE_HIT_RE = re.compile(r'^"?\$\{[A-Za-z_][A-Za-z0-9_]*\+(?:y|set)\}"?$')
_CACHE_HIT_CMD_RE = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\+:\}\s+false$")  # autoconf 2.69: `if ${var+:} false`
# The platform variables `_platform_condition` reads: the host triple's parts and AC_PROG_CC's
# verdict under its three names (`GCC` is "yes" or empty).
_HOST_VARS = ("host", "host_os", "host_cpu")
_GNUC_VARS = ("GCC", "ac_compiler_gnu", "ac_cv_c_compiler_gnu")


def flag_command(name: str) -> str:
    """The pseudo-command whose exit status models `--enable-NAME` being given."""
    return f"__RCCAC_FLAG_{name}__"


@dataclass(frozen=True)
class Not:
    cond: Cond


@dataclass(frozen=True)
class And:
    conds: tuple[Cond, ...]


@dataclass(frozen=True)
class Or:
    conds: tuple[Cond, ...]


@dataclass(frozen=True)
class Unknown:
    text: str


Cond = TRUE_T | FALSE_T | Truthy | Eq | Glob | HostOs | HostCpu | Gnuc | Flag | FlagValue | Not | And | Or | Unknown
# What a `decided` predicate says of one value: a truth value, a condition, or None when it
# cannot tell.
Verdict = Cond | bool | None


def conj(*conds: Cond) -> Cond:
    """Conjunction with the identities applied: constants folded, nested And flattened, duplicates
    removed; a single operand is returned as it is.
    """
    return _connective(conds, And, identity=TRUE, absorbing=FALSE)


def disj(*conds: Cond) -> Cond:
    """Disjunction with the identities applied: constants folded, nested Or flattened, duplicates
    removed; a single operand is returned as it is.
    """
    return _connective(conds, Or, identity=FALSE, absorbing=TRUE)


def _connective(conds: tuple[Cond, ...], cls: type[And | Or], *, identity: Cond, absorbing: Cond) -> Cond:
    """`conj` and `disj`: `identity` operands dropped, an `absorbing` one decides, nested `cls`
    flattened, duplicates removed, a single operand returned as it is."""
    flat: list[Cond] = []
    for cond in conds:
        if cond is identity:
            continue
        if cond is absorbing:
            return absorbing
        if isinstance(cond, cls):
            flat.extend(cond.conds)
        else:
            flat.append(cond)
    unique: list[Cond] = []
    for cond in flat:
        if cond not in unique:
            unique.append(cond)
    # `a && !a`: never (the option blocks' `enableval` assignments, given / not given); `a || !a`:
    # always (every later assignment of `enableval` covers both branches of its option).  Not for
    # an Unknown: it stands for a test the analysis could not read, not for a proposition.
    if any(neg(c) in unique for c in unique if not _unknown_either_way(c)):
        return absorbing
    if not unique:
        return identity
    if len(unique) == 1:
        return unique[0]
    return cls(tuple(unique))


def _unknown_either_way(cond: Cond) -> bool:
    return isinstance(cond, Unknown) or isinstance(cond, Not) and isinstance(cond.cond, Unknown)


def neg(cond: Cond) -> Cond:
    if cond is TRUE:
        return FALSE
    if cond is FALSE:
        return TRUE
    if isinstance(cond, Not):
        return cond.cond
    return Not(cond)


def has_unknown(cond: Cond) -> bool:
    if isinstance(cond, Unknown):
        return True
    if isinstance(cond, Not):
        return has_unknown(cond.cond)
    if isinstance(cond, (And, Or)):
        return any(has_unknown(c) for c in cond.conds)
    return False


def host_os_atoms(cond: Cond) -> list[HostOs]:
    if isinstance(cond, HostOs):
        return [cond]
    if isinstance(cond, Not):
        return host_os_atoms(cond.cond)
    if isinstance(cond, (And, Or)):
        out: list[HostOs] = []
        for c in cond.conds:
            out.extend(host_os_atoms(c))
        return out
    return []


def host_cpu_atoms(cond: Cond) -> list[HostCpu]:
    return _atoms_of_type(cond, HostCpu)


def gnuc_atoms(cond: Cond) -> list[Gnuc]:
    return _atoms_of_type(cond, Gnuc)


def _atoms_of_type[AtomT: Cond](cond: Cond, cls: type[AtomT]) -> list[AtomT]:
    """The distinct atoms of `cond` that are instances of `cls`, in order of appearance."""
    return [a for a in atoms(cond) if isinstance(a, cls)]


def substitute_host_cpu(cond: Cond, host_cpu: str | None) -> Cond:
    """Evaluate the `HostCpu` atoms for a concrete `$host_cpu` (None: matches nothing)."""

    def one(atom: Cond) -> Cond:
        if isinstance(atom, HostCpu):
            if host_cpu is None:
                return FALSE
            return TRUE if any(fnmatch.fnmatchcase(host_cpu, p) for p in atom.patterns) else FALSE
        return atom

    return _map_all(cond, one)


def substitute_gnuc(cond: Cond, gnuc: bool) -> Cond:
    return _map_all(cond, lambda a: (TRUE if gnuc else FALSE) if isinstance(a, Gnuc) else a)


def host_condition(pattern: str) -> Cond:
    """A `$host` pattern (`x86_64-*-linux*`, `*-apple-darwin*`, `i?86-*`) as
    CPU and OS atoms; the vendor field is ignored."""
    parts = pattern.split("-")
    if len(parts) >= 3:
        cpu, os_ = parts[0], "-".join(parts[2:])
    elif len(parts) == 2:
        cpu, os_ = parts
    else:
        cpu, os_ = "*", pattern
    return conj(TRUE if cpu in ("*", "") else HostCpu((cpu,)), TRUE if os_ in ("*", "") else HostOs((os_,)))


def _map_all(cond: Cond, fn: Callable[[Cond], Cond]) -> Cond:
    """`cond` with every atom replaced by `fn(atom)` (constants and connectives kept)."""
    if isinstance(cond, Not):
        return neg(_map_all(cond.cond, fn))
    if isinstance(cond, And):
        return conj(*(_map_all(c, fn) for c in cond.conds))
    if isinstance(cond, Or):
        return disj(*(_map_all(c, fn) for c in cond.conds))
    if cond is TRUE or cond is FALSE:
        return cond
    return fn(cond)


def substitute_host_os(cond: Cond, host_os: str | None) -> Cond:
    """Evaluate the `HostOs` atoms for a concrete `$host_os` (None: matches nothing)."""

    def one(atom: Cond) -> Cond:
        if isinstance(atom, HostOs):
            if host_os is None:
                return FALSE
            return TRUE if any(fnmatch.fnmatchcase(host_os, p) for p in atom.patterns) else FALSE
        return atom

    return _map_all(cond, one)


def map_atoms(cond: Cond, fn: Callable[[Truthy | Eq | Glob], Cond]) -> Cond:
    """`cond` with every variable atom (Truthy, Eq, Glob) replaced by `fn(atom)`."""
    return _map_all(cond, lambda atom: fn(atom) if isinstance(atom, (Truthy, Eq, Glob)) else atom)


def mentions(cond: Cond, name: str) -> bool:
    """True when an atom of `cond` tests the variable `name`."""
    if isinstance(cond, (Truthy, Eq, Glob)):
        return cond.name == name
    if isinstance(cond, Not):
        return mentions(cond.cond, name)
    if isinstance(cond, (And, Or)):
        return any(mentions(c, name) for c in cond.conds)
    return False


def substitute_empty(cond: Cond, name: str) -> Cond:
    """Evaluate the atoms over `name` as if the variable were empty."""
    return substitute_unset(cond, lambda other: other == name)


def substitute_unset(cond: Cond, is_unset: Callable[[str], bool]) -> Cond:
    """Evaluate the atoms over every variable `is_unset` accepts as if it
    were empty: what an unset shell variable is to configure."""
    if isinstance(cond, Truthy):
        return FALSE if is_unset(cond.name) else cond
    if isinstance(cond, Eq):
        return (TRUE if cond.value == "" else FALSE) if is_unset(cond.name) else cond
    if isinstance(cond, Glob):
        return (TRUE if fnmatch.fnmatchcase("", cond.pattern) else FALSE) if is_unset(cond.name) else cond
    if isinstance(cond, Not):
        return neg(substitute_unset(cond.cond, is_unset))
    if isinstance(cond, And):
        return conj(*(substitute_unset(c, is_unset) for c in cond.conds))
    if isinstance(cond, Or):
        return disj(*(substitute_unset(c, is_unset) for c in cond.conds))
    return cond


def flag_atoms(cond: Cond) -> list[Flag | FlagValue]:
    """The `Flag` and `FlagValue` atoms of a condition (configure options)."""
    return [a for a in atoms(cond) if isinstance(a, (Flag, FlagValue))]


def option_names(cond: Cond) -> list[str]:
    out: list[str] = []
    for atom in flag_atoms(cond):
        if atom.name not in out:
            out.append(atom.name)
    return out


def substitute_option(cond: Cond, name: str, value: str | None) -> Cond:
    """Evaluate the atoms of option `name` for one configure invocation: `value` is the value the
    option was given, None when it was not given.  Which invocations a select() arm stands for
    is `lower.flag_arms`'s business: an arm is the conditions' value over all of them."""

    def one(atom: Cond) -> Cond:
        if isinstance(atom, Flag) and atom.name == name:
            return TRUE if value is not None else FALSE
        if isinstance(atom, FlagValue) and atom.name == name:
            if value is None:
                return TRUE if atom.value == "no" else FALSE
            return TRUE if atom.value == value else FALSE
        return atom

    return _map_all(cond, one)


# -- values ----------------------------------------------------------------------


@dataclass(frozen=True)
class Lit:
    text: str


@dataclass(frozen=True)
class Probe:
    """The result of a check (a cache variable bound to a Bazel check name)."""

    name: str


@dataclass(frozen=True)
class Ref:
    """A variable this body never assigns (set by another module)."""

    name: str


@dataclass(frozen=True)
class UnknownValue:
    text: str


Value = Lit | Probe | Ref | UnknownValue


@dataclass
class Assignment:
    """One assignment of a shell variable."""

    cond: Cond
    """The path condition under which it runs."""
    value: Value
    """The value assigned."""
    text: str
    """The assignment as written."""


@dataclass
class Analysis:
    """What one symbolic pass over a body found."""

    guards: dict[int, Cond] = field(default_factory=dict)
    """Placeholder id -> the path condition under which the call runs."""
    assigns: dict[str, list[Assignment]] = field(default_factory=dict)
    """Variable -> its assignments, in order."""
    order: list[str] = field(default_factory=list)
    """Variables in first-assignment order."""
    unknowns: list[str] = field(default_factory=list)
    """Constructs the walk did not follow, as reported to the user."""
    placeholders: set[int] = field(default_factory=set)
    """The ids the caller put in the text."""
    evaluator: Evaluator | None = None
    """The pass itself, to evaluate further tests against the final state."""
    inherited: dict[str, list[Assignment]] = field(default_factory=dict)
    """Assignments made by the macros this body expands in place (translate's `_settle`), under the
    calls' conditions: what an AC_SUBST'd variable the body itself never assigns is set to."""
    program_literals: dict[str, str] = field(default_factory=dict)
    """The literal values the pass was seeded with for the variables the body's own program
    lookups (AC_CHECK_PROG, AC_PATH_PROG...) set: the decisions' stand-ins, which win over the
    lookups' own expansion.  The translator reads them back when a `$var` stands for a program."""

    def decisions(self, name: str) -> list[Assignment]:
        return self.assigns.get(name, [])


def _platform_condition(var: str, patterns: list[str]) -> Cond | None:
    """`$host_os`, `$host_cpu`, `$host` and `$GCC` are facts of the build
    platform and compiler, not shell state: their tests become platform atoms."""
    if var == "host_os":
        return HostOs(tuple(patterns))
    if var == "host_cpu":
        return HostCpu(tuple(patterns))
    if var == "host":
        return disj(*(host_condition(p) for p in patterns))
    if var in _GNUC_VARS:
        if all(p in YES for p in patterns):
            return Gnuc()
        if all(p in NO or p == "" for p in patterns):
            return neg(Gnuc())
        return Unknown(f"test ${var} = {'|'.join(patterns)}")
    return None


def _is_option_value(value: Value | None) -> TypeGuard[Ref]:
    """The value is a configure option's (`$__rccac_optval_x`)."""
    return isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX)


def _option_match(value: Ref, patterns: list[str]) -> Cond | None:
    if not _is_option_value(value):
        return None
    conds: list[Cond] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[") and pattern not in ("*yes", "yes*", "*yes*", "*no", "no*", "*no*"):
            return None
        conds.append(_option_value(value.name, pattern.strip("*")))
    return disj(*conds)


def _symbol_match(value: Probe | Ref, patterns: list[str]) -> Cond:
    """`case $var in patterns)` over a probe's result or another module's variable."""
    conds: list[Cond] = []
    for pattern in patterns:
        if pattern == "*":
            return TRUE
        if pattern in ("*yes", "yes", "yes*", "*yes*"):
            conds.append(Truthy(value.name))
        elif pattern in ("*no", "no", "no*", "*no*"):
            conds.append(neg(Truthy(value.name)))
        elif not any(ch in pattern for ch in "*?["):
            conds.append(Eq(value.name, pattern))
        else:
            conds.append(Glob(value.name, pattern))
    return disj(*conds)


def _option_value(optval: str, text: str) -> Cond:
    """`test "$with_x" = text` on an option's value."""
    name = optval[len(OPTVAL_PREFIX) :]
    if text in YES:
        return FlagValue(name, "yes")
    if text in NO:
        return FlagValue(name, "no")
    return FlagValue(name, text)


def atoms(cond: Cond) -> list[Cond]:
    """The distinct atoms of a condition, constants excluded, in order of appearance."""
    out: list[Cond] = []
    _atoms_of(cond, out)
    return out


def _atoms_of(cond: Cond, out: list[Cond]) -> None:
    if isinstance(cond, Not):
        _atoms_of(cond.cond, out)
    elif isinstance(cond, (And, Or)):
        for sub in cond.conds:
            _atoms_of(sub, out)
    elif cond is not TRUE and cond is not FALSE and cond not in out:
        out.append(cond)


def evaluate(cond: Cond, values: dict[Cond, bool]) -> bool:
    """The truth value of `cond` under `values`, a truth value per atom."""
    if cond is TRUE:
        return True
    if cond is FALSE:
        return False
    if isinstance(cond, Not):
        return not evaluate(cond.cond, values)
    if isinstance(cond, And):
        return all(evaluate(c, values) for c in cond.conds)
    if isinstance(cond, Or):
        return any(evaluate(c, values) for c in cond.conds)
    return values[cond]


def tautology(cond: Cond) -> bool:
    """True when `cond` holds for every valuation of its atoms (small conds only).

    An option's atoms are not independent: it is either not given, or given
    with exactly one value (one of those the condition names, or plain
    `yes`); those are the valuations enumerated for it."""
    if has_unknown(cond):
        return False
    names = option_names(cond)
    if names:
        name = names[0]
        values = {a.value for a in flag_atoms(cond) if isinstance(a, FlagValue) and a.name == name} | {"yes"}
        values.discard("no")
        return all(tautology(substitute_option(cond, name, value)) for value in [None, *sorted(values)])
    atom_list = atoms(cond)
    if len(atom_list) > 12:
        return False
    return all(
        evaluate(cond, {atom: bool(bits >> i & 1) for i, atom in enumerate(atom_list)})
        for bits in range(1 << len(atom_list))
    )


# -- the walk --------------------------------------------------------------------

# Cache variables gnulib sets from `--enable-cross-guesses`: their defaults.
CROSS_GUESS_DEFAULTS = {
    "gl_cross_guess_normal": "guessing no",
    "gl_cross_guess_inverted": "guessing yes",
    # The checks run against the target toolchain, as configure run natively
    # on the target would.
    "cross_compiling": "no",
    # configure's own PATH separator; `;` only on DJGPP and the like (make's
    # `test "$PATH_SEPARATOR" = ';'`).
    "PATH_SEPARATOR": ":",
}

# Values a probe cache variable takes; everything else is a plain string.
YES = {"yes", "guessing yes"}
NO = {"no", "guessing no"}


class Evaluator:
    """One symbolic pass; `bindings` seeds variables (probe results, defaults)."""

    def __init__(
        self,
        bindings: dict[str, Value] | None = None,
        by_value: set[str] | None = None,
        value_words: set[str] | None = None,
        fatal: set[int] | None = None,
        pinned: set[str] | None = None,
        unset_empty: Callable[[str], bool] | None = None,
    ) -> None:
        self.state: dict[str, Value | None] = dict(bindings or {})  # None: conditional/unknown
        # Whether a variable nothing bound before the body ran is empty on the paths the body
        # does not assign it (what an unset shell variable is to `test`); the caller knows which
        # variables the script around the body, or the environment, may have set.
        self.unset_empty = unset_empty
        # Variables whose binding stands whatever the body assigns them: the cache variable of a
        # compiler-option test (`pa_cv_CFLAGS__Wall=yes` before the probe that may reset it), which
        # the toolchain decides, not the shell around the probe.
        self.pinned: set[str] = set(pinned or ())
        # Placeholders of AC_MSG_ERROR: the paths reaching them end configure, so a variable
        # assigned on every other path counts as assigned on every path (`decided`).
        self.fatal: set[int] = set(fatal or ())
        self.fatal_paths: list[Cond] = []
        # Variables a decisions file gives a literal (`probe:gzip_cv_assembler` = "no"):
        # `test $var = yes` compares the value, where a probe's yes/no is its success.
        self.by_value: set[str] = set(by_value or ())
        # Variables other modules provide with the words GNU assigns (a port's M4_VARIABLE such
        # as gl_cv_have_unlimited_file_name_length): compared by value when this body does not
        # assign them itself; its own assignments (gl_INIT's `GL_GENERATE_X=false`) still decide.
        self.value_words: set[str] = set(value_words or ())
        self.branch_state: dict[str, tuple[Cond, Value]] = {}  # last conditional assignment
        self.path: Cond = TRUE  # path condition of the statement being evaluated
        self.analysis = Analysis()
        for name, value in CROSS_GUESS_DEFAULTS.items():
            self.state.setdefault(name, Lit(value))
        self.bound: frozenset[str] = frozenset(self.state)  # bound before the body ran

    def run(self, seq: sh.Seq) -> Analysis:
        self.seq(seq, TRUE)
        self.analysis.evaluator = self
        return self.analysis

    def condition_of(self, expr: sh.Expr, path: Cond = TRUE) -> Cond:
        """Evaluate a test against the state at the end of the body."""
        self.path = path
        return self.expr(expr)

    # statements

    def seq(self, seq: sh.Seq, path: Cond) -> None:
        for stmt in seq.stmts:
            self.stmt(stmt, path)

    def stmt(self, stmt: sh.Stmt, path: Cond) -> None:
        """Walk one statement under the path condition `path`: assignments record their value and
        condition, placeholders (macro calls) record `path` as their guard, if / case split the
        path, loops and constructs the parser did not follow are reported and their bodies walked
        under an Unknown condition.
        """
        self.path = path
        if isinstance(stmt, sh.Seq):
            self.seq(stmt, path)
        elif isinstance(stmt, sh.Placeholder):
            self.analysis.guards[stmt.node_id] = path
            if stmt.node_id in self.fatal:
                self.fatal_paths.append(path)
        elif isinstance(stmt, sh.Assign):
            self.assign(stmt, path)
        elif isinstance(stmt, sh.If):
            self.if_statement(stmt, path)
        elif isinstance(stmt, sh.Case):
            self.case_statement(stmt, path)
        elif isinstance(stmt, sh.For):
            self.analysis.unknowns.append(f"for {stmt.var} in ...")
            self.seq(stmt.body, conj(path, Unknown(f"for {stmt.var}")))
        elif isinstance(stmt, sh.Unknown):
            self.analysis.unknowns.append(stmt.text.strip().split("\n")[0])
        elif isinstance(stmt, sh.Cmd) and _FATAL_CMD_RE.match(stmt.text):
            # `as_fn_error $? "..."`: AC_MSG_ERROR already expanded (inside AS_IF, an else
            # branch): configure stops here, like a fatal placeholder.
            self.fatal_paths.append(path)
        # Cmd: no effect on the variables we track.

    def assign(self, stmt: sh.Assign, path: Cond) -> None:
        """Record an assignment in the variable's decision list as (path, value). An unconditional
        assignment also sets the current value; a conditional one voids it, so a later test
        consults the decision list (`decided`) instead of a single symbol.
        """
        value = self.value(stmt.value)
        entry = Assignment(path, value, f"{stmt.name}={stmt.value.text}")
        if stmt.name not in self.analysis.assigns:
            self.analysis.assigns[stmt.name] = []
            self.analysis.order.append(stmt.name)
        self.analysis.assigns[stmt.name].append(entry)
        if stmt.name in self.pinned:
            return
        if path is TRUE:
            self.state[stmt.name] = value
        else:
            # Conditionally assigned: the current value is no longer a single
            # symbol.  A later `test "$x" = ...` consults the decision list
            # (`decided`) instead of guessing.
            self.state[stmt.name] = None
            self.branch_state[stmt.name] = (path, value)

    def if_statement(self, stmt: sh.If, path: Cond) -> None:
        taken_before: Cond = FALSE
        for expr, body in stmt.branches:
            if expr is None:
                cond = neg(taken_before)
            else:
                this = self.expr(expr)
                cond = conj(neg(taken_before), this)
                taken_before = disj(taken_before, this)
            self.seq(body, conj(path, cond))

    def case_statement(self, stmt: sh.Case, path: Cond) -> None:
        """`case` arms in order: an arm's condition is its patterns matching the subject and no
        earlier arm having matched (first match wins).
        """
        subject_var = stmt.subject.single_var
        subject_literal = stmt.subject.literal
        taken_before: Cond = FALSE
        for patterns, body in stmt.items:
            this: Cond
            if subject_literal is not None:
                this = TRUE if any(fnmatch.fnmatchcase(subject_literal, p) for p in patterns) else FALSE
            elif subject_var is not None:
                this = self.match(subject_var, patterns)
            else:
                # Each arm its own unknown: whether `*/no/*` matches `/$a/$b/` is one question
                # per arm, so the default arm is not the complement of the first.
                this = Unknown(f"case {stmt.subject.text} in {'|'.join(patterns)}")
            cond = conj(neg(taken_before), this)
            taken_before = disj(taken_before, this)
            self.seq(body, conj(path, cond))

    # expressions

    def expr(self, expr: sh.Expr) -> Cond:
        """A shell condition as a Cond: `test` comparisons and unary tests through `test_eq` /
        `nonempty`, `!`, `&&` and `||`, a macro placeholder as its guard, a flag command as a
        Flag atom, the cache-hit idioms as constants; anything else is Unknown.
        """
        if isinstance(expr, sh.Not):
            return neg(self.expr(expr.expr))
        if isinstance(expr, sh.And):
            return conj(*(self.expr(e) for e in expr.exprs))
        if isinstance(expr, sh.Or):
            return disj(*(self.expr(e) for e in expr.exprs))
        if isinstance(expr, sh.TestEq):
            cond = self.test_eq(expr.lhs, expr.rhs)
            return neg(cond) if expr.negate else cond
        if isinstance(expr, sh.TestUnary):
            var = expr.arg.single_var
            if expr.op in ("-n", "-z") and var is not None:
                cond = self.nonempty(var)
                return neg(cond) if expr.op == "-z" else cond
            if expr.op in ("-n", "-z") and _CACHE_HIT_RE.match(expr.arg.text.strip()):
                return TRUE if expr.op == "-z" else FALSE  # `test ${var+y}`: no cache to hit
            return Unknown(f"test {expr.op} {expr.arg.text}")
        if isinstance(expr, sh.CmdExpr):
            if expr.placeholder is not None:
                self.analysis.guards.setdefault(expr.placeholder, TRUE)
                return Unknown(f"status of macro call {expr.placeholder}")
            text = expr.text.strip()
            flag = FLAG_COMMAND_RE.match(text)
            if flag:
                return Flag(flag.group(1))
            if _CACHE_HIT_RE.match(text) or _CACHE_HIT_CMD_RE.match(text):
                return FALSE
            command_var = sh.simple_var(text)
            if command_var is not None:
                # `if $GL_GENERATE_FOO_H; then`: gnulib keeps `true` / `false` in GL_GENERATE_*
                # and runs it.
                return self.compare(command_var, Lit("true"))
            if text in (":", "true"):
                return TRUE
            if text == "false":
                return FALSE
            return Unknown(text.split("\n")[0])
        return Unknown(getattr(expr, "text", "?"))

    def test_eq(self, lhs: sh.Word, rhs: sh.Word) -> Cond:
        """`test A = B` (and `!=`, `-eq`, `-ne`): the cache-hit idiom, `test "$A:$B" = x:y` as a
        conjunction, swapped operands, the `test "x$var" = xyes` prefix idiom, then `compare` on
        the variable; two literals fold to a constant.
        """
        if _CACHE_HIT_RE.match(lhs.text.strip()) and rhs.literal == "set":
            return FALSE  # the cache variable is never pre-set
        # `test "$A:$B" = false:0`: a conjunction of comparisons.
        if len(lhs.vars) > 1 and rhs.literal is not None and ":" in rhs.literal:
            parts = rhs.literal.split(":")
            if len(parts) == len(lhs.vars) and lhs.text.count("$") == len(lhs.vars):
                return conj(*(self.compare(var, Lit(part)) for var, part in zip(lhs.vars, parts)))
        var = lhs.single_var
        if var is None and rhs.single_var is not None and lhs.literal is not None:
            var, rhs = rhs.single_var, lhs  # `test yes = "$x"`
        if var is None and rhs.literal is not None:
            # `test "x$var" = xyes`: a literal prefix guards against empty values.
            prefixed = _PREFIXED_VAR_RE.match(lhs.text)
            if prefixed and len(lhs.vars) == 1 and rhs.literal.startswith(prefixed.group(1)):
                return self.compare(prefixed.group(2), Lit(rhs.literal[len(prefixed.group(1)) :]))
        if var is None:
            if lhs.literal is not None and rhs.literal is not None:
                return TRUE if lhs.literal == rhs.literal else FALSE
            return Unknown(f"test {lhs.text} = {rhs.text}")
        if rhs.literal is None:
            other = rhs.single_var
            if other is not None:
                return Unknown(f"test ${var} = ${other}")
            # `test "x$var" = xyes` style: strip a common literal prefix.
            return Unknown(f"test ${var} = {rhs.text}")
        return self.compare(var, Lit(rhs.literal))

    def compare(self, var: str, literal: Lit) -> Cond:
        """`$var = text`: the platform variables become platform atoms; a known literal folds to a
        constant; a probe or another module's variable becomes Truthy / Eq (an option's value a
        FlagValue); a conditionally assigned variable goes through `decided`.
        """
        text = literal.text
        platform = _platform_condition(var, [text])
        if platform is not None:
            return platform
        value = self.lookup(var)
        if value is None:
            if var in self.by_value:
                return Eq(var, text, by_value=True)
            decided = self.decided(var, lambda v: self.value_compare(var, v, text))
            if decided is not None:
                return decided
            return Unknown(f"test ${var} = {text}")
        verdict = self.value_compare(var, value, text)
        if verdict is None:
            return Unknown(f"test ${var} = {text}")
        return verdict if not isinstance(verdict, bool) else (TRUE if verdict else FALSE)

    def value_compare(self, var: str, value: Value, text: str) -> Verdict:
        """`$var = text` for one value the variable may hold: a literal folds to True / False; an
        option's value is a FlagValue; a probe or another module's variable is its success (Truthy)
        or its value (Eq); None when the value is not known.  The same resolution for a variable
        holding the value directly and for one it was copied into (nasm's PA_FIND_FUNC:
        `pa_find_func_found="$pa_cv_func___builtin_clz"`, then `test x"$pa_find_func_found" != xyes`)."""
        if isinstance(value, Lit):
            return value.text == text
        if var in self.by_value or (isinstance(value, (Probe, Ref)) and value.name in self.by_value):
            return Eq(value.name if isinstance(value, (Probe, Ref)) else var, text, by_value=True)
        if isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX):
            return _option_value(value.name, text)
        if isinstance(value, Ref) and (value.name in self.value_words or _ALLCAPS_RE.fullmatch(value.name)):
            # Another module's ALLCAPS variable holds words (gnulib's `test $HAVE_LIBGMP
            # != yes`, the port substituting "no"): compared by value, not as a probe's success.
            return Eq(value.name, text, by_value=True)
        if isinstance(value, (Probe, Ref)):
            if text in YES:
                return Truthy(value.name)
            if text in NO:
                return neg(Truthy(value.name))
            return Eq(value.name, text)
        return None

    def decided(self, var: str, predicate: Callable[[Value], Verdict]) -> Cond | None:
        """The condition under which a conditionally assigned variable's
        final value satisfies `predicate` (last assignment wins).

        Only when every assignment has a literal value and the assignments
        together cover every path; otherwise None (the value before the
        first assignment is not known here), unless nothing bound the variable
        before the body ran and `unset_empty` says so: then the paths no
        assignment covers hold the empty value (`excuse="disabled"` under one
        option, then `test x"$excuse" = x`).  Only for assignments under known
        conditions: with an opaque one in the list, the paths it leaves are as
        opaque as before.
        """
        # The macros the body expanded in place assigned it too (translate's `_settle` records
        # them in `inherited`, under the calls' conditions), after the body's own assignments: a
        # call follows the initialization it overrides (gmp's `want_cxx=no`, then
        # GMP_PROG_CXX_WORKS's `want_cxx=yes` under --enable-cxx; AM_CONDITIONAL(WANT_CXX) reads
        # both).
        decisions = self.analysis.decisions(var) + self.analysis.inherited.get(var, [])
        if not decisions:
            return None
        if (
            self.unset_empty is not None
            and var not in self.bound
            and self.unset_empty(var)
            and not any(has_unknown(d.cond) for d in decisions)
        ):
            decisions = [Assignment(TRUE, Lit(""), f"{var} unset")] + decisions
        verdicts: list[Cond | bool] = []
        for d in decisions:
            verdict = predicate(d.value)
            if verdict is None:
                return None
            verdicts.append(verdict)
        # Paths that stop configure count as covered: the ones over the atoms the assignments
        # already mention (the if/elif chain's own `else as_fn_error`), so the tautology check
        # stays small; an unrelated error path (or one over an unknown) adds nothing.
        own: list[Cond] = []
        for d in decisions:
            _atoms_of(d.cond, own)
        fatal = [path for path in self.fatal_paths if not has_unknown(path) and all(a in own for a in atoms(path))]
        if not tautology(disj(*(d.cond for d in decisions), *fatal)):
            return None
        result: Cond = FALSE
        for index, decision in enumerate(decisions):
            later = disj(*(d.cond for d in decisions[index + 1 :]))
            holds = conj(decision.cond, neg(later))
            verdict = verdicts[index]
            if isinstance(verdict, bool):
                if verdict:
                    result = disj(result, holds)
            else:  # a condition: the value is an option's (`gl_x=$enableval`)
                result = disj(result, conj(holds, verdict))
        return result

    def nonempty(self, var: str) -> Cond:
        """`test -n $var` / `test -z $var` (and a bare word used as a condition): the same
        resolution as `compare`, for emptiness.
        """
        if var in _HOST_VARS + _GNUC_VARS and var not in self.state:
            return TRUE if var in _HOST_VARS else Gnuc()
        value = self.lookup(var)
        if isinstance(value, Lit):
            return TRUE if value.text else FALSE
        if isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX):
            return TRUE  # an option's value is never empty once given
        if isinstance(value, (Probe, Ref)):
            return Truthy(value.name)
        if value is None:
            decided = self.decided(
                var,
                lambda v: (
                    bool(v.text)
                    if isinstance(v, Lit)
                    else TRUE
                    if _is_option_value(v)
                    else Truthy(v.name)
                    if isinstance(v, (Probe, Ref))
                    else None
                ),
            )
            if decided is not None:
                return decided
        return Unknown(f"test -n ${var}")

    def match(self, var: str, patterns: list[str]) -> Cond:
        """`case $var in patterns)`: the platform variables become platform atoms; a known literal
        folds to a constant; an option's value compares each pattern; a probe or another module's
        variable becomes Truthy / Eq / Glob; a conditionally assigned variable goes through
        `decided`.
        """
        if "*" in patterns:
            return TRUE
        platform = _platform_condition(var, patterns)
        if platform is not None:
            return platform
        value = self.lookup(var)
        if isinstance(value, Lit):
            return TRUE if any(fnmatch.fnmatchcase(value.text, p) for p in patterns) else FALSE
        if _is_option_value(value):
            matched = _option_match(value, patterns)
            return matched if matched is not None else Unknown(f"case ${var} in {'|'.join(patterns)}")
        if value is None:
            decided = self.decided(
                var,
                lambda v: (
                    any(fnmatch.fnmatchcase(v.text, p) for p in patterns)
                    if isinstance(v, Lit)
                    else _option_match(v, patterns)
                    if _is_option_value(v)
                    else _symbol_match(v, patterns)
                    if isinstance(v, (Probe, Ref))
                    else None
                ),
            )
            if decided is not None:
                return decided
        if isinstance(value, (Probe, Ref)):
            return _symbol_match(value, patterns)
        return Unknown(f"case ${var} in {'|'.join(patterns)}")

    # values

    def lookup(self, var: str) -> Value | None:
        if var in self.state:
            value = self.state[var]
            if value is None:
                # `enableval=yes` a few lines up in the same branch: the
                # value assigned under a path that implies the current one.
                branch = self.branch_state.get(var)
                if branch is not None and self._on_path(branch[0]):
                    return branch[1]
            return value
        # A variable only an inlined macro assigned so far, conditionally (settled into
        # `branch_state` without a binding): known on the paths that assignment covers.
        branch = self.branch_state.get(var)
        if branch is not None and self._on_path(branch[0]):
            return branch[1]
        return Ref(var)

    def _on_path(self, cond: Cond) -> bool:
        """Whether `cond` holds wherever the current path does: the very same path (two statements
        of one branch, whatever opaque conditions the path carries) or one it implies."""
        return cond == self.path or tautology(disj(neg(self.path), cond))

    def value(self, word: sh.Word) -> Value:
        if word.unknown:
            return UnknownValue(word.text)
        if word.literal is not None:
            return Lit(word.literal)
        var = word.single_var
        if var is not None:
            value = self.lookup(var)
            return value if value is not None else UnknownValue(word.text)
        # `FUSE_CFLAGS="$FUSE_CFLAGS -DFUSE_USE_VERSION=32"` after `FUSE_CFLAGS=""`: a word whose
        # every variable holds a known literal is that literal spelled out.
        pieces: dict[str, str] = {}
        for name in word.vars:
            bound = self.lookup(name)
            if not isinstance(bound, Lit):
                return UnknownValue(word.text)
            pieces[name] = bound.text
        text = _WORD_VAR_RE.sub(lambda m: pieces.get(m.group(1) or m.group(2), m.group(0)), word.text)
        return Lit(sh.unquote_word(text))
