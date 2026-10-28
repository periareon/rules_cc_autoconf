"""Decisions the generator cannot make on its own, supplied up front.

The converter never emits a TODO.  Where a construct has no mechanical
translation (a runtime probe without a cross-compiling guess, a value
computed by a shell command, a macro without a port...), it looks the
construct up in a decisions file by a stable key.  A missing decision is
reported, the construct is left out of the output, and the key is added to
the decisions file with its context as comments and `UNDECIDED` as its
value; nothing is guessed.  Regenerating with the same sources and the same
decisions file reproduces the same output.

The decisions file is Starlark, written in the vocabulary of the output and
read with Python's `ast` module (never executed).  It defines the `decision`
struct inline (`STRUCT`), so that the words read as `decision.IGNORE`:

    \"\"\"Decisions for foo 1.2.\"\"\"

    decision = struct(UNDECIDED = "UNDECIDED", IGNORE = "IGNORE", ...)

    DECISIONS = {
        "probe:gzip_cv_underline": "no",
        "probe:my_cv_feature": select({"@platforms//os:windows": 0, "//conditions:default": 1}),
        "probe:my_cv_works": checks.AC_TRY_COMPILE(name = "my_cv_works", code = "int main(void) { return 0; }"),
        "variable:WARN_CFLAGS": decision.IGNORE,
        "macro:gl_FOO": "@rules_cc_autoconf//gnulib/m4/foo",
        "flag:gcc_warnings": False,
    }

A value is a word of the struct (`decision.UNDECIDED`, `decision.IGNORE`,
`decision.ACCEPT`, `decision.ACCEPT_PARTIAL`, `decision.ALWAYS`,
`decision.NEVER`, `decision.MANUAL`; the bare word is still read, for files
written before the struct); a literal; a `select({...})` over configuration labels; or
`checks.*` / `macros.*` / `utils.*` calls exactly as they would appear in
`BUILD.bazel` (one call or a list of calls).  Which of these a key accepts
depends on its kind; see `FORMS`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeGuard

from tools.convert.ir import AttrValue, Call, Raw

WORDS = ("UNDECIDED", "IGNORE", "ACCEPT", "ACCEPT_PARTIAL", "ALWAYS", "NEVER", "MANUAL")
STRUCT_NAME = "decision"  # `decision.IGNORE`: the struct each decisions file defines inline (STRUCT)
CALL_NAMESPACES = ("checks", "macros", "utils")
VARIABLE_NAME = "DECISIONS"
DEFAULTS_NAME = "DEFAULTS"  # kind -> the decision for every key of that kind the table leaves undecided
MIGRATION_GUIDE = "https://periareon.github.io/rules_cc_autoconf/migration.html"

# The words a decision of each kind may be (`normalize`); a kind absent here takes none.
WORD_FORMS = {
    "probe": ("IGNORE", "ACCEPT"),
    "variable": ("IGNORE", "ACCEPT_PARTIAL"),
    "guard": ("ALWAYS", "NEVER"),
    "macro": ("IGNORE",),
    "actions": ("IGNORE",),
    "flag": ("IGNORE",),
    "am_conditional": ("ALWAYS", "NEVER"),
    "src_condition": ("ALWAYS", "NEVER"),
    "define": ("IGNORE",),
    "check": ("IGNORE",),
    "subst": ("IGNORE",),
    "generated": ("IGNORE",),
    "cppflag": ("IGNORE",),
    "link": ("IGNORE",),
    "library": ("IGNORE",),
    "program": ("ALWAYS", "NEVER", "MANUAL"),
    "subdir": ("ALWAYS", "NEVER", "MANUAL"),
    "license": ("IGNORE",),
}

# What a decision of each kind may be, in the words of the file.
FORMS = {
    "probe": 'decision.IGNORE | decision.ACCEPT | "<value>" | select({"<label>": "<value>", ...}) | '
    'checks.AC_TRY_COMPILE(name = "<cache variable>", ...) | checks.AC_TRY_LINK(...) | [checks ...]',
    "variable": 'decision.IGNORE | decision.ACCEPT_PARTIAL | "<value>" | select({"<label>": "<value>", ...}) | '
    'checks.AC_SUBST("<NAME>", ..., condition = ..., if_true = ..., if_false = ...) | [checks ...]',
    "guard": 'decision.ALWAYS | decision.NEVER | ["<cache variable>", ...] (requires) | '
    'select({"<label>": ["<cache variable>", ...]})',
    "macro": 'decision.IGNORE | "<dep label>" | checks.X(...) | [checks ...]',
    "actions": "decision.IGNORE | checks.X(...) | [checks ...]",
    "flag": 'decision.IGNORE | True | False (the default of the bool_flag) | "<value>" (the default of a value option\'s string_flag)',
    "am_conditional": 'decision.ALWAYS | decision.NEVER | ":<config_setting>" (sources go in that select arm)',
    "src_condition": 'decision.ALWAYS | decision.NEVER | "<condition expression>"',
    "define": 'decision.IGNORE (a define a dependency already provides) | <value> | select({"<label>": <value>}) (the '
    "config header's line pinned to that value, over a port's result; never requested)",
    "check": "decision.IGNORE (a check a dependency already provides; never requested)",
    "subst": "decision.IGNORE (a substitution a dependency already provides; never requested)",
    "generated": "decision.IGNORE | genrule(...) / run_binary(...) / <rule>(...) writing the file | "
    '["load(...)", <rule>(...)] | ":<target>" (a rule of the hand-written BUILD.bazel writing it)',
    "cppflag": 'decision.IGNORE | "<value>" (the C token, e.g. "\\"so\\"")',
    "link": 'decision.IGNORE | ":<label>" (a dep) | "-l<name>" (a linkopt)',
    "library": "decision.IGNORE (a registry library configure probes for, built without; never requested)",
    "program": 'decision.ALWAYS | decision.NEVER | decision.MANUAL (defined, tags = ["manual"]) | '
    '["@platforms//os:linux", ...] (the platforms the program builds on, as target_compatible_with; never requested)',
    "subdir": "decision.ALWAYS | decision.NEVER (the sub-configure is not converted) | decision.MANUAL (its targets "
    'defined with tags = ["manual"], as while undecided) | ["<label>", ...] (the configuration labels, platform '
    "constraints or config_settings, the sub-configure's targets build under, as target_compatible_with; one "
    '"!<label>,<label>" entry for everywhere but those)',
    "license": 'decision.IGNORE | "<SPDX identifier>" (the registry\'s package metadata: a license() over the '
    "exported license file; requested when the text and the source headers do not settle it)",
    "copts": '["<flag>", ...] (compiler options every target of the package compiles with, after NO_WARNINGS; '
    "never requested)",
    "conlyopts": '["<flag>", ...] (C-only compiler options every target of the package compiles with; never requested)',
    "cxxopts": '["<flag>", ...] (C++-only compiler options every target of the package compiles with; never requested)',
}


_SPDX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")


class DecisionError(ValueError):
    """The decisions file is malformed or a decision has the wrong shape."""


@dataclass(frozen=True)
class Word:
    """One of the bare words (`IGNORE`, ...)."""

    name: str


@dataclass
class Select:
    """`select({label: value, ...})`."""

    arms: dict[str, DecisionValue]


@dataclass(frozen=True)
class Snippet:
    """A rule call other than the check builders, kept as its source text
    (`genrule(...)`, `run_binary(...)`): emitted verbatim into the BUILD file."""

    text: str


Scalar = str | int | float
"""A literal a probe, a variable or a define may be decided to: a string or a number.  A bool is
not one (`flag` alone takes a bool); `_scalar` tells them apart, which the type cannot."""

DecisionValue = (
    Scalar | bool | Word | Select | Snippet | Call | Raw | Sequence["DecisionValue"] | Mapping[str, "DecisionValue"]
)
"""What `_value` reads from a decisions file: a literal, a word of the `decision` struct, a
`select()`, a `checks.X(...)` call, a rule call kept as its text, or a list or string-keyed dict
of these.  Closed: `normalize` reads each kind's shapes out of it and rejects the rest.  `Raw`
never comes from the file; a table built in code may hold one, as the IR's call arguments do.
The containers are spelled as the covariant `Sequence` / `Mapping`, as `ir.AttrValue` spells
them, so a table built in code (`Decisions({"copts:.": ["-w"]})`) needs no copy."""

ContextValue = str | None | Sequence["ContextValue"] | Mapping[str, "ContextValue"]
"""What a request's context holds under one heading: a text (one comment line per line), a
list of texts or of blocks (a list per program), a mapping of sub-headings to these, or None
(skipped, as an empty text or container is).  `_render_context` renders every kind."""


@dataclass
class Request:
    """A decision the converter needs."""

    key: str
    """The stable key, `<kind>:<identifier>`: what the decisions file names the construct by."""

    kind: str
    """The kind of decision asked for: a key of `FORMS`."""

    where: str
    """The target and source location the construct is at, for the skeleton's comment and the report."""

    summary: str
    """Why the decision is asked, in one line (a condition's text may span several)."""

    context: dict[str, ContextValue] = field(default_factory=dict)
    """What the skeleton's comment block shows below the summary: program text, the cross-compiling
    arm, the list of decisions asked for..."""


