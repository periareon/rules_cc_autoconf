"""Index of the hand-written ports: which macro lives in which target.

Built from the BUILD files of `//gnulib/m4/*`, `//autoconf/macros/*` and
`//gnulib/macros/*`.  Each gnulib package's docstring names the upstream m4
file it ports, so a macro is resolved by its *definition file* first and by
name second (`gl_HEADER_ERRNO_H` is defined in errno_h.m4 and ported in
`//gnulib/m4/errno_h`; target names are not unique across packages, so a
name alone does not identify a port).

`index.toml` next to this module is a snapshot of the scan (regenerate with
`bazel run //tools/convert:update_index`), used where the checkout is not
available: tests and `bazel run` from an exported binary.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert import tomlout

try:
    from python.runfiles import Runfiles

    from autoconf.tests.gnu.autotools import IS_WINDOWS

    RUNFILES: type[Runfiles] | None = Runfiles
except ImportError:  # outside Bazel: the snapshot next to this module is read instead
    IS_WINDOWS = False
    RUNFILES = None

_BLOB_URL_RE = re.compile(r"https://github\.com/coreutils/gnulib/blob/[0-9a-f]+/m4/([^\s\"']+)")
# `name = "..."` of a rule call at column 0 (`autoconf(`, `alias(`...); the
# `name = ` of a check inside a rule is indented and is not a target.
_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\(\s*name\s*=\s*"([^"]+)"', re.MULTILINE)
# `checks.AC_SUBST("HAVE_X", ...)`: the substitutions a port provides.  A
# project's conditions over such a variable must name its subst result
# (`ac_cv_subst_HAVE_X`) when a define of the same name exists as well.
# `AC_SUBST("X", ...)` and the `subst = "X"` of a probe (gl_SCHED_H's HAVE_STRUCT_SCHED_PARAM).
_SUBST_RE = re.compile(r'(?:\bsubst\s*=\s*|\bAC_SUBST\(\s*)"([A-Za-z_][A-Za-z0-9_]*)"')
# The defines a port's checks produce: explicit (`define = "HAVE_X"`,
# `AC_DEFINE("X"`) and the HAVE_* / HAVE_DECL_* of the list macros.
_DEFINE_RE = re.compile(r'(?:\bdefine\s*=\s*|\bAC_DEFINE(?:_UNQUOTED)?\(\s*)"([A-Za-z_][A-Za-z0-9_]*)"')
# `checks.AC_SUBST("GL_X_TRUE", condition = "gl_cv_x", ...)`: the port's
# spelling of an automake conditional; the condition is what gnulib.mk's
# `if GL_X` means in the rules' terms.
_AM_CONDITIONAL_RE = re.compile(
    r'\bAC_SUBST\(\s*"([A-Za-z_][A-Za-z0-9_]*)_TRUE",\s*condition\s*=\s*"([^"]+)"', re.DOTALL
)
_PORT_DEP_RE = re.compile(
    r'"(?:@rules_cc_autoconf)?//((?:gnulib/m4|autoconf/macros|gnulib/macros)/[A-Za-z0-9_.+-]+)(?::[^"]*)?"'
)
# An `autoconf_defaults(...)` rule: its `substs` / `variables` / `defines`
# dicts hold literal defaults a probe may override, kept apart from the
# results the port's checks produce.
_DEFAULTS_BLOCK_RE = re.compile(r"^autoconf_defaults\(\n(.*?)^\)", re.DOTALL | re.MULTILINE)
_DEFAULTS_ATTR_RE = re.compile(r"^    (substs|variables|defines) = ")
_ATTR_LINE_RE = re.compile(r"^    [a-z_]+ = ")
_DICT_KEY_RE = re.compile(r'^\s+"([A-Za-z_][A-Za-z0-9_]*)": ')
_VARIABLE_RE = re.compile(r'\bM4_VARIABLE\(\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# `name = "gl_cv_x"` of a check (indented inside a rule's `checks`): the cache
# variables a port's probes record, which gnulib.mk conditions may read.
_CHECK_NAME_RE = re.compile(r'^ {8,}name = "([A-Za-z_][A-Za-z0-9_]*)",', re.MULTILINE)
# `checks.AC_CHECK_TYPE("gid_t")`: a singular check names its cache variable implicitly
# (`ac_cv_type_gid_t`), as the list macros do for each subject; a project check of the same
# subject next to the port is a second producer of that result.
_SINGULAR_CHECK_RE = re.compile(r'\bchecks\.AC_CHECK_(HEADER|FUNC|DECL|TYPE|MEMBER)\(\s*"([^"]+)"')
_CACHE_PREFIX = {
    "HEADER": "ac_cv_header_",
    "FUNC": "ac_cv_func_",
    "DECL": "ac_cv_have_decl_",
    "TYPE": "ac_cv_type_",
    "MEMBER": "ac_cv_member_",
}
_LIST_CHECK_RE = re.compile(
    r"\b(?:AC_CHECK_(HEADERS|FUNCS|DECLS|TYPES|MEMBERS)(?:_ONCE)?|GL_CHECK_(FUNCS)_(?:ANDROID|MACOS))\(\s*\[([^\]]*)\]",
    re.DOTALL,
)
_LIST_ITEM_RE = re.compile(r'"([^"]+)"')
# `gl_macros.GL_NEXT_HEADERS(["errno.h"])` / `GL_CHECK_NEXT_HEADERS([...])`: two substs per header,
# `NEXT_ERRNO_H` and `NEXT_AS_FIRST_DIRECTIVE_ERRNO_H` (gnulib/macros.bzl), which no literal
# `checks.AC_SUBST("...")` in the BUILD file spells.
_NEXT_HEADERS_RE = re.compile(r"GL_(?:CHECK_)?NEXT_HEADERS\(\s*\[([^\]]*)\]")
_QUOTED_RE = re.compile(r'"([^"]+)"')
# A check with `subst = True` defines and substitutes one name: `define = "X"`, the first
# argument of AC_COMPUTE_INT / AC_DEFINE / AC_DEFINE_UNQUOTED (uchar_h's
# `AC_COMPUTE_INT("BITSIZEOF_WCHAR_T", ..., subst = True)`), or the HAVE_* a singular check
# derives (openat's `AC_CHECK_FUNC("openat", define = "HAVE_OPENAT", subst = True)`).
_CHECK_CALL_RE = re.compile(r"\b(?:checks|macros)\.(AC_[A-Z_]+)\(")
_DEFINE_KWARG_RE = re.compile(r'\bdefine\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_NAMED_CHECKS = ("AC_COMPUTE_INT", "AC_DEFINE", "AC_DEFINE_UNQUOTED")
_NEXT_HEADER_RE = re.compile(r"GL_NEXT_HEADER\(")
_NAME_KWARG_RE = re.compile(r'\bname\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"')
_SUBST_KWARG_RE = re.compile(r'\bsubst\s*=\s*(True\b|"([A-Za-z_][A-Za-z0-9_]*)")')
SNAPSHOT_RLOCATION = "_main/tools/convert/index.toml"
UPDATE_COMMAND = "bazel run //tools/convert:update_index"


@dataclass
class Port:
    """One package of hand ports."""

    package: str
    """The package path, `gnulib/m4/fstat`."""

    targets: set[str] = field(default_factory=set)
    """The names of the package's rules (`autoconf(name = ...)`, aliases): the macros it ports."""

    substs: set[str] = field(default_factory=set)
    """AC_SUBST names the package's checks provide."""

    defines: set[str] = field(default_factory=set)
    """config.h names the package's checks provide."""

    conditionals: dict[str, str] = field(default_factory=dict)
    """Automake conditional -> the condition expression the port spells it with."""

    deps: set[str] = field(default_factory=set)
    """Port packages the BUILD file depends on."""

    variables: set[str] = field(default_factory=set)
    """M4_VARIABLE names the package's checks provide."""

    cache: set[str] = field(default_factory=set)
    """Cache variable names the package's checks record (`ac_cv_lib_error_at_line`)."""

    dual: set[str] = field(default_factory=set)
    """Names one check both defines and substitutes (`AC_DEFINE("HAVE_ISWCNTRL", 1, subst = True)`,
    `AC_COMPUTE_INT(..., subst = True)`): in `substs` and `defines`, yet one producer, so a condition
    names them bare rather than through an `ac_cv_subst_` spelling that no check carries."""

    default_substs: set[str] = field(default_factory=set)
    """AC_SUBST names the package's autoconf_defaults targets hold (gnulib's `*_H_DEFAULTS`): read
    beneath the results, a probe of the name overrides them."""

    default_variables: set[str] = field(default_factory=set)
    """M4 variable names the package's autoconf_defaults targets hold."""

    default_defines: set[str] = field(default_factory=set)
    """Define names the package's autoconf_defaults targets hold."""

    probes: dict[str, str] = field(default_factory=dict)
    """The HAVE_* name a singular or list check produces -> the cache variable of that probe
    (`HAVE_WCWIDTH` -> `ac_cv_func_wcwidth`): a condition over the name is the probe's success,
    which no default of the name (`HAVE_WCWIDTH=1` in wchar_h's) can stand in for."""

    m4_file: str | None = None
    """The upstream m4 file the package ports, `fstat.m4` (gnulib packages only)."""

    url: str | None = None
    """The upstream blob URL from the BUILD docstring."""

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

    def defaults_resolution(self) -> Resolution:
        """A `_DEFAULTS` macro of this package: the public `:defaults` target, read through
        `default_deps` (reason "defaults"); the aggregator, as an ordinary dependency, where
        the package has none (reason "file")."""
        if "defaults" in self.targets:
            return Resolution(self.label("defaults"), self.package, "defaults", "defaults")
        return Resolution(self.label(), self.package, None, "file")


