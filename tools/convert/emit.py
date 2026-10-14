"""Render `ir.Package`s as Starlark in the style buildifier leaves behind.

The output is meant to be a fixed point of `buildifier`: four-space
indentation, one element per line with trailing commas for multi-element
lists and keyword calls, `name` first then `checks`, `visibility`, `deps`,
and `deps` sorted the way buildifier sorts labels.
"""

from __future__ import annotations

import re
from typing import Any

from tools.convert.ir import PLATFORM_FAMILIES, Assign, Call, Dep, Package, Raw, Rule, Target

INDENT = "    "
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote(text: str) -> str:
    """A Starlark string literal."""
    if "\n" in text:
        body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        return '"""\\\n' + body + '"""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, bool)) or value is None


def _short(value: Any) -> bool:
    return _is_scalar(value) and not (isinstance(value, str) and "\n" in value)


def fmt(value: Any, indent: str) -> str:
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
    if isinstance(value, list):
        return fmt_list(value, indent)
    if isinstance(value, dict):
        inner = indent + INDENT
        lines = ["{"]
        for key, item in value.items():
            lines.append(f"{inner}{fmt(key, inner)}: {fmt(item, inner)},")
        lines.append(indent + "}")
        return "\n".join(lines)
    if isinstance(value, Call):
        return fmt_call(value, indent)
    raise TypeError(f"cannot render {value!r}")


def fmt_list(items: list, indent: str) -> str:
    """A Starlark list: inline when it has one short item, one item per line otherwise, with a
    Call's comments above it.
    """
    if not items:
        return "[]"
    single = len(items) == 1 and _short(items[0])
    if single:
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


def fmt_call(call: Call, indent: str) -> str:
    """Positional-only calls with short scalars stay on one line."""
    if not call.kwargs and all(_short(a) for a in call.args):
        text = f"{call.fn}({', '.join(fmt(a, indent) for a in call.args)})"
        if len(text) + len(indent) <= 100:
            return text
    inner = indent + INDENT
    lines = [f"{call.fn}("]
    for arg in call.args:
        lines.append(f"{inner}{fmt(arg, inner)},")
    for key in ordered_keys(call.kwargs):
        lines.append(f"{inner}{key} = {fmt_kwarg(key, call.kwargs[key], inner)},")
    lines.append(indent + ")")
    return "\n".join(lines)


_ORDERED_LINE_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif|define|undef)\b")


def fmt_kwarg(key: str, value: Any, indent: str) -> str:
    """A keyword argument; an `includes` list with preprocessor conditionals
    keeps its order (buildifier sorts `includes` lists otherwise)."""
    if key == "includes" and isinstance(value, list) and len(value) > 1 \
            and any(isinstance(v, str) and _ORDERED_LINE_RE.match(v) for v in value):
        inner = indent + INDENT
        lines = ["[", f"{inner}# do not sort"]
        lines += [f"{inner}{fmt(v, inner)}," for v in value]
        lines.append(indent + "]")
        return "\n".join(lines)
    return fmt(value, indent)


def ordered_keys(kwargs: dict) -> list[str]:
    """`name` first, the rest alphabetically (the hand ports' convention)."""
    keys = sorted(kwargs)
    if "name" in keys:
        keys.remove("name")
        keys.insert(0, "name")
    return keys


def label_sort_key(label: str) -> tuple:
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


def fmt_checks(items: list, indent: str) -> str:
    """`[...] + macros.X(...) + [...]`: list segments and list-valued calls."""
    segments: list = []
    for item in items:
        if (isinstance(item, Call) and item.returns_list) or isinstance(item, Raw):
            segments.append(item)
        else:
            if not segments or not isinstance(segments[-1], list):
                segments.append([])
            segments[-1].append(item)
    rendered = []
    for segment in segments:
        if isinstance(segment, list):
            rendered.append(fmt_list_force(segment, indent))
        elif isinstance(segment, Raw):
            rendered.append(segment.text)
        else:
            rendered.append(fmt_call_force(segment, indent, trailing="; ".join(segment.comments)))
    return " + ".join(rendered)


def fmt_list_force(items: list, indent: str) -> str:
    """A list literal, always one element per line (checks lists)."""
    if not items:
        return "[]"
    inner = indent + INDENT
    lines = ["["]
    for item in items:
        lines.extend(comment_lines(item.comments, inner) if isinstance(item, Call) else [])
        lines.append(f"{inner}{fmt(item, inner)},")
    lines.append(indent + "]")
    return "\n".join(lines)


def fmt_call_force(call: Call, indent: str, trailing: str = "") -> str:
    """A call, always one argument per line (list-valued macro calls).

    A comment on the call goes on its own line inside the parentheses, which
    is where buildifier moves a trailing comment after `(`.
    """
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


DEFAULT_ARM = "//conditions:default"


def strip_annotations(package: Package) -> None:
    """Drop every comment and source location from the IR (the default output).

    The translator records where each check, dep and rule came from; with
    `--annotate` that is rendered as comments, otherwise the BUILD file stays
    short.
    """

    def value(item: Any) -> None:
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
            value(statement.checks)
            value(statement.selects)
            for dep in statement.deps + statement.default_deps:
                dep.comment = None
        elif isinstance(statement, Rule):
            statement.comments = []
            value(statement.attrs)
        elif isinstance(statement, Assign):
            statement.comments = []
            value(statement.value)