@dataclass(frozen=True)
class Decision:
    """A decision as the converter consumes it: `normalize`'s reading of a file value for its
    kind.  One shape is set per decision and the fields of the other shapes stay None; a word
    (`IGNORE`, ...) is `word`, read through the boolean properties."""

    kind: str
    """The kind the value was read for: a key of `FORMS`."""

    key: str
    """The decisions file key the value answers (`DEFAULTS[kind]` for a kind's default)."""

    word: str | None = None
    """The word the decision is, as `WORDS` spells it (`IGNORE`, `ACCEPT`, `ACCEPT_PARTIAL`,
    `ALWAYS`, `NEVER`); None when the decision is a value."""

    value: Scalar | None = None
    """probe, variable, define, cppflag: the one literal the construct takes."""

    values: dict[str, Scalar] | None = None
    """probe, variable, define: `select({label: value})`, one literal per configuration label."""

    checks: list[Call] | None = None
    """probe, variable, macro, actions: the replacement checks, emitted as written."""

    try_compile: Call | None = None
    """probe: one `checks.AC_TRY_COMPILE(...)` standing for the probe, `name` set to its cache variable."""

    try_link: Call | None = None
    """probe: one `checks.AC_TRY_LINK(...)` standing for the probe, `name` set to its cache variable."""

    platforms: list[str] | None = None
    """program: the platform labels the program builds on (`target_compatible_with`)."""

    flags: list[str] | None = None
    """copts, conlyopts, cxxopts: the compiler options every target of the package compiles with."""

    requires: list[str] | None = None
    """guard: the cache variables the guarded checks require."""

    arms: dict[str, list[str] | Literal["never"]] | None = None
    """guard: `select({label: [...]})`, the requires list per configuration label; `[]` for an
    ALWAYS arm (no requirement there), `"never"` for a NEVER arm (the checks stay out of it)."""

    arm: str | None = None
    """am_conditional: the `config_setting` label whose select() arm the sources go in (`!label`
    for the complement, from `sources.invert_am_decision`)."""

    dep: str | None = None
    """macro, link: the label of the dependency that stands for the construct."""

    default: bool | None = None
    """flag: the default of the `bool_flag`."""

    spdx: str | None = None
    """license: the SPDX identifier of the license file."""

    condition: str | None = None
    """src_condition: the condition expression the sources compile under."""

    label: str | None = None
    """generated: the target of the hand-written BUILD file that writes the file."""

    rule: str | None = None
    """generated: the rule call writing the file, as its source text."""

    loads: list[str] | None = None
    """generated: the `load(...)` lines the rule needs, with `rule`."""

    linkopt: str | None = None
    """link: the `-l` option the program links with."""

    @property
    def ignore(self) -> bool:
        """`decision.IGNORE`: leave the construct out for good."""
        return self.word == "IGNORE"

    @property
    def accept(self) -> bool:
        """`decision.ACCEPT`: keep the converter's own translation of the probe."""
        return self.word == "ACCEPT"

    @property
    def accept_partial(self) -> bool:
        """`decision.ACCEPT_PARTIAL`: keep the assignments the converter could follow, drop the rest."""
        return self.word == "ACCEPT_PARTIAL"

    @property
    def always(self) -> bool:
        """`decision.ALWAYS`: the guarded checks or sources always apply."""
        return self.word == "ALWAYS"

    @property
    def never(self) -> bool:
        """`decision.NEVER`: the guarded checks or sources never apply."""
        return self.word == "NEVER"

    @property
    def manual(self) -> bool:
        """`decision.MANUAL` (program, subdir): the targets are defined with `tags = ["manual"]`, out of
        `//...`, where configure builds them only when a probe holds that no configuration expresses."""
        return self.word == "MANUAL"


