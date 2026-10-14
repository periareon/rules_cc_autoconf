"""Index of the hand-written ports: which macro lives in which target.

Built from the BUILD files of `//gnulib/m4/*`, `//autoconf/macros/*` and
`//gnulib/macros/*`.  Each gnulib package's docstring names the upstream m4
file it ports, so a macro is resolved by its *definition file* first and by
name second (`gl_HEADER_ERRNO_H` is defined in errno_h.m4 and ported in
`//gnulib/m4/errno_h`; 2359 target names over 945 packages are not unique
enough on their own).

`index.toml` next to this module is a snapshot of the scan (regenerate with
`bazel run //tools/convert:update_index`), used where the checkout is not
available: tests and `bazel run` from an exported binary.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert import tomlout

_BLOB_URL_RE = re.compile(r"https://github\.com/coreutils/gnulib/blob/[0-9a-f]+/m4/([^\s\"']+)")
# `name = "..."` of a rule call at column 0 (`autoconf(`, `alias(`...); the
# `name = ` of a check inside a rule is indented and is not a target.
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\(\s*name\s*=\s*"([^"]+)"', re.M)
# `checks.AC_SUBST("HAVE_X", ...)`: the substitutions a port provides.  A
# project's conditions over such a variable must name its subst result
# (`ac_cv_subst_HAVE_X`) when a define of the same name exists as well.
_SUBST_RE = re.compile(r'\bAC_SUBST\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# The defines a port's checks produce: explicit (`define = "HAVE_X"`,
# `AC_DEFINE("X"`) and the HAVE_* / HAVE_DECL_* of the list macros.
_DEFINE_RE = re.compile(r'(?:\bdefine\s*=\s*|\bAC_DEFINE(?:_UNQUOTED)?\(\s*)"([A-Za-z_][A-Za-z0-9_]*)"')
# `checks.AC_SUBST("GL_X_TRUE", condition = "gl_cv_x", ...)`: the port's
# spelling of an automake conditional; the condition is what gnulib.mk's
# `if GL_X` means in the rules' terms.
_AM_CONDITIONAL_RE = re.compile(r'\bAC_SUBST\(\s*"([A-Za-z_][A-Za-z0-9_]*)_TRUE",\s*condition\s*=\s*"([^"]+)"', re.S)
_PORT_DEP_RE = re.compile(
    r'"(?:@rules_cc_autoconf)?//((?:gnulib/m4|autoconf/macros|gnulib/macros)/[A-Za-z0-9_.+-]+)(?::[^"]*)?"')
# An `autoconf_defaults(...)` rule: its `substs` / `variables` / `defines`
# dicts hold literal defaults a probe may override, kept apart from the
# results the port's checks produce.
_DEFAULTS_BLOCK_RE = re.compile(r"^autoconf_defaults\(\n(.*?)^\)", re.S | re.M)
_DEFAULTS_ATTR_RE = re.compile(r"^    (substs|variables|defines) = ")
_ATTR_LINE_RE = re.compile(r"^    [a-z_]+ = ")
_DICT_KEY_RE = re.compile(r'^\s+"([A-Za-z_][A-Za-z0-9_]*)": ')
_VARIABLE_RE = re.compile(r'\bM4_VARIABLE\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# `name = "gl_cv_x"` of a check (indented inside a rule's `checks`): the cache
# variables a port's probes record, which gnulib.mk conditions may read.
_CHECK_NAME_RE = re.compile(r'^ {8,}name = "([A-Za-z_][A-Za-z0-9_]*)",', re.M)
_LIST_CHECK_RE = re.compile(
    r'\b(?:AC_CHECK_(HEADERS|FUNCS|DECLS|TYPES|MEMBERS)(?:_ONCE)?|GL_CHECK_(FUNCS)_(?:ANDROID|MACOS))\(\s*\[([^\]]*)\]', re.S)
_LIST_ITEM_RE = re.compile(r'"([^"]+)"')
SNAPSHOT_RLOCATION = "_main/tools/convert/index.toml"
UPDATE_COMMAND = "bazel run //tools/convert:update_index"


@dataclass
class Port:
    """One package of hand ports."""

    package: str  # e.g. "gnulib/m4/fstat"
    targets: set[str] = field(default_factory=set)
    substs: set[str] = field(default_factory=set)  # AC_SUBST names the package's checks provide
    defines: set[str] = field(default_factory=set)  # config.h names the package's checks provide
    conditionals: dict[str, str] = field(default_factory=dict)  # automake conditional -> condition expression
    deps: set[str] = field(default_factory=set)  # port packages the BUILD file depends on
    variables: set[str] = field(default_factory=set)  # M4_VARIABLE names the package's checks provide
    cache: set[str] = field(default_factory=set)
    """Cache variable names the package's checks record (`ac_cv_lib_error_at_line`)."""

    default_substs: set[str] = field(default_factory=set)
    """AC_SUBST names the package's autoconf_defaults targets hold (gnulib's `*_H_DEFAULTS`): read
    beneath the results, a probe of the name overrides them."""

    default_variables: set[str] = field(default_factory=set)
    """M4 variable names the package's autoconf_defaults targets hold."""

    default_defines: set[str] = field(default_factory=set)
    """Define names the package's autoconf_defaults targets hold."""
    m4_file: str | None = None  # e.g. "fstat.m4" (gnulib packages only)
    url: str | None = None  # the upstream blob URL from the BUILD docstring

    @property
    def basename(self) -> str:
        return self.package.rsplit("/", 1)[-1]

    @property
    def aggregator(self) -> str | None:
        """The target named after the package (a dependency-only aggregate)."""
        return self.basename if self.basename in self.targets else None

    def label(self, target: str | None = None) -> str:
        if target is None or target == self.basename:
            return f"//{self.package}"
        return f"//{self.package}:{target}"

    def defaults_resolution(self) -> "Resolution":
        """A `_DEFAULTS` macro of this package: the public `:defaults` target, read through
        `default_deps` (reason "defaults"); the aggregator, as an ordinary dependency, where
        the package has none (reason "file")."""
        if "defaults" in self.targets:
            return Resolution(self.label("defaults"), self.package, "defaults", "defaults")
        return Resolution(self.label(), self.package, None, "file")