@dataclass
class Resolution:
    """The port target a macro name resolved to (`PortIndex.resolve`)."""

    label: str
    """The repository-relative label, `//gnulib/m4/fstat:gl_FUNC_FSTAT`."""

    package: str
    """The port's package path."""

    target: str | None
    """The target within the package; None for the package aggregator."""

    reason: str
    """How the name resolved: "target" | "file" | "defaults" (a `:defaults` target, for default_deps)
    | "name" | "macro-package"."""

    @property
    def exact(self) -> bool:
        return self.reason in ("target", "macro-package")


def _implicit_cache(text: str) -> set[str]:
    """The cache variables the singular and list check macros of a BUILD file name implicitly."""
    out: set[str] = set()
    for match in _SINGULAR_CHECK_RE.finditer(text):
        if _NAME_KWARG_RE.search(_call_span(text, match.end())):
            continue  # named by the check itself (`_CHECK_NAME_RE` records it)
        out.add(_CACHE_PREFIX[match.group(1)] + _cache_name(match.group(2)))
    for kind, funcs_kind, items in _LIST_CHECK_RE.findall(text):
        prefix = _CACHE_PREFIX[(kind or funcs_kind).rstrip("S")]
        out |= {prefix + _cache_name(item) for item in _LIST_ITEM_RE.findall(items)}
    return out


