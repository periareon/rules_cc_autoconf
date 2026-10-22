"""Render `ir.Package`s as Starlark in the style buildifier leaves behind.

The output is meant to be a fixed point of `buildifier`: four-space
indentation, one element per line with trailing commas for multi-element
lists and keyword calls, `name` first then `checks`, `visibility`, `deps`,
and `deps` sorted the way buildifier sorts labels.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Mapping, Sequence
from typing import assert_never

from tools.convert.ir import (
    DEFAULT_ARM,
    PLATFORM_FAMILIES,
    Assign,
    AttrValue,
    Call,
    CheckItem,
    Dep,
    Each,
    Package,
    Raw,
    Rule,
    Statement,
    Target,
)

INDENT = "    "


def quote(text: str) -> str:
    """A Starlark string literal."""
    if "\n" in text:
        body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return '"""\\\n' + body + '"""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_scalar(value: AttrValue) -> bool:
    return isinstance(value, (str, int, bool)) or value is None


def _short(value: AttrValue) -> bool:
    return _is_scalar(value) and not (isinstance(value, str) and "\n" in value)


def fmt(value: AttrValue, indent: str) -> str:
    """Render a value; continuation lines are indented relative to `indent`."""
    if isinstance(value, Raw):
        return value.text
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, Call):
        return fmt_call(value, indent)
    if isinstance(value, Sequence):
        return fmt_list(value, indent)
    if isinstance(value, Mapping):
        inner = indent + INDENT
        lines = ["{"]
        for key, item in dict_items(value):
            lines.append(f"{inner}{fmt(key, inner)}: {fmt(item, inner)},")
        lines.append(indent + "}")
        return "\n".join(lines)
    assert_never(value)


def dict_key_order(key: str) -> tuple[int, str]:
    """buildifier's order for the keys of a dict literal (`unsorted-dict-items`): plain string
    order, with `//conditions:default` after the other keys and keys that start with an
    underscore last."""
    return (2 if key.startswith("_") else 1 if key == "//conditions:default" else 0, key)


def dict_items[V](value: Mapping[str, V]) -> list[tuple[str, V]]:
    """The items of a dict in the order buildifier wants them written, when every key is a
    string; as given otherwise."""
    if all(isinstance(k, str) for k in value):
        return sorted(value.items(), key=lambda kv: dict_key_order(kv[0]))
    return list(value.items())


def fmt_list(items: Sequence[AttrValue], indent: str, *, force: bool = False) -> str:
    """A Starlark list: inline when it has one short item, one item per line otherwise, with a
    Call's comments above it.  With `force`, always one element per line (checks lists).
    """
    if not items:
        return "[]"
    if not force and len(items) == 1 and _short(items[0]):
        return f"[{fmt(items[0], indent)}]"
    inner = indent + INDENT
    lines = ["["]
    for item in items:
        if isinstance(item, Call):
            lines.extend(comment_lines(item.comments, inner))
        lines.append(f"{inner}{fmt(item, inner)},")
    lines.append(indent + "]")
    return "\n".join(lines)


def comment_lines(comments: list[str], indent: str) -> list[str]:
    """`# ...` lines; a comment with embedded newlines becomes several."""
    out = []
    for comment in comments:
        for line in comment.replace("\r", "").split("\n"):
            out.append(f"{indent}# {line.rstrip()}" if line.strip() else f"{indent}#")
    return out


def fmt_call(call: Call, indent: str, *, force: bool = False, trailing: str = "") -> str:
    """Positional-only calls with short scalars stay on one line; with `force`, always one
    argument per line (list-valued macro calls).

    A `trailing` comment on the call goes on its own line inside the parentheses, which
    is where buildifier moves a trailing comment after `(`.
    """
    if not force and not call.kwargs and all(_short(a) for a in call.args):
        text = f"{call.fn}({', '.join(fmt(a, indent) for a in call.args)})"
        if len(text) + len(indent) <= 100:
            return text
    inner = indent + INDENT
    lines = [f"{call.fn}("]
    if trailing:
        lines.extend(comment_lines([trailing], inner))
    for arg in call.args:
        lines.append(f"{inner}{fmt(arg, inner)},")
    for key in ordered_keys(call.kwargs):
        lines.append(f"{inner}{key} = {fmt_kwarg(key, call.kwargs[key], inner)},")
    lines.append(indent + ")")
    return "\n".join(lines)


_ORDERED_LINE_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b")


def fmt_kwarg(key: str, value: AttrValue, indent: str) -> str:
    """A keyword argument; an `includes` list with preprocessor conditionals
    keeps its order (buildifier sorts `includes` lists otherwise)."""
    if (
        key == "includes"
        and isinstance(value, list)
        and len(value) > 1
        and any(isinstance(v, str) and _ORDERED_LINE_RE.match(v) for v in value)
    ):
        inner = indent + INDENT
        lines = ["[", f"{inner}# do not sort"]
        lines += [f"{inner}{fmt(v, inner)}," for v in value]
        lines.append(indent + "]")
        return "\n".join(lines)
    return fmt(value, indent)


def ordered_keys(kwargs: Mapping[str, AttrValue]) -> list[str]:
    """`name` first, the rest alphabetically (the hand ports' convention)."""
    keys = sorted(kwargs)
    if "name" in keys:
        keys.remove("name")
        keys.insert(0, "name")
    return keys


def label_sort_key(label: str) -> tuple[int, str]:
    """buildifier's label order: `:local`, then `//`, then `@`, lexicographic."""
    if label.startswith(":"):
        group = 0
    elif label.startswith("//"):
        group = 1
    else:
        group = 2
    return (group, label)


def fmt_deps(deps: list[Dep], indent: str) -> str:
    inner = indent + INDENT
    ordered = sorted(deps, key=lambda d: label_sort_key(d.label))
    if len(ordered) == 1 and not ordered[0].comment:
        return f"[{quote(ordered[0].label)}]"
    lines = ["["]
    for dep in ordered:
        line = f"{inner}{quote(dep.label)},"
        if dep.comment:
            line += f"  # {dep.comment}"
        lines.append(line)
    lines.append(indent + "]")
    return "\n".join(lines)


def fmt_checks(items: Sequence[CheckItem], indent: str) -> str:
    """`[...] + macros.X(...) + [...]`: list segments and list-valued calls."""
    segments: list[list[CheckItem] | Call | Raw] = []
    current: list[CheckItem] | None = None  # the open list segment, closed by a list-valued item
    for item in items:
        if (isinstance(item, Call) and item.returns_list) or isinstance(item, Raw):
            segments.append(item)
            current = None
        else:
            if current is None:
                current = []
                segments.append(current)
            current.append(item)
    rendered: list[str] = []
    for segment in segments:
        if isinstance(segment, list):
            rendered.append(fmt_list(segment, indent, force=True))
        elif isinstance(segment, Raw):
            rendered.append(segment.text)
        else:
            rendered.append(fmt_call(segment, indent, force=True, trailing="; ".join(segment.comments)))
    return " + ".join(rendered)


def strip_annotations(package: Package) -> None:
    """Drop every comment and source location from the IR (the default output).

    The translator records where each check, dep and rule came from; with
    `--annotate` that is rendered as comments, otherwise the BUILD file stays
    short.
    """

    def value(item: AttrValue) -> None:
        if isinstance(item, Call):
            item.comments = []
            for arg in item.args:
                value(arg)
            for arg in item.kwargs.values():
                value(arg)
        elif isinstance(item, list):
            for element in item:
                value(element)
        elif isinstance(item, dict):
            for element in item.values():
                value(element)

    for statement in [*package.before, *package.targets, *package.after]:
        if isinstance(statement, Target):
            statement.comments = []
            statement.loc = None
            for item in statement.checks:
                value(item)
            for items in statement.arm_lists:
                for item in items:
                    value(item)
            for dep in statement.deps + statement.default_deps:
                dep.comment = None
        elif isinstance(statement, Rule):
            statement.comments = []
            value(statement.attrs)
        elif isinstance(statement, Assign):
            statement.comments = []
            value(statement.value)
        elif isinstance(statement, Each):
            statement.comments = []
            value(statement.attrs)


def arm_sort_key(label: str) -> tuple[int, str]:
    return (1, "") if label == DEFAULT_ARM else (0, label)


def _fmt_arm_checks(items: Sequence[CheckItem], indent: str) -> str:
    # An arm holds list-valued macro calls too (libpcap's per-OS
    # AC_CHECK_HEADERS): `[...] + macros.X(...)`, never a nested list.
    return fmt_checks(items, indent) if items else "[]"


def fmt_select[ArmItem: CheckItem](
    arms: Mapping[str, Sequence[ArmItem]],
    indent: str,
    render: Callable[[Sequence[ArmItem], str], str] = _fmt_arm_checks,
) -> str:
    """A `select({...})` over `arms`, the default arm last (added empty when absent), each arm's
    items rendered by `render(items, inner_indent)`."""
    inner = indent + INDENT
    lines = ["select({"]
    labels = sorted(arms, key=arm_sort_key)
    if DEFAULT_ARM not in labels:
        labels.append(DEFAULT_ARM)
    for label in labels:
        lines.append(f"{inner}{quote(label)}: {render(arms.get(label, []), inner)},")
    lines.append(indent + "})")
    return "\n".join(lines)


def fmt_all_checks(target: Target, indent: str) -> str:
    parts = []
    if target.checks:
        parts.append(fmt_checks(target.checks, indent))
    for family in sorted(target.selects, key=lambda f: (f not in PLATFORM_FAMILIES, f)):
        parts.append(fmt_select(target.selects[family], indent))
    return " + ".join(parts)


def _fmt_dep_list(deps: list[Dep], suffix: list[str], indent: str) -> str:
    rendered = fmt_deps(deps, indent) if deps else ""
    joined = " + ".join(suffix)
    return f"{rendered} + {joined}" if rendered and joined else (rendered or joined)


_DEFAULTS_ATTR = {"checks.AC_SUBST": "substs", "checks.M4_VARIABLE": "variables", "checks.AC_DEFINE": "defines"}


def fmt_defaults_dict(items: Sequence[Call], indent: str) -> str:
    """`{"NAME": "value", ...}` from literal AC_SUBST / M4_VARIABLE / AC_DEFINE calls, their
    comments above the entries."""
    inner = indent + INDENT
    lines = ["{"]
    for item in items:
        lines += comment_lines(item.comments, inner)
        lines.append(f"{inner}{quote(str(item.args[0]))}: {quote(str(item.args[1]))},")
    lines.append(indent + "}")
    return "\n".join(lines)


def fmt_defaults(target: Target, indent: str) -> dict[str, str]:
    """The `substs`, `variables` and `defines` of an autoconf_defaults target: the plain
    checks as one dict each, a select() over the per-family arms appended with `|`."""
    out: dict[str, str] = {}
    for fn, attr in _DEFAULTS_ATTR.items():
        plain = [c for c in target.checks if isinstance(c, Call) and c.fn == fn]
        parts = [fmt_defaults_dict(plain, indent)] if plain else []
        for family in target.selects:
            arms = {
                label: [c for c in items if isinstance(c, Call) and c.fn == fn]
                for label, items in target.selects[family].items()
            }
            if not any(arms.values()):
                continue
            parts.append(fmt_select(arms, indent, render=fmt_defaults_dict))
        if parts:
            out[attr] = " | ".join(parts)
    return out


def render_target(target: Target) -> str:
    """An `autoconf(...)` rule: comments and location above it, then name, checks (the plain ones
    plus one select() per family), the extra attributes, default_deps, visibility and deps, in
    buildifier's attribute order.  An `autoconf_defaults(...)` rule renders its literal checks
    as `substs` / `variables` / `defines` and the defaults it composes as `deps`.
    """
    lines = comment_lines(target.comments, "")
    if target.loc:
        lines.append(f"# {target.loc}")
    lines.append(f"{target.kind}(")
    lines.append(f"{INDENT}name = {quote(target.name)},")
    rendered: dict[str, str] = {}
    if target.kind == "autoconf_defaults":
        rendered.update(fmt_defaults(target, INDENT))
        deps = target.default_deps + target.deps
        if deps or target.default_deps_suffix or target.deps_suffix:
            rendered["deps"] = _fmt_dep_list(deps, target.default_deps_suffix + target.deps_suffix, INDENT)
    else:
        if target.checks or target.selects:
            rendered["checks"] = fmt_all_checks(target, INDENT)
        if target.default_deps or target.default_deps_suffix:
            rendered["default_deps"] = _fmt_dep_list(target.default_deps, target.default_deps_suffix, INDENT)
        if target.deps or target.deps_suffix:
            rendered["deps"] = _fmt_dep_list(target.deps, target.deps_suffix, INDENT)
    for key, value in target.attrs.items():
        rendered[key] = fmt_kwarg(key, value, INDENT)
    rendered["visibility"] = f"[{quote(target.visibility)}]"
    for key in sorted(rendered, key=attr_sort_key):
        lines.append(f"{INDENT}{key} = {rendered[key]},")
    lines.append(")")
    return "\n".join(lines)


# buildifier's attribute order: a priority per well-known name, 0 (then
# alphabetical) for the rest.
ATTR_PRIORITY = {
    "name": -99,
    "archive": -98,
    "gwt_name": -98,
    "gwt_module": -97,
    "size": -96,
    "timeout": -95,
    "testonly": -94,
    "src": -93,
    "srcdir": -92,
    "srcs": -91,
    "out": -90,
    "outs": -89,
    "hdrs": -88,
    "has_services": -87,
    "include": -86,
    "of": -85,
    "baseline": -84,
    "package_name": -83,
    "destdir": 1,
    "exports": 2,
    "runtime_deps": 3,
    "deps": 4,
    "implementation": 5,
    "implements": 6,
    "alwayslink": 7,
}


def attr_sort_key(name: str) -> tuple[int, str]:
    return (ATTR_PRIORITY.get(name, 0), name)


def render_rule(rule: Rule) -> str:
    lines = comment_lines(rule.comments, "")
    lines.append(f"{rule.kind}(")
    ordered = sorted(["name"] + [k for k in rule.attrs if k != "name"], key=attr_sort_key)
    for key in ordered:
        value = rule.name if key == "name" else rule.attrs[key]
        lines.append(f"{INDENT}{key} = {fmt(value, INDENT)},")
    lines.append(")")
    return "\n".join(lines)


def render_assign(assign: Assign) -> str:
    lines = comment_lines(assign.comments, "")
    lines.append(f"{assign.name} = {fmt(assign.value, '')}")
    return "\n".join(lines)


def render_each(each: Each) -> str:
    inner = INDENT + INDENT
    lines = comment_lines(each.comments, "")
    lines += ["[", f"{INDENT}{each.kind}("]
    for key in sorted(each.attrs, key=attr_sort_key):
        lines.append(f"{inner}{key} = {fmt(each.attrs[key], inner)},")
    lines += [f"{INDENT})", f"{INDENT}for {each.over}", "]"]
    return "\n".join(lines)


def render_statement(statement: Statement) -> str:
    if isinstance(statement, Target):
        return render_target(statement)
    if isinstance(statement, Rule):
        return render_rule(statement)
    if isinstance(statement, Assign):
        return render_assign(statement)
    if isinstance(statement, Each):
        return render_each(statement)
    if isinstance(statement, Raw):
        return statement.text.rstrip("\n")
    raise TypeError(f"cannot render {statement!r}")


# Where a rule the generated text calls is loaded from; a `//` path is this repository's and
# gets the rendering repo in front.  Found in the rendered text, so the comprehensions over
# GNULIB_GENERATED_HDRS count too and a load stays even when every `Rule` of the kind is
# replaced by BUILD.bazel's (cpio's `config`, the one autoconf_hdr Rule beside two
# comprehensions of them).
LOAD_FILES = {
    "autoconf": "//autoconf:autoconf.bzl",
    "autoconf_defaults": "//autoconf:autoconf_defaults.bzl",
    "autoconf_hdr": "//autoconf:autoconf_hdr.bzl",
    "autoconf_linkopts": "//autoconf:autoconf_linkopts.bzl",
    "autoconf_package_info": "//autoconf:autoconf_package_info.bzl",
    "autoconf_srcs": "//autoconf:autoconf_srcs.bzl",
    "cc_gnulib_conditional_hdrs": "//gnulib:conditional_hdrs.bzl",
    "bool_flag": "@bazel_skylib//rules:common_settings.bzl",
    "string_flag": "@bazel_skylib//rules:common_settings.bzl",
    "copy_file": "@bazel_skylib//rules:copy_file.bzl",
    "write_file": "@bazel_skylib//rules:write_file.bzl",
    "cc_binary": "@rules_cc//cc:cc_binary.bzl",
    "cc_library": "@rules_cc//cc:cc_library.bzl",
    "cc_shared_library": "@rules_cc//cc:cc_shared_library.bzl",
}
CHECKS_FILE = "//autoconf:checks.bzl"
NAMESPACES = ("checks", "macros", "utils")
"""The check namespaces of `CHECKS_FILE`, loaded when the text reads a member of one."""
LOAD_LINE_RE = re.compile(r'load\(("[^"]+"),\s*(.+)\)')
"""A single-line `load()`: the quoted file and the symbol list."""


def load_statement(path: str, symbols: list[str], *, repo: str = "") -> str:
    """`load("<path>", <symbols>)`, the symbols as written (`"x"`, `alias = "x"`); a `//` path
    gets `repo` in front."""
    return f"load({quote(repo + path if path.startswith('//') else path)}, " + ", ".join(symbols) + ")"


def merge_loads(statements: list[str]) -> list[str]:
    """One `load()` per file: the symbols of single-line statements for the same file joined
    and sorted; multi-line statements pass through.  Sorted by file, as buildifier orders them."""
    by_file: dict[str, set[str]] = {}
    passthrough: list[str] = []
    for statement in statements:
        match = LOAD_LINE_RE.fullmatch(statement.strip())
        if match and "\n" not in statement:
            by_file.setdefault(match.group(1), set()).update(s.strip() for s in match.group(2).split(","))
        else:
            passthrough.append(statement)
    merged = [f"load({path}, " + ", ".join(sorted(symbols)) + ")" for path, symbols in by_file.items()]
    return sorted(set(merged + passthrough), key=lambda s: s.split('"')[1])


def loads(body: str, package: Package, *, repo: str, in_repo: bool) -> list[str]:
    """The `load()` statements the rendered body needs, one per file, sorted."""
    statements = []
    kinds = {t.kind for t in package.targets}
    for kind, path in LOAD_FILES.items():
        if kind in kinds or re.search(rf"^\s*{kind}\(", body, re.MULTILINE):
            if kind == "autoconf" and in_repo:
                # The ports of this repository use the toolchain's cache rule under the same name.
                statements.append('load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")')
            else:
                statements.append(load_statement(path, [quote(kind)], repo=repo))
    # `utils.AC_LANG_PROGRAM(`, not ppp's source "pppd/utils.c": the namespaces' members are
    # macro-cased.
    symbols = [s for s in NAMESPACES if re.search(rf"(?<![\w/]){s}\.(?=[A-Z_])", body)]
    if symbols:
        statements.append(load_statement(CHECKS_FILE, [quote(s) for s in symbols], repo=repo))
    if re.search(r"\bgl_macros\.", body):
        statements.append(load_statement("//gnulib:macros.bzl", ['gl_macros = "macros"'], repo=repo))
    for statement in package.before + package.after:
        if isinstance(statement, (Rule, Each)) and statement.load_from:
            symbol = statement.load_symbol or statement.kind
            local = statement.kind.split(".")[0]  # `selects.config_setting_group` loads `selects`
            entry = quote(symbol) if symbol == local else f"{local} = {quote(symbol)}"
            statements.append(load_statement(statement.load_from, [entry], repo=repo))
    # The decisions' loads (`package.loads`) merge into the same files' statements.
    return merge_loads(statements + package.loads)


_LOAD_START_RE = re.compile(r"^load\(", re.MULTILINE)


def split_loads(text: str) -> tuple[list[str], str]:
    """The top-level `load(...)` statements of a BUILD text (multi-line ones included) and the
    text without them."""
    loads: list[str] = []
    rest = []
    pos = 0
    for match in _LOAD_START_RE.finditer(text):
        if match.start() < pos:
            continue
        depth = 0
        end = match.start()
        for end in range(match.start(), len(text)):
            if text[end] == "(":
                depth += 1
            elif text[end] == ")":
                depth -= 1
                if depth == 0:
                    break
        end += 1
        if text[end : end + 1] == "\n":
            end += 1
        rest.append(text[pos : match.start()])
        loads.append(text[match.start() : end].rstrip("\n"))
        pos = end
    rest.append(text[pos:])
    return loads, "".join(rest)


_CALL_START_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*)\(", re.MULTILINE)
_NAME_KWARG_RE = re.compile(r'\bname\s*=\s*"([^"]+)"')


def rule_names(text: str) -> list[str]:
    """The `name` of every top-level rule call in a BUILD text, in order.  Parsed as Python
    (a BUILD file is one syntactically); a text Python rejects falls back to a scan of the
    calls that start at column 0."""
    try:
        module = ast.parse(text)
    except SyntaxError:
        names = []
        for match in _CALL_START_RE.finditer(text):
            end = text.find("\n)", match.end())
            name = _NAME_KWARG_RE.search(text, match.end(), end if end >= 0 else len(text))
            if name:
                names.append(name.group(1))
        return names
    names = []
    for statement in module.body:
        call = statement.value if isinstance(statement, ast.Expr) else None
        if isinstance(call, ast.Call):
            for keyword in call.keywords:
                if (
                    keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    names.append(keyword.value.value)
    return names


def statement_names(statement: Statement) -> list[str]:
    """The target names a statement declares (a Raw may hold several rules)."""
    if isinstance(statement, (Target, Rule)):
        return [statement.name]
    if isinstance(statement, Raw):
        return rule_names(statement.text)
    return []


def omit_replaced(package: Package, hand_names: set[str], hand_text: str = "") -> list[str]:
    """Drop the generated statements whose every declared name the hand-written BUILD.bazel
    declares too (`hand_names`): that rule stands under the label instead, and consumers are
    unchanged.  A Raw statement holding a replaced rule among others is kept whole: that case
    is reported, not resolved.  A constant nothing left reads goes with them, the readers being
    the remaining statements and `hand_text`, the BUILD.bazel's text (gmp's NO_WARNINGS goes
    once every library is replaced; bison's stays for COPTS, libpcap's for the program
    BUILD.bazel declares with it).  Returns the names dropped, for the report and the macro's
    docstring."""
    replaced: list[str] = []
    _drop_replaced(package.targets, hand_names, replaced)
    _drop_replaced(package.before, hand_names, replaced)
    _drop_replaced(package.after, hand_names, replaced)
    if replaced:
        rendered = {id(st): render_statement(st) for st in [*package.targets, *package.before, *package.after]}
        while True:
            unread = [
                st
                for st in package.before + package.after
                if isinstance(st, Assign)
                and not re.search(
                    rf"\b{st.name}\b",
                    "\n".join([text for key, text in rendered.items() if key != id(st)] + [hand_text]),
                )
            ]
            if not unread:
                break
            for statements in (package.before, package.after):
                statements[:] = [st for st in statements if st not in unread]
            for st in unread:
                del rendered[id(st)]
    return replaced


def _drop_replaced[StatementT: Statement](
    statements: list[StatementT], hand_names: set[str], replaced: list[str]
) -> None:
    """Remove from `statements`, in place, the ones whose every declared name is in `hand_names`,
    appending the names to `replaced`."""
    kept: list[StatementT] = []
    for statement in statements:
        declared = statement_names(statement)
        if declared and all(n in hand_names for n in declared):
            replaced += declared
            continue
        kept.append(statement)
    statements[:] = kept


def render_package(package: Package, *, repo: str = "", in_repo: bool = True) -> str:
    statements = [*package.before, *package.targets, *package.after]
    body = "\n\n".join(render_statement(s) for s in statements)
    # A generated file's banner is a comment block; any other docstring is quoted.
    if package.docstring.startswith("#"):
        head = package.docstring
    else:
        head = f'"""{package.docstring}\n"""' if "\n" in package.docstring else f'"""{package.docstring}"""'
    parts = [head, "", *loads(body, package, repo=repo, in_repo=in_repo), "", body]
    return "\n".join(parts).rstrip("\n") + "\n"