@dataclass
class Resolution:
    label: str  # repo-relative, e.g. "//gnulib/m4/fstat:gl_FUNC_FSTAT"
    package: str
    target: str | None  # None for the package aggregator
    reason: str  # "target" | "file" | "defaults" (a `:defaults` target, for default_deps) | "name" | "macro-package"

    @property
    def exact(self) -> bool:
        return self.reason in ("target", "macro-package")


def parse_build(package: str, text: str) -> Port:
    """The port a BUILD file describes: its targets, the AC_SUBST names and the defines its checks
    provide (list macros expanded to their HAVE_* names), the automake conditionals it spells as
    `<NAME>_TRUE` substs, and the upstream m4 file from the docstring URL.
    """
    port = Port(package=package)
    match = _BLOB_URL_RE.search(text)
    if match:
        port.m4_file = match.group(1)
        port.url = match.group(0)
    port.targets = set(_NAME_RE.findall(text))
    port.substs = set(_SUBST_RE.findall(text))
    port.defines = set(_DEFINE_RE.findall(text))
    port.conditionals = dict(_AM_CONDITIONAL_RE.findall(text))
    port.deps = {d for d in _PORT_DEP_RE.findall(text) if d != package}
    port.variables = set(_VARIABLE_RE.findall(text))
    port.cache = set(_CHECK_NAME_RE.findall(text))
    buckets = {"substs": port.default_substs, "variables": port.default_variables, "defines": port.default_defines}
    for block in _DEFAULTS_BLOCK_RE.finditer(text):
        current = None
        for line in block.group(1).splitlines():
            attr = _DEFAULTS_ATTR_RE.match(line)
            if attr:
                current = buckets[attr.group(1)]
            elif _ATTR_LINE_RE.match(line):
                current = None
            elif current is not None:
                key = _DICT_KEY_RE.match(line)
                if key:
                    current.add(key.group(1))
    for kind, gl_kind, items in _LIST_CHECK_RE.findall(text):
        prefix = "HAVE_DECL_" if (kind or gl_kind) == "DECLS" else "HAVE_"
        for item in _LIST_ITEM_RE.findall(items):
            port.defines.add(prefix + re.sub(r"[^A-Za-z0-9_]", "_", item.strip().replace("*", "P")).upper())
    return port