# -- reading the file ---------------------------------------------------------


def _assigned_name(statement: ast.stmt) -> str | None:
    """The name a single-target assignment (`X = ...`) binds; None for any other statement."""
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
        return statement.targets[0].id
    return None


def _parse(text: str, source: str) -> tuple[dict[str, DecisionValue], ast.Dict | None, dict[str, DecisionValue]]:
    """The `DECISIONS` table of a decisions file, its AST node, and the `DEFAULTS` by kind."""
    try:
        module = ast.parse(text, filename=source)
    except SyntaxError as error:
        raise DecisionError(f"{source}:{error.lineno}: {error.msg}") from None
    table: dict[str, DecisionValue] = {}
    node: ast.Dict | None = None
    defaults: dict[str, DecisionValue] = {}
    for statement in module.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # docstring
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
            and statement.value.func.id == "load"
        ):
            continue  # load() lines are for readers and buildifier
        assigned = _assigned_name(statement)
        if assigned == STRUCT_NAME:
            continue  # `decision = struct(...)`: the words, spelled out for the reader
        if assigned == DEFAULTS_NAME and isinstance(statement, ast.Assign):
            if not isinstance(statement.value, ast.Dict):
                raise DecisionError(f"{source}:{statement.lineno}: {DEFAULTS_NAME} must be a dict literal")
            for key_node, value_node in zip(statement.value.keys, statement.value.values):
                if not (
                    isinstance(key_node, ast.Constant) and isinstance(key_node.value, str) and key_node.value in FORMS
                ):
                    raise DecisionError(
                        f"{source}:{value_node.lineno}: {DEFAULTS_NAME} keys are decision kinds ({', '.join(FORMS)})"
                    )
                kind = key_node.value
                value = _value(value_node, source, text)
                if isinstance(value, Word) and value.name == "UNDECIDED":
                    raise DecisionError(f"{source}:{value_node.lineno}: a default cannot be UNDECIDED")
                normalize(kind, f"{DEFAULTS_NAME}[{kind!r}]", value)  # the shape check, as for a key
                defaults[kind] = value
            continue
        if assigned == VARIABLE_NAME and isinstance(statement, ast.Assign):
            if not isinstance(statement.value, ast.Dict):
                raise DecisionError(f"{source}:{statement.lineno}: {VARIABLE_NAME} must be a dict literal")
            node = statement.value
            for key_node, value_node in zip(node.keys, node.values):
                if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                    raise DecisionError(f"{source}:{value_node.lineno}: keys must be string literals")
                if key_node.value in table:
                    raise DecisionError(f"{source}:{key_node.lineno}: duplicate key {key_node.value!r}")
                table[key_node.value] = _value(value_node, source, text)
            continue
        raise DecisionError(
            f"{source}:{statement.lineno}: only a docstring, load() lines, `{STRUCT_NAME} = struct(...)`, "
            f"`{DEFAULTS_NAME} = {{...}}` and `{VARIABLE_NAME} = {{...}}` are allowed"
        )
    return table, node, defaults


