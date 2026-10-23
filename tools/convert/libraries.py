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

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from python.runfiles import Runfiles

    from autoconf.tests.gnu.autotools import IS_WINDOWS

    RUNFILES: type[Runfiles] | None = Runfiles
except ImportError:  # outside Bazel: the table is read from the source tree
    RUNFILES = None
    IS_WINDOWS = False

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
    if RUNFILES is not None:
        runfiles = RUNFILES.Create()
        if runfiles:
            found = runfiles.Rlocation(SNAPSHOT_RLOCATION, "_main" if IS_WINDOWS else None)
            if found and Path(found).exists():
                return Path(found)
    sibling = Path(__file__).resolve().parent / "libraries.toml"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"Cannot locate {SNAPSHOT_RLOCATION}")


_PINS: dict[str, str] = {}
"""Module -> version overriding the table's (`pin_versions`): entry mode pins the modules the
registry checkout holds to its newest version, so a set cut together depends on itself."""


def pin_versions(versions: dict[str, str]) -> None:
    """Override the table's version for each module of `versions`."""
    _PINS.update(versions)
    table.cache_clear()
    _by_module.cache_clear()


@lru_cache(maxsize=1)
def table() -> dict[str, Library]:
    """`-l` name -> registry library, from libraries.toml (versions as pinned)."""
    data = tomllib.loads(_snapshot_path().read_text(encoding="utf-8"))
    return {
        name: Library(entry["module"], _PINS.get(entry["module"], entry["version"]), entry["label"])
        for name, entry in data["libraries"].items()
    }


@lru_cache(maxsize=1)
def _by_module() -> dict[str, Library]:
    """Module name -> its library, for modules that back exactly one `-l` name or whose name is
    one of them (`zlib` -> z; `openssl` backs ssl and crypto and is not resolved)."""
    groups: dict[str, dict[str, Library]] = {}
    for lib, entry in table().items():
        groups.setdefault(entry.module, {})[lib] = entry
    out: dict[str, Library] = {}
    for module, libs in groups.items():
        short = module.removeprefix("lib")
        if len(libs) == 1:
            out[module] = next(iter(libs.values()))
        elif module in libs:
            out[module] = libs[module]
        elif short in libs:
            out[module] = libs[short]
    return out


_SPEC_RE = re.compile(r"([A-Za-z0-9_.+-]+)\s*(?:(>=|<=|!=|=|>|<)\s*(\$?[A-Za-z0-9._~${}+-]*))?")

# pkg-config names that the `-l` names and module names of the table do not spell: the
# `-l` names they stand for (`openssl` is both of OpenSSL's libraries).
PKG_CONFIG_ALIASES: dict[str, list[str]] = {
    "alsa": ["asound"],
    "libusb-1.0": ["usb-1.0"],
    "openssl": ["ssl", "crypto"],
    "libxml-2.0": ["xml2"],
    "libsystemd": ["systemd"],
    "libudev": ["udev"],
    "mount": ["mount"],
    "blkid": ["blkid"],
    "uuid": ["uuid"],
    "libpcre2-8": ["pcre2-8"],
    "libpcre": ["pcre"],
    "libzmq": ["zmq"],
    "libcrypto": ["crypto"],
    "libssl": ["ssl"],
}


def parse_specs(text: str) -> list[tuple[str, str | None, str | None]]:
    """The packages of a PKG_CHECK_MODULES list (`fuse3 >= 3.1 glib-2.0`): (name, operator,
    version), the latter two None without a constraint; a version spelled by a shell variable
    (`ogg >= $REQ`) is kept as written, for the caller to treat as unchecked."""
    return [(m.group(1), m.group(2), m.group(3) or None) for m in _SPEC_RE.finditer(text) if m.group(1)]


def known_pkg(name: str) -> list[Library] | None:
    """The registry libraries a pkg-config package name stands for (usually one), or None."""
    if name in PKG_CONFIG_ALIASES:
        found = [table().get(lib) for lib in PKG_CONFIG_ALIASES[name]]
        return [f for f in found if f is not None] or None
    library = known(name)
    return [library] if library is not None else None


def upstream_version(version: str) -> str:
    """The upstream version in a registry version: `1.3.2.bcr.2` -> `1.3.2`."""
    return version.split(".bcr.", 1)[0]


def _version_key(version: str) -> list[tuple[int, int | str]]:
    """pkg-config's ordering: dotted segments compared numerically, letters after numbers."""
    key: list[tuple[int, int | str]] = []
    for segment in re.split(r"[.\-_+~]", version):
        for part in re.findall(r"\d+|[A-Za-z]+", segment):
            key.append((0, int(part)) if part.isdigit() else (1, part))
    return key


def version_satisfies(have: str, operator: str | None, want: str | None) -> bool:
    """`have operator want` in pkg-config's version order; True without a constraint."""
    if not operator or not want:
        return True
    a, b = _version_key(have), _version_key(want)
    # Missing trailing segments count as zero (1.3 == 1.3.0).
    while len(a) < len(b):
        a.append((0, 0))
    while len(b) < len(a):
        b.append((0, 0))
    return {">=": a >= b, "<=": a <= b, "=": a == b, "!=": a != b, ">": a > b, "<": a < b}[operator]


# `-l` names a platform's own C library or SDK provides: link options, never registry modules.
# Without the list a trailing-digit lookup would take Windows' crypt32 for libxcrypt.
SYSTEM_LIBRARIES = frozenset(
    {
        # POSIX / libc
        "c",
        "m",
        "dl",
        "rt",
        "pthread",
        "resolv",
        "socket",
        "nsl",
        "util",
        "crypt",
        "iconv",
        "intl",
        "execinfo",
        "kvm",
        # Windows
        "advapi32",
        "bcrypt",
        "crypt32",
        "dnsapi",
        "gdi32",
        "iphlpapi",
        "kernel32",
        "mswsock",
        "netapi32",
        "ntdll",
        "ole32",
        "oleaut32",
        "psapi",
        "secur32",
        "shell32",
        "shlwapi",
        "user32",
        "userenv",
        "uuid",
        "winmm",
        "ws2_32",
        "wsock32",
    }
) - {"crypt", "iconv", "intl"}  # these three the registry does provide (libxcrypt, libiconv, gettext)


def known(name: str) -> Library | None:
    """The registry library for a library name, if any: the `-l` name (`gmp`, `-lgmp`), the
    `lib`-prefixed spelling (`libz`), the module or pkg-config name (`zlib`, `libcurl`), or one
    with a trailing version (`libpng16`).  A platform's own library (`SYSTEM_LIBRARIES`) is
    none of these, whatever a stripped spelling would match."""
    name = name.removeprefix("-l")
    if name in SYSTEM_LIBRARIES:
        return None
    candidates = [name]
    if name.startswith("lib"):
        candidates.append(name[3:])
    candidates += [c.rstrip("0123456789.") for c in list(candidates) if c.rstrip("0123456789.") != c]
    for candidate in candidates:
        found = table().get(candidate) or _by_module().get(candidate)
        if found is not None:
            return found
    return None
