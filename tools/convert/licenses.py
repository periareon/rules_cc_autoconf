"""The SPDX identifier of a license file a release ships, read from its text.

A `COPYING` or `LICENSE` is the license text, not the project's licensing statement: the GPL
family's text is the same for a project that is `GPL-3.0-only` and one that is
`GPL-3.0-or-later`, and the clause that decides it ("or (at your option) any later version")
lives in the source headers.  So the text gives the family and version, the source headers the
suffix, and anything the fingerprints do not know (a custom notice, a dual license written out
by hand, Creative Commons) is left to a `license:` decision.
"""

from __future__ import annotations

import re
from pathlib import Path

# An anchor in the normalized text (lowercase, one space between words) and the SPDX family it
# names; the one found earliest in the text is the title's.
_GNU_FAMILIES = (
    ("gnu affero general public license", "AGPL"),
    ("gnu lesser general public license", "LGPL"),
    ("gnu library general public license", "LGPL"),
    ("gnu general public license", "GPL"),
)
_VERSIONED = (
    ("gnu free documentation license", "GFDL"),
    ("apache license", "Apache"),
    ("mozilla public license", "MPL"),
)
_PERMISSIVE = (
    ("permission is hereby granted, free of charge", "MIT"),
    ("permission to use, copy, modify, and/or distribute this software for any purpose with or without fee", "ISC"),
    ("permission to use, copy, modify, and distribute this software for any purpose with or without fee", "ISC"),
)
_BSD_ANCHOR = "redistribution and use in source and binary forms"
_VERSION_RE = re.compile(r"version (\d+(?:\.\d+)?)")
_HEADER_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".hh", ".y", ".l", ".m4", ".ac", ".am", ".sh", ".py")
_HEADER_LINES = 40
_HEADER_FILES = 400
_SKIP_DIRS = {".git", "build-aux", "m4", "po", "doc", "tests", "test"}


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _spdx_version(version: str) -> str:
    """`3` -> `3.0`, `2.1` -> `2.1`: how SPDX writes the GNU and Apache versions."""
    return version if "." in version else f"{version}.0"


def detect(src: Path, file: str) -> tuple[str | None, str]:
    """The SPDX identifier of `src/file` and the evidence in one line; None with the reason when
    the text is not one the fingerprints know, or a GNU license whose `-only` / `-or-later`
    suffix the source headers do not settle."""
    text = _normalized((src / file).read_text(encoding="utf-8", errors="replace")[:8000])
    # The title comes first; the GPL's preamble mentions the Library GPL further down, so the
    # family is the earliest anchor, not the first in the table.
    found = sorted((text.index(anchor), family) for anchor, family in _GNU_FAMILIES if anchor in text)
    if found:
        at, family = found[0]
        match = _VERSION_RE.search(text, at)
        if match is None:
            return None, f"{file} is a {family} text without a version line"
        version = _spdx_version(match.group(1))
        suffix, evidence = _gnu_suffix(src, version)
        if suffix is None:
            return None, f"{file} is the {family} {version} text, but {evidence}"
        return f"{family}-{version}{suffix}", f"{file} is the {family} {version} text and {evidence}"
    for anchor, family in _VERSIONED:
        if anchor in text:
            match = _VERSION_RE.search(text, text.index(anchor))
            if match is None:
                return None, f"{file} is a {family} text without a version line"
            version = _spdx_version(match.group(1))
            return f"{family}-{version}", f"{file} is the {family} {version} text"
    for anchor, family in _PERMISSIVE:
        if anchor in text:
            return family, f"{file} is the {family} text"
    if _BSD_ANCHOR in text:
        # The third clause is the non-endorsement one, spelled "neither the name" or, in the
        # BSD-derived projects' own texts (libpcap), "the names of the authors may not be used".
        if "advertising materials" in text:
            clauses = 4
        elif "neither the name" in text or "may not be used to endorse" in text:
            clauses = 3
        else:
            clauses = 2
        return f"BSD-{clauses}-Clause", f"{file} is a {clauses}-clause BSD text"
    return None, f"{file} is not a license text the converter recognizes"


def _gnu_suffix(src: Path, version: str) -> tuple[str | None, str]:
    """`-or-later` or `-only`, from the source headers: "or (at your option) any later version"
    against a bare "version N of the License".  None when no header says, or they disagree."""
    later = only = 0
    only_re = re.compile(
        rf"version {re.escape(version.removesuffix('.0'))}(?:\.0)? of the (?:gnu )?[a-z ]*general public license"
    )
    for path in _source_files(src):
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                head = _normalized("".join(handle.readline() for _ in range(_HEADER_LINES)))
        except OSError:
            continue
        if "any later version" in head:
            later += 1
        elif only_re.search(head):
            only += 1
    if later and not only:
        return "-or-later", f"{later} source headers accept any later version"
    if only and not later:
        return "-only", f"{only} source headers name that version alone"
    if later and only:
        return None, f"the source headers disagree ({later} accept a later version, {only} do not)"
    return None, "no source header says whether a later version is accepted"


def _source_files(src: Path) -> list[Path]:
    """Up to `_HEADER_FILES` sources under `src`, the build and test directories left out."""
    out: list[Path] = []
    for path in sorted(src.rglob("*")):
        if len(out) >= _HEADER_FILES:
            break
        if (
            path.is_file()
            and path.suffix in _HEADER_SUFFIXES
            and not _SKIP_DIRS & set(path.relative_to(src).parts[:-1])
        ):
            out.append(path)
    return out