def _attr_value(value: DecisionValue, where: str) -> AttrValue:
    """A decision value as a call's argument: a Starlark value.  A word, a `select()` or a rule
    call has no place inside `checks.X(...)` (the emitter renders none of them) and is an error."""
    if isinstance(value, (Word, Select, Snippet, float)):
        raise DecisionError(
            f"{where}: only strings, ints, bools, lists, dicts and nested calls are allowed inside a call"
        )
    if isinstance(value, (str, int, Call, Raw)):  # a bool is an int
        return value
    if isinstance(value, Mapping):
        return {key: _attr_value(item, where) for key, item in value.items()}
    return [_attr_value(item, where) for item in value]


def _dict(node: ast.Dict, source: str, text: str | None) -> dict[str, DecisionValue]:
    """A dict literal's items; the keys must be string literals."""
    out: dict[str, DecisionValue] = {}
    for key_node, value_node in zip(node.keys, node.values):
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            raise DecisionError(f"{source}:{node.lineno}: dict keys must be string literals")
        out[key_node.value] = _value(value_node, source, text)
    return out


def _value(node: ast.expr, source: str, text: str | None = None) -> DecisionValue:
    """A decision value from its AST: literal, Word, Select, Call, Snippet, list or dict."""
    where = f"{source}:{node.lineno}"
    if isinstance(node, ast.Constant):
        if node.value is None:
            raise DecisionError(f"{where}: None is not a decision")
        if isinstance(node.value, (str, int, float)):  # a bool is an int
            return node.value
        raise DecisionError(f"{where}: unsupported literal ({type(node.value).__name__})")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        operand = node.operand.value
        if isinstance(operand, (int, float)):
            return -operand
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == STRUCT_NAME:
        if node.attr in WORDS:
            return Word(node.attr)
        raise DecisionError(f"{where}: {STRUCT_NAME} has no {node.attr!r} (one of {', '.join(WORDS)})")
    if isinstance(node, ast.Name):
        if node.id in WORDS:
            return Word(node.id)  # the bare word, as files written before the `decision` struct have it
        raise DecisionError(
            f"{where}: unknown name {node.id!r} (expected {STRUCT_NAME}.X, X one of {', '.join(WORDS)})"
        )
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(item, source, text) for item in node.elts]
    if isinstance(node, ast.Dict):
        return _dict(node, source, text)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _value(node.left, source, text), _value(node.right, source, text)
        return (left if isinstance(left, list) else [left]) + (right if isinstance(right, list) else [right])
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "select":
            if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Dict):
                raise DecisionError(f"{where}: select() takes one dict literal")
            return Select(_dict(node.args[0], source, text))
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in CALL_NAMESPACES
        ):
            fn = f"{node.func.value.id}.{node.func.attr}"
            args = [_attr_value(_value(a, source, text), where) for a in node.args]
            kwargs: dict[str, AttrValue] = {}
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise DecisionError(f"{where}: **kwargs are not allowed")
                kwargs[keyword.arg] = _attr_value(_value(keyword.value, source, text), where)
            return Call(fn, args, kwargs, returns_list=node.func.value.id == "macros", from_decision=True)
        if isinstance(node.func, ast.Name) and text is not None:
            segment = ast.get_source_segment(text, node)
            if segment:
                return Snippet(segment)  # a rule of the user's choosing (generated files)
        raise DecisionError(f"{where}: only select(...), {'/'.join(CALL_NAMESPACES)}.X(...) and rule calls are allowed")
    raise DecisionError(f"{where}: unsupported expression ({type(node).__name__})")


# -- what a value means for each kind ----------------------------------------


def _scalar(value: DecisionValue) -> TypeGuard[Scalar]:
    """Whether `value` is a literal a probe, a variable or a define may take: a string or a
    number, not a bool (an int to Python, a `flag` decision to the file)."""
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _strings(value: DecisionValue) -> list[str] | None:
    """The items of a list whose items are all strings; None for any other value."""
    if not isinstance(value, list):
        return None
    items = [item for item in value if isinstance(item, str)]
    return items if len(items) == len(value) else None


_HEADER_NAME_RE = re.compile(r"^<?[A-Za-z0-9_./+-]+\.h>?$")


def _spell_includes(call: Call) -> None:
    """A decision's `includes` as the checks render them: `#include` lines.  A bare header name
    (`dirent.h`, `<sys/mtio.h>`), which reads naturally but would compile as a stray token and
    fail the probe silently, becomes `#include <...>`; a line that already is a directive stays."""
    value = call.kwargs.get("includes")
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else None
    if items is None:
        return
    spelled = []
    for item in items:
        text = str(item).strip()
        if _HEADER_NAME_RE.match(text):
            text = f"#include <{text.strip('<>')}>"
        spelled.append(text)
    call.kwargs["includes"] = spelled if isinstance(value, list) else spelled[0]


