"""The second rendering of a project-mode package: the BUILD text `emit` renders, as the
generated `autoconf.bzl` and the hand-written `BUILD.bazel` that loads it.

`autoconf.bzl` holds the package's targets as the body of one macro, `autoconf_targets()`, with
the constants several rules share at the top level and `visibility("private")`, so only its own
package loads it.  It is made from the BUILD text after buildifier formatted that as a BUILD
file (one argument per line, the lists it sorts sorted), so the statements read as they would
in a BUILD file.  `BUILD.bazel` is written once, as a stub that calls the macro, and then
belongs to the maintainer: a rule it declares with a generated target's name replaces that
target (`emit.omit_replaced`).
"""

from __future__ import annotations

import ast
import re
import textwrap
from typing import TypeGuard

from tools.convert.emit import (
    CHECKS_FILE,
    INDENT,
    LOAD_FILES,
    LOAD_LINE_RE,
    NAMESPACES,
    load_statement,
    merge_loads,
    quote,
    split_loads,
)

MACRO = "autoconf_targets"
"""The macro `autoconf.bzl` defines: the package's generated targets, called from its BUILD.bazel."""
BZL_NAME = "autoconf.bzl"
"""The generated file of a project-mode package, beside the hand-written BUILD.bazel that loads it."""
BUILD_NAME = "BUILD.bazel"
"""The hand-written file: written once as a stub, never regenerated."""
RULES_REPO = "@rules_cc_autoconf"
"""The repository of the rules, as a generated module names it."""

# What a BUILD file calls by a bare name that a .bzl file has as well; any other bare callee
# a BUILD file has without a `load()` is a member of `native` in a .bzl file.
_STARLARK_BUILTINS = frozenset(
    {
        "load",
        "visibility",
        "select",
        "fail",
        "print",
        "len",
        "str",
        "int",
        "bool",
        "float",
        "list",
        "dict",
        "tuple",
        "range",
        "sorted",
        "reversed",
        "enumerate",
        "zip",
        "min",
        "max",
        "any",
        "all",
        "hasattr",
        "getattr",
        "dir",
        "repr",
        "type",
        "hash",
        "struct",
        "depset",
        "Label",
        "provider",
    }
)
_DOCSTRING_RE = re.compile(r'\A("""|\'\'\')(.*?)\1[ \t]*\n?', re.DOTALL)
_INNER_ASSIGN_RE = re.compile(rf"^{INDENT}([A-Z][A-Z0-9_]*) = ")
_STUB_COMMENT = (
    "# The targets of configure.ac and the Makefile.am files, declared by the generated\n"
    "# autoconf.bzl beside this file; this file is hand-written and kept as it is.  A target\n"
    "# added below is the module's own; one named like a generated target replaces it at the\n"
    "# next conversion."
)
_SUMMARY = "The targets configure.ac and the Makefile.am files describe, in the package that calls this."


def _loaded_symbols(module: ast.Module) -> set[str]:
    """The names the `load()` statements of a parsed BUILD text bind."""
    out: set[str] = set()
    for node in module.body:
        call = node.value if isinstance(node, ast.Expr) else None
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "load":
            out |= {a.value for a in call.args[1:] if isinstance(a, ast.Constant) and isinstance(a.value, str)}
            out |= {k.arg for k in call.keywords if k.arg}
    return out