class PortIndex:
    """Macro name / m4 file -> port target lookup."""

    def __init__(self, port_list: list[Port]) -> None:
        self.ports = sorted(port_list, key=lambda p: p.package)
        self.by_package = {port.package: port for port in self.ports}
        self.by_file: dict[str, Port] = {}
        self.by_name: dict[str, list[Port]] = {}
        for port in self.ports:
            if port.m4_file:
                self.by_file.setdefault(port.m4_file, port)
            for target in port.targets:
                self.by_name.setdefault(target, []).append(port)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_files(cls, build_files: dict[str, Path]) -> "PortIndex":
        """`package -> BUILD.bazel path` (missing files are skipped)."""
        port_list = []
        for package, path in sorted(build_files.items()):
            if not path.is_file():
                continue
            port_list.append(parse_build(package, path.read_text(encoding="utf-8", errors="replace")))
        return cls(port_list)

    @classmethod
    def from_workspace(cls, root: Path) -> "PortIndex":
        """Scan a checkout; packages are discovered on disk."""
        files: dict[str, Path] = {}
        for base in ("gnulib/m4", "autoconf/macros", "gnulib/macros"):
            directory = root / base
            if not directory.is_dir():
                continue
            for entry in sorted(directory.iterdir()):
                build = entry / "BUILD.bazel"
                if entry.is_dir() and build.is_file():
                    files[f"{base}/{entry.name}"] = build
        return cls.from_files(files)

    @classmethod
    def from_snapshot(cls, path: Path | None = None) -> "PortIndex":
        """Load `index.toml` (from runfiles when no path is given)."""
        if path is None:
            path = snapshot_path()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls([
            Port(package=entry["package"], targets=set(entry["targets"]), substs=set(entry.get("substs", [])),
                 defines=set(entry.get("defines", [])), conditionals=dict(entry.get("conditionals", {})),
                 deps=set(entry.get("deps", [])), variables=set(entry.get("variables", [])),
                 cache=set(entry.get("cache", [])),
                 default_substs=set(entry.get("default_substs", [])),
                 default_variables=set(entry.get("default_variables", [])),
                 default_defines=set(entry.get("default_defines", [])),
                 m4_file=entry.get("m4_file"), url=entry.get("url"))
            for entry in data["ports"]
        ])

    def to_snapshot(self) -> str:
        """The index as the text of index.toml: a header saying how it is generated, one
        `[[ports]]` table per package, stable key order, empty fields omitted."""
        rows = [
            {
                "package": port.package,
                "m4_file": port.m4_file,
                "url": port.url,
                "targets": sorted(port.targets),
                "substs": sorted(port.substs),
                "defines": sorted(port.defines),
                "conditionals": dict(sorted(port.conditionals.items())),
                "deps": sorted(port.deps),
                "variables": sorted(port.variables),
                "cache": sorted(port.cache),
                "default_substs": sorted(port.default_substs),
                "default_variables": sorted(port.default_variables),
                "default_defines": sorted(port.default_defines),
            }
            for port in self.ports
        ]
        return (tomlout.header(UPDATE_COMMAND, "the hand ports of this repository (//gnulib/m4, //autoconf/macros, "
                               "//gnulib/macros): their targets and the results they provide") + "\n"
                + tomlout.table_array("ports", rows))

    @property
    def substs(self) -> set[str]:
        """Every substitution some port provides, as a result or as a default."""
        return set().union(*(port.substs | port.default_substs for port in self.ports))

    @property
    def provided(self) -> set[str]:
        """Every define, substitution and M4 variable some port provides, as a result or as a default."""
        return set().union(*(port.defines | port.substs | port.variables | port.default_substs
                             | port.default_variables | port.default_defines for port in self.ports))

    def providers_of(self, name: str) -> list["Port"]:
        """The ports that provide `name` as a define, subst, M4 variable, cache variable or default."""
        return [port for port in self.ports
                if name in port.defines or name in port.substs or name in port.variables or name in port.cache
                or name in port.default_substs or name in port.default_variables or name in port.default_defines]

    @property
    def conditionals(self) -> dict[str, str]:
        """Every automake conditional some port spells, with its condition."""
        out: dict[str, str] = {}
        for port in self.ports:
            out.update(port.conditionals)
        return out

    @property
    def ambiguous(self) -> set[str]:
        """Names that are a subst of one port and a define of a port (the
        same or another): conditions must say which result they mean."""
        return self.substs & set().union(*(port.defines for port in self.ports))

    def differences(self, other: "PortIndex") -> list[str]:
        """Human-readable differences between two indexes (empty when equal)."""
        out = []
        mine, theirs = set(self.by_package), set(other.by_package)
        out += [f"package only here: {p}" for p in sorted(mine - theirs)]
        out += [f"package only there: {p}" for p in sorted(theirs - mine)]
        for package in sorted(mine & theirs):
            a, b = self.by_package[package], other.by_package[package]
            if a.targets != b.targets or a.m4_file != b.m4_file:
                out.append(f"package differs: {package}")
        return out

    # -- lookup -----------------------------------------------------------

    def resolve(self, name: str, loc_file: str | None = None) -> Resolution | None:
        """The target providing macro `name`, defined in `loc_file` if known."""
        if loc_file:
            port = self.by_file.get(Path(loc_file).name)
            if port is not None:
                if name in port.targets and not name.endswith("_DEFAULTS"):
                    return Resolution(port.label(name), port.package, name, "target")
                if name.endswith("_DEFAULTS"):
                    return port.defaults_resolution()
                if port.aggregator:
                    return Resolution(port.label(), port.package, None, "file")
        # `//autoconf/macros/AC_FOO` and `//gnulib/macros/FOO` are named after
        # the macro they port.
        for base in ("autoconf/macros", "gnulib/macros"):
            port = self.by_package.get(f"{base}/{name}")
            if port is not None:
                target = name if name in port.targets else None
                return Resolution(port.label(target), port.package, target, "macro-package")
        candidates = self.by_name.get(name, [])
        if len(candidates) == 1:
            port = candidates[0]
            return Resolution(port.label(name), port.package, name, "name")
        return None

    def file_port(self, loc_file: str) -> Port | None:
        return self.by_file.get(Path(loc_file).name)


def snapshot_path() -> Path:
    """`index.toml` through runfiles, falling back to the source tree."""
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
    sibling = Path(__file__).resolve().parent / "index.toml"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"Cannot locate {SNAPSHOT_RLOCATION}")