def _calls(value: DecisionValue) -> list[Call] | None:
    """One `checks.X(...)` call or a non-empty list of them, their `includes` spelled as
    directives; None for any other value."""
    if isinstance(value, Call):
        calls = [value]
    elif isinstance(value, list) and value and all(isinstance(v, Call) for v in value):
        calls = [v for v in value if isinstance(v, Call)]
    else:
        return None
    for call in calls:
        _spell_includes(call)
    return calls


def _literal_or_select(kind: str, key: str, value: DecisionValue, bad: Callable[[], DecisionError]) -> Decision | None:
    """A scalar as the decision's `value`, a `select()` of scalars as its `values`; None for any
    other value.  A `select()` with an arm that is not a scalar raises `bad()`."""
    if _scalar(value):
        return Decision(kind, key, value=value)
    if isinstance(value, Select):
        arms: dict[str, Scalar] = {}
        for label, arm in value.arms.items():
            if not _scalar(arm):
                raise bad()
            arms[label] = arm
        return Decision(kind, key, values=arms)
    return None


def normalize(kind: str, key: str, value: DecisionValue) -> Decision:
    """The decision as the converter consumes it: the one shape of `Decision` the value has for
    its kind (`value`, `checks`, a word...); `DecisionError` for a value the kind does not take."""

    def bad() -> DecisionError:
        return DecisionError(f"decision {key!r} has the wrong shape for its kind; expected {FORMS.get(kind, '?')}")

    if kind not in FORMS:
        raise DecisionError(f"decision {key!r}: {kind!r} is not a kind of decision")
    # A key spells its kind (`program:src/hostid`): a caller asking for another kind is a bug of
    # the converter, which would otherwise read the decision in the wrong shape.
    if key.split(":", 1)[0] != kind and not key.startswith(f"{DEFAULTS_NAME}["):
        raise DecisionError(f"decision {key!r} read as a {kind!r} decision, but its key says {key.split(':', 1)[0]!r}")
    if isinstance(value, Word):
        word = value.name
        if word not in WORD_FORMS.get(kind, ()):
            raise bad()
        return Decision(kind, key, word=word)
    if kind in ("probe", "variable"):
        literal = _literal_or_select(kind, key, value, bad)
        if literal is not None:
            return literal
    if kind == "probe":
        calls = _calls(value)
        if calls is None:
            raise bad()
        if len(calls) == 1 and calls[0].fn in ("checks.AC_TRY_COMPILE", "checks.AC_TRY_LINK"):
            call = calls[0]
            var = key.split(":", 1)[1]
            if "name" in call.kwargs and call.kwargs["name"] != var and not var.startswith("autoconf_probe_"):
                # An anonymous probe (`autoconf_probe_<n>`) has no cache variable of its own: the
                # replacement may record the one its actions assigned (ppp's ac_cv_openssl_md4).
                raise DecisionError(f"decision {key!r}: the probe must set {var!r}, not {call.kwargs['name']!r}")
            call.kwargs.setdefault("name", var)
            if call.fn.endswith("COMPILE"):
                return Decision(kind, key, try_compile=call)
            return Decision(kind, key, try_link=call)
        return Decision(kind, key, checks=calls)
    if kind == "variable":
        calls = _calls(value)
        if calls is None:
            raise bad()
        return Decision(kind, key, checks=calls)
    if kind == "program":
        platforms = _strings(value)
        if platforms is not None and all(v.startswith("@") for v in platforms):
            return Decision(kind, key, platforms=platforms)
        raise bad()
    if kind == "subdir":
        # Platform constraints or config_settings; `!a,b` (everywhere but) only on its own.
        labels = _strings(value)
        if labels is not None and labels and all(v.startswith(("@", "//", ":")) for v in labels):
            return Decision(kind, key, platforms=labels)
        if labels is not None and len(labels) == 1 and labels[0].startswith("!"):
            return Decision(kind, key, platforms=labels)
        raise bad()
    if kind in ("copts", "conlyopts", "cxxopts"):
        flags = _strings(value)
        if flags is not None:
            return Decision(kind, key, flags=flags)
        raise bad()
    if kind == "guard":
        requires = _strings(value)
        if requires is not None:
            return Decision(kind, key, requires=requires)
        if isinstance(value, Select):
            # An arm may also be ALWAYS (no requirement there) or NEVER (the checks stay out of it).
            arms: dict[str, list[str] | Literal["never"]] = {}
            for label, arm in value.arms.items():
                if isinstance(arm, Word) and arm.name in ("ALWAYS", "NEVER"):
                    arms[label] = [] if arm.name == "ALWAYS" else "never"
                    continue
                arm_requires = _strings(arm)
                if arm_requires is None:
                    raise bad()
                arms[label] = arm_requires
            return Decision(kind, key, arms=arms)
        raise bad()
    if kind == "macro":
        if isinstance(value, str):
            return Decision(kind, key, dep=value)
        calls = _calls(value)
        if calls is None:
            raise bad()
        return Decision(kind, key, checks=calls)
    if kind == "actions":
        calls = _calls(value)
        if calls is None:
            raise bad()
        return Decision(kind, key, checks=calls)
    if kind == "flag":
        if isinstance(value, bool):
            return Decision(kind, key, default=value)
        if isinstance(value, str):  # the default of a value option's string_flag
            return Decision(kind, key, value=value)
        raise bad()
    if kind == "license":
        if isinstance(value, str) and _SPDX_RE.fullmatch(value):
            return Decision(kind, key, spdx=value)
        raise bad()
    if kind == "am_conditional":
        if isinstance(value, str):
            return Decision(kind, key, arm=value)
        raise bad()
    if kind == "src_condition":
        if isinstance(value, str):
            return Decision(kind, key, condition=value)
        raise bad()
    if kind == "generated":
        if isinstance(value, str) and value.startswith((":", "//", "@")):
            return Decision(kind, key, label=value)
        if isinstance(value, Snippet):
            return Decision(kind, key, rule=value.text, loads=[])
        if isinstance(value, list) and value and isinstance(value[-1], Snippet):
            loads = _strings(value[:-1])
            if loads is not None and all(v.startswith("load(") for v in loads):
                return Decision(kind, key, rule=value[-1].text, loads=loads)
        raise bad()
    if kind == "cppflag":
        if isinstance(value, str):
            return Decision(kind, key, value=value)
        raise bad()
    if kind == "link":
        if isinstance(value, str) and value and value[0] in ":@/":
            return Decision(kind, key, dep=value)
        if isinstance(value, str) and value.startswith("-"):
            return Decision(kind, key, linkopt=value)
        raise bad()
    if kind == "define":
        # A value, or a select() of values: the project's config header pins the define there
        # (grep's NL_LANGINFO_MTSAFE on macOS, where its gnulib predates the port's).
        literal = _literal_or_select(kind, key, value, bad)
        if literal is None:
            raise bad()
        return literal
    if kind in ("check", "subst"):
        raise bad()
    raise DecisionError(f"unknown decision kind {kind!r} for {key!r}")


