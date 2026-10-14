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
read with Python's `ast` module (never executed):

    \"\"\"Decisions for foo 1.2.\"\"\"

    DECISIONS = {
        "probe:gzip_cv_underline": "no",
        "probe:my_cv_feature": select({"@platforms//os:windows": 0, "//conditions:default": 1}),
        "probe:my_cv_works": checks.AC_TRY_COMPILE(name = "my_cv_works", code = "int main(void) { return 0; }"),
        "variable:WARN_CFLAGS": IGNORE,
        "macro:gl_FOO": "@rules_cc_autoconf//gnulib/m4/foo",
        "flag:gcc_warnings": False,
    }

A value is one of the words `UNDECIDED`, `IGNORE`, `ACCEPT`, `ACCEPT_PARTIAL`,
`ALWAYS`, `NEVER`; a literal; a `select({...})` over configuration labels;
or `checks.*` / `macros.*` / `utils.*` calls exactly as they would appear in
`BUILD.bazel` (one call or a list of calls).  Which of these a key accepts
depends on its kind; see `FORMS`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert.ir import Call

WORDS = ("UNDECIDED", "IGNORE", "ACCEPT", "ACCEPT_PARTIAL", "ALWAYS", "NEVER")
CALL_NAMESPACES = ("checks", "macros", "utils")
VARIABLE_NAME = "DECISIONS"

# What a decision of each kind may be, in the words of the file.
FORMS = {
    "probe": 'IGNORE | ACCEPT | "<value>" | select({"<label>": "<value>", ...}) | '
             'checks.AC_TRY_COMPILE(name = "<cache variable>", ...) | checks.AC_TRY_LINK(...) | [checks ...]',
    "variable": 'IGNORE | ACCEPT_PARTIAL | "<value>" | select({"<label>": "<value>", ...}) | '
                'checks.AC_SUBST("<NAME>", ..., condition = ..., if_true = ..., if_false = ...) | [checks ...]',
    "guard": 'ALWAYS | NEVER | ["<cache variable>", ...] (requires) | select({"<label>": ["<cache variable>", ...]})',
    "macro": 'IGNORE | "<dep label>" | checks.X(...) | [checks ...]',
    "actions": "IGNORE | checks.X(...) | [checks ...]",
    "flag": "IGNORE | True | False (the default of the bool_flag)",
    "am_conditional": 'ALWAYS | NEVER | ":<config_setting>" (sources go in that select arm)',
    "src_condition": 'ALWAYS | NEVER | "<condition expression>"',
    "define": "IGNORE (a define a dependency already provides; never requested)",
    "check": "IGNORE (a check a dependency already provides; never requested)",
    "subst": "IGNORE (a substitution a dependency already provides; never requested)",
    "generated": 'IGNORE | genrule(...) / run_binary(...) / <rule>(...) writing the file | '
                 '["load(...)", <rule>(...)]',
    "cppflag": 'IGNORE | "<value>" (the C token, e.g. "\\"so\\"")',
    "link": 'IGNORE | ":<label>" (a dep) | "-l<name>" (a linkopt)',
}


class DecisionError(ValueError):
    """The decisions file is malformed or a decision has the wrong shape."""


@dataclass(frozen=True)
class Word:
    """One of the bare words (`IGNORE`, ...)."""

    name: str


@dataclass
class Select:
    """`select({label: value, ...})`."""

    arms: dict


@dataclass
class Request:
    """A decision the converter needs."""

    key: str  # stable: "<kind>:<identifier>"
    kind: str  # probe | variable | guard | macro | actions | flag | am_conditional | src_condition
    where: str  # target and source location
    summary: str
    context: dict = field(default_factory=dict)  # program text, cross arm, decision list...


@dataclass(frozen=True)
class Snippet:
    """A rule call other than the check builders, kept as its source text
    (`genrule(...)`, `run_binary(...)`): emitted verbatim into the BUILD file."""

    text: str


# -- reading the file ---------------------------------------------------------


def _parse(text: str, source: str) -> tuple[dict, ast.Dict | None]:
    """The `DECISIONS` table of a decisions file, plus its AST node."""
    try:
        module = ast.parse(text, filename=source)
    except SyntaxError as error:
        raise DecisionError(f"{source}:{error.lineno}: {error.msg}") from None
    table: dict = {}
    node: ast.Dict | None = None
    for statement in module.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # docstring
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) \
                and isinstance(statement.value.func, ast.Name) and statement.value.func.id == "load":
            continue  # load() lines are for readers and buildifier
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 \
                and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == VARIABLE_NAME:
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
            f"{source}:{statement.lineno}: only a docstring, load() lines and `{VARIABLE_NAME} = {{...}}` are allowed")
    return table, node


