"""A TOML writer for the converter's generated tables (`index.toml`, `libraries.toml`).

The standard library reads TOML (`tomllib`) but does not write it.  The tables are flat:
strings, booleans, numbers, lists and string-keyed maps (inline tables), so a few lines
suffice, and the output is deterministic (sorted keys where the caller does not order them)
so a regeneration that changes nothing produces no diff.  A leading comment says how the
file is generated, which is the reason these are TOML and not JSON.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_empty(item: object) -> bool:
    """A value the tables leave out: None, an empty string, list, dict or set."""
    return item in (None, "", [], {}, set())


def key(name: str) -> str:
    """A TOML key: bare when it may be, quoted otherwise (`"pcre2-8"` is bare, `"glib-2.0"` too;
    `"a b"` is quoted)."""
    return name if _BARE_KEY_RE.match(name) else json.dumps(name)


def value(item: object) -> str:
    """A TOML value for a string, a bool, a number, a list (a set is sorted) or a string-keyed
    map (inline table)."""
    if isinstance(item, str):
        return json.dumps(item)  # a JSON string is a valid TOML basic string
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, (list, tuple, set, frozenset)):
        items = sorted(item) if isinstance(item, (set, frozenset)) else list(item)
        if not items:
            return "[]"
        return "[\n" + "".join(f"    {value(v)},\n" for v in items) + "]"
    if isinstance(item, dict):
        return "{ " + ", ".join(f"{key(k)} = {value(v)}" for k, v in item.items()) + " }"
    raise TypeError(f"no TOML form for {type(item).__name__}")


def header(command: str, what: str) -> str:
    """The comment that opens a generated file."""
    return f"# @generated: {what}.\n# Regenerate with `{command}`; do not edit.\n"


def table_array(name: str, rows: list[dict[str, object]]) -> str:
    """`[[name]]` entries, one per row, keys in the row's order, empty values left out."""
    out = []
    for row in rows:
        lines = [f"[[{key(name)}]]"]
        for k, v in row.items():
            if _is_empty(v):
                continue
            lines.append(f"{key(k)} = {value(v)}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def tables(name: str, rows: dict[str, dict[str, object]]) -> str:
    """`[name.<row>]` tables, one per row, in the rows' order."""
    out = []
    for row_name, row in rows.items():
        lines = [f"[{key(name)}.{key(row_name)}]"]
        lines += [f"{key(k)} = {value(v)}" for k, v in row.items() if not _is_empty(v)]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def table(name: str, entries: Mapping[str, object]) -> str:
    """One `[name]` table of key = value lines, in the entries' order, empty values left out."""
    lines = [f"[{key(name)}]"]
    lines += [f"{key(k)} = {value(v)}" for k, v in entries.items() if not _is_empty(v)]
    return "\n".join(lines) + "\n"