# -- the decisions of one conversion -----------------------------------------


class Decisions:
    """The decisions file plus the requests made while converting."""

    def __init__(
        self,
        data: dict[str, DecisionValue] | None = None,
        source: str | None = None,
        node: ast.Dict | None = None,
        text: str = "",
        defaults: dict[str, DecisionValue] | None = None,
    ) -> None:
        self.data: dict[str, DecisionValue] = dict(data or {})
        self.source = source
        self._node = node
        self._text = text
        self.defaults: dict[str, DecisionValue] = dict(defaults or {})
        self.pending: dict[str, Request] = {}
        self.defaulted: dict[str, Request] = {}  # requests a kind's default answered
        self.shared: Decisions | None = None
        """The decisions bundled with the ports (`//gnulib/m4:decisions.bzl`), read after this
        file's own and before `DEFAULTS`: the answers to the questions every gnulib project raises."""
        self.inherited: dict[str, str] = {}
        """Keys the bundled file answered for this conversion, with the answer as spelled."""
        self.used: set[str] = set()

    @classmethod
    def load(cls, path: Path | None) -> Decisions:
        """The decisions in `path`; a missing file means no decisions yet."""
        if path is None or not path.is_file():
            return cls(source=str(path) if path else None)
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        data, node, defaults = _parse(text, str(path))
        return cls(data, str(path), node, text, defaults)

    @classmethod
    def from_text(cls, text: str, source: str = "<decisions>") -> Decisions:
        data, node, defaults = _parse(text, source)
        return cls(data, source, node, text, defaults)

    def _record(self, request: Request) -> None:
        if request.key not in self.pending:
            self.pending[request.key] = request

    def resolve(self, request: Request) -> Decision | None:
        """The normalized decision for `request`: the key's, else the kind's default (recorded
        under `defaulted`), else None and the request is pending."""
        value = self.data.get(request.key)
        if value is not None:
            self.used.add(request.key)
        if value is None or (isinstance(value, Word) and value.name == "UNDECIDED"):
            shared = self._shared(request.key)
            if shared is not None:
                return normalize(request.kind, request.key, shared)
            if request.kind in self.defaults:
                self.defaulted.setdefault(request.key, request)
                return normalize(request.kind, request.key, self.defaults[request.kind])
            self._record(request)
            return None
        return normalize(request.kind, request.key, value)

    def lookup(self, key: str) -> DecisionValue | None:
        """The value the file holds for `key`, the bundled file's when it has none: a presence
        test that does not count the key as used (`resolve` and `optional` do).  Raw, as read:
        the callers test for a Word or a literal, not a normalized decision."""
        value = self.data.get(key)
        if value is None and self.shared is not None:
            return self.shared.data.get(key)
        return value

    def items(self) -> list[tuple[str, DecisionValue]]:
        """Every (key, value) the conversion can answer with: the bundled file's beneath this
        file's own, which win."""
        merged = dict(self.shared.data) if self.shared is not None else {}
        merged.update(self.data)
        return sorted(merged.items())

    def _shared(self, key: str) -> DecisionValue | None:
        """The bundled file's answer to `key`, recorded under `inherited`; None without one."""
        if self.shared is None:
            return None
        value = self.shared.data.get(key)
        if value is None or (isinstance(value, Word) and value.name == "UNDECIDED"):
            return None
        self.shared.used.add(key)
        self.inherited.setdefault(key, _spell(value))
        return value

    def optional(self, key: str, kind: str) -> Decision | None:
        """A decision that is applied when present and never requested."""
        value = self.data.get(key)
        if value is None or (isinstance(value, Word) and value.name == "UNDECIDED"):
            shared = self._shared(key)
            return normalize(kind, key, shared) if shared is not None else None
        self.used.add(key)
        return normalize(kind, key, value)

    def decided(self, key: str) -> bool:
        """Whether the file, or the bundled one beneath it, answers `key`: an entry whose value is
        not `UNDECIDED`.  Reading the tables directly, it does not count the key as used
        (`optional` and `resolve` do)."""
        value = self.data.get(key)
        if value is not None and not (isinstance(value, Word) and value.name == "UNDECIDED"):
            return True
        return self.shared is not None and self.shared.decided(key)

    @property
    def unused(self) -> list[str]:
        """Decisions the conversion never asked for."""
        return sorted(k for k in self.data if k not in self.used)

    @property
    def missing(self) -> list[str]:
        """Pending keys that are not in the file at all (as opposed to UNDECIDED)."""
        return sorted(k for k in self.pending if k not in self.data)

    # -- writing the skeleton -------------------------------------------------

    def entries(self, keys: list[str] | None = None) -> str:
        """Table entries for pending keys: context as comments, `UNDECIDED` as the value."""
        lines: list[str] = []
        for key in keys if keys is not None else sorted(self.pending):
            request = self.pending[key]
            if lines:
                lines.append("")
            lines.extend(comment_block(request, "    "))
            lines.append(f'    "{key}": {STRUCT_NAME}.UNDECIDED,')
        return "\n".join(lines) + ("\n" if lines else "")

    def skeleton(self, title: str = "the rules_cc_autoconf converter", version: str = "?") -> str:
        """A complete decisions file for every pending key; `version` is the rules_cc_autoconf
        that wrote it, named in the header."""
        return HEADER.format(title=title, version=version) + f"{VARIABLE_NAME} = {{\n" + self.entries() + "}\n"

    def write_needed(self, path: Path, title: str | None = None, version: str = "?") -> list[str]:
        """Create `path`, or add entries for the pending keys it lacks; returns the keys added.

        The table stays sorted by key (buildifier's `unsorted-dict-items`): a new entry goes in
        front of the first existing key that sorts after it, above that key's comment block,
        or at the end when none does."""
        keys = self.missing
        if not keys:
            return []
        if self._node is None or not self._text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.skeleton(title or path.stem, version), encoding="utf-8", newline="\n")
            return keys
        text = self._text
        for key in keys:
            text = self._insert_entry(text, key)
        self._text = text
        path.write_text(text, encoding="utf-8", newline="\n")
        return keys

    def _insert_entry(self, text: str, key: str) -> str:
        """`text` with the skeleton entry for `key` at its sorted place in the table."""
        _, node, _ = _parse(text, self.source or "<decisions>")
        if node is None or node.end_lineno is None:
            raise DecisionError(f"{self.source}: no {VARIABLE_NAME} table to add {key!r} to")
        lines = text.split("\n")
        end = node.end_lineno  # 1-based line of the closing brace
        if node.lineno == end:
            if node.keys:
                raise DecisionError(
                    f"{self.source}:{end}: cannot add entries to a one-line {VARIABLE_NAME} table; "
                    "put each entry on its own line"
                )
            line = lines[end - 1]
            lines[end - 1] = line[: line.index("{") + 1]
            lines.insert(end, line[line.index("}") :])
            end += 1
        entry = self.entries([key]).rstrip("\n").split("\n")
        following = [k for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value > key]
        if following:
            at = min(k.lineno for k in following) - 1  # 0-based line of that key
            while at > 0 and lines[at - 1].lstrip().startswith("#"):
                at -= 1  # its comment block goes with it
            lines[at:at] = entry + [""]
        else:
            at = end - 1
            if node.keys and lines[at - 1].strip():
                entry = [""] + entry
            lines[at:at] = entry
        return "\n".join(lines)

    def report_lines(self) -> list[str]:
        lines = []
        for key, request in sorted(self.pending.items()):
            lines.append(f"- `{key}` ({request.where}): {request.summary}")
            lines.append(f"  - one of: `{FORMS.get(request.kind, '?')}`")
        return lines

    def inherited_lines(self) -> list[str]:
        """The report's lines for the answers the bundled file gave."""
        return [f"- `{key}` -> `{value}`" for key, value in sorted(self.inherited.items())]

    def defaulted_lines(self) -> list[str]:
        """The requests a kind's default answered, with the default, for the report."""
        return [
            f"- `{key}` ({request.where}): {request.summary} -> `{_spell(self.defaults[request.kind])}`"
            for key, request in sorted(self.defaulted.items())
        ]