def _is_native_call(node: ast.AST, loaded: set[str]) -> TypeGuard[ast.Call]:
    """A call of a bare name no `load()` binds and Starlark has no builtin for: a member of
    `native` in a .bzl file."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id not in loaded
        and node.func.id not in _STARLARK_BUILTINS
    )


def _end_lineno(node: ast.stmt | ast.expr) -> int:
    """The last line of a parsed node (`ast.parse` sets it on every statement and expression)."""
    if node.end_lineno is None:
        raise ValueError(f"{type(node).__name__} at line {node.lineno} has no end line")
    return node.end_lineno


def _comprehension_as_loop(chunk: list[str]) -> list[str]:
    """`render_each`'s comprehension statement as a `for` loop: inside a function a bare
    comprehension is an expression without effect (buildifier's `no-effect`).  A loop variable
    the body does not read gets the `_` prefix (`unused-variable`)."""
    open_at = chunk.index("[")
    if chunk[-1] != "]" or not chunk[-2].startswith(f"{INDENT}for "):
        return chunk
    body = chunk[open_at + 1 : -2]
    target, _, source = chunk[-2].strip()[len("for ") :].partition(" in ")
    body_text = "\n".join(body)
    variables = [
        v if re.search(rf"\b{re.escape(v)}\b", body_text) else f"_{v}"
        for v in (part.strip() for part in target.split(","))
    ]
    return chunk[:open_at] + [f"for {', '.join(variables)} in {source}:"] + body


def _without_unread_constants(chunks: list[list[str]]) -> list[list[str]]:
    """The macro's statements without a constant no other statement reads (gawk's BISON_HDRS
    once BUILD.bazel declares the program that read it): an unused variable in a function,
    which buildifier flags; at the top level it would be loadable and is kept."""
    while True:
        names = {
            m.group(1): i
            for i, chunk in enumerate(chunks)
            for m in [_INNER_ASSIGN_RE.match(next(line for line in chunk if not line.lstrip().startswith("#")))]
            if m
        }
        unread = [
            i
            for name, i in names.items()
            if not any(re.search(rf"\b{name}\b", "\n".join(c)) for j, c in enumerate(chunks) if j != i)
        ]
        if not unread:
            return chunks
        chunks = [c for i, c in enumerate(chunks) if i not in unread]


def to_bzl(text: str, *, replaced: list[str] | None = None) -> str:
    """A rendered (and buildifier-formatted) BUILD text as the package's `autoconf.bzl`: the
    docstring and the `load()`s stay at the top, `visibility("private")` follows, a constant
    stays at the top level unless it calls `glob()` or reads one that moved, and everything else
    goes into the `autoconf_targets()` macro: indented, `native.` on what a BUILD file calls
    without a load, a comprehension statement written as a `for` loop.  Lines that continue a
    multi-line string keep their column.  `replaced` names the generated targets left out for
    BUILD.bazel's own; the macro's docstring says so."""
    lines = text.splitlines()
    module = ast.parse(text)
    loaded = _loaded_symbols(module)
    # Right to left on each line, so the earlier columns hold.
    for native_call in sorted(
        (n for n in ast.walk(module) if _is_native_call(n, loaded)),
        key=lambda n: (n.lineno, n.col_offset),
        reverse=True,
    ):
        line = lines[native_call.lineno - 1]
        col = len(line.encode("utf-8")[: native_call.col_offset].decode("utf-8", errors="replace"))
        lines[native_call.lineno - 1] = f"{line[:col]}native.{line[col:]}"
    continued = {
        line_no
        for n in ast.walk(module)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and _end_lineno(n) > n.lineno
        for line_no in range(n.lineno + 1, _end_lineno(n) + 1)
    }
    docstring: list[str] = []
    loads: list[str] = []
    top: list[list[str]] = []
    inner: list[list[str]] = []
    moved: set[str] = set()
    previous_end = 0
    for index, node in enumerate(module.body):
        # The comment lines above a statement belong to it.
        chunk_start = previous_end + 1
        previous_end = _end_lineno(node)
        chunk = lines[chunk_start - 1 : previous_end]
        while chunk and not chunk[0].strip():
            chunk.pop(0)
            chunk_start += 1
        call = node.value if isinstance(node, ast.Expr) else None
        if index == 0 and isinstance(call, ast.Constant) and isinstance(call.value, str):
            docstring = chunk
            continue
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "load":
            loads += chunk
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            reads = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            if not reads & moved and not any(_is_native_call(n, loaded) for n in ast.walk(node.value)):
                top.append(chunk)
                continue
            moved.add(node.targets[0].id)
        if isinstance(call, ast.ListComp):
            chunk = _comprehension_as_loop(chunk)
        if any(line.startswith("native.package(") for line in chunk):
            # Allowed in a macro; buildifier advises against it in .bzl files in general.
            chunk = ["# buildifier: disable=native-package", *chunk]
        inner.append(
            [line if not line or chunk_start + i in continued else INDENT + line for i, line in enumerate(chunk)]
        )
    inner = _without_unread_constants(inner)
    # buildifier's function-docstring-header: a one-line summary first.
    doc = [f'{INDENT}"""{_SUMMARY}"""']
    if replaced:
        left_out = textwrap.wrap(f"Left out, since BUILD.bazel declares them: {', '.join(replaced)}.", 95)
        doc = [f'{INDENT}"""{_SUMMARY}', "", *(f"{INDENT}{line}" for line in left_out), f'{INDENT}"""']
    out: list[str] = []
    if docstring:
        out += docstring + [""]
    if loads:
        out += loads + [""]
    out += ['visibility("private")', ""]
    for chunk in top:
        out += chunk + [""]
    out += ["# buildifier: disable=unnamed-macro", f"def {MACRO}():", *doc]
    body = [line for chunk in inner for line in [""] + chunk] or ["", f"{INDENT}pass"]
    return "\n".join(out + body) + "\n"


def assigned_names(bzl_text: str) -> tuple[set[str], set[str]]:
    """The names an `autoconf.bzl` assigns at the top level (loadable by BUILD.bazel) and inside
    the macro (not)."""
    module = ast.parse(bzl_text)
    top = {t.id for n in module.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    inner = {
        t.id
        for f in module.body
        if isinstance(f, ast.FunctionDef)
        for n in ast.walk(f)
        if isinstance(n, ast.Assign)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    return top, inner


def names_used(text: str) -> set[str]:
    """Every name a BUILD text reads: the rules it calls, the namespaces it reads
    (`checks.AC_DEFINE`), the constants it uses (`NO_WARNINGS`).  A text Python cannot parse
    (a Starlark-only construct) falls back to its identifiers, an over-approximation."""
    try:
        return {n.id for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Name)}
    except SyntaxError:
        return set(re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)", text))


def loads_needed(hand_text: str, bzl_text: str) -> list[str]:
    """The `load()` statements a hand-written BUILD text needs for the names it uses without
    loading them: from `autoconf.bzl`'s loads first, then from the table of known rules for what
    the generated file no longer loads because BUILD.bazel took over its only user (make's
    cc_binary).  For the layout before `autoconf.bzl`, which let the hand text lean on the
    generated file's loads."""
    try:
        hand_loaded = _loaded_symbols(ast.parse(hand_text))
    except SyntaxError:
        hand_loaded = set()
    needed = names_used(hand_text) - hand_loaded - _STARLARK_BUILTINS
    out: list[str] = []
    for statement in split_loads(bzl_text)[0]:
        match = LOAD_LINE_RE.fullmatch(statement.strip())
        if not match:
            continue
        entries = {
            (s.split("=")[0].strip() if "=" in s else s.strip().strip('"')): s.strip()
            for s in match.group(2).split(",")
        }
        wanted = [entry for name, entry in entries.items() if name in needed]
        if wanted:
            out.append(f"load({match.group(1)}, " + ", ".join(wanted) + ")")
            needed -= set(entries)
    known = {**LOAD_FILES, **{name: CHECKS_FILE for name in NAMESPACES}}
    out += [load_statement(known[name], [quote(name)], repo=RULES_REPO) for name in sorted(needed & set(known))]
    return merge_loads(out)


def build_file(hand_text: str = "", *, symbols: list[str] | None = None, loads: list[str] | None = None) -> str:
    """The hand-written BUILD.bazel of a generated package as the converter writes it once: a
    docstring the hand text opens with, the `load()`s (the hand text's, `loads`, and
    `autoconf.bzl`'s macro with the top-level constants `symbols` the hand text reads), the
    macro call under its comment unless the text has one, then the hand text as given.
    `hand_text` is empty for the plain stub."""
    hand_loads, body = split_loads(hand_text)
    body = body.strip("\n")
    docstring = ""
    match = _DOCSTRING_RE.match(body)
    if match:
        docstring = match.group(0).rstrip("\n")
        body = body[match.end() :].strip("\n")
    ours = load_statement(f":{BZL_NAME}", [quote(s) for s in sorted({MACRO, *(symbols or ())})])
    parts = [docstring] if docstring else []
    parts.append("\n".join(merge_loads([*hand_loads, *(loads or []), ours])))
    if not re.search(rf"^{MACRO}\(", body, re.MULTILINE):
        parts.append(f"{_STUB_COMMENT}\n{MACRO}()")
    if body:
        parts.append(body)
    return "\n\n".join(parts) + "\n"
