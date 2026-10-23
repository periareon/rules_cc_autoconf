"""MODULE.bazel of a converted project, and the registry modules the output may load."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.convert.common import generated_banner, module_name


@dataclass(frozen=True)
class Module:
    """A registry module the generated MODULE.bazel may declare."""

    name: str
    """The module name, as `bazel_dep` spells it."""

    version: str
    """The pinned registry version (empty for rules_cc_autoconf: the converter's own)."""

    role: str
    """`generated`: declared in every MODULE.bazel; `tool`: when the output runs its rules (bison,
    flex); `loaded`: when a BUILD file of the source tree, a hand-written one or the generated
    output loads from it."""

    why: str
    """What the generated output needs the module for, for the reader of this table."""


# The one table of modules and their pinned registry versions.
MODULES = {
    "rules_cc": Module("rules_cc", "0.2.9", "generated", "cc_library and friends"),
    "platforms": Module("platforms", "1.0.0", "generated", "the select() constraints"),
    "bazel_skylib": Module("bazel_skylib", "1.8.2", "generated", "bool_flag, write_file, diff_test"),
    "bison": Module("bison", "3.8.2.bcr.11", "tool", "yacc sources"),
    "flex": Module("flex", "2.6.4.bcr.8", "tool", "lex sources"),
    "rules_license": Module("rules_license", "1.0.0", "loaded", "tcl's compat/zlib/BUILD.bazel"),
    "package_metadata": Module("package_metadata", "0.0.13", "loaded", "the license() of a `license:` decision"),
    "rules_shell": Module("rules_shell", "0.8.0", "loaded", "sh_test in a hand-written BUILD file"),
    "m4": Module("m4", "1.4.21.bcr.6", "loaded", "the m4 a flex/bison module's own rule runs"),
    "rules_cc_autoconf": Module("rules_cc_autoconf", "", "generated", "the rules, at the converter's version"),
}
_LOAD_REPO_RE = re.compile(r'(?:load\(\s*|[=\[(,]\s*)"@@?([A-Za-z0-9_.-]+)//')


def load_repos(text: str) -> set[str]:
    """The external repositories a BUILD text loads."""
    return set(_LOAD_REPO_RE.findall(text))


def source_tree_loads(src: Path) -> set[str]:
    """The external repositories the BUILD and .bzl files already in `src` load."""
    repos: set[str] = set()
    for path in src.rglob("*"):
        if path.name in ("BUILD", "BUILD.bazel") or path.suffix == ".bzl":
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            repos.update(load_repos(text))
    return repos


def existing_module(path: Path) -> tuple[dict[str, str], list[str]]:
    """What a MODULE.bazel already at the destination says: the `module()` attributes besides
    `name` and `version` (`compatibility_level`, `bazel_compatibility`, `repo_name`), as source
    text by attribute, which the regenerated file keeps; and the source of every other top-level
    statement the converter does not write itself (an override, a `use_extension`, a
    `register_toolchains`), for the report.  Comments and `bazel_dep` lines are not listed: the
    former are the converter's banner, the latter are regenerated from the conversion."""
    if not path.is_file():
        return {}, []
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}, [f"{path.name}: not read (not a Starlark subset Python parses)"]
    attrs: dict[str, str] = {}
    others: list[str] = []
    for node in tree.body:
        call = node.value if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) else None
        fn = call.func.id if call is not None and isinstance(call.func, ast.Name) else None
        if call is not None and fn == "module":
            for keyword in call.keywords:
                # A `**kwargs` expansion (`arg` None) names no attribute to keep.
                if keyword.arg is not None and keyword.arg not in ("name", "version"):
                    attrs[keyword.arg] = ast.get_source_segment(text, keyword.value) or ""
        elif fn == "bazel_dep" or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
            continue
        else:
            segment = ast.get_source_segment(text, node) or ""
            others.append(segment.split("\n")[0] + (" ..." if "\n" in segment else ""))
    return attrs, others


def _module_bazel(
    name: str,
    version: str,
    rules_version: str,
    dev_override: str | None,
    flags: bool,
    tools: Sequence[str] = (),
    command: str | None = None,
    source_modules: Sequence[str] = (),
    external_modules: dict[str, str] | None = None,
    module_attrs: dict[str, str] | None = None,
) -> str:
    """The MODULE.bazel of a converted project: the banner, module(), the rules_cc_autoconf dep
    (with a local_path_override under --dev), rules_cc and platforms, bazel_skylib when flags are
    emitted, the bison / flex modules when parsers or scanners are generated, and the modules the
    source tree's own BUILD files load (`source_modules`, keys of MODULES with the `loaded` role).
    `module_attrs` are the attributes a MODULE.bazel already at the destination gave `module()`
    besides name and version (`existing_module`), kept as written.
    """
    lines = [
        generated_banner(f"{name} {version}".strip(), "MODULE.bazel", command),
        "",
        "module(",
        f'    name = "{module_name(name)}",',
        f'    version = "{version or "0.0.0"}",',
        *[f"    {attr} = {text}," for attr, text in sorted((module_attrs or {}).items())],  # buildifier's order
        ")",
        "",
        f'bazel_dep(name = "rules_cc_autoconf", version = "{rules_version}")',
    ]
    if dev_override:
        lines += [
            "local_path_override(",
            '    module_name = "rules_cc_autoconf",',
            f'    path = "{dev_override}",',
            ")",
        ]
    lines += [dep_line("rules_cc"), dep_line("platforms")]
    if flags:
        lines.append(dep_line("bazel_skylib"))
    if tools:
        lines.append("")
        lines.append("# The BCR modules whose bison() / flex() rules generate the parsers and scanners.")
        lines += [dep_line(tool) for tool in tools]
    if external_modules:
        lines.append("")
        lines.append("# Registry libraries the configure script finds (AC_CHECK_LIB, AC_SEARCH_LIBS) or the")
        lines.append("# Makefiles link; their targets are deps of what links them.")
        lines += [
            f'bazel_dep(name = "{module}", version = "{version}")'
            for module, version in sorted(external_modules.items())
        ]
    if source_modules:
        lines.append("")
        lines.append("# Modules the BUILD files shipped in the source tree, or the hand-written BUILD files, load.")
        lines += [dep_line(r) for r in source_modules]
    return "\n".join(lines) + "\n"


def dep_line(name: str) -> str:
    """The `bazel_dep(...)` line of a module of `MODULES`, at the pinned version."""
    module = MODULES[name]
    return f'bazel_dep(name = "{module.name}", version = "{module.version}")'