def _spell(value: DecisionValue) -> str:
    """A decision value as the file writes it."""
    if isinstance(value, Word):
        return f"{STRUCT_NAME}.{value.name}"
    return repr(value)


STRUCT = """# The words a decision may be, besides a literal, a select() or checks.X(...).
decision = struct(
    # Not decided yet: the construct stays out and the run exits 3.
    UNDECIDED = "UNDECIDED",
    # Leave the construct out for good.
    IGNORE = "IGNORE",
    # Keep the converter\'s own translation of the probe.
    ACCEPT = "ACCEPT",
    # Keep the assignments the converter could follow, drop the rest.
    ACCEPT_PARTIAL = "ACCEPT_PARTIAL",
    # The guarded checks or sources always apply.
    ALWAYS = "ALWAYS",
    # The guarded checks or sources never apply.
    NEVER = "NEVER",
)
"""

# The decisions section of the converter's guide: the kinds, their forms, DEFAULTS, the hand-added keys.
CONVERT_GUIDE = "https://periareon.github.io/rules_cc_autoconf/convert.html#decisions"

HEADER = (
    '''"""Decisions for {title}.

Generated by: rules_cc_autoconf {version}
What it is:   the answers the converter could not derive, read back by the
              converter on every run; Bazel never loads this file
To finish:    replace every `decision.UNDECIDED`, then run the same convert
              command again
Reference:    '''
    + CONVERT_GUIDE
    + '''
"""

'''
    + STRUCT
    + "\n"
)


