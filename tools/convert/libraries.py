"""C libraries a configure script probes for (`AC_CHECK_LIB`, `AC_SEARCH_LIBS`, `-l` in its
own shell, a probe including the library's header) or a Makefile.am links (`-lgmp`) that the
Bazel Central Registry provides as modules.

A converted project cannot link what the host happens to have: a probe against the sandbox's
toolchain alone would differ from machine to machine.  Where the library is a registry
module, the converter gives the probe the library instead: the module is a `bazel_dep` of the
generated MODULE.bazel, its target a `cc_deps` entry of the root autoconf targets (so every
check compiles against its headers and links its archive, as configure does with the library
installed) and a `dep` of what links it.  `AC_CHECK_LIB`, `AC_SEARCH_LIBS` and the header
probes stay real checks.  The report says so; a `link:` decision still overrides a Makefile
item.

The table is `libraries.toml` next to this module, generated from a registry clone by
`bazel run //tools/convert:update_libraries` (see `update_libraries.py` for the `-l` names it
knows and how each is verified).  Library names are the `-l` names.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SNAPSHOT_RLOCATION = "_main/tools/convert/libraries.toml"


@dataclass(frozen=True)
class Library:
    """A registry module standing for a `-l` library."""

    module: str
    """The registry module."""

    version: str
    """The module version the table was generated against."""

    label: str
    """The module's library target, as a dependency label (`@gmp//:gmp`)."""


def _snapshot_path() -> Path:
    """`libraries.toml` through runfiles, falling back to the source tree."""
    try:
        from python.runfiles import Runfiles  # pylint: disable=import-outside-toplevel
        from autoconf.tests.gnu.autotools import IS_WINDOWS  # pylint: disable=import-outside-toplevel

        runfiles = Runfiles.Create()
        if runfiles:
            found = runfiles.Rlocation(SNAPSHOT_RLOCATION, "_main" if IS_WINDOWS else None)
            if found and Path(found).exists():
                return Path(found)
    except ImportError:
        pass
    sibling = Path(__file__).resolve().parent / "libraries.toml"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"Cannot locate {SNAPSHOT_RLOCATION}")


@lru_cache(maxsize=1)
def table() -> dict[str, Library]:
    """`-l` name -> registry library, from libraries.toml."""
    data = tomllib.loads(_snapshot_path().read_text(encoding="utf-8"))
    return {name: Library(entry["module"], entry["version"], entry["label"]) for name, entry in data["libraries"].items()}


@lru_cache(maxsize=1)
def _by_module() -> dict[str, Library]:
    """Module name -> its library, for modules that back exactly one `-l` name or whose name is
    one of them (`zlib` -> z; `openssl` backs ssl and crypto and is not resolved)."""
    groups: dict[str, dict[str, Library]] = {}
    for lib, entry in table().items():
        groups.setdefault(entry.module, {})[lib] = entry
    out: dict[str, Library] = {}
    for module, libs in groups.items():
        short = module[3:] if module.startswith("lib") else module
        if len(libs) == 1:
            out[module] = next(iter(libs.values()))
        elif module in libs:
            out[module] = libs[module]
        elif short in libs:
            out[module] = libs[short]
    return out


def known(name: str) -> Library | None:
    """The registry library for a library name, if any: the `-l` name (`gmp`, `-lgmp`), the
    `lib`-prefixed spelling (`libz`), the module or pkg-config name (`zlib`, `libcurl`), or one
    with a trailing version (`libpng16`)."""
    name = name[2:] if name.startswith("-l") else name
    candidates = [name]
    if name.startswith("lib"):
        candidates.append(name[3:])
    candidates += [c.rstrip("0123456789.") for c in list(candidates) if c.rstrip("0123456789.") != c]
    for candidate in candidates:
        found = table().get(candidate) or _by_module().get(candidate)
        if found is not None:
            return found
    return None
