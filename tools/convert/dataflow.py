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
from dataclasses import dataclass, field
from typing import Union

from tools.convert import shparse as sh

_PREFIXED_VAR_RE = re.compile(r'^"?([A-Za-z0-9_.-]+)\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?$')

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
    """A variable equals a literal."""

    name: str
    value: str


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


OPTVAL_PREFIX = "__rccac_optval_"  # the shell variable standing for an option's value


FLAG_COMMAND_RE = re.compile(r"^__RCCAC_FLAG_([A-Za-z0-9_]+)__$")
# AC_CACHE_VAL's cache-hit test: `if ${var+y}` (2.70+) or `test "${var+set}" = set`.
# A fresh configure run has no cache, so the commands always run.
_CACHE_HIT_RE = re.compile(r'^"?\$\{[A-Za-z_][A-Za-z0-9_]*\+(?:y|set)\}"?$')
_CACHE_HIT_CMD_RE = re.compile(r'^\$\{[A-Za-z_][A-Za-z0-9_]*\+:\}\s+false$')  # autoconf 2.69: `if ${var+:} false`
_COMMAND_VAR_RE = re.compile(r'^"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?$')  # `if $GL_GENERATE_FOO_H; then`: true or false


def flag_command(name: str) -> str:
    """The pseudo-command whose exit status models `--enable-NAME` being given."""
    return f"__RCCAC_FLAG_{name}__"


@dataclass(frozen=True)
class Not:
    cond: "Cond"


@dataclass(frozen=True)
class And:
    conds: tuple["Cond", ...]


@dataclass(frozen=True)
class Or:
    conds: tuple["Cond", ...]


@dataclass(frozen=True)
class Unknown:
    text: str


Cond = Union[TRUE_T, FALSE_T, Truthy, Eq, Glob, HostOs, Flag, Not, And, Or, Unknown]


def conj(*conds: Cond) -> Cond:
    """Conjunction with the identities applied: constants folded, nested And flattened, duplicates
    removed; a single operand is returned as it is.
    """
    flat: list[Cond] = []
    for cond in conds:
        if cond is TRUE:
            continue
        if cond is FALSE:
            return FALSE
        if isinstance(cond, And):
            flat.extend(cond.conds)
        else:
            flat.append(cond)
    unique: list[Cond] = []
    for cond in flat:
        if cond not in unique:
            unique.append(cond)
    if not unique:
        return TRUE
    if len(unique) == 1:
        return unique[0]
    return And(tuple(unique))


def disj(*conds: Cond) -> Cond:
    """Disjunction with the identities applied: constants folded, nested Or flattened, duplicates
    removed; a single operand is returned as it is.
    """
    flat: list[Cond] = []
    for cond in conds:
        if cond is FALSE:
            continue
        if cond is TRUE:
            return TRUE
        if isinstance(cond, Or):
            flat.extend(cond.conds)
        else:
            flat.append(cond)
    unique: list[Cond] = []
    for cond in flat:
        if cond not in unique:
            unique.append(cond)
    if not unique:
        return FALSE
    if len(unique) == 1:
        return unique[0]
    return Or(tuple(unique))


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
    out: list = []
    _atoms_of(cond, out)
    return [a for a in out if isinstance(a, HostCpu)]


def gnuc_atoms(cond: Cond) -> list[Gnuc]:
    out: list = []
    _atoms_of(cond, out)
    return [a for a in out if isinstance(a, Gnuc)]


def substitute_host_cpu(cond: Cond, host_cpu: str | None) -> Cond:
    """Evaluate the `HostCpu` atoms for a concrete `$host_cpu` (None: matches nothing)."""
    def one(atom):
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


