"""Which files of an output directory the converter owns, and how a conversion lands in one.

A conversion is rendered into a staging directory and then placed into its destination: a
registry entry's `overlay/`, a `3rdparty/<name>` directory, or the project tree itself.
Hand-written files live there too, so the converter owns only the files it generated:

- a file is the converter's when its head carries the mark (`@generated` and the name
  rules_cc_autoconf, both, so a project's own generated files are never taken for ours);
- a file in the way that is not the converter's and differs from what would be written
  stops the run before anything is written;
- after writing, the converter's files that this run did not produce are removed, and
  nothing else.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

MARK = "@" + "generated"  # spelled apart so this file does not carry the mark itself
OWNER = "rules_cc_autoconf"
_HEAD = 4096
_SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".convert-cache"}

# The comment that carries the mark, by suffix: (opening, closing).
_COMMENT = {
    ".bazel": ("# ", ""),
    ".bzl": ("# ", ""),
    ".py": ("# ", ""),
    ".sh": ("# ", ""),
    ".txt": ("# ", ""),
    ".toml": ("# ", ""),
    ".yml": ("# ", ""),
    ".yaml": ("# ", ""),
    ".c": ("/* ", " */"),
    ".cc": ("/* ", " */"),
    ".cpp": ("/* ", " */"),
    ".h": ("/* ", " */"),
    ".hh": ("/* ", " */"),
    ".in": ("/* ", " */"),
    ".m4": ("dnl ", ""),
    ".ac": ("dnl ", ""),
}


class Conflict(ValueError):
    """Hand-written files stand where the conversion would write."""

    def __init__(self, destination: Path, paths: list[str]) -> None:
        self.destination = destination
        self.paths = paths
        listed = "\n".join(f"  {p}" for p in paths)
        super().__init__(
            f"{destination}: these files are not the converter's and would be overwritten:\n{listed}\n"
            "Hand-written files are kept where they are: move these aside, or delete them."
        )


def is_owned(path: Path) -> bool:
    """True when the file's head carries the converter's mark."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD)
    except OSError:
        return False
    return MARK.encode("ascii") in head and OWNER.encode("ascii") in head


def marked(text: str, suffix: str, what: str) -> str:
    """`text` with a one-line mark on top, in the comment syntax of `suffix` (after a `#!` line);
    unchanged for a suffix whose comment syntax is not known."""
    if suffix not in _COMMENT:
        return text
    opening, closing = _COMMENT[suffix]
    line = f"{opening}{MARK}: {what}, by {OWNER} convert; do not edit.{closing}\n"
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return f"{first}\n{line}{rest}"
    return line + text


def owned_files(root: Path) -> list[Path]:
    """The converter's files under `root` (symlinked directories, VCS and Bazel output
    directories not entered)."""
    out: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith("bazel-") and not os.path.islink(os.path.join(directory, d))
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.is_file() and not path.is_symlink() and is_owned(path):
                out.append(path)
    return out


def sync(staging: Path, destination: Path, *, regenerated: tuple[str, ...] = ()) -> tuple[list[Path], list[Path]]:
    """Place the files under `staging` into `destination`; returns (written, removed).

    Raises `Conflict`, before writing anything, when a file in the way is not the converter's
    and differs from what would be written (an identical file is rewritten without a word).
    The paths in `regenerated` are replaced whatever was there: MODULE.bazel, whose hand-written
    `module()` entry the conversion read and kept.  Then the converter's files under
    `destination` that `staging` does not hold are removed, with the directories that empties."""
    files = {p.relative_to(staging).as_posix(): p for p in staging.rglob("*") if p.is_file()}
    conflicts = [
        rel
        for rel, src in sorted(files.items())
        if rel not in regenerated
        and (destination / rel).is_file()
        and not is_owned(destination / rel)
        and not _same_text(destination / rel, src.read_bytes())
    ]
    if conflicts:
        raise Conflict(destination, conflicts)
    written: list[Path] = []
    for rel, src in sorted(files.items()):
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        written.append(target)
    removed: list[Path] = []
    for path in owned_files(destination):
        if path.relative_to(destination).as_posix() in files:
            continue
        path.unlink()
        removed.append(path)
        parent = path.parent
        while parent != destination and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    return written, removed


def _same_text(path: Path, data: bytes) -> bool:
    """Whether the file holds `data`, line endings aside (a checkout on Windows may carry CRLF)."""
    return path.read_bytes().replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n")


def place(target: Path, text: str) -> Path:
    """Write one generated file, under the same rule as `sync`: a hand-written file in the way
    that differs is a `Conflict`."""
    data = text.encode("utf-8")
    if target.is_file() and not is_owned(target) and not _same_text(target, data):
        raise Conflict(target.parent, [target.name])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