def _value(node: ast.AST, source: str, text: str | None = None):
    """A decision value from its AST: literal, Word, Select, Call, Snippet, list or dict."""
    where = f"{source}:{getattr(node, 'lineno', '?')}"
    if isinstance(node, ast.Constant):
        if node.value is None:
            raise DecisionError(f"{where}: None is not a decision")
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    if isinstance(node, ast.Name):
        if node.id in WORDS:
            return Word(node.id)
        raise DecisionError(f"{where}: unknown name {node.id!r} (expected one of {', '.join(WORDS)})")
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_value(item, source, text) for item in node.elts]
    if isinstance(node, ast.Dict):
        out = {}
        for key_node, value_node in zip(node.keys, node.values):
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                raise DecisionError(f"{where}: dict keys must be string literals")
            out[key_node.value] = _value(value_node, source, text)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _value(node.left, source, text), _value(node.right, source, text)
        return (left if isinstance(left, list) else [left]) + (right if isinstance(right, list) else [right])
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "select":
            if len(node.args) != 1 or node.keywords or not isinstance(node.args[0], ast.Dict):
                raise DecisionError(f"{where}: select() takes one dict literal")
            return Select(_value(node.args[0], source, text))
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in CALL_NAMESPACES:
            fn = f"{node.func.value.id}.{node.func.attr}"
            args = [_value(a, source, text) for a in node.args]
            kwargs = {}
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise DecisionError(f"{where}: **kwargs are not allowed")
                kwargs[keyword.arg] = _value(keyword.value, source, text)
            return Call(fn, args, kwargs, returns_list=node.func.value.id == "macros")
        if isinstance(node.func, ast.Name) and text is not None:
            segment = ast.get_source_segment(text, node)
            if segment:
                return Snippet(segment)  # a rule of the user's choosing (generated files)
        raise DecisionError(f"{where}: only select(...), {'/'.join(CALL_NAMESPACES)}.X(...) and rule calls are allowed")
    raise DecisionError(f"{where}: unsupported expression ({type(node).__name__})")


# -- what a value means for each kind ----------------------------------------


def _scalar(value) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def _calls(value) -> list[Call] | None:
    if isinstance(value, Call):
        return [value]
    if isinstance(value, list) and value and all(isinstance(v, Call) for v in value):
        return list(value)
    return None


def normalize(kind: str, key: str, value) -> dict:
    """The decision as the converter consumes it (`{"value": ...}`, `{"checks": [...]}`...)."""

    def bad() -> DecisionError:
        return DecisionError(f"decision {key!r} has the wrong shape for its kind; expected {FORMS.get(kind, '?')}")

    if isinstance(value, Word):
        word = value.name
        allowed = {
            "probe": ("IGNORE", "ACCEPT"), "variable": ("IGNORE", "ACCEPT_PARTIAL"), "guard": ("ALWAYS", "NEVER"),
            "macro": ("IGNORE",), "actions": ("IGNORE",), "flag": ("IGNORE",),
            "am_conditional": ("ALWAYS", "NEVER"), "src_condition": ("ALWAYS", "NEVER"),
            "define": ("IGNORE",), "check": ("IGNORE",), "subst": ("IGNORE",),
            "generated": ("IGNORE",), "cppflag": ("IGNORE",), "link": ("IGNORE",),
        }.get(kind, ())
        if word not in allowed:
            raise bad()
        return {word.lower(): True}
    if kind == "probe":
        if _scalar(value):
            return {"value": value}
        if isinstance(value, Select):
            if not all(_scalar(v) for v in value.arms.values()):
                raise bad()
            return {"values": value.arms}
        calls = _calls(value)
        if calls is None:
            raise bad()
        if len(calls) == 1 and calls[0].fn in ("checks.AC_TRY_COMPILE", "checks.AC_TRY_LINK"):
            call = calls[0]
            var = key.split(":", 1)[1]
            if "name" in call.kwargs and call.kwargs["name"] != var:
                raise DecisionError(f"decision {key!r}: the probe must set {var!r}, not {call.kwargs['name']!r}")
            call.kwargs["name"] = var
            return {"try_compile" if call.fn.endswith("COMPILE") else "try_link": call}
        return {"checks": calls}
    if kind == "variable":
        if _scalar(value):
            return {"value": value}
        if isinstance(value, Select):
            if not all(_scalar(v) for v in value.arms.values()):
                raise bad()
            return {"values": value.arms}
        calls = _calls(value)
        if calls is None:
            raise bad()
        return {"checks": calls}
    if kind == "guard":
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return {"requires": value}
        if isinstance(value, Select) and all(
                isinstance(v, list) and all(isinstance(r, str) for r in v) for v in value.arms.values()):
            return {"select": value.arms}
        raise bad()
    if kind == "macro":
        if isinstance(value, str):
            return {"dep": value}
        calls = _calls(value)
        if calls is None:
            raise bad()
        return {"checks": calls}
    if kind == "actions":
        calls = _calls(value)
        if calls is None:
            raise bad()
        return {"checks": calls}
    if kind == "flag":
        if isinstance(value, bool):
            return {"default": value}
        raise bad()
    if kind == "am_conditional":
        if isinstance(value, str):
            return {"select": value}
        raise bad()
    if kind == "src_condition":
        if isinstance(value, str):
            return {"condition": value}
        raise bad()
    if kind == "generated":
        if isinstance(value, Snippet):
            return {"rule": value.text, "loads": []}
        if isinstance(value, list) and value and isinstance(value[-1], Snippet) \
                and all(isinstance(v, str) and v.startswith("load(") for v in value[:-1]):
            return {"rule": value[-1].text, "loads": list(value[:-1])}
        raise bad()
    if kind == "cppflag":
        if isinstance(value, str):
            return {"value": value}
        raise bad()
    if kind == "link":
        if isinstance(value, str) and value and value[0] in ":@/":
            return {"dep": value}
        if isinstance(value, str) and value.startswith("-"):
            return {"linkopt": value}
        raise bad()
    if kind in ("define", "check", "subst"):
        raise bad()
    raise DecisionError(f"unknown decision kind {kind!r} for {key!r}")