def _map_all(cond: Cond, fn) -> Cond:
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
    if isinstance(cond, HostOs):
        if host_os is None:
            return FALSE
        return TRUE if any(fnmatch.fnmatchcase(host_os, p) for p in cond.patterns) else FALSE
    if isinstance(cond, Not):
        return neg(substitute_host_os(cond.cond, host_os))
    if isinstance(cond, And):
        return conj(*(substitute_host_os(c, host_os) for c in cond.conds))
    if isinstance(cond, Or):
        return disj(*(substitute_host_os(c, host_os) for c in cond.conds))
    return cond


def map_atoms(cond: Cond, fn) -> Cond:
    """`cond` with every variable atom (Truthy, Eq, Glob) replaced by `fn(atom)`."""
    if isinstance(cond, (Truthy, Eq, Glob)):
        return fn(cond)
    if isinstance(cond, Not):
        return neg(map_atoms(cond.cond, fn))
    if isinstance(cond, And):
        return conj(*(map_atoms(c, fn) for c in cond.conds))
    if isinstance(cond, Or):
        return disj(*(map_atoms(c, fn) for c in cond.conds))
    return cond


def rename_vars(cond: Cond, rename) -> Cond:
    """`cond` with every variable an atom tests renamed by `rename(name)`."""
    def one(atom):
        if isinstance(atom, Truthy):
            return Truthy(rename(atom.name))
        if isinstance(atom, Eq):
            return Eq(rename(atom.name), atom.value)
        return Glob(rename(atom.name), atom.pattern)
    return map_atoms(cond, one)


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


def substitute_unset(cond: Cond, is_unset) -> Cond:
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


def flag_atoms(cond: Cond) -> list:
    """The `Flag` and `FlagValue` atoms of a condition (configure options)."""
    out: list = []
    _atoms_of(cond, out)
    return [a for a in out if isinstance(a, (Flag, FlagValue))]


def option_names(cond: Cond) -> list[str]:
    out: list[str] = []
    for atom in flag_atoms(cond):
        if atom.name not in out:
            out.append(atom.name)
    return out


def substitute_option(cond: Cond, name: str, value: str | None) -> Cond:
    """Evaluate the atoms of option `name` for a configuration: `value` is the
    option's value, None when it was not given (`FlagValue(name, "no")`)."""
    def one(atom):
        if isinstance(atom, Flag) and atom.name == name:
            return TRUE if value is not None else FALSE
        if isinstance(atom, FlagValue) and atom.name == name:
            if value is None:
                return TRUE if atom.value == "no" else FALSE
            return TRUE if atom.value == value else FALSE
        return atom
    return _map_all(cond, one)


def substitute_flag(cond: Cond, name: str, value: bool) -> Cond:
    """Evaluate the atoms of option `name` as given (`--enable-x`) or not."""
    return substitute_option(cond, name, "yes" if value else None)


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


Value = Union[Lit, Probe, Ref, UnknownValue]


@dataclass
class Assignment:
    cond: Cond
    value: Value
    text: str  # the assignment as written


@dataclass
class Analysis:
    guards: dict[int, Cond] = field(default_factory=dict)  # placeholder id -> path condition
    assigns: dict[str, list[Assignment]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)  # variables in first-assignment order
    unknowns: list[str] = field(default_factory=list)  # constructs that were not followed
    placeholders: set[int] = field(default_factory=set)  # ids the caller put in the text
    evaluator: "Evaluator | None" = None  # to evaluate further tests against the final state

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
    if var == "GCC":
        if all(p in YES for p in patterns):
            return Gnuc()
        if all(p in NO or p == "" for p in patterns):
            return neg(Gnuc())
        return Unknown(f"test $GCC = {'|'.join(patterns)}")
    return None


def _is_option_value(value) -> bool:
    return isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX)


def _option_verdict(value, text: str):
    return _option_value(value.name, text) if _is_option_value(value) else None