def arm_sort_key(label: str) -> tuple:
    return (1, "") if label == DEFAULT_ARM else (0, label)


def fmt_select(arms: dict[str, list], indent: str) -> str:
    inner = indent + INDENT
    lines = ["select({"]
    labels = sorted(arms, key=arm_sort_key)
    if DEFAULT_ARM not in labels:
        labels.append(DEFAULT_ARM)
    for label in labels:
        items = arms.get(label, [])
        # An arm holds list-valued macro calls too (libpcap's per-OS
        # AC_CHECK_HEADERS): `[...] + macros.X(...)`, never a nested list.
        rendered = fmt_checks(items, inner) if items else "[]"
        lines.append(f"{inner}{quote(label)}: {rendered},")
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


def fmt_defaults_dict(items: list, indent: str) -> str:
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
            arms = {label: [c for c in items if isinstance(c, Call) and c.fn == fn]
                    for label, items in target.selects[family].items()}
            if not any(arms.values()):
                continue
            inner = indent + INDENT
            lines = ["select({"]
            labels = sorted(arms, key=arm_sort_key)
            if DEFAULT_ARM not in labels:
                labels.append(DEFAULT_ARM)
            for label in labels:
                lines.append(f"{inner}{quote(label)}: {fmt_defaults_dict(arms.get(label, []), inner)},")
            lines.append(indent + "})")
            parts.append("\n".join(lines))
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
    lines.append(f'{INDENT}name = {quote(target.name)},')
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
    "name": -99, "archive": -98, "gwt_name": -98, "gwt_module": -97, "size": -96, "timeout": -95,
    "testonly": -94, "src": -93, "srcdir": -92, "srcs": -91, "out": -90, "outs": -89, "hdrs": -88,
    "has_services": -87, "include": -86, "of": -85, "baseline": -84, "package_name": -83,
    "destdir": 1, "exports": 2, "runtime_deps": 3, "deps": 4, "implementation": 5, "implements": 6,
    "alwayslink": 7,
}


def attr_sort_key(name: str) -> tuple:
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


def render_statement(statement) -> str:
    if isinstance(statement, Target):
        return render_target(statement)
    if isinstance(statement, Rule):
        return render_rule(statement)
    if isinstance(statement, Assign):
        return render_assign(statement)
    if isinstance(statement, Raw):
        return statement.text.rstrip("\n")
    raise TypeError(f"cannot render {statement!r}")


def loads(body: str, package: Package, *, repo: str, in_repo: bool) -> list[str]:
    """The `load()` statements the rendered body needs, sorted."""
    statements = []
    if any(t.kind == "autoconf" for t in package.targets) or re.search(r"^autoconf\(", body, re.M):
        if in_repo:
            statements.append('load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")')
        else:
            statements.append(f'load("{repo}//autoconf:autoconf.bzl", "autoconf")')
    if any(t.kind == "autoconf_defaults" for t in package.targets) or re.search(r"^autoconf_defaults\(", body, re.M):
        statements.append(f'load("{repo}//autoconf:autoconf_defaults.bzl", "autoconf_defaults")')
    symbols = [s for s in ("checks", "macros", "utils") if re.search(rf"\b{s}\.", body)]
    if symbols:
        statements.append(f'load("{repo}//autoconf:checks.bzl", ' + ", ".join(quote(s) for s in symbols) + ")")
    if re.search(r"\bgl_macros\.", body):
        statements.append(f'load("{repo}//gnulib:macros.bzl", gl_macros = "macros")')
    by_file: dict[str, set[str]] = {}
    for statement in package.before + package.after:
        if isinstance(statement, Rule) and statement.load_from:
            path = statement.load_from
            if path.startswith("//"):
                path = repo + path
            symbol = statement.load_symbol or statement.kind
            entry = quote(symbol) if symbol == statement.kind else f"{statement.kind} = {quote(symbol)}"
            by_file.setdefault(path, set()).add(entry)
    for path, symbols_set in by_file.items():
        statements.append(f'load({quote(path)}, ' + ", ".join(sorted(symbols_set)) + ")")
    statements += [line for line in package.loads if line not in statements]
    return sorted(statements, key=lambda s: s.split('"')[1])


_LOAD_START_RE = re.compile(r"^load\(", re.M)


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
        if text[end:end + 1] == "\n":
            end += 1
        rest.append(text[pos:match.start()])
        loads.append(text[match.start():end].rstrip("\n"))
        pos = end
    rest.append(text[pos:])
    return loads, "".join(rest)


def append_fragment(package: Package, text: str, source: str) -> None:
    """A hand-written BUILD fragment (`--append`): its `load()`s join the generated ones at the
    top, the rest follows the generated statements as given, before buildifier formats the file.
    This is where a project's tests live: the converter owns BUILD.bazel, the fragment is kept."""
    loads, body = split_loads(text)
    package.loads += [line for line in loads if line not in package.loads]
    body = body.strip("\n")
    if body:
        package.after.append(Raw(f"# -- {source}: appended as given --\n{body}"))


def render_package(package: Package, *, repo: str = "", in_repo: bool = True) -> str:
    statements = [*package.before, *package.targets, *package.after]
    body = "\n\n".join(render_statement(s) for s in statements)
    head = f'"""{package.docstring}\n"""' if "\n" in package.docstring else f'"""{package.docstring}"""'
    parts = [head, "", *loads(body, package, repo=repo, in_repo=in_repo), "", body]
    return "\n".join(parts).rstrip("\n") + "\n"