CONTEXT_LINES = 40  # longest context block in the skeleton's comments


_EXCERPT_KEY_RE = re.compile(r"^[^\s:]+:~?\d+(?:-\d+)?$")  # `file:line` or `file:first-last`
SHELL_EXCERPT = "m4 (shell)"  # the context key of `file:~line: statement` lines
_FENCED = {"program": "c", "recipe": "sh", "commands": "sh"}  # context keys whose value is code


def excerpt_language(location: str) -> str:
    """The fence language of an excerpt at `location` (`file:lines`): m4 for configure.ac and the
    m4 files, make for Makefiles and gnulib.mk, nothing for the rest."""
    name = location.split(":", 1)[0].rsplit("/", 1)[-1]
    if name in ("configure.in", "configure.ac") or name.endswith((".m4", ".ac")):
        return "m4"
    if name.startswith("Makefile") or name.endswith((".am", ".mk", ".mak")):
        return "make"
    return ""


def _fenced(indent: str, header: str, language: str, lines: list[str]) -> list[str]:
    """`header:` and the lines in a ```language fence, as comment lines."""
    return (
        [f"{indent}# {header}:", f"{indent}# ```{language}"]
        + [f"{indent}#   {line}".rstrip() for line in lines]
        + [f"{indent}# ```"]
    )


def comment_block(request: Request, indent: str) -> list[str]:
    """The request's context as `#` lines: a banner naming the kind and the construct, why it is
    asked, and its context.  One line locates the construct: an excerpt's `file:lines` header
    when the context has one, the request's `where` otherwise.  Excerpts and other code
    (the probe program, a recipe, shell commands) sit in fenced blocks tagged with their
    language, so they stand out from the derived context around them.  The forms a kind accepts
    are the report's and the documentation's to list; the `decision` struct at the top of the
    file names the words."""
    kind, _, name = request.key.partition(":")
    why = request.summary.replace("\r", "").split("\n")  # a condition's text may span lines
    lines = [f"{indent}# ---- {kind}: {name}"]
    if not any(_EXCERPT_KEY_RE.match(key) or key == SHELL_EXCERPT for key in request.context):
        lines.append(f"{indent}# where: {request.where}")
    lines += [f"{indent}# why:   {why[0]}"] + [f"{indent}#        {line.strip()}" for line in why[1:]]
    for name, value in request.context.items():
        if value in (None, "", [], {}):
            continue
        rendered = _render_context(value)
        if len(rendered) > CONTEXT_LINES:
            rendered = rendered[:CONTEXT_LINES] + [f"... ({len(rendered) - CONTEXT_LINES} more lines)"]
        if _EXCERPT_KEY_RE.match(name):
            lines += _fenced(indent, name, excerpt_language(name), rendered)
        elif name == SHELL_EXCERPT:
            for line in rendered:
                location, _, statement = line.partition(": ")
                lines += _fenced(indent, location, "sh", [statement])
        elif name in _FENCED:
            lines += _fenced(indent, name, _FENCED[name], rendered)
        elif len(rendered) == 1:
            lines.append(f"{indent}# {name}: {rendered[0]}")
        else:
            lines.append(f"{indent}# {name}:")
            lines.extend(f"{indent}#   {line}".rstrip() for line in rendered)
    return lines


def _render_context(value: ContextValue) -> list[str]:
    """A request's context value as comment lines: a string line by line, a list item by item
    (multi-line items under a `-`), a dict as `key: value` or `key:` followed by indented lines.
    """
    if isinstance(value, str):
        return value.replace("\r", "").split("\n")
    if isinstance(value, Mapping):
        out: list[str] = []
        for name, item in value.items():
            rendered = _render_context(item)
            if len(rendered) == 1:
                out.append(f"{name}: {rendered[0]}")
            else:
                out.append(f"{name}:")
                out.extend("  " + line for line in rendered)
        return out
    if isinstance(value, Sequence):
        out = []
        for item in value:
            rendered = _render_context(item)
            if isinstance(item, (list, dict)) and len(rendered) > 1:
                out.append("-")
                out.extend("  " + line for line in rendered)
            else:
                out.extend(rendered)
        return out
    return [str(value)]