def _option_match(value, patterns: list[str]):
    if not _is_option_value(value):
        return None
    conds = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?[") and pattern not in ("*yes", "yes*", "*yes*", "*no", "no*", "*no*"):
            return None
        conds.append(_option_value(value.name, pattern.strip("*")))
    return disj(*conds)


def _option_value(optval: str, text: str) -> Cond:
    """`test "$with_x" = text` on an option's value."""
    name = optval[len(OPTVAL_PREFIX):]
    if text in YES:
        return FlagValue(name, "yes")
    if text in NO:
        return FlagValue(name, "no")
    return FlagValue(name, text)


def _atoms_of(cond: Cond, out: list) -> None:
    if isinstance(cond, Not):
        _atoms_of(cond.cond, out)
    elif isinstance(cond, (And, Or)):
        for sub in cond.conds:
            _atoms_of(sub, out)
    elif cond is not TRUE and cond is not FALSE and cond not in out:
        out.append(cond)


def _evaluate(cond: Cond, values: dict) -> bool:
    if cond is TRUE:
        return True
    if cond is FALSE:
        return False
    if isinstance(cond, Not):
        return not _evaluate(cond.cond, values)
    if isinstance(cond, And):
        return all(_evaluate(c, values) for c in cond.conds)
    if isinstance(cond, Or):
        return any(_evaluate(c, values) for c in cond.conds)
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
    atoms: list = []
    _atoms_of(cond, atoms)
    if len(atoms) > 12:
        return False
    return all(_evaluate(cond, {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)})
               for bits in range(1 << len(atoms)))


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

    def __init__(self, bindings: dict[str, Value] | None = None) -> None:
        self.state: dict[str, Value | None] = dict(bindings or {})  # None: conditional/unknown
        self.branch_state: dict[str, tuple[Cond, Value]] = {}  # last conditional assignment
        self.path: Cond = TRUE  # path condition of the statement being evaluated
        self.analysis = Analysis()
        for name, value in CROSS_GUESS_DEFAULTS.items():
            self.state.setdefault(name, Lit(value))

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
        # Cmd: no effect on the variables we track.

    def assign(self, stmt: sh.Assign, path: Cond) -> None:
        """Record an assignment in the variable's decision list as (path, value). An unconditional
        assignment also sets the current value; a conditional one voids it, so a later test
        consults the decision list (`decided`) instead of a single symbol.
        """
        value = self.value(stmt.value, path)
        entry = Assignment(path, value, f"{stmt.name}={stmt.value.text}")
        if stmt.name not in self.analysis.assigns:
            self.analysis.assigns[stmt.name] = []
            self.analysis.order.append(stmt.name)
        self.analysis.assigns[stmt.name].append(entry)
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
            if subject_literal is not None:
                this = TRUE if any(fnmatch.fnmatchcase(subject_literal, p) for p in patterns) else FALSE
            elif subject_var is not None:
                this = self.match(subject_var, patterns)
            else:
                this = Unknown(f"case {stmt.subject.text}")
            cond = conj(neg(taken_before), this)
            taken_before = disj(taken_before, this)
            self.seq(body, conj(path, cond))

    # expressions

    def expr(self, expr: sh.Expr) -> Cond:  # noqa: C901
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
            flag = FLAG_COMMAND_RE.match(expr.text.strip())
            if flag:
                return Flag(flag.group(1))
            if _CACHE_HIT_RE.match(expr.text.strip()) or _CACHE_HIT_CMD_RE.match(expr.text.strip()):
                return FALSE
            command_var = _COMMAND_VAR_RE.match(expr.text.strip())
            if command_var:
                # gnulib keeps `true` / `false` in GL_GENERATE_* and runs it.
                return self.compare(command_var.group(1), Lit("true"))
            if expr.text.strip() in (":", "true"):
                return TRUE
            if expr.text.strip() == "false":
                return FALSE
            return Unknown(expr.text.strip().split("\n")[0])
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
                return self.compare(prefixed.group(2), Lit(rhs.literal[len(prefixed.group(1)):]))
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
        if isinstance(value, Lit):
            return TRUE if value.text == text else FALSE
        if isinstance(value, Probe):
            if text in YES:
                return Truthy(value.name)
            if text in NO:
                return neg(Truthy(value.name))
            return Eq(value.name, text)
        if isinstance(value, Ref):
            if value.name.startswith(OPTVAL_PREFIX):
                return _option_value(value.name, text)
            if text in YES:
                return Truthy(value.name)
            if text in NO:
                return neg(Truthy(value.name))
            return Eq(value.name, text)
        if value is None:
            decided = self.decided(var, lambda v: v.text == text if isinstance(v, Lit) else _option_verdict(v, text))
            if decided is not None:
                return decided
        return Unknown(f"test ${var} = {text}")

    def decided(self, var: str, predicate) -> Cond | None:
        """The condition under which a conditionally assigned variable's
        final value satisfies `predicate` (last assignment wins).

        Only when every assignment has a literal value and the assignments
        together cover every path; otherwise None (the value before the
        first assignment is not known here).
        """
        decisions = self.analysis.decisions(var)
        if not decisions:
            return None
        verdicts = [predicate(d.value) for d in decisions]
        if any(v is None for v in verdicts):
            return None
        if not tautology(disj(*(d.cond for d in decisions))):
            return None
        result: Cond = FALSE
        for index, decision in enumerate(decisions):
            later = disj(*(d.cond for d in decisions[index + 1 :]))
            holds = conj(decision.cond, neg(later))
            verdict = verdicts[index]
            if verdict is True:
                result = disj(result, holds)
            elif verdict is not False:  # a condition: the value is an option's (`gl_x=$enableval`)
                result = disj(result, conj(holds, verdict))
        return result

    def nonempty(self, var: str) -> Cond:
        """`test -n $var` / `test -z $var` (and a bare word used as a condition): the same
        resolution as `compare`, for emptiness.
        """
        if var in ("host", "host_os", "host_cpu", "GCC") and var not in self.state:
            return TRUE if var != "GCC" else Gnuc()
        value = self.lookup(var)
        if isinstance(value, Lit):
            return TRUE if value.text else FALSE
        if isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX):
            return TRUE  # an option's value is never empty once given
        if isinstance(value, (Probe, Ref)):
            return Truthy(value.name)
        if value is None:
            decided = self.decided(var, lambda v: bool(v.text) if isinstance(v, Lit)
                                   else TRUE if _is_option_value(v) else None)
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
        if isinstance(value, Ref) and value.name.startswith(OPTVAL_PREFIX):
            conds = []
            for pattern in patterns:
                if any(ch in pattern for ch in "*?[") and pattern not in ("*yes", "yes*", "*yes*", "*no", "no*", "*no*"):
                    return Unknown(f"case ${var} in {'|'.join(patterns)}")
                conds.append(_option_value(value.name, pattern.strip("*")))
            return disj(*conds)
        if value is None:
            decided = self.decided(
                var, lambda v: any(fnmatch.fnmatchcase(v.text, p) for p in patterns) if isinstance(v, Lit)
                else _option_match(v, patterns))
            if decided is not None:
                return decided
        if isinstance(value, (Probe, Ref)):
            conds = []
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
        return Unknown(f"case ${var} in {'|'.join(patterns)}")

    # values

    def lookup(self, var: str) -> Value | None:
        if var in self.state:
            value = self.state[var]
            if value is None:
                # `enableval=yes` a few lines up in the same branch: the
                # value assigned under a path that implies the current one.
                branch = self.branch_state.get(var)
                if branch is not None and tautology(disj(neg(self.path), branch[0])):
                    return branch[1]
            return value
        return Ref(var)

    def value(self, word: sh.Word, path: Cond = TRUE) -> Value:
        if word.unknown:
            return UnknownValue(word.text)
        if word.literal is not None:
            return Lit(word.literal)
        var = word.single_var
        if var is not None:
            value = self.lookup(var)
            return value if value is not None else UnknownValue(word.text)
        return UnknownValue(word.text)