# -- the decisions of one conversion -----------------------------------------


class Decisions:
    """The decisions file plus the requests made while converting."""

    def __init__(self, data: dict | None = None, source: str | None = None, node: ast.Dict | None = None,
                 text: str = "") -> None:
        self.data: dict = dict(data or {})
        self.source = source
        self._node = node
        self._text = text
        self.pending: dict[str, Request] = {}
        self.used: set[str] = set()

    @classmethod
    def load(cls, path: Path | None) -> "Decisions":
        """The decisions in `path`; a missing file means no decisions yet."""
        if path is None or not path.is_file():
            return cls(source=str(path) if path else None)
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        data, node = _parse(text, str(path))
        return cls(data, str(path), node, text)

    @classmethod
    def from_text(cls, text: str, source: str = "<decisions>") -> "Decisions":
        data, node = _parse(text, source)
        return cls(data, source, node, text)

    def _record(self, request: Request) -> None:
        if request.key not in self.pending:
            self.pending[request.key] = request

    def resolve(self, request: Request) -> dict | None:
        """The normalized decision for `request`, or None (then it is pending)."""
        value = self.data.get(request.key)
        if value is None:
            self._record(request)
            return None
        self.used.add(request.key)
        if isinstance(value, Word) and value.name == "UNDECIDED":
            self._record(request)
            return None
        return normalize(request.kind, request.key, value)

    def optional(self, key: str, kind: str) -> dict | None:
        """A decision that is applied when present and never requested."""
        value = self.data.get(key)
        if value is None:
            return None
        self.used.add(key)
        if isinstance(value, Word) and value.name == "UNDECIDED":
            return None
        return normalize(kind, key, value)

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
            lines.extend(comment_block(request, "    "))
            lines.append(f'    "{key}": UNDECIDED,')
        return "\n".join(lines) + ("\n" if lines else "")

    def skeleton(self, title: str = "the rules_cc_autoconf converter") -> str:
        """A complete decisions file for every pending key."""
        return HEADER.format(title=title) + f"{VARIABLE_NAME} = {{\n" + self.entries() + "}\n"

    def write_needed(self, path: Path, title: str | None = None) -> list[str]:
        """Create `path`, or add entries for the pending keys it lacks; returns the keys added."""
        keys = self.missing
        if not keys:
            return []
        if self._node is None or not self._text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.skeleton(title or path.stem), encoding="utf-8", newline="\n")
            return keys
        lines = self._text.split("\n")
        end = self._node.end_lineno  # 1-based line of the closing brace
        if self._node.lineno == end:
            if self._node.keys:
                raise DecisionError(f"{self.source}:{end}: cannot add entries to a one-line {VARIABLE_NAME} table; "
                                    "put each entry on its own line")
            line = lines[end - 1]
            lines[end - 1] = line[:line.index("{") + 1]
            lines.insert(end, line[line.index("}"):])
        closing = end - 1 if self._node.lineno != end else end
        lines[closing:closing] = self.entries(keys).rstrip("\n").split("\n")
        self._text = "\n".join(lines)
        path.write_text(self._text, encoding="utf-8", newline="\n")
        return keys

    def report_lines(self) -> list[str]:
        lines = []
        for key, request in sorted(self.pending.items()):
            lines.append(f"- `{key}` ({request.where}): {request.summary}")
            lines.append(f"  - one of: `{FORMS.get(request.kind, '?')}`")
        return lines