def _cache_name(subject: str) -> str:
    """autoconf's spelling of a subject in a cache variable (translate.cache_var)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", subject.strip().replace("*", "p")).lower()


def _call_span(text: str, start: int) -> str:
    """The text of a call from just after its opening paren to its closing one."""
    depth, index = 1, start
    while index < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[index], 0)
        index += 1
    return text[start:index]


def _macro_substs(text: str) -> set[str]:
    out: set[str] = set()
    for match in _NEXT_HEADERS_RE.finditer(text):
        for header in _QUOTED_RE.findall(match.group(1)):
            upper = re.sub(r"[^A-Za-z0-9]", "_", header).upper()
            out |= {f"NEXT_{upper}", f"NEXT_AS_FIRST_DIRECTIVE_{upper}"}
    # `GL_NEXT_HEADER("stdarg.h")` / `GL_NEXT_HEADER(..., name = "NEXT_STDARG_H")`: one subst,
    # named by `name` or after the header.
    for match in _NEXT_HEADER_RE.finditer(text):
        span = _call_span(text, match.end())
        named = _NAME_KWARG_RE.search(span)
        quoted = _QUOTED_RE.findall(span)
        if named:
            out.add(named.group(1))
        elif quoted:
            out.add("NEXT_" + re.sub(r"[^A-Za-z0-9]", "_", quoted[0]).upper())
    return out


def _subst_true_names(text: str) -> set[str]:
    """The names the checks carrying `subst = True` both define and substitute (the span to the
    call's closing paren), and the names probes substitute by `subst = "NAME"` (getrusage's
    `define = "HAVE_GETRUSAGE", subst = "HAVE_GETRUSAGE"`): one producer either way."""
    out: set[str] = set()
    for match in _CHECK_CALL_RE.finditer(text):
        span = _call_span(text, match.end())
        subst = _SUBST_KWARG_RE.search(span)
        if subst is None:
            continue
        if subst.group(2):
            out.add(subst.group(2))
            continue
        kind = match.group(1)
        named = _DEFINE_KWARG_RE.search(span)
        quoted = _QUOTED_RE.findall(span)
        if named:
            out.add(named.group(1))
        elif kind in _NAMED_CHECKS and quoted:
            out.add(quoted[0])
        elif kind.startswith("AC_CHECK_") and quoted:
            out.add(_have_name(quoted[0], decl=kind == "AC_CHECK_DECL"))
    return out


def _have_name(subject: str, decl: bool = False) -> str:
    """autoconf's HAVE_* (HAVE_DECL_*) spelling of a checked subject."""
    return ("HAVE_DECL_" if decl else "HAVE_") + re.sub(
        r"[^A-Za-z0-9_]", "_", subject.strip().replace("*", "P")
    ).upper()


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
    port.dual = _subst_true_names(text)
    port.substs = set(_SUBST_RE.findall(text)) | _macro_substs(text) | port.dual
    port.defines = set(_DEFINE_RE.findall(text))
    port.conditionals = dict(_AM_CONDITIONAL_RE.findall(text))
    port.deps = {d for d in _PORT_DEP_RE.findall(text) if d != package}
    port.variables = set(_VARIABLE_RE.findall(text))
    port.cache = set(_CHECK_NAME_RE.findall(text)) | _implicit_cache(text)
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
        for item in _LIST_ITEM_RE.findall(items):
            port.defines.add(_have_name(item, decl=(kind or gl_kind) == "DECLS"))
            prefix = _CACHE_PREFIX[(kind or gl_kind).rstrip("S")]
            port.probes[_have_name(item, decl=(kind or gl_kind) == "DECLS")] = prefix + _cache_name(item)
    for match in _SINGULAR_CHECK_RE.finditer(text):
        kind, subject = match.group(1), match.group(2)
        span = _call_span(text, match.end())
        named = _DEFINE_KWARG_RE.search(span)
        define = named.group(1) if named else _have_name(subject, decl=kind == "DECL")
        # The probe's cache variable: the `name =` it gives itself (getdelim's
        # `_gl_cv_func_getdelim`), else autoconf's spelling.
        cache = _NAME_KWARG_RE.search(span)
        port.probes[define] = cache.group(1) if cache else _CACHE_PREFIX[kind] + _cache_name(subject)
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
    def from_files(cls, build_files: dict[str, Path]) -> PortIndex:
        """`package -> BUILD.bazel path` (missing files are skipped)."""
        port_list = []
        for package, path in sorted(build_files.items()):
            if not path.is_file():
                continue
            port_list.append(parse_build(package, path.read_text(encoding="utf-8", errors="replace")))
        return cls(port_list)

    @classmethod
    def from_workspace(cls, root: Path) -> PortIndex:
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
    def from_snapshot(cls, path: Path | None = None) -> PortIndex:
        """Load `index.toml` (from runfiles when no path is given)."""
        if path is None:
            path = snapshot_path()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(
            [
                Port(
                    package=entry["package"],
                    targets=set(entry["targets"]),
                    substs=set(entry.get("substs", [])),
                    defines=set(entry.get("defines", [])),
                    conditionals=dict(entry.get("conditionals", {})),
                    deps=set(entry.get("deps", [])),
                    variables=set(entry.get("variables", [])),
                    cache=set(entry.get("cache", [])),
                    dual=set(entry.get("dual", [])),
                    default_substs=set(entry.get("default_substs", [])),
                    default_variables=set(entry.get("default_variables", [])),
                    default_defines=set(entry.get("default_defines", [])),
                    probes=dict(entry.get("probes", {})),
                    m4_file=entry.get("m4_file"),
                    url=entry.get("url"),
                )
                for entry in data["ports"]
            ]
        )

    def to_snapshot(self) -> str:
        """The index as the text of index.toml: a header saying how it is generated, one
        `[[ports]]` table per package, stable key order, empty fields omitted."""
        rows: list[dict[str, object]] = [
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
                "dual": sorted(port.dual),
                "default_substs": sorted(port.default_substs),
                "default_variables": sorted(port.default_variables),
                "default_defines": sorted(port.default_defines),
                "probes": dict(sorted(port.probes.items())),
            }
            for port in self.ports
        ]
        return (
            tomlout.header(
                UPDATE_COMMAND,
                "the hand ports of this repository (//gnulib/m4, //autoconf/macros, "
                "//gnulib/macros): their targets and the results they provide",
            )
            + "\n"
            + tomlout.table_array("ports", rows)
        )

    @property
    def substs(self) -> set[str]:
        """Every substitution some port provides, as a result or as a default."""
        return set().union(*(port.substs | port.default_substs for port in self.ports))

    @property
    def result_substs(self) -> set[str]:
        """The substitutions some port's checks produce (a probe's `subst =` included, the defaults
        not): a name here has a result of its own on every platform (an arm substitutes a constant
        where the probe does not run), which a condition may name bare."""
        return set().union(*(port.substs for port in self.ports))

    @property
    def plain_substs(self) -> set[str]:
        """The substitutions some port's own `checks.AC_SUBST` produces (not a probe's `subst =`,
        not a default): a condition over one of these names means that result."""
        return set().union(*(port.substs - port.dual for port in self.ports))

    @property
    def probes(self) -> dict[str, str]:
        """HAVE_* name -> the cache variable of the one probe producing it across the ports; a name two
        ports probe differently is left out."""
        out: dict[str, str] = {}
        conflicting: set[str] = set()
        for port in self.ports:
            for name, cache in port.probes.items():
                if out.setdefault(name, cache) != cache:
                    conflicting.add(name)
        return {name: cache for name, cache in out.items() if name not in conflicting}

    @property
    def variables(self) -> set[str]:
        """Every M4 variable some port provides as a result (`checks.M4_VARIABLE`): a shell
        variable holding the words GNU assigns (`gl_cv_have_unlimited_file_name_length` is "no"
        when PATH_MAX is defined), compared by value, where a probe compares by success."""
        return set().union(*(port.variables for port in self.ports))

    @property
    def provided(self) -> set[str]:
        """Every define, substitution and M4 variable some port provides, as a result or as a default."""
        return set().union(
            *(
                port.defines
                | port.substs
                | port.variables
                | port.default_substs
                | port.default_variables
                | port.default_defines
                for port in self.ports
            )
        )

    def providers_of(self, name: str) -> list[Port]:
        """The ports that provide `name` as a define, subst, M4 variable, cache variable or default."""
        return [
            port
            for port in self.ports
            if name in port.defines
            or name in port.substs
            or name in port.variables
            or name in port.cache
            or name in port.default_substs
            or name in port.default_variables
            or name in port.default_defines
        ]

    @property
    def conditionals(self) -> dict[str, str]:
        """Every automake conditional some port spells, with its condition."""
        out: dict[str, str] = {}
        for port in self.ports:
            out.update(port.conditionals)
        return out

    @property
    def ambiguous(self) -> set[str]:
        """Names that are a subst of one port and a define of a port (the same or another), from
        different checks: conditions must say which result they mean.  A name one check both
        defines and substitutes (`dual`) is one producer and stays unambiguous."""
        dual = set().union(*(port.dual for port in self.ports))
        return (self.substs & set().union(*(port.defines for port in self.ports))) - dual

    def differences(self, other: PortIndex) -> list[str]:
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

    def resolve(self, name: str, loc_file: str | None = None, library: bool = False) -> Resolution | None:
        """The target providing macro `name`, defined in `loc_file` if known.  `library` says
        `loc_file` is one gnulib-tool wrote (gnulib-comp.m4's gl_FILE_LIST): a target of that
        name in one port is the macro, whatever the snapshot named the file (bison's
        m4/printf-posix-rpl.m4, since merged into printf-posix.m4)."""
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
            return Resolution(port.label(name), port.package, name, "target" if library else "name")
        return None

    def file_port(self, loc_file: str) -> Port | None:
        return self.by_file.get(Path(loc_file).name)


def snapshot_path() -> Path:
    """`index.toml` through runfiles, falling back to the source tree."""
    runfiles = RUNFILES.Create() if RUNFILES is not None else None
    if runfiles:
        found = runfiles.Rlocation(SNAPSHOT_RLOCATION, "_main" if IS_WINDOWS else None)
        if found and Path(found).exists():
            return Path(found)
    sibling = Path(__file__).resolve().parent / "index.toml"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"Cannot locate {SNAPSHOT_RLOCATION}")


def workspace_root(explicit: Path | None = None) -> Path:
    """The rules_cc_autoconf checkout a generator writes into: `explicit` (a `--workspace` flag),
    else the one `bazel run` was started from (`BUILD_WORKSPACE_DIRECTORY`), else the tree this
    module sits in."""
    if explicit:
        return explicit
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root is not None:
        return Path(root)
    return Path(__file__).resolve().parents[2]


def ports_checkout() -> Path | None:
    """The checkout `bazel run` was started from, when it holds the hand ports (`gnulib/m4`);
    None otherwise (an exported binary, or a run from another workspace)."""
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root and (Path(root) / "gnulib/m4").is_dir():
        return Path(root)
    return None