HEADER = '''"""Decisions for {title}: the one hand-maintained file of this conversion.

Written once by `bazel run @rules_cc_autoconf//tools/convert -- convert` and
read back on every run; BUILD.bazel and MODULE.bazel next to it are generated
from it.  Each key below is a construct of the configure.ac or m4
that the converter cannot translate on its own; the comments above it show
the source it stands for and what the converter found in it.  Replace each
`UNDECIDED` with one of these values, then run the same command again:

    UNDECIDED        not decided yet: the construct stays out, the run exits 3
    IGNORE           leave the construct out for good
    ACCEPT           keep the converter's own translation of the probe as it is
    ACCEPT_PARTIAL   keep the assignments the converter could follow, drop the rest
    ALWAYS / NEVER   the guarded checks or sources always / never apply
    "text", 0, 1     a literal: the cache value, the substituted value, a dep
                     label, a condition expression or a config_setting label
    True / False     the default of a configure option's bool_flag
    select({{...}})    a literal per configuration label ("//conditions:default" last)
    checks.X(...)    a check written exactly as it would appear in BUILD.bazel,
    macros.X(...)    or a list of them; a replacement probe must set the cache
                     variable it replaces (name = "...")

Which of these a key takes depends on its kind, the part before the colon:

    probe:<cache variable>    IGNORE | ACCEPT | a literal | select() | a replacement
                              probe (checks.AC_TRY_COMPILE / checks.AC_TRY_LINK) | checks
    variable:<NAME>           IGNORE | ACCEPT_PARTIAL | a literal | select() | checks
    guard:<target>:<check>    ALWAYS | NEVER | ["<cache variable>", ...] (requires) |
                              select({{"<label>": ["<cache variable>", ...]}})
    macro:<NAME>              IGNORE | "<dep label>" | checks
    actions:<macro>:<arg>     IGNORE | checks
    flag:<option>             IGNORE | True | False
    am_conditional:<NAME>     ALWAYS | NEVER | ":<config_setting>"
    src_condition:<NAME>      ALWAYS | NEVER | "<condition expression>"
    generated:<path>          IGNORE | a rule writing the file, as it would appear in
                              BUILD.bazel (genrule(...), run_binary(...)); a load() it
                              needs goes first: ["load(...)", run_binary(...)]
    cppflag:<dir>:<NAME>      IGNORE | "<value>" (a -D value only configure knows)
    link:<dir>/<target>:<item> IGNORE | ":<label>" (a dep) | "-l<name>" (a linkopt)

Three kinds are never asked for and apply when present: when Bazel reports a
define, subst or check "defined both locally and in dependencies", the
project's own copy is dropped with

    define:<NAME>             IGNORE
    subst:<NAME>              IGNORE
    check:<cache variable>    IGNORE

These words are read by the converter, not by Bazel: the file is parsed, never
loaded.  Entries the converter stops asking for are reported as unused.
"""

'''


CONTEXT_LINES = 40  # longest context block in the skeleton's comments


def comment_block(request: Request, indent: str) -> list[str]:
    """The request's context as `#` lines."""
    lines = [f"{indent}# {request.where}: {request.summary}"]
    for name, value in request.context.items():
        if value in (None, "", [], {}):
            continue
        rendered = _render_context(value)
        if len(rendered) > CONTEXT_LINES:
            rendered = rendered[:CONTEXT_LINES] + [f"... ({len(rendered) - CONTEXT_LINES} more lines)"]
        if len(rendered) == 1:
            lines.append(f"{indent}# {name}: {rendered[0]}")
        else:
            lines.append(f"{indent}# {name}:")
            lines.extend(f"{indent}#   {line}".rstrip() for line in rendered)
    return lines


def _render_context(value) -> list[str]:
    """A request's context value as comment lines: a string line by line, a list item by item
    (multi-line items under a `-`), a dict as `key: value` or `key:` followed by indented lines.
    """
    if isinstance(value, str):
        return value.replace("\r", "").split("\n")
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            rendered = _render_context(item)
            if isinstance(item, (list, dict)) and len(rendered) > 1:
                out.append("-")
                out.extend("  " + line for line in rendered)
            else:
                out.extend(rendered)
        return out
    if isinstance(value, dict):
        out = []
        for name, item in value.items():
            rendered = _render_context(item)
            if len(rendered) == 1:
                out.append(f"{name}: {rendered[0]}")
            else:
                out.append(f"{name}:")
                out.extend("  " + line for line in rendered)
        return out
    return [str(value)]
