"""Turn expanded DEFUN bodies into `autoconf` targets.

One `Target` per expanded `AC_DEFUN` body (deduplicated by name and
arguments).  Leaf macro calls become `checks.*` / `macros.*` calls where the
mapping is direct; `AC_REQUIRE`s and calls of macros that already have a port
become `deps`.  The shell between calls is parsed (`shparse`) and evaluated
symbolically (`dataflow`): the condition under which a call runs becomes its
`requires` (or a `select()` arm for `case $host_os` and configure options),
and assignments to substituted variables become conditional `AC_SUBST` /
`M4_VARIABLE` checks.

The output never carries a TODO.  What has no mechanical translation is
looked up in the decisions file (`decisions.py`) by a stable key; without a
decision the construct is left out and reported, so that regenerating from
the same sources and decisions reproduces the same output.

Project mode (`project=ProjectState(...)`) adds the configure.ac top level
(`translate_root`) and the gl_INIT conventions of a gnulib project.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import libraries, lower, m4args
from tools.convert import shparse as sh
from tools.convert.index import PortIndex, Resolution
from tools.convert.ir import (
    PLATFORM_FAMILIES,
    AttrValue,
    Call,
    CheckItem,
    Loc,
    Package,
    Raw,
    Target,
    family_of_labels,
    strings,
)
from tools.convert.m4loc import CallLocator, M4Source, Span
from tools.convert.markers import INTERNAL_DEFUN_RE, Node, Text, Tree, normalize_path

# gnulib-common helpers that have a `gl_macros` equivalent returning a list
# of checks.  The first group gets its own target per argument (shared
# probes); the second is inlined into the caller's checks.
HELPER_TARGET_MACROS = {
    "gl_CHECK_FUNCS_ANDROID": "gl_macros.GL_CHECK_FUNCS_ANDROID",
    "gl_CHECK_FUNCS_MACOS": "gl_macros.GL_CHECK_FUNCS_MACOS",
    "gl_CHECK_FUNCS_ANDROID_MACOS": "gl_macros.GL_CHECK_FUNCS_ANDROID_MACOS",
}
HELPER_INLINE_MACROS = {
    "gl_NEXT_HEADERS": "gl_macros.GL_NEXT_HEADERS",
    "gl_CHECK_NEXT_HEADERS": "gl_macros.GL_CHECK_NEXT_HEADERS",
}

# Calls whose expansion we look *through*: their children are the body's
# statements (the probe inside AC_CACHE_CHECK), their own text is boilerplate.
TRANSPARENT = {"AC_CACHE_CHECK", "AC_CACHE_VAL"}
# Substitutions `autoconf_package_info` produces from AC_INIT: never a `variable:` decision.
RULES_PROVIDED_SUBSTS = frozenset(
    {
        "PACKAGE",
        "VERSION",
        "PACKAGE_NAME",
        "PACKAGE_TARNAME",
        "PACKAGE_VERSION",
        "PACKAGE_STRING",
        "PACKAGE_BUGREPORT",
        "PACKAGE_URL",
    }
)
# pkg-config's macros as the converter's pkg_stub.m4 expands them: the macro's node holds a
# RCCAC_PKG_FOUND and a RCCAC_PKG_MISSING node (args: variable, package list, action), and the
# converter's verdict on the package list picks which one is the body's shell.
PKG_MACROS = {"PKG_CHECK_MODULES", "PKG_CHECK_MODULES_STATIC", "PKG_CHECK_EXISTS"}
PKG_FOUND, PKG_MISSING = "RCCAC_PKG_FOUND", "RCCAC_PKG_MISSING"

# Calls that carry no information for a port.
IGNORED = {
    "AC_MSG_WARN",
    "AC_MSG_CHECKING",
    "AC_MSG_RESULT",
    "AC_MSG_NOTICE",
    "AC_CONFIG_COMMANDS",
    "AC_CONFIG_MACRO_DIRS",
    "AC_CONFIG_MACRO_DIR",
    "AC_CONFIG_AUX_DIR",
    "AC_CONFIG_SRCDIR",
    "AC_OUTPUT",
    "AC_ARG_VAR",
    "AC_PREREQ",
}

# Compiler-warning machinery: flags belong in copts, not in checks.
WARNING_MACROS = {
    "gl_WARN_ADD",
    "gl_MANYWARN_ALL_GCC",
    "gl_MANYWARN_COMPLEMENT",
    "gl_CC_GNULIB_WARNINGS",
    "gl_CC_ALLOW_WARNINGS",
    "gl_CXX_ALLOW_WARNINGS",
    "gl_COMPILER_OPTION_IF",
    "gl_UNKNOWN_WARNINGS_ARE_ERRORS",
    "gl_GCC_VERSION_IFELSE",
    "gl_WARN_ADD_IF_SUPPORTED",
}

PROBES = {"AC_COMPILE_IFELSE", "AC_LINK_IFELSE", "AC_PREPROC_IFELSE", "AC_RUN_IFELSE"}

# Configure options automake, libtool and gnulib add for their own machinery.
TOOLING_OPTIONS = {
    "dependency-tracking",
    "silent-rules",
    "maintainer-mode",
    "libtool-lock",
    "shared",
    "static",
    "fast-install",
    "rpath",
    "nls",
    "largefile",
    "year2038",
    "cross-guesses",
    "option-checking",
    "pic",
    "gnu-ld",
    "sysroot",
    "aix-soname",
}

# libtool's macro files: everything defined there probes the linker and the
# shared-library conventions of the host, which Bazel's C++ toolchain owns.
# Nothing of it belongs in a port, so the DEFUNs are dropped wholesale.
LIBTOOL_FILES = {"libtool.m4", "ltoptions.m4", "ltsugar.m4", "ltversion.m4", "lt~obsolete.m4", "ltdl.m4"}

# Variables of the configure environment and the compiler driver: no check
# ever produces them, so a condition over one of them has no translation.
ENVIRONMENT_VARIABLES = {
    "CC",
    "CXX",
    "CPP",
    "CXXCPP",
    "GCC",
    "GXX",
    "CFLAGS",
    "CXXFLAGS",
    "CPPFLAGS",
    "LDFLAGS",
    "LIBS",
    "LIBOBJS",
    "LTLIBOBJS",
    "EXEEXT",
    "OBJEXT",
    "AR",
    "ARFLAGS",
    "RANLIB",
    "LD",
    "NM",
    "STRIP",
    "INSTALL",
    "MAKE",
    "SHELL",
    "CONFIG_SHELL",
    "PATH",
    "IFS",
    "YACC",
    "LEX",
    "PKG_CONFIG",
    "DEFS",
    "ECHO_N",
    "ECHO_C",
    "ECHO_T",
    "LN_S",
    "SED",
    "GREP",
    "EGREP",
    "FGREP",
    "AWK",
    "MKDIR_P",
}

# Autoconf's own check macros that have no port: the common ones have a
# fixed translation (below); the rest are decisions.  Program and tool
# lookups (AC_PROG_*, AC_PATH_*) are never checks.
_CHECK_BUILTIN_RE = re.compile(r"^AC_(HEADER|FUNC|TYPE|STRUCT|C|SYS|CHECK|DECL|FIND|LIB|CXX|F77|FC)_")

LANGUAGES = {"C": "c", "C++": "cpp"}

_NUMBER_RE = re.compile(r"^-?[0-9]+$")
_ALLCAPS_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_BUILTIN_RE = re.compile(r"^_?(AC|AH|AM|AS|AU|m4|_m4)_")
_MODULE_INDICATOR_RE = re.compile(r"^gl_[A-Z0-9_]+_MODULE_INDICATOR$")
# AC_EGREP_CPP programs of the form `#if !defined X` (or `#ifndef X`) / token / `#endif`,
# repeated: the token appears iff some macro is undefined.
_EGREP_BLOCK_RE = re.compile(
    r"#\s*(?:if\s*!\s*defined|ifndef)\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)?\s*\n\s*(\S+)\s*\n\s*#\s*endif"
)


@dataclass
class Options:
    repo: str = ""
    """Label prefix for ports: "" (this repo) or "@rules_cc_autoconf"."""
    in_repo: bool = True
    """Load `autoconf_cache` as the hand ports do."""
    layout: str = "per-file"
    """"per-file" | "package"."""
    package_prefix: str = "m4/"
    """Per-file layout: packages land at <prefix><stem>."""
    gnulib_commit: str | None = None
    """Docstring URL for unported gnulib files."""


@dataclass
class FlagInfo:
    macro: str
    """AC_ARG_ENABLE | AC_ARG_WITH."""
    option: str
    """As written: "dfltcc", "gcc-warnings"."""
    help: str
    """The option's help text (AS_HELP_STRING's description)."""
    default: bool = False
    """Whether the bool_flag is on by default (what configure does when the option is not given)."""


@dataclass
class GnulibLibrary:
    """One gnulib library of the tree (libpaper has two: `libgnu/` for the library and `src/libgnu/`
    for the programs, each with its own Makefile.gnulib)."""

    directory: str
    """Its directory relative to the package (`lib`, `src/libgnu`)."""
    label: str
    """The cc_library's label (`:lib/gnu`)."""
    archive: str
    """The archive automake names it by (`libgnu.a`, `libgnu.la`): what LDADD lines list."""
    private_headers_label: str
    """The `<name>_private_headers` library the sources compile with."""


@dataclass
class ProjectState:
    """What project mode collects beyond the targets."""

    flatten_files: set[str] = field(default_factory=set)
    """DEFUNs expanded inline (gnulib-comp.m4)."""
    gnulib_files: set[str] = field(default_factory=set)
    """Base names of the m4 files gnulib-tool copied into the tree (gl_FILE_LIST): their macros are
    gnulib's, ported under their names even where the snapshot's file name is not a port's."""
    gnulib_packages: dict[str, str] = field(default_factory=dict)
    """Package -> aggregator label."""
    gnulib_defaults: dict[str, str] = field(default_factory=dict)
    """Header ports whose `_DEFAULTS` the script AC_REQUIREs: package -> its `:defaults` label (default_deps)."""
    src_conditions: dict[str, list[df.Cond]] = field(default_factory=dict)
    """GL_COND_OBJ_X -> conds."""
    hand_targets: dict[str, str] = field(default_factory=dict)
    """The rules the hand-written BUILD.bazel declares, name -> rule kind (`alias`, `string_flag`): what a
    `generated:` decision may name, and what an option flag of the same name steps aside for unless it is a
    flag itself."""
    hand_packages: set[str] = field(default_factory=set)
    hand_build: str = ""
    """The hand-written BUILD.bazel's text: what it keys a select() on (`:libz_enabled`) is an
    option setting the generated file must declare, reached by a translated condition or not."""
    """Package paths (`tests`) whose BUILD file is hand-written beside the generated root: a generated
    file under one of them cannot be an output of the root package."""
    cond_headers: dict[str, str] = field(default_factory=dict)
    """Header -> GL_GENERATE var."""
    header_conditions: dict[str, df.Cond] = field(default_factory=dict)
    """GL_GENERATE var -> when it is true."""
    src_condition_exprs: dict[str, str] = field(default_factory=dict)
    """GL_COND_OBJ_X -> its lowered expression."""
    generated_headers: set[str] = field(default_factory=set)
    """The gnulib wrapper headers the gnulib library generates (`lib/stdio.h`), with their directory:
    a BUILT_SOURCES entry elsewhere names one of them, not a file to write."""
    generated_includes: set[str] = field(default_factory=set)
    """Files of the tree that generated sources include (`colorize.c` is `#include <colorize-posix.c>`):
    textual headers of the library compiling them."""
    enabled_switches: set[str] = field(default_factory=set)
    """gl_GNULIB_ENABLED_<module> conditionals of gnulib-tool's --conditional-dependencies that the
    Makefile tests; every module is enabled, so each is provided as `true`."""
    generate_defaults: set[str] = field(default_factory=set)
    """GL_GENERATE_<HDR>_H switches of the conditional wrapper headers; the ones no port provides are set
    to false (the system header is used)."""
    indicator_defines: list[str] = field(default_factory=list)
    """GNULIB_<MODULE> defines of the library's gl_INIT (gl_MODULE_INDICATOR), 1 in config.h."""
    indicator_test_defines: dict[str, str | int] = field(default_factory=dict)
    """GNULIB_X -> IN_PKG_GNULIB_TESTS (or the number the define carries, as `literal` reads it)."""
    indicator_substs: list[str] = field(default_factory=list)
    """@GNULIB_X@ substitutions the library's gl_INIT turns on (1): the wrapper headers' module indicators."""
    indicator_test_substs: dict[str, str] = field(default_factory=dict)
    """gl_<HEADER>_MODULE_INDICATOR in the tests' gl_INIT: GNULIB_X -> IN_<PKG>_GNULIB_TESTS, the
    @GNULIB_X@ the library's wrapper headers read (0 there, 1 in the tests)."""
    flags: dict[str, FlagInfo] = field(default_factory=dict)
    """bool_flag name -> info."""
    init_args: list[str] | None = None
    """AC_INIT's arguments (quadrigraphs undone); None until AC_INIT is seen."""
    automake: bool = False
    """Whether the script calls AM_INIT_AUTOMAKE."""
    config_headers: list[str] = field(default_factory=list)
    """AC_CONFIG_HEADERS entries (`config.h`, `lib/config.h:lib/config.hin`)."""
    config_subdirs: list[str] = field(default_factory=list)
    """AC_CONFIG_SUBDIRS: sub-configures, literal names."""
    subdir_conditions: dict[str, df.Cond] = field(default_factory=dict)
    """Sub-configure -> the condition its AC_CONFIG_SUBDIRS call ran under (`project._subdir_gates`)."""
    config_files: list[str] = field(default_factory=list)
    """AC_CONFIG_FILES entries (`Makefile`, `src/Makefile`)."""
    am_conditionals: dict[str, df.Cond] = field(default_factory=dict)
    """AM_CONDITIONAL name -> the condition under which it holds."""
    am_conditional_tests: dict[str, str] = field(default_factory=dict)
    """AM_CONDITIONAL name -> its shell test as written, for AM_COND_IF's synthesized `if`."""
    gnulib_libraries: list[GnulibLibrary] = field(default_factory=list)
    """The gnulib libraries of the tree, in the order their fragments were found."""
    private_header_libs: dict[str, str] = field(default_factory=dict)
    """Library label -> its _private_headers label."""
    config_targets: list[str] = field(default_factory=list)
    """autoconf_hdr target names, first is "config"."""
    subst_headers: list[str] = field(default_factory=list)
    """AC_CONFIG_FILES outputs that are headers (in config_headers)."""
    tarname: str = ""
    """PACKAGE_TARNAME: automake's $(PACKAGE) in installation directories."""
    header_libraries: list[str] = field(default_factory=list)
    """config_headers (+ top_headers) labels."""
    literal_substs: dict[str, str] = field(default_factory=dict)
    """Unconditional AC_SUBST literals."""
    subst_values: dict[str, list[str]] = field(default_factory=dict)
    """Every literal the main target's AC_SUBSTs give a variable: unconditionally, under a
    condition, or in a select() arm (`LIBLZMA` = `-llzma` where the library was found)."""
    port_ambiguous: set[str] = field(default_factory=set)
    """Names that are a port subst and a define (index)."""
    port_substs: set[str] = field(default_factory=set)
    """Substitutions the ports provide (index)."""
    port_result_substs: set[str] = field(default_factory=set)
    """Substitutions some port's checks produce, a probe's `subst =` included (index): a result of the
    name exists on every platform, so a condition names it bare (a platform arm may substitute a
    constant where the probe does not run, as spawn_h's HAVE_POSIX_SPAWN on Windows)."""
    port_plain_substs: set[str] = field(default_factory=set)
    """Substitutions a port's own `checks.AC_SUBST` produces (index): a condition over such a name
    is that result, where a HAVE_* a probe alone produces is the probe's success."""
    port_probes: dict[str, str] = field(default_factory=dict)
    """HAVE_* name -> the cache variable of the port probe producing it (index), for spelling
    gnulib.mk's `test $HAVE_X = 0` as `!ac_cv_func_x`: the rules resolve the bare name to the
    header's default (1) where the probe failed and defined nothing."""
    port_variables: set[str] = field(default_factory=set)
    """M4 variables the ports provide (index): gnulib's HAVE_<MODULE> shell variables that a port tracks
    without substituting (obstack's HAVE_OBSTACK), which gnulib's replacement rule reads too."""
    port_conditionals: dict[str, str] = field(default_factory=dict)
    """Automake conditionals the ports spell."""
    c_standard: str | None = None
    """A standard the script forces with CFLAGS=-std=...: pinned on every target (`pinned_standards`)
    and recorded by a checks.AC_LANG_STANDARD of the main target."""
    cxx_standard: str | None = None
    """The same for CXXFLAGS=-std=...."""
    required_standards: set[str] = field(default_factory=set)
    """Standards a check already records (AC_PROG_CC_C99, AX_CXX_COMPILE_STDCXX, or one added for the
    above), by their ISO name (`gnu11` -> `c11`): a second one would be a duplicate producer."""
    makefile_standards: dict[str, str] = field(default_factory=dict)
    """`-std=` found in a Makefile's flags, per language."""
    pinned_standards: dict[str, str] = field(default_factory=dict)
    """Language (`C` / `CXX`) -> the standard, as spelled on the command line (`gnu99`), that configure
    switches the compiler into for the whole build (AC_PROG_CC_C99, AX_CXX_COMPILE_STDCXX, `-std=` in
    the flags): `project._standard_flags` pins it on every target and check of the package (see
    `common.standard_statement`), and `standard_origins` says what set it, for the comment."""
    standard_origins: dict[str, str] = field(default_factory=dict)
    """Language -> what in the script chose `pinned_standards[lang]` (`AC_PROG_CC_C99`)."""
    templates: list[Path] = field(default_factory=list)
    """The .in.h templates of the gnulib library."""

    # Configure options as `select()` keys: the config_setting labels the
    # output uses (only those get a flag rendered) and the values the
    # project compares each option with (`--with-ssl=openssl`).
    option_settings: dict[str, tuple[str, str]] = field(default_factory=dict)
    """Label -> (option, value)."""
    flag_values: dict[str, set[str]] = field(default_factory=dict)
    """Option -> the literals other than yes/no the script compares it with (`--with-ssl=openssl`)."""
    flag_defaults: dict[str, str] = field(default_factory=dict)
    """Literal the not-given branch assigns."""
    external_modules: dict[str, str] = field(default_factory=dict)
    """Registry modules (tools/convert/libraries.py) MODULE.bazel declares: module -> version."""

    configure_libs: dict[str, str] = field(default_factory=dict)
    """Registry libraries configure puts in LIBS (AC_CHECK_LIB, AC_SEARCH_LIBS, `-l` by hand), which
    every target links: `-l` name -> label."""
    library_arms: dict[str, str] = field(default_factory=dict)
    """Label of a `configure_libs` library -> the one configure-option `select()` arm every check
    that probes it sits in (gawk's `-lmpfr -lgmp` under --enable-mpfr): what links it depends on
    it in that arm, as configure only finds it there (`results._library_check_targets`).  A
    library probed unconditionally, or by no check at all, is not listed and is every target's."""
    configure_link_vars: list[str] = field(default_factory=list)
    """Substitutions holding the `-l` flag of a system library configure appends to LIBS (an
    AC_SEARCH_LIBS result, AC_CHECK_LIB's default action), which every target links through the
    shared `autoconf_linkopts`, as LIBS is: `-ldl` for dlopen on glibc before 2.34."""
    pkg_libs: dict[str, list[str]] = field(default_factory=dict)
    """`FUSE_LIBS` -> the registry library labels a found PKG_CHECK_MODULES stands for: the deps of what links `$(FUSE_LIBS)`."""
    pkg_conditions: dict[str, df.Cond] = field(default_factory=dict)
    """`X_LIBS` -> the condition its PKG_CHECK_MODULES ran under (grep's pcre under --enable-perl-regexp):
    what links `$(X_LIBS)` depends on the library under the same select() arm."""
    pkg_cc_deps: set[str] = field(default_factory=set)
    """Registry libraries found through pkg-config: `cc_deps` of the root targets, so later probes see them."""

    program_lists: dict[str, list[ListedProgram]] = field(default_factory=dict)
    """configure's value of a substituted `*_PROGRAMS` variable (coreutils' `bin_PROGRAMS = @bin_PROGRAMS@`):
    variable -> the programs it names, each with the condition configure lists it under."""

    shell_list_appends: dict[str, list[tuple[str, df.Cond, str]]] = field(default_factory=dict)
    """Words a probe's action appends to a shell list variable (`gl_ADD_PROG([optional_bin_progs], [hostid])`
    in AC_CHECK_FUNCS' action-if-found): variable -> (word, condition, where)."""

    extra_templates: dict[str, str] = field(default_factory=dict)
    """Templates the converter writes next to the BUILD file (path -> text): the wrapper headers a
    gnulib.mk recipe writes with echo lines, rendered as one autoconf_hdr template."""

    package_path: str = "."
    """The package in the module (`.` at the root; gawk's `extension`): the scope of `library:` decisions."""
    produced_results: set[str] = field(default_factory=set)
    """Every result name the project's checks or the ports they depend on produce: a source condition
    naming anything else (a check an IGNORE decision dropped) cannot be evaluated."""
    libobj_dir: str = ""
    """AC_CONFIG_LIBOBJ_DIR: where AC_LIBOBJ's replacement sources live (libpcap's missing/)."""
    libobj_names: dict[str, str] = field(default_factory=dict)
    """GL_COND_OBJ_STRLCPY -> strlcpy (AC_LIBOBJ's argument)."""

    subst_sources: dict[str, list[tuple[str, df.Cond]]] = field(default_factory=dict)
    """Substituted variables whose value is a list of source files configure computes (libpcap's
    `PLATFORM_C_SRC="pcap-linux.c"`, `MODULE_C_SRC="$MODULE_C_SRC pcap-usb-linux.c"`): variable -> the
    files, each under the condition configure lists it."""

    generated_dir_headers: dict[str, list[str]] = field(default_factory=dict)
    """Headers a `generated:` decision or recipe writes, by directory: automake's -I. lets every
    target of the directory include them (coreutils' src/version.h), so they join their srcs."""

    manual_programs: list[tuple[str, str]] = field(default_factory=list)
    """Programs emitted with `tags = ["manual"]`: configure builds them only when a probe holds, which a
    target's existence cannot depend on: (target name, the condition as text)."""

    def module_name(self) -> str:
        """The registry module name the project converts to (AC_INIT's tarname, lower-cased)."""
        return re.sub(r"[^a-z0-9_.-]", "_", (self.tarname or "").lower())

    def label(self, name: str, value: str) -> str:
        label = f":{name}_enabled" if value == "yes" else f":{name}_{slug(value)}"
        self.option_settings[label] = (name, value)
        return label

    def values(self, name: str) -> set[str]:
        return self.flag_values.get(name, set())

    def default(self, name: str) -> bool:
        """Whether the option's bool_flag is on by default (`FlagInfo.default`)."""
        info = self.flags.get(name)
        return bool(info.default) if info is not None else False


@dataclass
class Report:
    notes: list[str] = field(default_factory=list)
    unresolved: set[str] = field(default_factory=set)
    inlined: set[str] = field(default_factory=set)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


@dataclass
class _Ctx:
    target: Target
    """The target the body's checks and deps go to."""
    package: Package
    """The target's package (where helper targets are added)."""
    lang: list[str]
    """The AC_LANG stack (`c` / `cpp`); the last entry is the current language."""
    analysis: df.Analysis
    """The dataflow of the body's shell."""
    file: str | None = None
    """The m4 file the body comes from."""
    locator: CallLocator | None = None
    """Finds the body's macro calls in its m4 source (None for the configure.ac top level)."""
    cache: str | None = None
    """The cache variable of the enclosing AC_CACHE_CHECK, which names its probe."""
    cache_comment: str | None = None
    """AC_CACHE_CHECK text, attached to its probe."""
    guard: df.Cond = df.TRUE
    """Inherited by the children of a transparent call."""
    explicit_substs: dict[str, str] = field(default_factory=dict)
    """var -> source comment."""
    probe_vars: set[str] = field(default_factory=set)
    """Cache vars bound to probes."""
    defines: set[str] = field(default_factory=set)
    """Names AC_DEFINE'd in this body."""
    dropped: set[str] = field(default_factory=set)
    """Cache vars whose probe was left out (undecided / IGNORE)."""
    auto_variables: bool = True
    """Emit ALLCAPS shell variables as substs."""
    root: bool = False
    """The configure.ac top level."""

    @property
    def language(self) -> str:
        return self.lang[-1] if self.lang else "c"

    @property
    def where(self) -> str:
        return f"{self.target.name} ({self.file or '?'})"

    def inner(self, cache: str | None, cache_comment: str | None, guard: df.Cond) -> _Ctx:
        """A context for the children of a transparent call: the same body state (the collections
        are shared, not copied), under `guard`, with the cache variable and comment given."""
        return replace(self, cache=cache, cache_comment=cache_comment, guard=guard)


@dataclass(frozen=True)
class ListedProgram:
    """A program configure puts in a substituted `*_PROGRAMS` variable."""

    name: str
    """As configure spells it (`hostid`, `src/libstdbuf.so`); automake's `src/` prefix and `$(EXEEXT)` removed."""

    cond: df.Cond
    """The condition configure lists it under (TRUE for the unconditional ones)."""

    source: str
    """Where the listing comes from, for the report (`AC_CHECK_FUNCS([gethostid]) action-if-found`)."""


class Translator:
    """Translate the selected DEFUN bodies of a tree."""

    def __init__(
        self,
        tree: Tree,
        *,
        index: PortIndex | None,
        selected_files: set[str],
        m4_sources: dict[str, str],
        options: Options,
        project: ProjectState | None = None,
        decisions: dec.Decisions | None = None,
    ) -> None:
        self.tree = tree
        self.index = index
        self.selected = {normalize_path(f) for f in selected_files}
        self.selected_names = {basename(f) for f in self.selected}
        self.sources = {normalize_path(k): M4Source(v) for k, v in m4_sources.items()}
        self.options = options
        self.project = project
        self.decisions = decisions if decisions is not None else dec.Decisions()
        self._anonymous_names: dict[tuple[str, int], str] = {}  # (macro, expansion line) -> name
        # PKG_CHECK_MODULES list -> verdict
        self._pkg_verdicts: dict[str, tuple[bool, list[libraries.Library], str]] = {}
        self._anonymous_locs: dict[tuple[str, int], tuple[str, int] | None] = {}
        self.report = Report()
        # Definition file of every expanded DEFUN (for resolving AC_REQUIREs).
        self.defun_files: dict[str, str] = {}
        for node in tree.defuns(include_internal=True):
            if node.loc_file and node.name not in self.defun_files:
                self.defun_files[node.name] = node.loc_file
        self.packages: dict[str, Package] = {}
        self._translated: dict[tuple[str, tuple[str, ...]], Target] = {}
        self.undefined_macros: dict[str, str] = {}  # macro nothing defined -> first body it was seen in
        self.tarname = self._tarname(tree)  # PACKAGE_TARNAME of the configure.ac, as AC_INIT defaults it
        self.dropped_global: set[str] = set()  # cache variables no check carries (multi-valued chains)
        self.toolchain_vars: set[str] = set()  # cache / action variables of compiler-option tests
        self._assigners: dict[str, set[int]] | None = None  # variable -> bodies whose own shell assigns it
        # Every cache variable and ALLCAPS shell variable something in the
        # whole configure script sets. One outside this set is never set:
        # configure reads it as empty, and so does the translation.
        self.cache_vars_set = self._collect_cache_vars(tree)
        if self.project is not None:
            for option, values in self._collect_option_values(tree).items():
                self.project.flag_values.setdefault(option, set()).update(values)
            for lang, flag in self._collect_standard_flags(tree):
                self._set_standard(lang, flag[len("-std=") :])
        for text in m4_sources.values():  # ports' m4 files, which the expansion may not spell out
            self.cache_vars_set.update(_assigned_names(text))

    @property
    def _state(self) -> ProjectState:
        """The project state of project mode.  The project-mode handlers run only when `project` was
        given (their callers check); reaching one without it is a bug."""
        if self.project is None:
            raise RuntimeError("project mode only: no ProjectState was given")
        return self.project

    @property
    def _ports(self) -> PortIndex:
        """The port index.  Read where a `Resolution` it produced is in hand, so it cannot be missing."""
        if self.index is None:
            raise RuntimeError("a port resolution without a port index")
        return self.index

    @staticmethod
    def _tarname(tree: Tree) -> str | None:
        for node in tree.walk():
            if node.kind == "call" and node.name == "AC_INIT":
                args = [m4args.quadrigraphs(a).strip() for a in node.args] + ["", "", "", ""]
                return args[3] or (default_tarname(args[0]) if args[0] else None)
        return None

    @staticmethod
    def _collect_cache_vars(tree: Tree) -> set[str]:
        """Every cache variable (`*_cv_*`) and ALLCAPS shell variable something in the expanded
        script sets: literal assignments in the shell text, plus the variables the check macros
        set implicitly (AC_CACHE_CHECK's, the probes' action variables, the `ac_cv_header_x_h` of
        AC_CHECK_HEADERS and friends). `_never_set` reads a name outside this set as empty, the
        way configure would.
        """
        out: set[str] = set()
        for text in _iter_texts(tree.items):
            out.update(_assigned_names(text.text))
        for node in tree.walk():
            if node.kind != "call":
                continue
            args = node.args + ["", "", ""]
            if node.name in ("AC_CACHE_CHECK", "AC_CACHE_VAL"):
                out.update(cache_node_var(node, lang) for lang in ("c", "cpp"))
            elif node.name in PROBES:
                out.update(
                    v
                    for v in (action_var(m4args.m4sh_to_shell(args[1])), action_var(m4args.m4sh_to_shell(args[2])))
                    if v
                )
            elif node.name in (
                "AC_CHECK_HEADERS",
                "AC_CHECK_HEADERS_ONCE",
                "AC_CHECK_FUNCS",
                "AC_CHECK_FUNCS_ONCE",
                "AC_REPLACE_FUNCS",
                "AC_CHECK_DECLS",
                "AC_CHECK_DECLS_ONCE",
                "AC_CHECK_TYPES",
                "AC_CHECK_MEMBERS",
            ):
                words = (
                    args[0].split(",")
                    if node.name in ("AC_CHECK_DECLS", "AC_CHECK_DECLS_ONCE", "AC_CHECK_TYPES", "AC_CHECK_MEMBERS")
                    else m4args.words(args[0])
                )
                out.update(cache_var(node.name, w) for w in words if w.strip())
            elif node.name in ("AC_CHECK_HEADER", "AC_CHECK_FUNC", "AC_CHECK_DECL", "AC_CHECK_TYPE", "AC_CHECK_MEMBER"):
                out.add(cache_var(node.name, args[0]))
            elif node.name == "AC_CHECK_LIB":
                out.add(f"ac_cv_lib_{cache_name(args[0])}_{cache_name(args[1])}")
            elif node.name == "AC_SEARCH_LIBS":
                out.add(f"ac_cv_search_{cache_name(args[0])}")
            elif node.name in ("AC_CHECK_SIZEOF", "AC_CHECK_ALIGNOF"):
                out.add(f"ac_cv_{'sizeof' if node.name == 'AC_CHECK_SIZEOF' else 'alignof'}_{cache_name(args[0])}")
        out.discard("")
        return out

    @staticmethod
    def _collect_option_values(tree: Tree) -> dict[str, set[str]]:
        """The literals the script compares `$enable_x` / `$with_x` with, other
        than yes and no: the values of a string-valued option."""
        out: dict[str, set[str]] = {}
        for text in _iter_texts(tree.items):
            for option, value in _OPTION_COMPARE_RE.findall(text.text):
                if value not in df.YES and value not in df.NO:
                    out.setdefault(option, set()).add(value)
        return out

    @staticmethod
    def _collect_standard_flags(tree: Tree) -> list[tuple[str, str]]:
        """`CFLAGS="$CFLAGS -std=gnu99"` in the script: a language standard every
        later check (and the project's compiles) runs under."""
        out: list[tuple[str, str]] = []
        for text in _iter_texts(tree.items):
            for lang, flag in _STANDARD_FLAG_RE.findall(text.text):
                if (lang, flag) not in out:
                    out.append((lang, flag))
        return out

    def _unset_empty(self, node: Node) -> Callable[[str], bool]:
        """Whether a variable nothing bound before `node`'s body ran is empty where the body does
        not assign it: a plain shell variable of the project's own configure script that only this
        body assigns, under one condition, and tests for emptiness afterwards (`excuse="explicitly
        disabled"` under `--disable-tool`, then `if test x"$excuse" = x` around the tool's checks,
        which run unless it was disabled).  Not one the environment or configure's own prelude
        (`ac_*`) may have set, nor an option's (`enable_*` / `with_*`: configure takes any
        `--enable-x` from the command line, and a macro the expansion does not spell out, such
        as libtool's LT_INIT, may declare it), nor one another body's shell assigns
        (AC_CANONICAL_TARGET's `target_cpu`, which the script then only renames in a `case`) or
        another module's macros provide; and not in a single m4 file's expansion, which lacks the
        script around it."""
        assigners = self._bodies_assigning()

        def unset_empty(name: str) -> bool:
            return (
                self.project is not None
                and name not in ENVIRONMENT_VARIABLES
                and not name.startswith(("ac_", "as_", "am_", "enable_", "with_"))
                and assigners.get(name, set()) <= {node.id}
                and not (self.index is not None and name in self.index.variables)
            )

        return unset_empty

    def _bodies_assigning(self) -> dict[str, set[int]]:
        """Variable -> the ids of the bodies whose own shell assigns it, over the whole expansion.
        A macro's text counts as its caller's where `_placeholder_text` inlines it (cache commands,
        see-through checks, pkg-config's actions, the one the verdict rules out included: it is
        dead, not hidden), and so do the action arguments it renders around a placeholder (a
        check's plain actions, an option's given / not-given actions, AM_COND_IF's branches),
        wherever the expansion places them.  Anything else a placeholder's expansion assigns is
        the macro's own, which the body never sees assigned: AC_CANONICAL_TARGET's `target_cpu`,
        the variable a probe's actions set (the probe's result, in the rules), the cache variable
        of a cache check a decision answers.  The top level is the root body, id 0
        (`translate_root`)."""
        if self._assigners is None:
            out: dict[str, set[int]] = {}

            def visit(items: list[Node | Text], owner: int, rendered: Mapping[str, int]) -> None:
                for item in items:
                    if isinstance(item, Text):
                        for name in _shell_assigned(item.text):
                            out.setdefault(name, set()).add(rendered.get(name, owner))
                    elif self._inlined_shell(item):
                        visit(item.items, owner, rendered)
                    else:
                        # The outermost caller rendering an assignment owns it; the inner calls
                        # only pass it on.
                        own = {name: owner for name in _shell_assigned(self._rendered_args(item))}
                        visit(item.items, item.id, {**own, **rendered})

            visit(self.tree.items, 0, {})
            self._assigners = out
        return self._assigners

    def _inlined_shell(self, node: Node) -> bool:
        """Whether `_placeholder_text` makes `node`'s shell its caller's own."""
        if node.kind == "defun" and node.name in PKG_MACROS | {PKG_FOUND, PKG_MISSING}:
            return True
        return node.kind == "call" and (
            (node.name in TRANSPARENT and not self._decided_probe(node)) or self._sees_through(node)
        )

    def _rendered_args(self, node: Node) -> str:
        """The action arguments `_placeholder_text` renders as shell around `node`'s placeholder."""
        if node.kind != "call":
            return ""
        if self.project is not None and node.name in ("AC_ARG_ENABLE", "AC_ARG_WITH"):
            return " ".join(node.args[2:4])
        if self.project is not None and node.name == "AM_COND_IF":
            return " ".join(node.args[1:3])
        if _action_probe_cache(node) is not None:
            index = _ACTION_PROBES[node.name]
            return " ".join(node.args[index : index + 2])
        return ""

    def _never_set(self, name: str) -> bool:
        """A cache variable or ALLCAPS variable (`$ISC`) nothing in the
        configure script sets (project mode: the expansion is the whole
        script; a single m4 file's expansion lacks the other modules that set
        its inputs). Variables of the environment stay unknown."""
        if self.project is None or name in self.cache_vars_set or name in ENVIRONMENT_VARIABLES:
            return False
        if name.startswith("ac_cv_std_"):
            return False  # a standard check's result (`_STANDARD_MACRO_VARIABLES`), set by the rules
        return "_cv_" in name or bool(_ALLCAPS_RE.match(name))

    # -- decisions ------------------------------------------------------------

    def _request(
        self,
        key: str,
        kind: str,
        ctx: _Ctx,
        summary: str,
        context: Mapping[str, dec.ContextValue] | None = None,
        *,
        node: Node | None = None,
        macro: tuple[str, str | None] | None = None,
        shell: list[str] | None = None,
    ) -> dec.Decision | None:
        """The decision for `key`, or None after recording the request.

        `node` or `macro` (name, first argument) names the m4 call the decision
        replaces; `shell` lists shell statements of the m4 to point at.  Their
        source text leads the request's context so the decisions file shows
        what is being decided.
        """
        if kind == "variable" and key.split(":", 1)[1] in RULES_PROVIDED_SUBSTS:
            # `autoconf_package_info` produces these from AC_INIT; a decision would be a second
            # producer, which the rules reject.
            self.report.note(
                f"{key.split(':', 1)[1]}: provided by autoconf_package_info from AC_INIT; the "
                "configure.ac assignment is left out"
            )
            return dec.Decision(kind, key, word="IGNORE")
        full: dict[str, dec.ContextValue] = {}
        if node is not None:
            macro = (node.name, node.args[0] if node.args else None)
        excerpt = self._excerpt(ctx, *macro) if macro else None
        if excerpt:
            full[excerpt[0]] = excerpt[1]
        if shell:
            lines = self._shell_lines(ctx, shell)
            if lines:
                full["m4 (shell)"] = lines
        for name, value in (context or {}).items():
            if name == "commands" and excerpt:
                continue  # the m4 excerpt already shows the macro's commands argument
            full[name] = value
        return self.decisions.resolve(dec.Request(key, kind, ctx.where, summary, full))

    def _source_span(self, ctx: _Ctx) -> tuple[M4Source, Span, CallLocator | None] | None:
        source = self.sources.get(ctx.file or "")
        if source is None:
            return None
        if ctx.locator is not None:
            return source, ctx.locator.span, ctx.locator
        return source, Span(1, len(source.lines)), None

    def _other_sources(self, ctx: _Ctx) -> list[tuple[str, M4Source]]:
        """The m4 files other than the context's: where flattened text (gl_INIT) comes from."""
        return [(file, source) for file, source in self.sources.items() if file != ctx.file]

    def _excerpt(self, ctx: _Ctx, macro: str, first_arg: str | None) -> tuple[str, list[str]] | None:
        """(`file:lines`, text) of the `macro(...)` call in the m4 source, if found."""
        found = self._source_span(ctx)
        candidates: list[tuple[str, M4Source, CallLocator]] = []
        if found is not None:
            source, span, locator = found
            # The DEFUN's locator keeps its cursor (the same order the comments
            # use); configure.ac itself is searched from the top each time.
            candidates.append((ctx.file or "", source, locator or CallLocator(source, span)))
        if first_arg:
            candidates += [
                (file, source, CallLocator(source, Span(1, len(source.lines))))
                for file, source in self._other_sources(ctx)
            ]
        for file, source, locator in candidates:
            line = locator.find(macro, first_arg)
            if line is None:
                continue
            call = source.defun_span(line)
            label = f"{file}:{call.start}" + (f"-{call.end}" if call.end != call.start else "")
            return label, [l.rstrip() for l in source.lines[call.start - 1 : call.end]]
        return None

    def _shell_lines(self, ctx: _Ctx, statements: list[str]) -> list[str]:
        """`file:~N: text` for shell statements of the m4 (first line of each)."""
        found = self._source_span(ctx)
        places: list[tuple[str, M4Source, Span]] = []
        if found is not None:
            places.append((ctx.file or "", found[0], found[1]))
        places += [(file, source, Span(1, len(source.lines))) for file, source in self._other_sources(ctx)]
        out: list[str] = []
        for statement in statements:
            first = " ".join(statement.strip().split("\n")[0].split())
            if len(first) < 4:
                continue
            # The analyzed shell may differ from the m4 (backticks rewritten,
            # macros expanded): fall back to the `NAME=` prefix of an assignment.
            needles = [first] + ([first.split("=", 1)[0] + "="] if "=" in first else [])
            entry = None
            for needle in needles:
                for file, source, span in places:
                    hit = next(
                        (n for n in range(span.start, span.end + 1) if needle in " ".join(source.lines[n - 1].split())),
                        None,
                    )
                    if hit is not None:
                        entry = f"{file}:~{hit}: {source.lines[hit - 1].strip()}"
                        break
                if entry:
                    break
            if entry and entry not in out:
                out.append(entry)
        return out

    # -- selection ----------------------------------------------------------

    def _gnulib_file(self, file: str | None) -> bool:
        """Whether gnulib-tool wrote `file` (gl_FILE_LIST): its macros are the ports', not the project's."""
        return self.project is not None and file is not None and basename(file) in self.project.gnulib_files

    def _is_flattened(self, node: Node) -> bool:
        return (
            self.project is not None
            and node.loc_file is not None
            and basename(node.loc_file) in self.project.flatten_files
        )

    def _is_selected(self, node: Node) -> bool:
        if not node.loc_file or node.is_internal or self._is_flattened(node):
            return False
        if basename(node.loc_file) in LIBTOOL_FILES:
            return False
        return node.loc_file in self.selected or basename(node.loc_file) in self.selected_names

    def _package_for(self, file: str) -> Package:
        key = "" if self.options.layout == "package" else self.options.package_prefix + stem(file)
        package = self.packages.get(key)
        if package is None:
            package = Package(path=key, docstring=self._docstring(file))
            self.packages[key] = package
        return package

    def _docstring(self, file: str) -> str:
        if self.index is not None:
            port = self.index.file_port(file)
            if port is not None and port.url:
                return port.url
        if self.options.gnulib_commit and file.startswith("m4/"):
            return f"https://github.com/coreutils/gnulib/blob/{self.options.gnulib_commit}/{file}"
        return f"Generated from {file} by rules_cc_autoconf convert."

    # -- entry points ---------------------------------------------------------

    def translate(self) -> list[Package]:
        """Translate the selected DEFUN bodies into packages (port mode, one target per macro). In
        project mode nothing is translated here: configure expands each macro where it is called,
        so `translate_root` inlines them in call order instead. Each distinct (name, args) is
        translated once; the per-file layout gets an aggregator target per package.
        """
        for node in self.tree.defuns():
            if not self._is_selected(node):
                continue
            if self.project is not None:
                continue  # expanded where it is called: configure defines things in sequence
            key = (node.name, tuple(node.args))
            if key in self._translated:
                continue
            package = self._package_for(node.loc_file or "")
            target = self._translate_defun(node, package)
            self._translated[key] = target
            package.targets.append(target)
        for package in self.packages.values():
            self._add_aggregator(package)
        return [self.packages[k] for k in sorted(self.packages)]

    def translate_root(self, name: str, *, loc_file: str) -> Target:
        """The configure.ac top level as one target (project mode).

        Only the region between `AC_INIT` and `AC_OUTPUT` is the project's
        own: what precedes AC_INIT's marker is the script preamble and
        AC_INIT's expansion (shell functions, PACKAGE_* substitutions).
        """
        items = list(self.tree.items)
        start = next(
            (i for i, it in enumerate(items) if isinstance(it, Node) and it.kind == "call" and it.name == "AC_INIT"),
            None,
        )
        end = next(
            (i for i, it in enumerate(items) if isinstance(it, Node) and it.kind == "call" and it.name == "AC_OUTPUT"),
            None,
        )
        prelude: list[str] = []
        if start is not None:
            # `m4_divert_text([INIT_PREPARE], [gl_printf_safe=yes])` lands ahead
            # of the body: keep such plain assignments as the body's prelude.
            for item in items[:start]:
                if isinstance(item, Text):
                    prelude += _PRELUDE_ASSIGN_RE.findall(item.text)
            items = items[start:end] if end is not None and end > start else items[start:]
        if prelude:
            items = [Text("\n".join(prelude) + "\n", 0), *items]
        root = Node(kind="defun", id=0, name=name, loc=f"{loc_file}:1", items=items)
        package = self._package_for(loc_file)
        target = self._translate_defun(root, package, root_level=True)
        target.loc = None
        return target

    def _add_aggregator(self, package: Package) -> None:
        """Per-file layout: a target named after the package that depends on every target of it but
        helpers and `_DEFAULTS`, the label consumers depend on.
        """
        if self.options.layout != "per-file" or not package.targets:
            return
        name = package.path.rsplit("/", 1)[-1]
        if package.target(name):
            return
        aggregator = Target(name=name)
        for target in package.targets:
            if target.name.endswith("_DEFAULTS") or target.helper:
                continue
            aggregator.add_dep(f":{target.name}")
        # The header's defaults: a public `:defaults` over the `_REQUIRE_DEFAULTS`
        # (which composes the `_DEFAULTS`), read by the aggregator and by every
        # consumer whose m4 AC_REQUIREs them.
        defaults_targets = [t for t in package.targets if t.kind == "autoconf_defaults"]
        if defaults_targets and not package.target("defaults"):
            defaults = Target(name="defaults", kind="autoconf_defaults")
            roots = [t for t in defaults_targets if t.name.endswith("_REQUIRE_DEFAULTS")] or defaults_targets
            for target in roots:
                defaults.add_default_dep(f":{target.name}")
            package.targets.append(defaults)
            aggregator.add_default_dep(":defaults", "the header's own defaults, as its m4 requires")
        if aggregator.deps or aggregator.default_deps:
            package.targets.append(aggregator)

    # -- one DEFUN ----------------------------------------------------------

    def _target_name(self, node: Node) -> str:
        if not node.args:
            return node.name
        return f"{node.name}_{slug(node.args[0])}"

    def _translate_defun(self, node: Node, package: Package, *, root_level: bool = False) -> Target:
        """One AC_DEFUN body (or the configure.ac top level when `root_level`) as an `autoconf`
        target: analyze its shell, walk its macro calls and placeholders, emit the variables the
        shell assigns, validate the conditions, and turn calls to macros nothing defines into
        `macro` decisions.
        """
        target = Target(name=self._target_name(node), loc=Loc(node.loc_file or "?", node.loc_line))
        if node.name.endswith("_DEFAULTS"):
            # gnulib's `*_H_DEFAULTS` / `*_H_REQUIRE_DEFAULTS`: literal values a
            # probe may override, an autoconf_defaults target (see _finish_defaults).
            target.visibility = "//visibility:private"
            target.kind = "autoconf_defaults"
        if node.args:
            target.comments.append(f"{node.name}({', '.join('[' + ' '.join(a.split()) + ']' for a in node.args)})")

        analysis = self._analyze(node)
        ctx = _Ctx(
            target=target,
            package=package,
            lang=["c"],
            analysis=analysis,
            file=node.loc_file,
            auto_variables=not root_level,
            root=root_level,
        )
        source = self.sources.get(node.loc_file or "")
        if source is not None and node.loc_line and not root_level:
            ctx.locator = CallLocator(source, source.defun_span(node.loc_line))

        self._walk(node.items, ctx)
        self._variables(ctx)
        self._validate_conditions(ctx)
        for name, where in list(self.undefined_macros.items()):
            if where != node.name:
                continue
            self._macro_decision(
                name,
                ctx,
                f"{name} is not defined by any m4 file the expansion saw (a missing macro directory, an "
                "autoconf-archive macro, or a tool's .m4 not shipped with the project); its calls were left out",
                {},
            )
        for unknown in analysis.unknowns:
            self.report.note(f"{ctx.where}: shell not followed: {unknown}")
        self._script_aborts(analysis, target)
        if target.kind == "autoconf_defaults":
            self._finish_defaults(target)
        return target

    def _finish_defaults(self, target: Target) -> None:
        """A `_DEFAULTS` macro assigns literals: an `autoconf_defaults` target, whose `deps` are
        the other defaults it AC_REQUIREs.  A probe, a conditional value or an ordinary
        dependency in it makes it a plain `autoconf` target instead (its values are then
        results, not defaults), with a note."""
        literal = all(
            isinstance(c, Call)
            and c.fn in ("checks.AC_SUBST", "checks.M4_VARIABLE", "checks.AC_DEFINE")
            and len(c.args) == 2
            and not c.kwargs
            and isinstance(c.args[1], (str, int))
            for items in [target.checks] + target.arm_lists
            for c in items
        )
        if literal and not target.deps and not target.deps_suffix:
            return
        self.report.note(
            f"{target.name}: not only literal defaults (a probe, a conditional value or an "
            "ordinary dependency): an autoconf target, its values results rather than defaults"
        )
        target.kind = "autoconf"

    def _analyze(self, node: Node, outer: df.Analysis | None = None) -> df.Analysis:
        """The dataflow of `node`'s shell; `outer` is the caller's analysis,
        whose settled literals (`M4tests_WITNESS=IN_M4_GNULIB_TESTS` at the
        top level) the body sees as configure would at the call."""
        text, undefined, unattributed = m4args.neutralize_unexpanded(self._placeholder_text(node))
        for name in unattributed:
            self.report.note(
                f"{node.name}: a call of {name} the expansion did not attribute (an unbracketed call "
                "in a case arm, or text m4 expanded that the shell never ran); left out"
            )
        if self.tarname and "_GNULIB_TESTS" in text:
            witness = "IN_" + re.sub(r"[^A-Z0-9_]", "_", self.tarname.upper()) + "_GNULIB_TESTS"
            text = _TESTS_WITNESS_RE.sub(witness, text)
        for name in undefined:
            self.undefined_macros.setdefault(name, node.name)
        seq = sh.parse(text)
        bindings: dict[str, df.Value] = {}
        if outer is not None and outer.evaluator is not None:
            for var, value in outer.evaluator.state.items():
                if isinstance(value, (df.Lit, df.Probe, df.Ref)) and not (
                    isinstance(value, df.Ref) and value.name == var
                ):
                    bindings[var] = value
            # A literal the caller assigned conditionally, on a path the call itself sits on
            # (`X=""` a few lines before a macro, in the same branch, whose expansion appends to
            # `$X`): the body sees it as configure would at the call.
            guard = outer.guards.get(node.id)
            if guard is not None:
                for var, (cond, value) in outer.evaluator.branch_state.items():
                    if (
                        var not in bindings
                        and outer.evaluator.state.get(var) is None
                        and isinstance(value, df.Lit)
                        and df.tautology(df.disj(df.neg(guard), cond))
                    ):
                        bindings[var] = value
        bindings.update(self._probe_bindings(node))
        toolchain = self._toolchain_decided_vars(node)
        self.toolchain_vars |= toolchain
        bindings.update({var: df.Lit("no") for var in toolchain})
        analysis = df.Evaluator(
            bindings,
            by_value=self._literal_decided(),
            fatal=self._fatal_ids(node),
            value_words=self.index.variables if self.index is not None else None,
            pinned=toolchain,
            unset_empty=self._unset_empty(node),
        ).run(seq)
        # The program lookups' stand-in values win over the lookups' own
        # expansion (`ac_cv_path_M4=$ac_path_M4` inside the loop).
        analysis.program_literals = {
            var: value.text
            for var, value in bindings.items()
            if isinstance(value, df.Lit) and var in self._program_vars(node)
        }
        analysis.placeholders = self._placeholder_ids(node)
        if seq.errors:
            analysis.unknowns[:0] = [f"shell syntax not understood at {e}" for e in seq.errors]
        debug_dir = os.environ.get("RCCAC_DEBUG_DIR")
        if debug_dir:
            Path(debug_dir).mkdir(parents=True, exist_ok=True)
            (Path(debug_dir) / f"{slug(node.name)}.placeholder.sh").write_text(text, encoding="utf-8", newline="\n")
        return analysis

    def _placeholder_text(self, node: Node) -> str:
        """The node's shell with every child macro call replaced by a placeholder.

        Configure options are modeled as `if <flag given>; then <action-if-given>
        else <action-if-not-given> fi` around their placeholder.
        """
        parts = []
        for item in node.items:
            if isinstance(item, Text):
                parts.append(item.text)
                continue
            if item.kind == "call" and item.name in TRANSPARENT and not self._decided_probe(item):
                # The cache commands are the body's own shell: a cache variable
                # assigned on several paths is then known to the tests that
                # follow (`test $bash_cv_signal_vintage = posix`), the way
                # configure knows it.  A decided probe keeps its decision.
                parts.append(self._placeholder_text(item))
                continue
            taken = self._pkg_taken(item)
            if taken is not None:
                # The node's own placeholder first: its guard is the condition the macro ran
                # under (grep's PKG_CHECK_MODULES behind --enable-perl-regexp), which the
                # dependency on the library follows.
                parts.append(f"\n{sh.placeholder(item.id)}\n")
                # A pkg-config macro: the action its verdict picks is the body's shell (what it
                # assigns, `FUSE_CFLAGS=...` or `grub_mount_excuse=...`, is known to the tests
                # that follow); the other action is not there at all.
                if taken:
                    parts.append(self._placeholder_text(item))
                continue
            if item.kind == "call" and self._sees_through(item):
                # AC_CHECK_FUNC(getifaddrs, [actions]) and the other singular checks with
                # actions: configure's own expansion (`if test "x$ac_cv_func_getifaddrs" = xyes;
                # then <actions> else ...`) is the body's shell, nested checks included, so the
                # actions' assignments and the nested calls sit under the check's result.
                parts.append(f"\n{sh.placeholder(item.id)}\n")
                parts.append(self._placeholder_text(item))
                continue
            parts.append(f"\n{sh.placeholder(item.id)}\n")
            if self.project is not None and item.kind == "call" and item.name in ("AC_ARG_ENABLE", "AC_ARG_WITH"):
                parts.append(self._flag_shell(item, before="".join(parts)))
            if self.project is not None and not isinstance(item, Text) and item.name == "AM_COND_IF":
                # The AM_CONDITIONALs are recorded when translated, after this analysis: read
                # the ones of this body ahead of it.
                # (`gl_CONDITIONAL` is gnulib-comp.m4's spelling of AM_CONDITIONAL.)
                for sibling in node.items:
                    if (
                        not isinstance(sibling, Text)
                        and sibling.name in ("AM_CONDITIONAL", "gl_CONDITIONAL")
                        and sibling.args
                    ):
                        self.project.am_conditional_tests.setdefault(
                            sibling.args[0].strip(), sibling.args[1] if len(sibling.args) > 1 else ""
                        )
                parts.append(self._cond_if_shell(item))
            if item.kind == "call":
                parts.append(_action_shell(item, self._sees_through(item)))
        return "".join(parts)

    def _fatal_ids(self, node: Node) -> set[int]:
        """The placeholders of AC_MSG_ERROR / AC_MSG_FAILURE: configure stops there, so a variable
        assigned on every other path is assigned on every path that continues."""
        out: set[int] = set()
        for item in node.items:
            if isinstance(item, Text):
                continue
            taken = self._pkg_taken(item)
            if taken is False:
                continue
            if (
                taken
                or item.kind == "call"
                and (item.name in TRANSPARENT and not self._decided_probe(item) or self._sees_through(item))
            ):
                out |= self._fatal_ids(item)
            elif item.kind == "call" and item.name in ("AC_MSG_ERROR", "AC_MSG_FAILURE"):
                out.add(item.id)
        return out

    def _placeholder_ids(self, node: Node) -> set[int]:
        """The ids `_placeholder_text` put into the text (through cache bodies)."""
        out: set[int] = set()
        for item in node.items:
            if isinstance(item, Text):
                continue
            taken = self._pkg_taken(item)
            if taken is not None:
                out.add(item.id)  # the macro's own placeholder (`_shell_text`)
            if taken is False:
                continue
            if taken or item.kind == "call" and item.name in TRANSPARENT and not self._decided_probe(item):
                out |= self._placeholder_ids(item)
            elif item.kind == "call" and self._sees_through(item):
                out.add(item.id)
                out |= self._placeholder_ids(item)
            else:
                out.add(item.id)
                if (
                    self.project is not None
                    and item.kind == "call"
                    and item.name in ("AC_ARG_ENABLE", "AC_ARG_WITH", "AM_COND_IF")
                ):
                    # `_flag_shell` / `_cond_if_shell`: every call and macro body of the actions.
                    out |= {child.id for child in item.children if not isinstance(child, Text)}
        return out

    def _pkg_taken(self, node: Node) -> bool | None:
        """None for anything but pkg-config's nodes; for PKG_CHECK_MODULES itself True (its body
        is the two action nodes); for RCCAC_PKG_FOUND / RCCAC_PKG_MISSING whether the verdict on
        the package list picks it.  A found verdict registers the registry modules."""
        if node.kind != "defun":
            return None
        if node.name in PKG_MACROS:
            return True
        if node.name not in (PKG_FOUND, PKG_MISSING):
            return None
        args = node.args + ["", ""]
        found, libs, _ = self._pkg_verdict(args[1])
        if node.name == PKG_FOUND and found:
            variable = args[0].strip()
            if self.project is not None:
                for library in libs:
                    self.project.external_modules[library.module] = library.version
                    self.project.pkg_cc_deps.add(library.label)
                    if variable and library.label not in self.project.pkg_libs.setdefault(f"{variable}_LIBS", []):
                        self.project.pkg_libs[f"{variable}_LIBS"].append(library.label)
                        self.report.notes.append(
                            f"pkg-config: $({variable}_LIBS) on an LDADD line is a dependency on {library.label}"
                        )
        return found if node.name == PKG_FOUND else not found

    def _pkg_verdict(self, specs: str) -> tuple[bool, list[libraries.Library], str]:
        """Whether the registry provides every package of a PKG_CHECK_MODULES list at a version
        satisfying its constraint, decided from tools/convert/libraries.toml: (verdict, the
        libraries, the reason), reported once per list."""
        specs = m4args.quadrigraphs(m4args.strip_m4(specs)).strip()
        if specs in self._pkg_verdicts:
            return self._pkg_verdicts[specs]
        libs: list[libraries.Library] = []
        reason = ""
        found = True
        unchecked: list[str] = []
        scope = self.project.package_path if self.project is not None else "."
        parsed = libraries.parse_specs(specs)
        if not parsed or any("$" in name for name, _o, _v in parsed) or ("$" in specs and not parsed):
            # `guile-$v`, `$X11_REQUIRES`: the package is named by a shell variable (make's loop
            # over guile versions); no table answers that.
            found, reason = False, f"`{specs}` names a package through a shell variable"
        for name, operator, version in parsed if found else []:
            found_libraries = libraries.known_pkg(name)
            if found_libraries is None:
                found, reason = False, f"no registry module provides {name}"
                break
            if version is not None and "$" in version:
                unchecked.append(f"{name} {operator} {version}")  # a constraint spelled by a shell variable
                operator = version = None
            library = found_libraries[0]
            # `library:<package>:<name>` IGNORE, by the pkg-config name or the module: the project is
            # built as on a host without the package (the not-found action runs).
            excluded = next(
                (
                    key
                    for key in (name, library.module)
                    if (decided := self.decisions.optional(f"library:{scope}:{key}", "library")) is not None
                    and decided.ignore
                ),
                None,
            )
            if excluded:
                found, reason = False, f"library:{scope}:{excluded} is IGNORE"
                break
            registry_version = libraries.upstream_version(library.version)
            if not libraries.version_satisfies(registry_version, operator, version):
                found, reason = (
                    False,
                    (
                        f"the registry's {library.module} {registry_version} "
                        f"does not satisfy {name} {operator} {version}"
                    ),
                )
                break
            libs += [lib for lib in found_libraries if lib not in libs]
        if found and specs:
            provided = ", ".join(f"{lib.module} {libraries.upstream_version(lib.version)}" for lib in libs)
            keys = ", ".join(f"`library:{scope}:{lib.module}`" for lib in libs)
            self.report.note(
                f"pkg-config [{specs}]: the registry's {provided} provides it; taken as found "
                "(an override to another version is not checked"
                + (f"; the constraint {', '.join(unchecked)} is not checked either" if unchecked else "")
                + f"); {keys} IGNORE leaves it out"
            )
        elif specs:
            self.report.note(f"pkg-config [{specs}]: {reason}; taken as not found (the not-found action runs)")
        self._pkg_verdicts[specs] = (found, libs, reason)
        return self._pkg_verdicts[specs]

    def _decided_probe(self, node: Node) -> bool:
        """The decisions file has a value for this cache check's variable."""
        var = cache_node_var(node)
        value = self.decisions.lookup(f"probe:{var}") if var else None
        return value is not None and not (isinstance(value, dec.Word) and value.name == "UNDECIDED")

    def _cond_if_shell(self, node: Node) -> str:
        """The shell that stands in for `AM_COND_IF([COND], [if-true], [if-false])` in the
        analysis: `if <COND's AM_CONDITIONAL test>; then <if-true> else <if-false> fi`, the
        branches' calls kept as placeholders, so they are translated under the conditional's
        condition (ppp's Linux-only header checks)."""
        args = node.args + ["", "", ""]
        name = args[0].strip()
        test = self.project.am_conditional_tests.get(name) if self.project is not None else None
        if test is None:
            self.report.note(
                f"AM_COND_IF([{name}]): no AM_CONDITIONAL of that name before it; its branches are "
                "translated unconditionally"
            )
            test = "true"
        placeholders = [
            (child.name, sh.placeholder(child.id)) for child in node.children if not isinstance(child, Text)
        ]
        shell_test = m4args.neutralize_macros(m4args.quadrigraphs(m4args.strip_m4(test))).strip() or "true"
        if_true = _action_text(args[1], placeholders)
        if_false = _action_text(args[2], placeholders)
        return f"\nif {shell_test}; then\n{if_true}\n:\nelse\n{if_false}\n:\nfi\n"

    def _flag_shell(self, node: Node, before: str = "") -> str:
        """The shell that stands in for an AC_ARG_ENABLE / AC_ARG_WITH in the analysis: `if <flag
        command>` running the given-branch actions with the option's value as the symbolic
        `$__rccac_optval_<flag>`, else the not-given actions, so later tests of `$enable_x`
        resolve to option atoms. Registers the option as a flag, records the literal values its
        actions compare it with and the default the not-given branch assigns, and honours a
        `flag:` decision (IGNORE drops the option).

        The flag's default is what configure does when the option is not given: the value the
        not-given action assigns (`enable_usb=yes`), else the value the given action's variable
        held before the call (`opt_multibyte=yes` ahead of `opt_multibyte=$enableval`, in
        `before`, the body's shell so far), else what the help string's `--enable-` /
        `--disable-` spelling says.
        """
        args = node.args + ["", "", "", ""]
        option = args[0].strip()
        if option in TOOLING_OPTIONS:
            return ""  # automake / libtool machinery, not a build option
        flag_name = flag_bool_name(option)
        var_prefix = "enable" if node.name == "AC_ARG_ENABLE" else "with"
        # The calls the actions expand to (an AC_DEFINE under `test "$enableval" = no`) keep
        # their place in the shell as placeholders, in expansion order: given, then not given.
        placeholders = [
            (child.name, sh.placeholder(child.id)) for child in node.children if not isinstance(child, Text)
        ]
        given = _action_text(args[2], placeholders)
        not_given = _action_text(args[3], placeholders)
        default_value, default_from = self._option_default(flag_name, var_prefix, given, not_given, before)
        state = self._state
        info = state.flags.setdefault(
            flag_name,
            FlagInfo(
                node.name,
                option,
                help_text(args[1]),
                default=default_value if default_value is not None else default_from_help(args[1]),
            ),
        )
        if default_from:
            self.report.note(
                f"--{'enable' if var_prefix == 'enable' else 'with'}-{option}: "
                f"{'on' if info.default else 'off'} by default, from {default_from}"
            )
        decision = self.decisions.optional(f"flag:{flag_name}", "flag")
        if decision is not None:
            info.default = bool(decision.default)
            if decision.ignore:
                del state.flags[flag_name]
                return ""
        # `$enableval` / `$withval` inside the option's own actions compare its value.
        for value in re.findall(r'"?x?\$\{?(?:enable|with)val\}?"?\s*(?:=|==|!=)\s*"?x?([A-Za-z0-9_.+/-]+)"?', given):
            if value not in df.YES and value not in df.NO:
                state.flag_values.setdefault(flag_name, set()).add(value)
        # The literal the not-given action assigns: to the option variable, or to the variable
        # the given action copies the value into (libssh2's `use_crypto=$withval` / `use_crypto=auto`).
        copies = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=\"?\$\{?(?:enable|with)val\}?\"?\s*$", given)
        default = next(
            (
                m
                for v in [f"{var_prefix}_{flag_name}", *copies]
                for m in [re.search(rf"(?m)^\s*{re.escape(v)}=\"?([A-Za-z0-9_.+/-]+)\"?\s*$", not_given)]
                if m
            ),
            None,
        )
        if default:
            state.flag_defaults[flag_name] = default.group(1)
        optval = f"${df.OPTVAL_PREFIX}{flag_name}"
        # Not given: autoconf leaves `with_x` unset (libpcap tests `! -z "$with_pcap"`); an
        # action-if-not-given that assigns it sets what the script asked for.
        unset = f"  {var_prefix}_{flag_name}=\n" if not default else ""
        return (
            f"\nif {df.flag_command(flag_name)}; then\n"
            f"  {var_prefix}val={optval}\n  {var_prefix}_{flag_name}={optval}\n{given}\nelse\n"
            f"{unset}{not_given}\nfi\n"
        )

    @staticmethod
    def _option_default(
        flag_name: str, var_prefix: str, given: str, not_given: str, before: str
    ) -> tuple[bool | None, str]:
        """(the option's default as configure sets it, where it was read from): the not-given
        action's assignment of the option variable (`enable_x=yes`) or of the variable the given
        action copies the value into (`use_x=$enableval` then `use_x=auto`), else that variable's
        last assignment before the call; (None, "") when neither says."""
        variables = [f"{var_prefix}_{flag_name}"]
        variables += [
            v
            for v in re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=\"?\$\{?(?:enable|with)val\}?\"?\s*$", given)
            if v not in variables
        ]
        for variable in variables:
            match = re.search(rf"(?m)^\s*{re.escape(variable)}=\"?([A-Za-z0-9_.+/-]+)\"?\s*$", not_given)
            if match and (match.group(1) in df.YES or match.group(1) in df.NO):
                return match.group(1) in df.YES, f"`{variable}={match.group(1)}` in the action-if-not-given"
        for variable in variables:
            presets = re.findall(rf"(?m)^\s*{re.escape(variable)}=\"?([A-Za-z0-9_.+/-]+)\"?\s*$", before)
            if presets and (presets[-1] in df.YES or presets[-1] in df.NO):
                return presets[-1] in df.YES, f"`{variable}={presets[-1]}` before the option"
        return None, ""

    @staticmethod
    def _program_vars(node: Node) -> set[str]:
        """The variables the body's own program lookups set."""
        return {
            _program_lookup_value(child.name, child.args)[0]
            for child in node.walk()
            if child.kind == "call" and child.name in _PROGRAM_LOOKUPS and child.args and _directly_in(child, node)
        }

    def _literal_decided(self) -> set[str]:
        """Cache variables the decisions file answers with a literal (or a select of literals):
        plain variables compared by value, not probes compared by success.  (The M4 variables
        the ports provide, `gl_cv_have_unlimited_file_name_length` set to "no" or "yes", are the
        evaluator's `value_words`: by value too, unless the body assigns them itself.)"""
        out: set[str] = set()
        for key, value in self.decisions.items():
            if not key.startswith("probe:"):
                continue
            if (
                isinstance(value, (str, int))
                and not isinstance(value, bool)
                or isinstance(value, dec.Select)
                and all(isinstance(v, (str, int)) for v in value.arms.values())
            ):
                out.add(key[len("probe:") :])
        return out

    def _toolchain_decided_vars(self, node: Node) -> set[str]:
        """The variables compiler-option tests in `node`'s shell set: the cache variable of an
        AC_CACHE_CHECK / AC_CACHE_VAL whose probes all compile an empty program (nasm's PA_ADD_CFLAGS,
        autoconf-archive's AX_CHECK_COMPILE_FLAG: the flag under test is in CFLAGS around the probe),
        or the action variable of such a probe outside a cache.  Bazel's toolchain owns the options, so
        the test reads "no" (the flag is not added) whatever the shell around the probe assigns."""
        out: set[str] = set()
        for child in node.walk():
            if child.kind != "call":
                continue
            if child.name in ("AC_CACHE_CHECK", "AC_CACHE_VAL"):
                var = cache_node_var(child)
                probes = [p for p in child.walk() if p is not child and p.kind == "call" and p.name in PROBES]
                if var and "$" not in var and probes and all(_compiler_option_test(p) for p in probes):
                    out.add(var)
            elif (
                child.name in PROBES
                and child.enclosing("AC_CACHE_CHECK", "AC_CACHE_VAL") is None
                and _compiler_option_test(child)
            ):
                padded = child.args + ["", "", ""]
                action = action_var(padded[1]) or action_var(padded[2])
                if action:
                    out.add(action)
        return out

    def _probe_bindings(self, node: Node) -> dict[str, df.Value]:
        """Cache variables set by probes with synthesized check names."""
        bindings: dict[str, df.Value] = {}
        for child in node.walk():
            if child.kind == "call" and child.name in _STANDARD_MACRO_VARIABLES and _directly_in(child, node):
                # `ac_cv_prog_cc_c99` is `no` when the macro could not switch the compiler: the
                # standard check's failure (`test "$ac_cv_prog_cc_c99" = no` -> `!ac_cv_std_c99`).
                macro_var, standard_cache = _STANDARD_MACRO_VARIABLES[child.name]
                bindings[macro_var] = df.Probe(standard_cache)
            if child.kind == "call" and child.name in PROBES and child.name != "AC_RUN_IFELSE":
                cache = child.enclosing("AC_CACHE_CHECK", "AC_CACHE_VAL")
                if cache is None:
                    var = action_var(child.args[1] if len(child.args) > 1 else "")
                    if var:
                        bindings[var] = df.Probe(var)
            elif (
                child.kind == "call"
                and _directly_in(child, node)
                and (_action_probe_cache(child) or self._sees_through(child))
            ):
                # Its actions were rendered as `if test "$cache" = yes` around the placeholder,
                # or its expansion tests the cache variable itself.
                probe_cache = _action_probe_cache(child) or _subject_cache(child)
                if probe_cache:
                    bindings.setdefault(probe_cache, df.Probe(probe_cache))
            elif child.kind == "call" and child.name in _PROGRAM_LOOKUPS and child.args and _directly_in(child, node):
                # Program lookups are not modeled: the shell sees the program's
                # plain name (flex: `AC_DEFINE_UNQUOTED([M4], ["$M4"])` after
                # AC_PATH_PROGS_FEATURE_CHECK([M4], [gm4 gnum4 m4], ...)).  Only
                # the body's own lookups: AC_PROG_CXX's internal
                # AC_CHECK_TOOLS(CXX, $ac_tool_prefix$ac_prog) is not a value.
                var, value = _program_lookup_value(child.name, child.args)
                if var and value and "$" not in value and "`" not in value:
                    bindings[var] = df.Lit(value)
        return bindings

    def _walk(self, items: list[Node | Text], ctx: _Ctx) -> None:
        for item in items:
            if isinstance(item, Text):
                continue  # covered by the dataflow pass
            if item.kind == "call":
                self._call(item, ctx)
            else:
                self._nested_defun(item, ctx)

    def _guard(self, node: Node, ctx: _Ctx) -> df.Cond:
        guard = ctx.analysis.guards.get(node.id)
        if guard is None:
            # A placeholder the shell parse never reached (a parse error
            # swallowed it): its condition is not known, not "always".
            guard = (
                df.Unknown("shell around the call could not be parsed")
                if node.id in ctx.analysis.placeholders
                else df.TRUE
            )
        return df.substitute_unset(df.conj(ctx.guard, guard), self._never_set)

    # -- placing checks under their condition ---------------------------------

    def _arms(self, guard: df.Cond) -> tuple[dict[str, df.Cond] | None, str]:
        """`select()` arms for a guard, or None with a reason when it cannot be
        split: one dimension at a time (an option, the host OS, the host CPU
        or the compiler), since Bazel's select() matches one key."""
        if df.flag_atoms(guard):
            if self.project is None:
                return None, "configure option outside project mode"
            arms = lower.flag_arms(guard, self.project)
            if arms is None:
                return None, "condition mixes configure options with each other or with the platform"
            return arms, ""
        dimensions = [
            name
            for name, atoms in (
                ("host OS", df.host_os_atoms(guard)),
                ("host CPU", df.host_cpu_atoms(guard)),
                ("compiler", df.gnuc_atoms(guard)),
            )
            if atoms
        ]
        if len(dimensions) > 1:
            return None, f"condition mixes {' and '.join(dimensions)} (one select() dimension at a time)"
        if df.host_cpu_atoms(guard):
            return lower.cpu_arms(guard), ""
        if df.gnuc_atoms(guard):
            return lower.compiler_arms(guard), ""
        return lower.os_arms(guard), ""

    def _place(self, items: Sequence[CheckItem], guard: df.Cond, ctx: _Ctx) -> None:
        """Append checks to the target under `guard` (requires and/or select arms)."""
        calls = [i for i in items if isinstance(i, Call)]
        for item in items:
            if isinstance(item, Raw):
                ctx.target.checks.append(item)
        if not calls or guard is df.FALSE:
            return
        if not lower.platform_atoms(guard) and not df.flag_atoms(guard):
            self._place_arm(calls, guard, ctx.target.checks, ctx)
            return
        arms, reason = self._arms(guard)
        if arms is None:
            decision = self._guard_decision(calls, guard, ctx, reason)
            if decision is not None:
                self._apply_guard_decision(calls, decision, ctx)
            else:
                self._drop(calls, ctx)
            return
        if df.flag_atoms(guard) and list(arms) == [lower.DEFAULT_ARM]:
            # The option's invocations agree on the condition: plain checks (none, for a
            # condition no invocation meets, as the arms would have been empty).
            if arms[lower.DEFAULT_ARM] is not df.FALSE:
                self._place_arm(calls, arms[lower.DEFAULT_ARM], ctx.target.checks, ctx)
            return
        unmatched = lower.unmatched_patterns(guard)
        family = family_of_labels(arms)
        if family in PLATFORM_FAMILIES:
            # Arms other conditions already opened (`case $host_os in linux*)`
            # elsewhere) win over the default arm on their platform, so a
            # check that does not mention their OS has to be in them too.
            for label in list(ctx.target.selects.get(family, {})):
                if label not in arms and label != lower.DEFAULT_ARM:
                    arms[label] = lower.residual(family, label, guard)
        for label, residual in arms.items():
            if residual is df.FALSE:
                ctx.target.arm(label, family)  # keep an (empty) default arm
                continue
            arm = ctx.target.arm(label, family)
            copies = [clone(c) for c in calls]
            if label == lower.DEFAULT_ARM and unmatched:
                for call in copies:
                    call.comments.append(f"host_os patterns without a Bazel constraint: {', '.join(unmatched)}")
            self._place_arm(copies, residual, arm, ctx)
        ctx.target.arm(lower.DEFAULT_ARM, family)

    def _place_arm(self, calls: list[Call], cond: df.Cond, dest: list[CheckItem], ctx: _Ctx) -> None:
        """Append `calls` to `dest` under `cond`: the condition becomes `requires` entries when the
        rules' grammar can express it, otherwise a `guard` decision decides (ALWAYS, NEVER, a
        requires list or a select); calls already in `dest` are not repeated.
        """
        reqs = lower.requires_list(cond)
        if reqs is None:
            decision = self._guard_decision(calls, cond, ctx, lower.expression(cond).unknown or "?")
            if decision is None:
                self._drop(calls, ctx)
                return
            self._apply_guard_decision(calls, decision, ctx, dest)
            return
        for call in calls:
            if reqs:
                existing = strings(call.kwargs.get("requires"))
                call.kwargs["requires"] = existing + [r for r in reqs if r not in existing]
            _append_unique(dest, call)

    def _guard_decision(self, calls: list[Call], cond: df.Cond, ctx: _Ctx, reason: str) -> dec.Decision | None:
        key = f"guard:{ctx.target.name}:{check_key(calls[0])}"
        return self._request(
            key,
            "guard",
            ctx,
            f"the condition under which {check_key(calls[0])} runs cannot be expressed ({reason})",
            {"checks": [check_key(c) for c in calls], "condition": describe(cond)},
        )

    def _drop(self, calls: list[Call], ctx: _Ctx) -> None:
        """Checks left out: what they would have produced is known to be missing,
        so conditions over it become decisions instead of dangling requires."""
        for call in calls:
            ctx.dropped |= provided_names(call)

    def _apply_guard_decision(
        self, calls: list[Call], decision: dec.Decision, ctx: _Ctx, dest: list[CheckItem] | None = None
    ) -> None:
        """Apply a `guard` decision to `calls`: NEVER drops them (recording what they would have
        produced), a select() puts copies in each arm (platform arms other conditions opened take
        the default arm's value), a requires list is attached to them, ALWAYS appends them as
        they are.
        """
        if decision.never:
            self._drop(calls, ctx)
            return
        if decision.arms is not None:
            # The default arm is "every other platform": host-OS arms other
            # conditions opened take the default value too (`_decided_arms`).
            family, select = self._decided_arms(decision.arms, ctx)
            for label, value in select.items():
                arm = ctx.target.arm(label, family)
                if value == "never":
                    continue  # the arm exists; the checks stay out of it
                copies = [clone(c) for c in calls]
                if isinstance(value, list):
                    for call in copies:
                        requires = strings(call.kwargs.get("requires")) + value
                        if requires:  # ALWAYS as an arm's value is an empty list: no attribute
                            call.kwargs["requires"] = requires
                produced = set().union(*(produced_keys(other) for other in arm if isinstance(other, Call)))
                for call in copies:
                    if produced_keys(call) & produced:
                        # libpcap checks linux/usbdevice_fs.h twice, with and without
                        # linux/compiler.h, on complementary paths; one decision covers both
                        # calls, and a second producer of the define would be rejected.
                        self.report.note(
                            f"{check_key(call)}: a check producing the same result is already in the "
                            f"{label} arm; this copy is left out"
                        )
                        continue
                    _append_unique(arm, call)
            ctx.target.arm(lower.DEFAULT_ARM, family)
            return
        target_list = dest if dest is not None else ctx.target.checks
        for call in calls:
            if decision.requires:
                call.kwargs["requires"] = strings(call.kwargs.get("requires")) + list(decision.requires)
            _append_unique(target_list, call)

    # -- deps -----------------------------------------------------------------

    def _loc(self, ctx: _Ctx, macro: str, first_arg: str | None) -> Loc | None:
        if ctx.locator is None or ctx.file is None:
            return None
        line = ctx.locator.find(macro, first_arg)
        if line is None:
            return None
        return Loc(ctx.file, line, approx=True)

    def _anonymous_name(self, node: Node, ctx: _Ctx, kind: str) -> str:
        """The result name of a probe with no cache variable: the call's place in the source
        (`autoconf_probe_configure_1234`), the same across converter versions, so a decision
        keyed by it holds.  Calls at one source line (a macro expanded several times, nested
        identical calls) are numbered `_2`, `_3` in expansion order.  Nothing here depends on
        the walk or on the decisions file: the name of a call is a function of the tree."""
        key = (node.name, node.line)
        if key in self._anonymous_names:
            return self._anonymous_names[key]
        loc = self._anonymous_loc(node)
        if loc is None:
            where = f"x{node.line}"
        else:
            same = sorted(
                n.line
                for n in self.tree.walk()
                if n.kind == "call" and n.name == node.name and self._anonymous_loc(n) == loc
            )
            rank = same.index(node.line) + 1
            where = f"{slug(stem(basename(loc[0])))}_{loc[1]}" + (f"_{rank}" if rank > 1 else "")
        name = f"{ctx.target.name.lower()}_{kind}_{where}" if kind == "probe" else f"{kind}_{where}"
        self._anonymous_names[key] = name
        return name

    def _anonymous_loc(self, node: Node) -> tuple[str, int] | None:
        """(file, line) of the `AC_*_IFELSE(` call in the m4 source: the first line naming the
        macro (or its obsolete spelling, `AC_TRY_LINK`) whose next lines carry the program's
        first distinctive text; the enclosing macro's file is searched first."""
        key = (node.name, node.line)
        if key in self._anonymous_locs:
            return self._anonymous_locs[key]
        needle = _probe_needle(node.args[0] if node.args else "")
        names = [node.name] + _IFELSE_SPELLINGS.get(node.name, [])
        pattern = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")
        enclosing = node.parent
        while enclosing is not None and enclosing.kind != "defun":
            enclosing = enclosing.parent
        first = enclosing.loc_file if enclosing is not None else None
        order = sorted(self.sources, key=lambda f: (f != first, f != "configure.ac", f))
        found = None
        for file in order:
            source = self.sources[file]
            for number in range(1, len(source.lines) + 1):
                if not pattern.search(source.lines[number - 1]):
                    continue
                window = "\n".join(source.lines[number - 1 : number + 8])
                if needle and needle in window or not needle and _EMPTY_PROGRAM_RE.search(window):
                    found = (file, number)
                    break
            if found:
                break
        self._anonymous_locs[key] = found
        return found

    def _source_comment(self, node: Node, ctx: _Ctx) -> str:
        head = node.name
        if node.args:
            head += f"({', '.join('[' + a.split(chr(10))[0].strip() + ']' for a in node.args)})"
        if len(head) > 96:
            head = head[:93] + "..."
        loc = self._loc(ctx, node.name, node.args[0] if node.args else None)
        if loc is None and ctx.root:
            return f"{ctx.file}: {head}"
        return f"{loc}: {head}" if loc else head

    def _port_label(self, resolution: Resolution) -> str:
        return f"{self.options.repo}{resolution.label}"

    def _local_label(self, name: str, file: str | None, ctx: _Ctx) -> str:
        if self.options.layout == "package" or (file and stem(file) == stem(ctx.file or "")):
            return f":{name}"
        return f"//{self.options.package_prefix}{stem(file or name)}:{name}"

    def _local_names(self, file: str | None) -> set[str]:
        """Names of DEFUNs defined in selected files (translated locally)."""
        if file is None:
            return set()
        if file in self.selected or basename(file) in self.selected_names:
            return {n for n, f in self.defun_files.items() if f == file and not INTERNAL_DEFUN_RE.match(n)}
        return set()

    def _add_port_dep(self, resolution: Resolution, ctx: _Ctx, comment: str) -> None:
        """A dep on a port; gnulib modules collapse to GNULIB_M4_DEPS in project mode.  A port's
        `:defaults` (reason "defaults") is a `default_deps` entry, GNULIB_M4_DEFAULT_DEPS in
        project mode: read beneath the results, never a producer."""
        if resolution.reason == "defaults":
            if self.project is not None and resolution.package.startswith("gnulib/m4/"):
                self.project.gnulib_defaults[resolution.package] = self._port_label(resolution)
                return
            ctx.target.add_default_dep(self._port_label(resolution), comment)
            return
        if self.project is not None and resolution.package.startswith("gnulib/m4/"):
            port = self._ports.by_package[resolution.package]
            self.project.gnulib_packages[resolution.package] = f"{self.options.repo}{port.label()}"
            return
        ctx.target.add_dep(self._port_label(resolution), comment)

    def _macro_decision(
        self,
        name: str,
        ctx: _Ctx,
        summary: str,
        context: Mapping[str, dec.ContextValue],
        macro: tuple[str, str | None] | None = None,
    ) -> None:
        """Apply the `macro:<name>` decision (dep / checks / ignore) or record the request."""
        decision = self._request(f"macro:{name}", "macro", ctx, summary, context, macro=macro or (name, None))
        if decision is None:
            self.report.unresolved.add(name)
            return
        if decision.ignore:
            return
        if decision.dep is not None:
            ctx.target.add_dep(decision.dep, f"{name} (decisions file)")
        ctx.target.checks.extend(decision.checks or [])

    def _require(self, name: str, ctx: _Ctx, via: str | None = None, when: df.Cond = df.TRUE) -> None:
        """AC_REQUIRE([name]) as a dependency: libtool machinery is dropped with a note; a macro of
        the selected m4 files becomes a `:name` dep (port mode) or nothing (project mode, where
        m4 already hoisted its expansion); a ported macro becomes a dep on its port (`_DEFAULTS`
        macros a `default_deps` entry on the package's `:defaults`); a macro with no port is a
        `macro` decision, unless it was only reached transitively (`via`).
        """
        file = self.defun_files.get(name)
        if file is not None and basename(file) in LIBTOOL_FILES:
            self._libtool_note(name)
            return
        comment = _when(f"AC_REQUIRE([{name}])" + (f" via {via}" if via else ""), when)
        if name in self._local_names(file):
            if self.project is None:
                if name.endswith("_DEFAULTS"):
                    ctx.target.add_default_dep(self._local_label(name, file, ctx), comment)
                else:
                    ctx.target.add_dep(self._local_label(name, file, ctx), comment)
            return  # project mode: the required body is expanded where m4 hoisted it
        if self.project is not None and file is not None and basename(file) in self.project.flatten_files:
            return  # gl_EARLY / gl_INIT: expanded inline where they are called
        resolution = self.index.resolve(name, file, library=self._gnulib_file(file)) if self.index else None
        if resolution is None:
            if via is not None:
                return
            if _BUILTIN_RE.match(name):
                self.report.note(f"{ctx.target.name}: AC_REQUIRE([{name}]) has no port (autoconf builtin); dropped")
            else:
                self._macro_decision(
                    name, ctx, f"AC_REQUIRE([{name}]) has no port", {"defined_in": file}, macro=("AC_REQUIRE", name)
                )
            return
        if via is not None and not (resolution.exact or resolution.reason == "file"):
            return
        if name.endswith("_DEFAULTS"):
            # The `_DEFAULTS` targets are private to their package: its public
            # `:defaults`, read through default_deps, stands for them.
            resolution = self._ports.by_package[resolution.package].defaults_resolution()
            comment += " (the header's defaults)" if resolution.reason == "defaults" else " (package aggregate)"
        elif resolution.reason == "file":
            comment += " (not ported individually; package aggregate)"
        self._add_port_dep(resolution, ctx, comment)

    def _harvest_requires(self, node: Node, ctx: _Ctx) -> None:
        for req in node.calls("AC_REQUIRE"):
            if req.args:
                self._require(req.args[0], ctx, via=node.name)

    # -- nested DEFUN calls ---------------------------------------------------

    def _libtool_note(self, name: str) -> None:
        self.report.note(f"{name}: libtool machinery (Bazel's C++ toolchain links the libraries); dropped")

    def _nested_defun(self, node: Node, ctx: _Ctx) -> None:
        """A macro call expanded inside the body being translated, dispatched by what it is: libtool
        noise, a gnulib module macro in project mode (a dep on its port or inlined), warning-flag
        macros (noted), shared probe helpers (their own target or a port), inline helper macros
        (list calls with the guard as condition), a ported macro (a dep), a selected macro (a
        dep, or inlined in project mode), anything else a `macro` decision.
        """
        name = node.name
        if node.is_internal:
            return
        taken = self._pkg_taken(node)
        if taken is not None:
            if taken:  # the action the verdict picked: its calls are the caller's own
                if node.name == PKG_FOUND and self.project is not None and node.args and node.args[0].strip():
                    self.project.pkg_conditions[f"{node.args[0].strip()}_LIBS"] = self._guard(node, ctx)
                self._walk(node.items, ctx)
            return
        if node.loc_file and basename(node.loc_file) in LIBTOOL_FILES:
            self._libtool_note(name)
            return
        guard = self._guard(node, ctx)
        if name in ("AC_LIB_HAVE_LINKFLAGS", "AC_LIB_LINKFLAGS") and self.project is not None:
            self._place(self._lib_linkflags(node, name), guard, ctx)
            return
        if self.project is not None and self._gnulib_project_macro(node, ctx, guard):
            return
        if name in WARNING_MACROS:
            self.report.note(f"{name}: compiler warning flags are not modeled (set copts instead)")
            return
        if name in HELPER_TARGET_MACROS:
            self._helper_target(node, ctx, guard)
            return
        if name in HELPER_INLINE_MACROS:
            call = Call(
                HELPER_INLINE_MACROS[name],
                [m4args.words(node.args[0])] if node.args else [[]],
                comments=[self._source_comment(node, ctx)],
                returns_list=True,
            )
            lowered = lower.expression(guard)
            if lowered.ok and lowered.expr:
                call.kwargs["condition"] = lowered.expr
            elif not lowered.ok:
                decision = self._guard_decision([call], guard, ctx, lowered.unknown or "?")
                if decision is None:
                    return
                if decision.never:
                    return
                if decision.requires is not None:
                    call.kwargs["condition"] = " && ".join(decision.requires)
            ctx.target.checks.append(call)
            self._harvest_requires(node, ctx)
            return
        comment = _when(self._source_comment(node, ctx), guard)
        resolution = (
            self.index.resolve(name, node.loc_file, library=self._gnulib_file(node.loc_file)) if self.index else None
        )
        if self._is_flattened(node):
            self._inline(node, ctx, guard)
            return
        if resolution and resolution.exact and (not self._is_selected(node) or self._gnulib_file(node.loc_file)):
            self._add_port_dep(resolution, ctx, comment)
            return
        if self.project is not None and self._is_selected(node):
            # The project's own macro: its checks see everything configure
            # defined before the call, so it expands into the caller.
            self.report.inlined.add(name)
            self._inline(node, ctx, guard)
            return
        if self._is_selected(node) and not (self.project is not None and node.args):
            ctx.target.add_dep(self._local_label(self._target_name(node), node.loc_file, ctx), comment)
            return
        if resolution is None and _BUILTIN_RE.match(name) and not self._is_selected(node):
            # automake / autoconf machinery defined after the prelude
            # (AM_COND_IF, AM_MISSING_PROG, ...): nothing a port needs.
            self.report.note(f"{name}: autotools machinery; dropped")
            return
        if node.args or resolution is None:
            # A parameterized helper (or an unknown macro): expand it in place.
            self.report.inlined.add(name)
            self._inline(node, ctx, guard)
            return
        self._add_port_dep(resolution, ctx, f"{name} (not ported individually; package aggregate)")

    def _inline(self, node: Node, ctx: _Ctx, guard: df.Cond) -> None:
        inner = ctx.inner(ctx.cache, None, guard)
        inner.analysis = self._analyze(node, outer=ctx.analysis)
        if self.project is not None:
            self._record_list_appends(node, inner.analysis, guard)
        self._walk(node.items, inner)
        self._settle(inner.analysis, ctx.analysis, ctx.analysis.guards.get(node.id, df.TRUE))

    @staticmethod
    def _settle(inner: df.Analysis, outer: df.Analysis, guard: df.Cond) -> None:
        """What an inlined macro assigned, as the caller's later calls see it: the caller's analysis ran
        before the macro's, so its state is updated by hand (nasm's PA_FIND_FUNC: each `_PA_FIND_FUNC`
        copies the probe's result into `pa_find_func_found`, which the next one tests).  An unconditional
        assignment under an unconditional call is the new value; anything else voids a literal the
        caller held, so a test consults nothing stale.  `guard` is the call's condition within the
        caller's own body (the caller's enclosing conditions apply to both alike)."""
        if outer.evaluator is None or inner.evaluator is None:
            return
        for var, assignments in inner.assigns.items():
            outer.inherited.setdefault(var, []).extend(
                df.Assignment(df.conj(guard, a.cond), a.value, a.text) for a in assignments
            )
            last = assignments[-1]
            if guard is df.TRUE and last.cond is df.TRUE and not isinstance(last.value, df.UnknownValue):
                outer.evaluator.state[var] = last.value
            else:
                if isinstance(outer.evaluator.state.get(var), df.Lit):
                    outer.evaluator.state[var] = None
                # The conditional literal stays known under its path, for a later call on the
                # same path (a pkg-config found action appending to the variable the macro set).
                if isinstance(last.value, df.Lit):
                    outer.evaluator.branch_state[var] = (df.conj(guard, last.cond), last.value)

    def _record_list_appends(self, node: Node, analysis: df.Analysis, guard: df.Cond) -> None:
        """Literal words an inlined macro assigns or appends to a shell variable, under the call's
        guard: an inlined macro's analysis is its own, so the caller's program lists (coreutils'
        `optional_bin_progs`, fed by `gl_ADD_PROG([optional_bin_progs], [cat])`) read them from here."""
        first = node.args[0].split("\n")[0].strip() if node.args else ""
        where = f"{node.name}([{', '.join(a.strip() for a in node.args[:2])}])" if first else node.name
        for var, assignments in analysis.assigns.items():
            for assignment in assignments:
                value = _assignment_value(assignment.text, var)
                if value is None:
                    continue
                rest = _without_self_reference(value, var)
                if not rest.strip() or "$" in rest or "`" in rest:
                    continue
                cond = df.conj(guard, df.substitute_empty(assignment.cond, var))
                if cond is df.FALSE:
                    continue
                if _STDC_INDICATOR_RE.match(var) and rest.split() == ["1"] and cond is df.TRUE:
                    # gl_INIT's `GL_STDC_BIT_WIDTH=1` (see `_variables`).
                    if var not in self._state.indicator_substs:
                        self._state.indicator_substs.append(var)
                    continue
                self._state.shell_list_appends.setdefault(var, []).extend((w, cond, where) for w in rest.split())

    def _indicator_subst(self, subst: str, node: Node, ctx: _Ctx | None) -> None:
        """Record the @GNULIB_X@ substitution an indicator macro of gl_INIT turns on.

        In the tests' gl_INIT the indicator is $gl_module_indicator_condition
        (= IN_<PKG>_GNULIB_TESTS): the library's headers must not enable the
        module (sed's lib/stdio.h would call rpl_fopen with no fopen.c).
        gl_MODULE_INDICATOR_SET_VARIABLE expands to `GL_GNULIB_X=1` (or
        `GL_M4_GNULIB_X=1` under gl_MODULE_INDICATOR_PREFIX) in the library's
        gl_INIT and to the `|| $gl_module_indicator_condition` form in the
        tests' (gnulib-common.m4); the expanded shell says which.
        """
        state = self._state
        in_tests = not re.search(r"\b[A-Z0-9_]*GNULIB_[A-Z0-9_]+=1\b", "".join(n.shell for n in node.walk()))
        condition = self._shell_literal("$gl_module_indicator_condition", ctx) if ctx is not None else None
        if in_tests and condition and condition != "1":
            if subst not in state.indicator_substs:  # a module of the library stays 1
                state.indicator_test_substs[subst] = condition
        else:
            state.indicator_substs.append(subst)
            state.indicator_test_substs.pop(subst, None)

    def _gnulib_project_macro(self, node: Node, ctx: _Ctx, guard: df.Cond) -> bool:
        """gl_INIT conventions: conditionals, conditional headers, indicators."""
        name = node.name
        args = node.args + ["", ""]
        state = self._state
        if name == "gl_CONDITIONAL":
            cond_name = args[0].strip()
            cond = self._test_condition(args[1], ctx, guard)
            state.src_conditions.setdefault(cond_name, []).append(df.conj(guard, cond))
            return True
        if name == "gl_CONDITIONAL_HEADER":
            self._conditional_header(args[0].strip(), ctx, guard)
            return True
        if self._indicator_macro(node, ctx, guard):
            return True
        if name in ("gl_LIBOBJ", "gl_REPLACE_FUNCS", "AC_LIBOBJ", "AC_REPLACE_FUNCS"):
            # Inside gl_INIT, AC_LIBOBJ is pushdef'd to gl_LIBOBJ: the range is
            # named after the macro as called.
            words = m4args.words(args[0])
            if name.endswith("REPLACE_FUNCS") and words:
                # AC_CHECK_FUNCS, plus AC_LIBOBJ for each function found missing (tcpdump's
                # `AC_REPLACE_FUNCS(... getopt_long)`: HAVE_GETOPT_LONG picks the getopt.h
                # tcpdump.c includes, and missing/getopt_long.c is compiled only without it).
                self._place([Call("macros.AC_CHECK_FUNCS", [words], {}, returns_list=True)], guard, ctx)
                for word in words:
                    self._libobj(word, df.conj(guard, df.neg(df.Truthy(cache_var("AC_CHECK_FUNCS", word)))))
                return True
            for word in words:
                self._libobj(word, guard)
            return True
        if name == "AM_INIT_AUTOMAKE":
            state.automake = True
            return True
        if name == "AM_CONDITIONAL":
            if args[0].strip():
                state.am_conditionals[args[0].strip()] = df.conj(guard, self._test_condition(args[1], ctx, guard))
                state.am_conditional_tests[args[0].strip()] = args[1] if len(args) > 1 else ""
            return True
        if name == "AM_COND_IF":
            # ppp's `AM_COND_IF([LINUX], [AC_CHECK_HEADERS([linux/if_packet.h ...])])`: the
            # synthesized `if <LINUX's test>` (`_cond_if_shell`) guards the children.
            self._walk(node.items, ctx)
            return True
        return name in (
            "gl_COMMON",
            "gl_COMMON_BODY",
            "gl_ASSERT_NO_GNULIB_POSIXCHECK",
            "gl_ASSERT_NO_GNULIB_TESTS",
            "gl_PROG_AR_RANLIB",
            "gl_FILE_LIST",
            "gl_LIBSOURCES",
            "AM_MISSING_PROG",
            "AM_SILENT_RULES",
            "AM_MAINTAINER_MODE",
        )

    def _conditional_header(self, header: str, ctx: _Ctx, guard: df.Cond) -> None:
        """gl_CONDITIONAL_HEADER([stdckdint.h]): GL_GENERATE_STDCKDINT_H is true when the shell
        variable STDCKDINT_H is non-empty (a module whose code gnulib-comp.m4 carries inline, as the
        newer header modules do: no m4 file, so no port provides the variable)."""
        state = self._state
        shell_var = shell_ident(header)
        var = f"GL_GENERATE_{shell_var}"
        state.cond_headers[header] = var
        # The module's code sets GL_GENERATE_X_H=true/false (older code
        # set X_H to the header's name); a variable the body never assigns
        # belongs to a ported macro, whose port provides the result.
        cond = self._test_condition(f'test "${var}" = true', ctx, guard)
        if not self._settles(cond, var):
            cond = self._test_condition(f'test -n "${shell_var}"', ctx, guard)
        if self._settles(cond, shell_var) and self._settles(cond, var):
            cond = df.conj(guard, cond)
            state.header_conditions[var] = (
                df.disj(state.header_conditions[var], cond) if var in state.header_conditions else cond
            )

    def _indicator_macro(self, node: Node, ctx: _Ctx, guard: df.Cond) -> bool:
        """The module indicator macros of gl_INIT (gl_MODULE_INDICATOR and its variants,
        gl_<HEADER>_MODULE_INDICATOR): the GNULIB_* defines and substitutions they turn on."""
        name = node.name
        args = node.args + ["", ""]
        state = self._state
        if name in ("gl_MODULE_INDICATOR", "gl_MODULE_INDICATOR_FOR_TESTS"):
            # GNULIB_<MODULE> is 1 for a module of the library; called from the
            # tests' gl_INIT it is IN_<PACKAGE>_GNULIB_TESTS (0 in the library,
            # 1 in the tests), and gl_MODULE_INDICATOR_FOR_TESTS defines
            # GNULIB_TEST_<MODULE>.  The expanded AC_DEFINE carries name and
            # value; the last definition wins in config.h, as in configure.
            defined = False
            for define in node.calls("AC_DEFINE_UNQUOTED") + node.calls("AC_DEFINE"):
                expanded = expanded_define(define)
                if expanded is None:
                    continue
                defined = True
                define_name, value = expanded
                # In the tests part of gl_INIT the value is the shell variable
                # gl_module_indicator_condition (= $<pkg>tests_WITNESS =
                # IN_<PKG>_GNULIB_TESTS); the body's assignments say what.
                if value.startswith("$"):
                    value = self._shell_literal(value, ctx) or value
                if name == "gl_MODULE_INDICATOR" and value == "1":
                    state.indicator_defines.append(define_name)
                else:
                    state.indicator_test_defines[define_name] = literal(value)
            if not defined and name == "gl_MODULE_INDICATOR":
                state.indicator_defines.append("GNULIB_" + shell_ident(args[0].strip()))
            return True
        if name in ("gl_MODULE_INDICATOR_INIT_VARIABLE", "gl_MODULE_INDICATOR_SET_VARIABLE"):
            # Called directly from gl_INIT (`gl_MODULE_INDICATOR_INIT_VARIABLE([GNULIB_STDIO_H_SIGPIPE],
            # [1])`, `gl_MODULE_INDICATOR_SET_VARIABLE([GNULIB_UNISTD_H_GETOPT])`): a wrapper header's
            # @GNULIB_X@ that no gl_<HEADER>_MODULE_INDICATOR spells.  sigpipe turns on three headers'
            # SIGPIPE indicators this way: stdio.h then renames printf to the EPIPE-emulating
            # replacement and signal.h defines SIGPIPE where the system lacks it.  A 0 is what the
            # ports' defaults hold already, and so are the header ports' own `_MODULE_INDICATOR_DEFAULTS`
            # initializations (`GNULIB_MDA_GCVT` 1 in stdlib_h.m4), which m4_require hoists out of
            # gl_INIT to configure's top level: only the calls inside gl_INIT's body (gnulib-comp.m4,
            # expanded inline; an AM_COND_IF block may lie between) are a module's.  Under a
            # condition (getopt-posix's, inside `AM_COND_IF([GL_COND_OBJ_GETOPT])`) the
            # substitution carries it; the default stands where it fails.
            value = "1"
            if name == "gl_MODULE_INDICATOR_INIT_VARIABLE":
                value = args[1].strip() if len(args) > 1 and args[1].strip() else "0"
            in_gl_init = any(self._is_flattened(a) for a in node.ancestors() if a.kind == "defun")
            if value != "0" and in_gl_init:
                subst = re.sub(r"[^A-Za-z0-9]", "_", args[0].strip())
                if guard is df.TRUE:
                    self._indicator_subst(subst, node, ctx)
                elif subst not in state.indicator_substs:
                    call = Call("checks.AC_SUBST", [subst, 1])
                    call.comments.append(
                        f"{self._source_comment(node, ctx)}: a header indicator under its "
                        "module's condition; the port's default 0 stands where it fails"
                    )
                    self._place([call], guard, ctx)
            return True
        if _MODULE_INDICATOR_RE.match(name):
            subst = "GNULIB_" + shell_ident(args[0].strip())
            self._indicator_subst(subst, node, ctx)
            # gl_<HEADER>_MODULE_INDICATOR also runs gl_MODULE_INDICATOR_FOR_TESTS:
            # GNULIB_TEST_<MODULE> in config.h.
            for define in node.calls("AC_DEFINE") + node.calls("AC_DEFINE_UNQUOTED"):
                expanded = expanded_define(define)
                if expanded is not None and expanded[0].startswith("GNULIB_TEST_"):
                    state.indicator_test_defines[expanded[0]] = literal(expanded[1])
            return True
        return False

    @staticmethod
    def _settles(cond: df.Cond, var: str) -> bool:
        """True when `cond` is known and does not still hang on `var` itself."""
        return not isinstance(cond, df.Unknown) and var not in describe(cond)

    def _test_condition(self, test: str, ctx: _Ctx | None = None, guard: df.Cond = df.TRUE) -> df.Cond:
        """The condition of a `gl_CONDITIONAL([..], [test ...])` second argument,
        evaluated against the enclosing body's variables when `ctx` is given."""
        shell = m4args.neutralize_macros(m4args.quadrigraphs(m4args.strip_m4(test))).strip()
        if not shell:
            return df.TRUE
        if shell == "false":
            return df.FALSE
        if "! {" in shell:
            # tree-sitter-bash reads `! { a || b; }` as a command named `{`;
            # the groups mean the same as subshells in a condition.
            shell = re.sub(r"\{\s+", "( ", re.sub(r";\s*\}", "; )", shell))
        case = _CASE_CONDITION_RE.match(" ".join(shell.split()))
        if case and case.group(3) != case.group(4):
            var, patterns = case.group(1), [p.strip() for p in case.group(2).split("|")]
            evaluator = ctx.analysis.evaluator if ctx is not None and ctx.analysis.evaluator else df.Evaluator()
            cond = df.HostOs(tuple(patterns)) if var in ("host_os", "host") else evaluator.match(var, patterns)
            return cond if case.group(3) in ("true", ":") else df.neg(cond)
        seq = sh.parse(f"if {shell}; then :; fi\n")
        if len(seq.stmts) == 1 and isinstance(seq.stmts[0], sh.If):
            expr, _ = seq.stmts[0].branches[0]
            if expr is not None:
                body_evaluator = ctx.analysis.evaluator if ctx is not None and ctx.analysis.evaluator else None
                if body_evaluator is not None:
                    return body_evaluator.condition_of(expr, guard)
                return df.Evaluator().expr(expr)
        return df.Unknown(shell)

    def _libobj(self, name: str, guard: df.Cond) -> None:
        cond_name = "GL_COND_OBJ_" + shell_ident(name.strip())
        self._state.src_conditions.setdefault(cond_name, []).append(guard)
        self._state.libobj_names.setdefault(cond_name, name.strip())

    def _port_for_check(self, candidates: list[str]) -> Resolution | None:
        """A hand port whose target is named after the check it carries.

        Shared probes (`gl_CHECK_FUNCS_ANDROID([newlocale])`, `AC_CHECK_DECLS_ONCE
        ([alarm])`) live in exactly one port each, under the define, the cache
        variable or the `MACRO_subject` name; a project that repeats the call
        at its top level must depend on that port, not probe again.
        """
        if self.index is None or self.project is None:
            return None
        for candidate in candidates:
            resolution = self.index.resolve(candidate)
            if resolution is not None and resolution.exact:
                return resolution
            ports = self.index.by_name.get(candidate, [])
            if len(ports) == 1:
                # The target itself is usually private to its package: depend
                # on the package aggregate, which carries it.
                return Resolution(ports[0].label(), ports[0].package, None, "file")
        return None

    def _helper_target(self, node: Node, ctx: _Ctx, guard: df.Cond) -> None:
        """A shared probe macro (HELPER_TARGET_MACROS, AC_FUNC_X style): a dep on the port that
        carries the probe when one does, else one helper target per (macro, function) in the
        package, so several modules can depend on the same probe and define.
        """
        func = node.args[0].strip() if node.args else ""
        name = f"{node.name}_{slug(func)}"
        port = self._port_for_check([name, f"HAVE_{cache_name(func).upper()}", f"ac_cv_func_{cache_name(func)}"])
        if port is not None:
            comment = _when(self._source_comment(node, ctx) + " (the port's shared probe)", guard)
            self._add_port_dep(port, ctx, comment)
            return
        helper = ctx.package.target(name)
        if helper is None:
            kwargs: dict[str, AttrValue] = {}
            if len(node.args) > 1 and node.args[1].strip():
                kwargs["includes"] = _includes_arg(node.args[1])
            call = Call(HELPER_TARGET_MACROS[node.name], [[func]], kwargs, returns_list=True)
            helper = Target(name=name, checks=[call], loc=Loc(node.loc_file or "?", node.loc_line), helper=True)
            helper.comments.append(
                f"{node.name}([{func}]{', [...]' if len(node.args) > 1 else ''}): shared probe and define; "
                "its own target so other modules can depend on it."
            )
            ctx.package.targets.append(helper)
        comment = _when(self._source_comment(node, ctx), guard)
        ctx.target.add_dep(f":{name}", comment)

    # -- leaf calls -----------------------------------------------------------

    def _call(self, node: Node, ctx: _Ctx) -> None:
        """Dispatch for one macro call of the body: AC_REQUIRE, the cache and transparent wrappers,
        AC_LANG_*, project-mode structural macros, ignored macros, then a `_m_<NAME>` handler
        when the translator has one, a dep on the port when the index knows the macro, a builtin
        translation, or a `macro` decision.
        """
        name = node.name
        args = node.args
        if name == "AC_REQUIRE":
            if args:
                self._require(args[0], ctx, when=self._guard(node, ctx))
            return
        if name in TRANSPARENT:
            self._cache_check(node, ctx)
            return
        if self.project is not None and self._gnulib_project_macro(node, ctx, self._guard(node, ctx)):
            return
        if name == "AC_LANG_PUSH":
            ctx.lang.append(LANGUAGES.get(args[0].strip() if args else "C", "c"))
            return
        if name == "AC_LANG_POP":
            if len(ctx.lang) > 1:
                ctx.lang.pop()
            return
        if name == "AC_LANG":
            ctx.lang[-1] = LANGUAGES.get(args[0].strip() if args else "C", "c")
            return
        if self.project is not None and self._project_call(node, ctx):
            return
        if name in IGNORED or name in ("AC_INIT", "AC_CONFIG_HEADERS", "AC_CONFIG_FILES", "AM_INIT_AUTOMAKE"):
            return

        comment = self._source_comment(node, ctx)
        handler = getattr(self, "_m_" + name, None)
        if handler is None:
            resolution = self.index.resolve(name, library=self._gnulib_file(node.loc_file)) if self.index else None
            if resolution is not None and resolution.exact:
                guard = self._guard(node, ctx)
                self._add_port_dep(resolution, ctx, _when(comment, guard))
                return
            if name in BUILTIN_CHECKS:
                calls, note = BUILTIN_CHECKS[name]
                copies = [clone(c) for c in calls]
                for copy in copies:
                    copy.comments.insert(0, f"{comment}: GNU autoconf builtin, translated by the converter")
                if note:
                    self.report.note(note)
                self._place(copies, self._guard(node, ctx), ctx)
                return
            if _BUILTIN_RE.match(name) and not _CHECK_BUILTIN_RE.match(name):
                # A library macro without a port (AC_PROG_CPP, AC_PROG_GREP...):
                # its internals were recorded under this call and are not
                # the project's own checks.
                self.report.note(f"{name}: no port in rules_cc_autoconf; dropped ({comment})")
                return
            summary = (
                f"{name} is an autoconf builtin without a port in rules_cc_autoconf ({comment})"
                if _BUILTIN_RE.match(name)
                else f"no translation for {comment}"
            )
            self._macro_decision(name, ctx, summary, {"arguments": args}, macro=(name, args[0] if args else None))
            return
        items = handler(node, ctx) or []
        for item in items:
            if isinstance(item, Call):
                item.comments.insert(0, comment)
                if ctx.cache_comment:
                    item.comments.insert(0, ctx.cache_comment)
                    ctx.cache_comment = None
        items = self._expand_program_shell(items, node, ctx)
        self._place(items, self._guard(node, ctx), ctx)
        self._harvest_requires(node, ctx)

    def _expand_program_shell(self, items: list[CheckItem], node: Node, ctx: _Ctx) -> list[CheckItem]:
        """Shell variables inside a probe's program.  configure expands them before compiling
        (gmp's `#define GMP_NAIL_BITS $GMP_NAIL_BITS` and `#include "$srcdir/gmp-h.in"`); the
        rules compile the text as written.  A variable the body assigns one literal is expanded
        here (gnulib's `statvfs_includes`, a multi-line string of includes), `$ac_includes_default`
        on a line of its own is the rules' own default includes, and anything else (a path of the
        source tree, a value chosen per ABI) makes the probe a decision: its value, a replacement,
        or IGNORE."""
        out: list[CheckItem] = []
        for item in items:
            if not isinstance(item, Call) or not ({"code", "includes"} & set(item.kwargs)):
                out.append(item)
                continue
            for key in ("code", "includes"):
                if key in item.kwargs:
                    item.kwargs[key] = self._expand_shell_refs(item.kwargs[key], ctx)
            unresolved = {
                name for key in ("code", "includes") if key in item.kwargs for name in _shell_refs(item.kwargs[key])
            }
            if not unresolved:
                out.append(item)
                continue
            name = item.kwargs.get("name")
            var = (
                name
                if isinstance(name, str) and name
                else next((n for n in sorted(provided_names(item)) if "_cv_" in n), ctx.target.name)
            )
            names = ", ".join(f"${n}" for n in sorted(unresolved))
            decision = self._request(
                f"probe:{var}",
                "probe",
                ctx,
                f"{node.name}: the program reads {names}, which configure expands and the rules cannot (a path "
                "of the source tree, a value chosen elsewhere); the check would compile the text as written",
                {"program": _program_lines(item), "shell_variables": sorted(unresolved)},
                node=node,
            )
            if decision is None or decision.ignore:
                ctx.dropped.add(var)
                continue
            if decision.accept:
                out.append(item)
                continue
            define = item.kwargs.get("define") if item.fn in _VALUE_DEFINING_CHECKS else None
            self._apply_probe_decision(
                var,
                decision,
                ctx,
                self._guard(node, ctx),
                self._source_comment(node, ctx),
                define=define if isinstance(define, str) else None,
            )
        return out

    def _expand_shell_refs(self, value: AttrValue, ctx: _Ctx) -> AttrValue:
        """`value` (a string, a list of lines, or a `utils.AC_LANG_PROGRAM` call) with its `$var`
        references expanded to the literals the body gives them.  A line of a list that expands
        to several lines becomes several; `$ac_includes_default` on a line of its own becomes
        `utils.AC_INCLUDES_DEFAULT`.  What stays unexpanded is for the caller to report."""
        if isinstance(value, Call):
            value.args = [self._expand_shell_refs(arg, ctx) for arg in value.args]
            return value
        if isinstance(value, list):
            out: list[AttrValue] = []
            for line in value:
                expanded = self._expand_shell_refs(line, ctx) if isinstance(line, str) else line
                parts = block_lines(expanded) if isinstance(expanded, str) and "\n" in expanded else [expanded]
                for part in parts:
                    out.append(
                        Raw("utils.AC_INCLUDES_DEFAULT")
                        if isinstance(part, str) and part.strip() == f"${_INCLUDES_DEFAULT_VARIABLE}"
                        else part
                    )
            return out
        if not isinstance(value, str) or "$" not in value:
            return value

        def expand(match: re.Match[str]) -> str:
            found = self._program_literal(match.group(1), ctx)
            return str(match.group(0)) if found is None else found

        return _SHELL_REF_RE.sub(expand, value)

    def _program_literal(self, name: str, ctx: _Ctx, depth: int = 0) -> str | None:
        """The text `$name` stands for inside a probe's program: the body's one unconditional
        assignment of it, with the `$var`s of that assignment expanded the same way.  configure's
        directories are never a literal (`#include "$srcdir/gmp-h.in"` wants a file of the tree),
        and `$ac_includes_default` stays as written: it is the rules' default includes, not text."""
        if name in _TREE_PATH_VARIABLES or name == _INCLUDES_DEFAULT_VARIABLE or depth > 3:
            return None
        literal = self._shell_literal(f"${name}", ctx)
        if literal is None:
            # gnulib's `statvfs_includes="\<newline>$ac_includes_default<newline>#include ..."`: the
            # value is no literal to the analysis (a variable inside), but every piece of it is.
            decisions = ctx.analysis.decisions(name) if ctx.analysis is not None else []
            if len(decisions) != 1 or decisions[0].cond is not df.TRUE:
                return None
            literal = _assignment_value(decisions[0].text, name)
            if literal is None:
                return None
        literal = literal.replace("\\\n", "")  # a line continuation inside double quotes
        resolved = True

        def expand(match: re.Match[str]) -> str:
            nonlocal resolved
            if match.group(1) == _INCLUDES_DEFAULT_VARIABLE:
                return str(match.group(0))
            inner = self._program_literal(match.group(1), ctx, depth + 1)
            if inner is None:
                resolved = False
                return str(match.group(0))
            return inner

        literal = _SHELL_REF_RE.sub(expand, literal)
        return literal if resolved else None

    def _cache_check(self, node: Node, ctx: _Ctx) -> None:
        """AC_CACHE_CHECK / AC_CACHE_VAL: look through to the probe inside."""
        padded = node.args + ["", "", ""]
        commands = padded[2] if node.name == "AC_CACHE_CHECK" else padded[1]
        var = cache_node_var(node, ctx.language)
        comment = self._source_comment(node, ctx)
        guard = self._guard(node, ctx)
        if "$" in var:
            # `AC_CACHE_CHECK([...], [$gt_func_gnugettext_libc], ...)`: the
            # variable is named by the shell; no check can be attached to it.
            decision = self._request(
                f"probe:{ctx.target.name}:{slug(var)}",
                "probe",
                ctx,
                f"{comment}: the cache variable's name is computed by the shell ({var}); "
                "only replacement checks (or IGNORE) can stand for it",
                {"cache_variable": var, "commands": block_lines(m4args.quadrigraphs(commands))},
                node=node,
            )
            if decision is not None:
                ctx.target.checks.extend(decision.checks or [])
                if not decision.ignore and decision.checks is None:
                    self.report.note(f"{comment}: the decision for {var} must be IGNORE or checks; left out")
            return
        if var in self.toolchain_vars:
            self.report.note(
                f"{comment}: a compiler-option test (an empty program compiled with the flag in "
                f'CFLAGS), which the toolchain decides; left out, {var} reads "no"'
            )
            return
        inner = ctx.inner(var or None, comment, guard)
        probes = [c for c in node.children if c.kind == "call" and c.name in PROBES]
        if not re.sub(r"[\[\]\s]", "", commands) or (
            len(probes) == 1
            and _EXPANDED_PROBE_RE.search(commands)
            and var
            and all(v.strip("\"'") in ("yes", "no") for v in re.findall(rf"\b{re.escape(var)}=(\S+)", commands))
        ):
            # `AC_CACHE_VAL(var, AC_TRY_COMPILE(...))` unquoted: m4 expanded the probe while
            # collecting the argument, so the markers record the probe's own expansion
            # (`cat confdefs.h - <<_ACEOF ... if ac_fn_c_try_compile`) as the commands; the
            # probe is the child, and the cache variable is its yes/no actions.
            commands = ""
        shell_commands = m4args.parse_call(commands) is None and bool(commands.strip())
        if shell_commands and var and self._cache_from_shell(node, ctx, var, guard, comment):
            return
        if shell_commands:
            # The commands do more than one probe call (save/restore flags,
            # several probes, shell logic): the probe alone may not be it.
            key = f"probe:{var or ctx.target.name}"
            decision = self._request(
                key,
                "probe",
                ctx,
                f"{comment}: the cache value comes from shell around the probe, not from one probe",
                {
                    "commands": block_lines(m4args.quadrigraphs(commands)),
                    "programs": [block_lines(p.program() or "") for p in probes],
                },
                node=node,
            )
            if decision is None or decision.ignore:
                if var:
                    ctx.dropped.add(var)
                return
            if not decision.accept:
                if var:
                    self._apply_probe_decision(var, decision, ctx, guard, comment)
                return
        self._walk(node.items, inner)

    def _cache_from_shell(self, node: Node, ctx: _Ctx, var: str, guard: df.Cond, comment: str) -> bool:
        """`AC_CACHE_VAL([var], [shell that calls other macros and assigns var])`:
        translate the nested calls, then `var` from the body's assignments
        (`M4_VARIABLE` with the conditions over the nested results).  False
        when the body assigns `var` nowhere the dataflow can follow."""
        decisions = ctx.analysis.decisions(var)
        if not decisions or any(not isinstance(d.value, df.Lit) for d in decisions):
            return False
        # make's AC_STRUCT_ST_MTIM_NSEC: `var=no`, then a probe inside a loop whose
        # action assigns `var=$ac_val`.  The action is the probe's argument, not
        # body shell the dataflow sees: the literal would be wrong.
        assigns = re.compile(rf"\b{re.escape(var)}=")
        if any(
            assigns.search(arg)
            for probe in node.walk()
            if probe is not node and probe.kind == "call" and probe.name in PROBES
            for arg in probe.args[1:3]
        ):
            return False
        # The nested checks first (the probes the assignments branch on).
        inner = ctx.inner(None, None, guard)
        self._walk(node.items, inner)
        ctx.probe_vars.add(var)
        decisions = [df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text) for d in decisions]
        check, dropped = self._lower_decisions(var, [(d.cond, d) for d in decisions], "checks.M4_VARIABLE", False)
        if check is not None and not dropped:
            check.comments.insert(0, f"{comment}: {var} from the shell of the cache commands")
            _append_unique(ctx.target.checks, check)
            return True
        # Several values on several paths: no single check spells it, but every
        # `test $var = value` in this body already became the matching
        # condition over the probes.  Uses from other bodies become decisions.
        ctx.probe_vars.discard(var)
        self.dropped_global.add(var)
        self.report.note(
            f"{comment}: {var} takes {len(decisions)} values on different paths; the tests on it "
            "in this body were rewritten to the probes it depends on"
        )
        return True

    def _project_call(self, node: Node, ctx: _Ctx) -> bool:
        """Top-level configure.ac macros that shape the project (project mode)."""
        name = node.name
        args = node.args
        state = self._state
        if name == "AC_INIT":
            state.init_args = [m4args.quadrigraphs(a) for a in args]
            return True
        if name == "AC_CONFIG_LIBOBJ_DIR":
            state.libobj_dir = args[0].strip() if args else ""
            return True
        if name == "AC_CONFIG_HEADERS":
            state.config_headers.extend(m4args.words(args[0]) if args else [])
            return True
        if name == "AC_CONFIG_FILES":
            state.config_files.extend(m4args.words(args[0]) if args else [])
            return True
        if name == "AC_CONFIG_SUBDIRS":
            # Literal directories only: `AC_CONFIG_SUBDIRS([$subdirs])` names
            # them at configure time and is reported, not followed.
            words = m4args.words(args[0]) if args else []
            state.config_subdirs.extend(w for w in words if "$" not in w)
            # The condition configure enters the sub-configure under (gawk's extension/ only when
            # `$enable_extensions` is yes): `project._subdir_gates` turns it into the
            # sub-package's `target_compatible_with`, or asks a `subdir:` decision.
            guard = self._guard(node, ctx)
            for word in words:
                if "$" in word:
                    self.report.note(f"AC_CONFIG_SUBDIRS({word}): computed at configure time; not followed")
                elif word in state.subdir_conditions:
                    state.subdir_conditions[word] = df.disj(state.subdir_conditions[word], guard)
                else:
                    state.subdir_conditions[word] = guard
            return True
        if name in ("AC_ARG_ENABLE", "AC_ARG_WITH"):
            # The option itself is modeled by the synthesized shell around its placeholder
            # (`_flag_shell`); the calls its actions expand to (gawk's `AC_DEFINE(NO_LINT)`
            # under `test "$enableval" = no`) are translated under the condition that shell
            # gives their placeholders.
            self._walk(node.items, ctx)
            return True
        return False

    # -- decided checks -------------------------------------------------------

    def _apply_probe_decision(
        self, var: str, decision: dec.Decision, ctx: _Ctx, guard: df.Cond, comment: str, define: str | None = None
    ) -> None:
        """A decided value or replacement probe for cache variable `var`.  `define` names the
        define a value-recording check (AC_CHECK_SIZEOF, AC_COMPUTE_INT) sets to the value: a
        decided value defines it too."""
        if decision.ignore:
            return
        if decision.values is None and decision.value is None:
            # A replacement probe answers yes/no by succeeding or failing; a
            # literal is a plain variable, which `test $var = yes` compares by value
            # (gzip_cv_assembler = "no" is a result that says no, not a failed probe).
            ctx.probe_vars.add(var)

        def decided(value: dec.Scalar) -> list[Call]:
            if isinstance(value, float):
                raise dec.DecisionError(f"probe:{var}: {value!r} is not a value a check takes (a string or an int)")
            calls = [Call("checks.M4_VARIABLE", [var, value], comments=[f"{comment} (decisions file)"])]
            if define:
                calls.append(Call("checks.AC_DEFINE", [define, value], comments=[f"{comment} (decisions file)"]))
            return calls

        if decision.values is not None:
            self._place_decided_values(decision.values, ctx, decided)
            return
        if decision.value is not None:
            self._place(decided(decision.value), guard, ctx)
            return
        for probe in (decision.try_compile, decision.try_link):
            if probe is not None:
                call = clone(probe)
                call.comments = [f"{comment} (decisions file)"]
                self._place([call], guard, ctx)
                return
        ctx.target.checks.extend(decision.checks or [])
        if decision.checks is None and not decision.accept:
            raise ValueError(f"decision for probe:{var} has no known form: {decision}")

    @staticmethod
    def _decided_arms[V](values: Mapping[str, V], ctx: _Ctx) -> tuple[str, dict[str, V]]:
        """A decision's select() arms, plus the host-OS arms other conditions already opened in
        the same family: they win over the default arm on their platform, so they take the
        default arm's value (a new arm copies the default arm when it is opened; an existing
        one would miss it)."""
        family = family_of_labels(values)
        arms = dict(values)
        if family in PLATFORM_FAMILIES and lower.DEFAULT_ARM in arms:
            for label in ctx.target.selects.get(family, {}):
                arms.setdefault(label, arms[lower.DEFAULT_ARM])
        return family, arms

    def _place_decided_values(
        self, values: Mapping[str, dec.Scalar], ctx: _Ctx, build: Callable[[dec.Scalar], list[Call]]
    ) -> None:
        """A decision's `values` (one per select() arm): `build(value)`'s checks in each arm, as plain
        checks when the default arm is the only one."""
        family, values = self._decided_arms(values, ctx)
        for label, value in values.items():
            dest = (
                ctx.target.checks if label == lower.DEFAULT_ARM and len(values) == 1 else ctx.target.arm(label, family)
            )
            dest.extend(build(value))
        if len(values) > 1:
            ctx.target.arm(lower.DEFAULT_ARM, family)

    def _apply_variable_decision(self, var: str, kind: str, decision: dec.Decision, ctx: _Ctx, comment: str) -> None:
        """Emit the checks a `variable:` decision asks for: a literal per select arm (`values`), a
        condition / if_true / if_false subst, one literal (optionally under requires), or
        verbatim checks; IGNORE emits nothing.
        """
        if decision.ignore:
            return
        comments = [f"{comment} (decisions file)"]
        if decision.values is not None:
            self._place_decided_values(
                decision.values, ctx, lambda value: [Call(kind, [var, literal(str(value))], comments=comments)]
            )
            return
        if decision.condition is not None:
            ctx.target.checks.append(Call(kind, [var], {"condition": decision.condition}, comments=comments))
            return
        if decision.value is not None:
            kwargs: dict[str, AttrValue] = {}
            if decision.requires is not None:
                kwargs["requires"] = list(decision.requires)
            ctx.target.checks.append(Call(kind, [var, literal(str(decision.value))], kwargs, comments=comments))
            return
        ctx.target.checks.extend(decision.checks or [])
        if decision.checks is None and not decision.accept_partial:
            raise ValueError(f"decision for variable:{var} has no known form: {decision}")

    def _sees_through(self, node: Node) -> bool:
        """`sees_through`, unless the decisions file answers the check's actions as a whole
        (`actions:AC_CHECK_FUNC:tgetent`): that decision stands in for them."""
        return sees_through(node) and self.decisions.lookup(_actions_key(node)) is None

    def _actions_decision(self, node: Node, ctx: _Ctx, actions: list[tuple[str, str]]) -> list[CheckItem]:
        """Action arguments with no mechanical translation: a decision or nothing."""
        first = node.args[0].split("\n")[0].strip() if node.args else ""
        key = _actions_key(node)
        decision = self._request(
            key,
            "actions",
            ctx,
            f"{node.name}([{first}]) has actions the converter does not translate",
            {label: text.strip() for label, text in actions},
            node=node,
        )
        if decision is None or decision.ignore:
            # The actions' own checks are not translated: what they would have produced is
            # known to be missing (tcpdump's AC_CACHE_CHECK for a buggy ether_ntohost inside
            # AC_CHECK_FUNCS' action), so conditions over it become guard decisions.
            for child in node.walk():
                if child is node or child.kind != "call":
                    continue
                padded = child.args + ["", ""]
                if child.name in ("AC_CACHE_CHECK", "AC_CACHE_VAL"):
                    ctx.dropped.add(cache_node_var(child))
                elif child.name in PROBES and (action := action_var(padded[1])):
                    ctx.dropped.add(action)
            return []
        return list(decision.checks or [])

    def _action_defines(
        self, node: Node, cache: str, actions: list[tuple[str, str]]
    ) -> tuple[list[Call], list[tuple[str, str]]]:
        """`AC_DEFINE([X], [v])` in an action argument -> a define that requires the
        probe's cache variable (negated for the not-found action)."""
        checks: list[Call] = []
        leftover: list[tuple[str, str]] = []
        for label, text in actions:
            # Progress messages are not actions; an AC_MSG_ERROR in the not-found
            # branch stops configure where the header or function is missing,
            # which a result that says so already reports.
            text = _FATAL_RE.sub("", strip_messages(text)).strip()
            if not text:
                continue
            found = action_define(text)
            defines = [found] if found is not None else action_defines(text)
            if defines:
                requires = cache if label == "action-if-found" else f"!{cache}"
                for define, value in defines:
                    checks.append(Call("checks.AC_DEFINE", [define, literal(value)], {"requires": [requires]}))
            else:
                leftover.append((label, text))
        return checks, leftover

    def _action_list_appends(
        self, node: Node, ctx: _Ctx, cache: str, actions: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        """An action that only appends words to a shell list variable (coreutils' `gl_ADD_PROG([optional_bin_progs],
        [hostid])`: `optional_bin_progs="$optional_bin_progs hostid"`): recorded as the words listed under the
        probe's result, for the program lists; not a decision.  Returns the actions that are something else."""
        if self.project is None:
            return actions
        leftover: list[tuple[str, str]] = []
        for label, text in actions:
            parsed = list_append_words(strip_messages(text))
            if parsed is None:
                leftover.append((label, text))
                continue
            var, words = parsed
            cond = df.Truthy(cache) if label == "action-if-found" else df.neg(df.Truthy(cache))
            cond = df.conj(self._guard(node, ctx), cond)
            first = node.args[0].split("\n")[0].strip() if node.args else ""
            where = f"{node.name}([{first}]) {label}"
            self.project.shell_list_appends.setdefault(var, []).extend((w, cond, where) for w in words)
        return leftover

    # Each handler returns a list of Call / Raw items.

    def _m_AC_DEFINE(self, node: Node, ctx: _Ctx, unquoted: bool = False) -> list[CheckItem]:
        """AC_DEFINE / AC_DEFINE_UNQUOTED: a literal value is emitted as is; a value taken from the
        shell is resolved from the analysis when the assignments allow it, otherwise it becomes a
        `variable:` decision. A `define:` IGNORE decision drops it (a dependency already provides
        it).
        """
        args = node.args + ["", ""]
        name, value = args[0].strip(), args[1]
        fn = "checks.AC_DEFINE_UNQUOTED" if unquoted else "checks.AC_DEFINE"
        if self._dropped_by_decision("define", name.split("(")[0]):
            return []
        ctx.defines.add(name.split("(")[0])
        if len(node.args) < 2 or not value.strip():
            call = Call(fn, [name])
            if len(node.args) >= 2:
                call.comments.append("empty value in the m4; consumers test it with #ifdef")
            return [call]
        if "$" in value or "`" in value:
            resolved = self._shell_value(value, ctx)
            if resolved is None and self._define_from_assignments(name, value, fn, ctx, node):
                return []
            if resolved is None:
                quoted = value.strip().startswith('"')
                decision = self._request(
                    f"variable:{name}",
                    "variable",
                    ctx,
                    f"{fn.split('.')[1]}([{name}]) takes its value from shell: {value.strip()}"
                    + (" (a string decision is the C literal's contents, without its quotes)" if quoted else ""),
                    {"value_expression": value.strip()},
                )
                if decision is None or decision.ignore:
                    ctx.dropped.add(name)
                else:
                    # `AC_DEFINE_UNQUOTED([X], ["$x"])` / `['$x']`: the decision is the
                    # shell value; the quotes around it in the m4 are the C literal's.
                    written = value.strip()
                    applied_fn = fn
                    if len(written) >= 2 and written[0] == written[-1] and written[0] in "'\"":
                        q = written[0]
                        if decision.value is not None:
                            decision = replace(decision, value=q + str(decision.value) + q)
                            applied_fn = "checks.AC_DEFINE"
                        elif decision.values is not None:  # one literal per select() arm
                            decision = replace(
                                decision, values={label: q + str(v) + q for label, v in decision.values.items()}
                            )
                            applied_fn = "checks.AC_DEFINE"
                        # The rules render an AC_DEFINE_UNQUOTED value without its
                        # quotes (its shell expansion); the C literal wants AC_DEFINE.
                    self._apply_variable_decision(name, applied_fn, decision, ctx, self._source_comment(node, ctx))
                return []
            stripped = value.strip()
            if isinstance(resolved, str) and len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
                resolved = f'"{resolved}"'  # `["$M4"]`: a C string literal, quotes and all
            # The shell expansion is done here: a plain define keeps the value as written.
            call = Call("checks.AC_DEFINE", [name, resolved])
            call.comments.append(f"value {value.strip()} in the m4")
            return [call]
        return [Call(fn, [name, literal(value)])]

    def _m_AC_DEFINE_UNQUOTED(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._m_AC_DEFINE(node, ctx, unquoted=True)

    def _define_from_assignments(self, name: str, value: str, fn: str, ctx: _Ctx, node: Node) -> bool:
        """`AC_DEFINE_UNQUOTED([X], [$var])` where the body assigns `var` a
        literal on each path: the define takes the same conditions."""
        match = _VAR_REF_RE.fullmatch(value)
        if not match:
            return False
        decisions = ctx.analysis.decisions(match.group(1))
        if not decisions or any(not isinstance(d.value, df.Lit) for d in decisions):
            return False
        decisions = [df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text) for d in decisions]
        if value.strip().startswith('"'):
            # `AC_DEFINE_UNQUOTED([SCCS_GET], ["$SCCS_GET"])`: the C string literal; the
            # rules render an AC_DEFINE_UNQUOTED value without its quotes, so AC_DEFINE.
            # (Every value is a `Lit`, checked above; the isinstance narrows for the type checker.)
            decisions = [
                df.Assignment(d.cond, df.Lit(f'"{d.value.text}"'), d.text)
                for d in decisions
                if isinstance(d.value, df.Lit)
            ]
            fn = "checks.AC_DEFINE"
        ctx.defines.add(name.split("(")[0])
        before = len(self.decisions.pending)
        self._decision_checks(
            name, decisions, ctx, kind=fn, comment=f"{self._source_comment(node, ctx)}: value {value.strip()} in the m4"
        )
        return len(self.decisions.pending) == before  # a decision request means it did not lower

    def _shell_literal(self, text: str, ctx: _Ctx, depth: int = 0) -> str | None:
        """The literal `$var` stands for when the body assigns `var` once,
        possibly through another variable (`x=$y`, `y=literal`)."""
        match = _VAR_REF_RE.fullmatch(text)
        if not match or depth > 3:
            return None
        program = ctx.analysis.program_literals.get(match.group(1))
        if program is not None:
            return program
        decisions = ctx.analysis.decisions(match.group(1))
        if not decisions:
            seeded = self._seeded_literal(match.group(1), ctx)
            return seeded.text if seeded is not None else None
        if len(decisions) != 1 or decisions[0].cond is not df.TRUE:
            return None
        value = decisions[0].value
        if isinstance(value, df.Lit):
            return value.text
        if isinstance(value, df.Ref):
            return self._shell_literal(f"${value.name}", ctx, depth + 1)
        return None

    @staticmethod
    def _seed(ctx: _Ctx, var: str, text: str) -> None:
        """`AC_SUBST([M4], [$ac_cv_path_M4])` assigns M4 inside its own expansion, out of the
        body's sight: let the body's shell see the value (flex defines M4 from it next)."""
        if ctx.analysis is not None and ctx.analysis.evaluator is not None and not ctx.analysis.decisions(var):
            ctx.analysis.evaluator.state[var] = df.Lit(text)

    @staticmethod
    def _seeded_literal(var: str, ctx: _Ctx) -> df.Lit | None:
        """A variable the analysis was seeded with rather than one the body assigns: a program
        lookup's result (`ac_cv_path_M4`), or a literal the caller's body settled."""
        evaluator = ctx.analysis.evaluator if ctx.analysis is not None else None
        value = evaluator.state.get(var) if evaluator is not None else None
        return value if isinstance(value, df.Lit) else None

    def _shell_value(self, text: str, ctx: _Ctx) -> str | int | None:
        """The literal a `$var` value has when the body assigns it unconditionally."""
        match = _VAR_REF_RE.fullmatch(text)
        if not match:
            return None
        program = ctx.analysis.program_literals.get(match.group(1))
        if program is not None:
            return literal(program)
        decisions = ctx.analysis.decisions(match.group(1))
        if len(decisions) == 1 and decisions[0].cond is df.TRUE and isinstance(decisions[0].value, df.Lit):
            return literal(decisions[0].value.text)
        if not decisions:
            seeded = self._seeded_literal(match.group(1), ctx)
            if seeded is not None:
                return literal(seeded.text)
        return None

    def _m_AC_SUBST(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """AC_SUBST: with a value argument, a literal subst or one resolved from the shell (else a
        `variable:` decision); the one-argument form only marks the variable as substituted, and
        the variables pass emits the check from the shell's assignments.
        """
        args = node.args
        var = args[0].strip()
        if len(args) > 1:
            value = args[1]
            if "$" in value or "`" in value:
                resolved = self._shell_value(value, ctx)
                if resolved is None:
                    decision = self._request(
                        f"variable:{var}",
                        "variable",
                        ctx,
                        f"AC_SUBST([{var}]) takes its value from shell: {value.strip()}",
                        {"value_expression": value.strip()},
                    )
                    if decision is None or decision.ignore:
                        ctx.dropped.add(var)
                    else:
                        self._apply_variable_decision(
                            var, "checks.AC_SUBST", decision, ctx, self._source_comment(node, ctx)
                        )
                    return []
                self._seed(ctx, var, str(resolved))
                return [Call("checks.AC_SUBST", [var, resolved])]
            self._seed(ctx, var, value.strip())
            return [Call("checks.AC_SUBST", [var, literal(value)])]
        # One-argument form: the value is whatever the shell assigned; the
        # variable pass emits the check from the assignments' decision list.
        ctx.explicit_substs[var] = self._source_comment(node, ctx)
        return []

    def _m_AC_CHECK_HEADERS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._plural(node, ctx, "macros.AC_CHECK_HEADERS", includes_index=3, comma=False)

    _m_AC_CHECK_HEADERS_ONCE = _m_AC_CHECK_HEADERS

    def _m_AC_CHECK_FUNCS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._plural(node, ctx, "macros.AC_CHECK_FUNCS", includes_index=None, comma=False)

    _m_AC_CHECK_FUNCS_ONCE = _m_AC_CHECK_FUNCS

    def _m_AC_REPLACE_FUNCS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """Port mode only: in project mode `_gnulib_project_macro` takes AC_REPLACE_FUNCS first."""
        items = self._plural(node, ctx, "macros.AC_CHECK_FUNCS", includes_index=None, comma=False)
        words = m4args.words(node.args[0] if node.args else "")
        self.report.note(
            f"{ctx.where}: AC_REPLACE_FUNCS([{' '.join(words)}]) AC_LIBOBJs the missing "
            "functions' sources; conditional sources are a project-level concern (autoconf_srcs)"
        )
        return items

    def _m_AC_CHECK_DECLS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._plural(node, ctx, "macros.AC_CHECK_DECLS", includes_index=3, comma=True)

    _m_AC_CHECK_DECLS_ONCE = _m_AC_CHECK_DECLS

    def _m_AC_CHECK_TYPES(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._plural(node, ctx, "macros.AC_CHECK_TYPES", includes_index=3, comma=True)

    def _m_AC_CHECK_MEMBERS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._plural(node, ctx, "macros.AC_CHECK_MEMBERS", includes_index=3, comma=True)

    def _plural(self, node: Node, ctx: _Ctx, fn: str, *, includes_index: int | None, comma: bool) -> list[CheckItem]:
        """The list macros (AC_CHECK_HEADERS, AC_CHECK_FUNCS, AC_CHECK_DECLS, ...): one
        `macros.X([...])` call over the literal list, minus the names a `check:` decision drops
        or a port already probes. Action arguments become defines where they are plain AC_DEFINEs
        over a single name, otherwise an `actions` decision. A computed list is a `macro`
        decision.
        """
        args = node.args + ["", "", "", ""]
        if not m4args.is_literal_list(args[0]):
            first = args[0].strip()
            decision = self._request(
                f"macro:{node.name}:{slug(first)}",
                "macro",
                ctx,
                f"{node.name} over a computed list: {first}",
                {"arguments": node.args},
                node=node,
            )
            if decision is None or decision.ignore:
                return []
            return list(decision.checks or [])
        names = [n.strip() for n in args[0].split(",")] if comma else m4args.words(args[0])
        names = [
            n
            for n in names
            if n
            and not self._dropped_by_decision("check", cache_var(node.name, n))
            and not self._dropped_by_decision("define", define_of(node.name, n))
        ]
        names = [
            n
            for n in names
            if not self._decided_check(node, cache_var(node.name, n), define_of(node.name, n), ctx)
            and not self._check_from_port(node, n, ctx)
        ]
        if not names:
            return []
        kwargs: dict[str, AttrValue] = {}
        if includes_index is not None and args[includes_index].strip():
            kwargs["includes"] = _includes_arg(args[includes_index])
        if ctx.language != "c":
            kwargs["language"] = ctx.language
        items: list[CheckItem] = [Call(fn, [names], kwargs, returns_list=True)]
        actions = [
            (label, args[i]) for label, i in (("action-if-found", 1), ("action-if-not-found", 2)) if args[i].strip()
        ]
        if actions:
            if len(names) == 1:
                cache = cache_var(node.name, names[0])
                defines, actions = self._action_defines(node, cache, actions)
                items += defines
                actions = self._action_list_appends(node, ctx, cache, actions)
                if _action_probe_cache(node):
                    actions = [(label, text) for label, text in actions if not pure_assignments(text)]
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _dropped_by_decision(self, kind: str, name: str) -> bool:
        """`define:<NAME>` / `check:<cache variable>` IGNORE: a check a
        dependency already provides (never requested, applied when present).
        A list check's member is matched by either name: `define:HAVE_NL_LANGINFO`
        and `check:ac_cv_func_nl_langinfo` both drop `nl_langinfo` from
        AC_CHECK_FUNCS, which is what the rules' duplicate-producer error names."""
        decision = self.decisions.optional(f"{kind}:{name}", kind)
        return decision is not None and decision.ignore

    def _decided_check(self, node: Node, var: str, define: str, ctx: _Ctx) -> bool:
        """A `probe:<cache variable>` decision answering a header, function, type or library
        check with a value: the value stands in for the probe, as it does for a cache check
        (`"no"` for a library the registry does not have, which configure would probe the host
        for), compared by value where the script tests it; `"yes"` also makes the check's
        define.  IGNORE drops the check, so conditions over it become decisions.  Never
        requested: a check without a decision is emitted as it is."""
        decision = self.decisions.optional(f"probe:{var}", "probe") if var else None
        if decision is None:
            return False
        comment = f"{self._source_comment(node, ctx)}: {var} (decisions file)"
        if decision.ignore:
            ctx.dropped.add(var)
            return True
        value = decision.value
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise dec.DecisionError(f'probe:{var}: a header, function or library check takes a value ("yes" / "no")')
        calls = [Call("checks.M4_VARIABLE", [var, value], comments=[comment])]
        if define and str(value) in df.YES:
            calls.append(Call("checks.AC_DEFINE", [define, 1], comments=[comment]))
        # Whatever the check ran under: the answer is a fact about the host the project is built
        # for, and the script's conditions over it carry the check's own condition already.
        self._place(calls, df.TRUE, ctx)
        return True

    def _check_from_port(self, node: Node, subject: str, ctx: _Ctx) -> bool:
        """Project mode: a `*_ONCE` check (or any check a port carries under its
        own target) becomes a dep on that port instead of a second probe."""
        if self.project is None or not self.project.flatten_files:
            return False  # only gl_INIT repeats the modules' own checks
        candidates = [
            f"{node.name}_{slug(subject)}",
            f"{node.name.replace('_ONCE', '')}_ONCE_{slug(subject)}",
            f"{node.name}_{slug(subject).upper()}",
            f"{node.name.replace('_ONCE', '')}_ONCE_{slug(subject).upper()}",
            cache_var(node.name, subject),
            define_of(node.name, subject),
        ]
        port = self._port_for_check(list(dict.fromkeys(candidates)))
        if port is None:
            return False
        self._add_port_dep(port, ctx, f"{self._source_comment(node, ctx)}: {subject} (the port's shared check)")
        return True

    def _singular(self, node: Node, ctx: _Ctx, fn: str, *, includes_index: int | None) -> list[CheckItem]:
        """The single-subject check macros (AC_CHECK_HEADER, AC_CHECK_FUNC, AC_CHECK_TYPE, ...):
        `checks.X(subject, ...)` with the define folded in from a plain AC_DEFINE action, the
        includes and the language; other actions become defines or an `actions` decision.
        """
        args = node.args + ["", "", "", ""]
        subject = m4args.quadrigraphs(args[0].strip())
        if (
            self._dropped_by_decision("check", cache_var(node.name, subject))
            or self._dropped_by_decision("define", define_of(node.name, subject))
            or self._decided_check(node, cache_var(node.name, subject), define_of(node.name, subject), ctx)
            or self._check_from_port(node, subject, ctx)
        ):
            return []
        kwargs: dict[str, AttrValue] = {}
        # A see-through check's actions are walked as nested calls (the AC_DEFINE among them,
        # under the check's result); folding it into `define` as well would define it twice.
        define = fold_define(args[1]) if not self._sees_through(node) else None
        if define:
            kwargs["define"] = define
            ctx.defines.add(define)
        if includes_index is not None and args[includes_index].strip():
            kwargs["includes"] = _includes_arg(args[includes_index])
        if ctx.language != "c":
            kwargs["language"] = ctx.language
        items: list[CheckItem] = [Call(fn, [subject], kwargs)]
        leftover = [
            (label, args[i])
            for label, i in (("action-if-found", 1), ("action-if-not-found", 2))
            if args[i].strip() and not (i == 1 and define)
        ]
        if leftover and self._sees_through(node):
            # The actions are the body's shell (see `_placeholder_text`): their assignments are in
            # the dataflow and the nested calls are translated here, under the check's result.
            ctx.probe_vars.add(cache_var(node.name, subject))
            self._walk(node.items, ctx)
            return items
        if leftover:
            cache = cache_var(node.name, subject)
            defines, leftover = self._action_defines(node, cache, leftover)
            items += defines
            leftover = self._action_list_appends(node, ctx, cache, leftover)
            if _action_probe_cache(node):
                # `needstrlcat=yes` in the not-found action: the dataflow saw it under the probe's result.
                leftover = [(label, text) for label, text in leftover if not pure_assignments(text)]
            if leftover:
                items += self._actions_decision(node, ctx, leftover)
        return items

    def _m_AC_CHECK_HEADER(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._singular(node, ctx, "checks.AC_CHECK_HEADER", includes_index=3)

    def _m_AC_CHECK_FUNC(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._singular(node, ctx, "checks.AC_CHECK_FUNC", includes_index=None)

    def _m_AC_CHECK_DECL(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._singular(node, ctx, "checks.AC_CHECK_DECL", includes_index=3)

    def _m_AC_CHECK_MEMBER(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._singular(node, ctx, "checks.AC_CHECK_MEMBER", includes_index=3)

    def _m_AC_CHECK_TYPE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """AC_CHECK_TYPE, including the obsolete two-argument form `AC_CHECK_TYPE([pid_t], [int])`,
        which defines the type to the fallback when it is missing.
        """
        args = node.args + ["", "", "", ""]
        default = args[1].strip()
        if (
            len(node.args) == 2
            and default
            and "(" not in default
            and "=" not in default
            and "$" not in default
            and not re.match(r"^[A-Z][A-Z0-9_]*$", default)
        ):
            # Obsolete two-argument form: AC_CHECK_TYPE([pid_t], [int]).
            type_name = args[0].strip()
            return [
                Call("checks.AC_CHECK_TYPE", [type_name]),
                Call(
                    "checks.AC_DEFINE",
                    [type_name, args[1].strip()],
                    {"requires": [f"!ac_cv_type_{cache_name(type_name)}"]},
                ),
            ]
        return self._singular(node, ctx, "checks.AC_CHECK_TYPE", includes_index=3)

    # -- language standards: a requirement, not a flag -------------------------
    #
    # configure switches the compiler into the mode a macro asks for (`-std=gnu99` appended to
    # CC) and records whether it could; only a script's own AC_MSG_ERROR stops it.  The
    # translation pins the mode on every target and check of the package (`pinned_standards`,
    # a compiler-family select until rules_cc names the standards as toolchain features) and
    # records the mode the compiler is then in with a checks.AC_LANG_STANDARD, which the
    # script's own test of `$ac_cv_prog_cc_c99` reads.

    def _set_standard(self, lang: str, standard: str) -> None:
        """A `CFLAGS="$CFLAGS -std=..."` of the script: the last one wins (configure appends in order)."""
        if lang == "CXX":
            self._state.cxx_standard = standard
        else:
            self._state.c_standard = standard

    def _standard(self, lang: str, standard: str, node: Node, pin: str) -> list[CheckItem]:
        """A macro that switches the compiler into `standard` mode (AC_PROG_CC_C99: `-std=gnu99`):
        the mode is pinned on the package (`pin`, as configure spells it) and the check records
        it, without stopping the build; autoconf's macro does not stop either, a script that
        wants to tests `$ac_cv_prog_cc_c99` itself (`_m_AC_MSG_ERROR`)."""
        if self.project is not None:
            self.project.required_standards.add(iso_standard(standard))
            self._pin_standard(lang, pin, node.name)
        self.report.note(f"{node.name}: every target and check compiles in {pin} mode, as configure would set CC")
        return [Call("checks.AC_LANG_STANDARD", [standard])]

    def _pin_standard(self, lang: str, pin: str, origin: str) -> None:
        """Record the standard a macro switches the compiler into: the first macro to do so wins
        (the next ones run under the first's CC).  A `-std=` of the script's CFLAGS or of a
        Makefile comes later on the compile line and overrides it (`sources._require_standards`)."""
        if lang not in self._state.pinned_standards:
            self._state.pinned_standards[lang] = pin
            self._state.standard_origins[lang] = origin

    def _cxx_standard(self, version: str, ext: str, mandatory: str, node: Node) -> list[CheckItem]:
        """AX_CXX_COMPILE_STDCXX: `checks.AX_CXX_COMPILE_STDCXX`, which defines HAVE_CXX<N> and, when
        mandatory, stops the build when the compiler is not in that mode; the mode is pinned on the
        package (`-std=gnu++N`, or `c++N` for `noext`), as the macro sets CXX."""
        ext = ext.strip() if ext.strip() in ("ext", "noext") else "ext"
        optional = mandatory.strip() == "optional"
        if self.project is not None:
            if not optional:
                self.project.required_standards.add(f"c++{version}")
            self._pin_standard("CXX", f"{'c' if ext == 'noext' else 'gnu'}++{version}", node.name)
        self.report.note(f"{node.name}: every target and check compiles in C++{version} mode, as the macro sets CXX")
        return [Call("checks.AX_CXX_COMPILE_STDCXX", [version, ext], {"mandatory": False} if optional else {})]

    def _m_AX_CXX_COMPILE_STDCXX(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", ""]
        return self._cxx_standard(args[0].strip() or "11", args[1], args[2], node)

    def _m_AX_CXX_COMPILE_STDCXX_11(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", ""]
        return self._cxx_standard("11", args[0], args[1], node)

    def _m_AX_CXX_COMPILE_STDCXX_14(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", ""]
        return self._cxx_standard("14", args[0], args[1], node)

    def _m_AX_CXX_COMPILE_STDCXX_17(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", ""]
        return self._cxx_standard("17", args[0], args[1], node)

    def _m_AC_PROG_CC_C99(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._standard("C", "c99", node, "gnu99")

    def _m_AC_PROG_CC_STDC(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """ISO C, which autoconf 2.69 takes to be C99 (falling back to C89); `ac_cv_prog_cc_stdc`."""
        return self._standard("C", "c99", node, "gnu99")

    def _m_AC_CHECK_SIZEOF(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", ""]
        kwargs: dict[str, AttrValue] = {}
        if args[2].strip():
            kwargs["includes"] = _includes_arg(args[2])
        # The rules define nothing unless asked; autoconf always defines SIZEOF_<TYPE>.
        kwargs["define"] = f"SIZEOF_{cache_name(args[0].strip()).upper()}"
        return [Call("checks.AC_CHECK_SIZEOF", [args[0].strip()], kwargs)]

    def _m_AC_CHECK_ALIGNOF(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", ""]
        kwargs: dict[str, AttrValue] = {}
        if args[1].strip():
            kwargs["includes"] = _includes_arg(args[1])
        kwargs["define"] = f"ALIGNOF_{cache_name(args[0].strip()).upper()}"
        return [Call("checks.AC_CHECK_ALIGNOF", [args[0].strip()], kwargs)]

    def _m_AC_COMPUTE_INT(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", "", ""]
        kwargs: dict[str, AttrValue] = {}
        if args[2].strip():
            kwargs["includes"] = _includes_arg(args[2])
        call = Call("checks.AC_COMPUTE_INT", [args[0].strip(), m4args.quadrigraphs(args[1].strip())], kwargs)
        call.comments.append(
            f"{args[0].strip()} is a shell variable in the m4; consumers of it need a define or subst of that name"
        )
        return [call]

    def _registry_library(self, lib: str, what: str) -> bool:
        """Project mode, a library the registry provides: the module becomes a bazel_dep, its
        target a `cc_deps` entry of the main autoconf target (so the probe compiles against its
        headers and links its archive, as configure does with the library installed) and a dep of
        everything the project links, as LIBS would.  The check itself stays a real probe."""
        found = libraries.known(lib)
        if found is None or self.project is None:
            return False
        if found.module == self.project.module_name():
            return False  # readline's configure probes -lreadline: the project itself, not a dependency
        decision = self.decisions.optional(f"library:{self.project.package_path}:{lib}", "library")
        if decision is not None and decision.ignore:
            self.report.note(
                f"{what}: -l{lib} left out (library:{self.project.package_path}:{lib} is IGNORE); "
                "the project builds as on a host without the library"
            )
            return False
        if lib not in self.project.configure_libs:
            self.project.external_modules[found.module] = found.version
            self.project.configure_libs[lib] = found.label
            self.report.note(
                f"{what}: -l{lib} is the registry module {found.module} ({found.label}): declared in "
                "MODULE.bazel, probed through the main target's cc_deps and linked by the project's "
                "targets"
            )
        return True

    def _lib_linkflags(self, node: Node, name: str) -> list[CheckItem]:
        """gettext's `AC_LIB_HAVE_LINKFLAGS([ssl], [crypto], [#include <openssl/ssl.h>], [SSL_new(0)])`
        (lib-link.m4): a link probe against `-lssl -lcrypto` that defines `HAVE_LIBSSL` and
        substitutes `HAVE_LIBSSL` (yes/no), `LIBSSL` and `LTLIBSSL` (the link words, empty when
        missing); `AC_LIB_LINKFLAGS` is the same without the probe (the library is required).
        The libraries go through the registry lookup, as `AC_CHECK_LIB`'s do.  The macro's own
        `--with-lib<name>-prefix` option and search over `$prefix` are a host's business."""
        args = node.args + ["", "", "", ""]
        lib = args[0].strip()
        deps = m4args.words(args[1])
        upper = re.sub(r"[^A-Za-z0-9_]", "_", lib).upper()
        words = " ".join(f"-l{word}" for word in [lib, *deps])
        for word in [lib, *deps]:
            self._registry_library(word, f"{name}([{lib}])")
        out: list[CheckItem] = []
        if name == "AC_LIB_HAVE_LINKFLAGS":
            cache = f"ac_cv_lib{re.sub(r'[^A-Za-z0-9_]', '_', lib)}"
            includes = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote_level(args[2])))
            body = m4args.quadrigraphs(m4args.unquote_level(args[3])).strip() or "return 0;"
            code = "\n".join([*includes, "int main(void) {", f"  {body}", "  return 0;", "}"])
            out.append(
                Call(
                    "checks.AC_TRY_LINK",
                    [],
                    {"name": cache, "code": code, "linkopts": words.split(), "define": f"HAVE_LIB{upper}"},
                    comments=[f"lib-link.m4: {name}([{lib}]{', [' + ' '.join(deps) + ']' if deps else ''})"],
                )
            )
            for subst, if_true, if_false in (
                (f"HAVE_LIB{upper}", "yes", "no"),
                (f"LIB{upper}", words, ""),
                (f"LTLIB{upper}", words, ""),
            ):
                out.append(
                    Call("checks.AC_SUBST", [subst], {"condition": cache, "if_true": if_true, "if_false": if_false})
                )
        else:
            out += [
                Call("checks.AC_SUBST", [f"LIB{upper}", words]),
                Call("checks.AC_SUBST", [f"LTLIB{upper}", words]),
                Call("checks.AC_SUBST", [f"LIB{upper}_PREFIX", ""]),
            ]
        return out

    def _m_AC_CHECK_LIB(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", "", "", ""]
        lib, function = args[0].strip(), args[1].strip()
        cache = f"ac_cv_lib_{cache_name(lib)}_{cache_name(function)}"
        if self._decided_check(node, cache, f"HAVE_LIB{cache_name(lib).upper()}", ctx):
            return []
        registry = self._registry_library(lib, f"AC_CHECK_LIB([{lib}], [{function}])")
        items: list[CheckItem] = [Call("checks.AC_CHECK_LIB", [lib, function])]
        if self.project is not None and not registry and not args[2].strip():
            # The default action-if-found is `LIBS="-l<lib> $LIBS"`: a system library (not a
            # registry module, which would be a dep of every target) reaches every link as a
            # substitution the shared linkopts target carries.  The check's own result is its
            # success, so a sibling substitution spells the flag.
            items.append(
                Call(
                    "checks.AC_SUBST",
                    [self._link_var(f"{cache}_LIBS")],
                    {"condition": cache, "if_true": f"-l{lib}", "if_false": ""},
                )
            )
        actions = [
            (label, args[i]) for label, i in (("action-if-found", 2), ("action-if-not-found", 3)) if args[i].strip()
        ]
        if actions:
            defines, actions = self._action_defines(node, cache, actions)
            items += defines
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _link_var(self, name: str) -> str:
        """Register `name` as a substitution every target links through `:linkopts` (LIBS)."""
        if name not in self._state.configure_link_vars:
            self._state.configure_link_vars.append(name)
        return name

    def _m_AC_SEARCH_LIBS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", "", "", ""]
        function, candidates = args[0].strip(), m4args.words(args[1])
        registry = [
            lib
            for lib in candidates
            if self._registry_library(lib, f"AC_SEARCH_LIBS([{function}], [{' '.join(candidates)}])")
        ]
        kwargs: dict[str, AttrValue] = {}
        if self.project is not None and candidates and not registry:
            # A search that finds the function in a library prepends `-l<lib>` to LIBS, whatever
            # its actions: for system libraries (a registry module would be a dep of every
            # target) the check's value, `-l<lib>` or empty, is a substitution the shared
            # linkopts target carries to every link.
            kwargs["subst"] = self._link_var(f"ac_cv_search_{cache_name(function)}_LIBS")
        items: list[CheckItem] = [Call("checks.AC_SEARCH_LIBS", [function, candidates], kwargs)]
        actions = [
            (label, args[i]) for label, i in (("action-if-found", 2), ("action-if-not-found", 3)) if args[i].strip()
        ]
        if actions and self._sees_through(node):
            # As `_singular`: the actions are the body's shell, their nested calls (tftp-hpa's
            # AC_LIBOBJ fallback) translated here under the check's result.
            ctx.probe_vars.add(f"ac_cv_search_{cache_name(args[0])}")
            self._walk(node.items, ctx)
            return items
        if actions:
            defines, actions = self._action_defines(node, f"ac_cv_search_{cache_name(args[0])}", actions)
            items += defines
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _m_AC_PATH_PROGS_FEATURE_CHECK(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """AC_PATH_PROGS_FEATURE_CHECK(VAR, [progs], feature-test, ...): sets ac_cv_path_VAR
        (configure.ac substitutes or defines from it); the last, plainest candidate stands in."""
        var, value = _program_lookup_value(node.name, node.args)
        call = Call("checks.M4_VARIABLE", [var, value])
        call.comments.append(f"{node.name}: program lookups are not modeled; the plain program name stands in")
        return [call]

    def _m_AC_CHECK_PROG(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", "", ""]
        var, value = args[0].strip(), (args[2] or args[1]).strip()
        call = Call("checks.AC_SUBST", [var, value])
        call.comments.append(f"{node.name}: program lookups are not modeled; the value-if-found is substituted")
        return [call]

    def _m_AC_CHECK_TOOL(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """AC_CHECK_TOOL / AC_PATH_PROG(VAR, prog, [value-if-not-found], [path]): unlike
        AC_CHECK_PROG, the third argument is the *not-found* value (libtool's
        `AC_CHECK_TOOL(OBJDUMP, objdump, false)`), so the program itself is substituted."""
        args = node.args + ["", "", "", ""]
        var, program = args[0].strip(), args[1].strip()
        call = Call("checks.AC_SUBST", [var, program])
        call.comments.append(f"{node.name}: program lookups are not modeled; the program name is substituted")
        return [call]

    _m_AC_PATH_PROG = _m_AC_CHECK_TOOL
    _m_AC_PATH_TOOL = _m_AC_CHECK_TOOL

    def _m_AC_CHECK_PROGS(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        args = node.args + ["", "", "", ""]
        var, programs = args[0].strip(), m4args.words(args[1])
        call = Call("checks.AC_SUBST", [var, programs[0] if programs else args[2].strip()])
        call.comments.append(f"{node.name}: program lookups are not modeled; the first candidate is substituted")
        return [call]

    _m_AC_PATH_PROGS = _m_AC_CHECK_PROGS

    def _probe(self, node: Node, ctx: _Ctx, fn: str, note: str | None = None) -> list[CheckItem]:
        """AC_COMPILE_IFELSE / AC_LINK_IFELSE / AC_PREPROC_IFELSE and the AC_TRY_* forms: a
        `checks.AC_TRY_COMPILE` / `AC_TRY_LINK` named after the enclosing cache variable, the
        action variable, or a synthesized probe name; the program keeps its AC_LANG_PROGRAM parts
        when it was written that way. Canonical yes/no actions bind the cache variable; other
        actions become defines or decisions.
        """
        args = node.args + ["", "", ""]
        program = args[0]
        kwargs: dict[str, AttrValue] = {}
        yes, no = (self._probe_action(a, node, ctx) for a in args[1:3])
        if _compiler_option_test(node):
            # libpcap's AC_LBL_CHECK_COMPILER_OPT: an empty program compiled with a candidate
            # option in CFLAGS tests the option, not the code; Bazel's toolchain owns the options.
            var = ctx.cache or action_var(yes) or action_var(no)
            self.report.note(
                f"{ctx.where}: {node.name} compiles an empty program: a compiler-option test "
                "(the CFLAGS of the moment), which the toolchain decides; left out"
                + (f', {var} reads "no"' if var else "")
            )
            return []
        var = ctx.cache or action_var(yes) or self._anonymous_name(node, ctx, "probe")
        kwargs["name"] = var
        lang_program = m4args.lang_program(program)
        if lang_program is not None:
            prologue = m4args.quadrigraphs(lang_program.prologue)
            body = m4args.quadrigraphs(lang_program.body)
            kwargs["code"] = Call("utils.AC_LANG_PROGRAM", [block_lines(prologue), block_lines(body)])
        else:
            kwargs["code"] = node.program() or m4args.quadrigraphs(program)
        if ctx.language != "c":
            kwargs["language"] = ctx.language
        call = Call(fn, [], kwargs)
        if note:
            call.comments.append(note)
        return self._probe_actions(node, ctx, call, var, yes, no, program)

    def _probe_actions(
        self, node: Node, ctx: _Ctx, call: Call, var: str, yes: str, no: str, program: str
    ) -> list[CheckItem]:
        """The checks a probe `call` named `var` and its two actions amount to: the probe alone for
        canonical yes/no actions (its cache variable bound), with a `define =` or trailing defines
        for AC_DEFINE actions, with a conditional subst when both arms substitute one name; anything
        else makes the probe a `probe:` decision.  May rename the probe after its action variable."""
        kwargs = call.kwargs
        # AC_MSG_RESULT(yes) / AC_MSG_RESULT(no) around the real action is
        # configure's progress output, not a result.
        yes, no = strip_messages(yes), strip_messages(no)
        # `[AC_DEFINE(HAVE_VA_COPY)], [AC_COMPILE_IFELSE(... __va_copy ...)]`: the
        # not-found branch is another probe, translated as its own node.
        nested = m4args.parse_call(no)
        if nested is not None and nested.name in PROBES:
            no = ""
        renamed = action_var(yes)
        if not ctx.cache and renamed and renamed != var:
            var = kwargs["name"] = renamed  # `AC_MSG_RESULT([yes]) mpfr_pthread_ok=yes`
        canonical = yes == f"{var}=yes" and no == f"{var}=no"
        if canonical or (not yes and not no) or (yes == f"{var}=yes" and not no):
            ctx.probe_vars.add(var)
            return [call]
        define = fold_define(yes)
        if define and not no:
            kwargs["define"] = define
            ctx.defines.add(define)
            ctx.probe_vars.add(var)
            return [call]
        defines = action_defines(yes)
        if defines and not no:
            # `AC_DEFINE([mpfr_float128], [__float128])`: a define with a value other than 1 (which
            # `fold_define` above does not fold): the probe, then the define on its success.
            ctx.probe_vars.add(var)
            out: list[CheckItem] = [call]
            for name, value in defines:
                out.append(
                    Call(
                        "checks.AC_DEFINE",
                        [name],
                        {"value": value, "requires": [var]} if value != "1" else {"requires": [var]},
                    )
                )
                ctx.defines.add(name)
            return out
        substs = (action_subst(yes), action_subst(no))
        if substs[0] and substs[1] and substs[0][0] == substs[1][0]:
            # mpc's `AC_LINK_IFELSE([...], [AC_SUBST([MPC_HAVE_COMPLEX_H], [1])], [AC_SUBST([...], [0])])`:
            # the two arms substitute one name to constants, which is one conditional
            # substitution over the probe.
            ctx.probe_vars.add(var)
            name_, (if_true, if_false) = substs[0][0], (substs[0][1], substs[1][1])
            return [
                call,
                Call(
                    "checks.AC_SUBST",
                    [name_],
                    {"condition": var, "if_true": literal(if_true), "if_false": literal(if_false)},
                    comments=[f"{node.name}: both arms substitute {name_}"],
                ),
            ]
        # The actions do something other than record the result: the probe's
        # outcome is not the cache value.
        context = {
            "program": block_lines(node.program() or m4args.quadrigraphs(program)),
            "action_if_true": yes,
            "action_if_false": no,
        }
        if yes == f"{var}=no" and no == f"{var}=yes":
            context["note"] = f"inverted: {var} is yes when the program does NOT compile/link"
        decision = self._request(
            f"probe:{var}",
            "probe",
            ctx,
            f"{node.name}: the actions decide {var}, not the probe's success",
            context,
            node=node,
        )
        if decision is None or decision.ignore:
            ctx.dropped.add(var)
            return []
        if decision.accept:
            ctx.probe_vars.add(var)
            return [call]
        self._apply_probe_decision(var, decision, ctx, self._guard(node, ctx), self._source_comment(node, ctx))
        return []

    @staticmethod
    def _probe_action(text: str, node: Node, ctx: _Ctx) -> str:
        """A probe's action argument as shell: m4sh forms spelled out (`AS_VAR_SET([cache], [yes])` is
        `cache=yes`), and the enclosing cache check's AS_VAR_PUSHDEF alias replaced by the variable it
        stands for (nasm's PA_HAVE_FUNC), so the canonical `var=yes` / `var=no` actions are seen as such."""
        text = m4args.m4sh_to_shell(text).strip()
        cache = node.enclosing("AC_CACHE_CHECK", "AC_CACHE_VAL") if ctx.cache else None
        if cache is not None:
            padded = cache.args + ["", ""]
            alias = (padded[1] if cache.name == "AC_CACHE_CHECK" else padded[0]).strip()
            if alias and alias != ctx.cache and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
                text = re.sub(rf"(?<![A-Za-z0-9_$]){re.escape(alias)}=", f"{ctx.cache}=", text)
        return text

    def _m_AC_COMPILE_IFELSE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._probe(node, ctx, "checks.AC_TRY_COMPILE")

    def _m_AC_LINK_IFELSE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._probe(node, ctx, "checks.AC_TRY_LINK")

    def _m_AC_PREPROC_IFELSE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        return self._probe(
            node, ctx, "checks.AC_TRY_COMPILE", note="AC_PREPROC_IFELSE in the m4: modeled as a compile probe"
        )

    def _m_AC_RUN_IFELSE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """Runtime probes are not modeled: the cross-compiling branch decides.

        The m4's own `case $host_os` guesses become `M4_VARIABLE` values per
        platform (1 for "yes", 0 for "no"); the program stays in a comment so
        the reviewer sees what the guess stands for.  Without a usable guess,
        or with one blanket guess for every platform, the value is a decision.
        """
        args = node.args + ["", "", "", ""]
        var = ctx.cache or action_var(args[1].strip())
        program = node.program() or m4args.quadrigraphs(args[0])
        cross = args[3]
        comment = self._source_comment(node, ctx)
        guard = self._guard(node, ctx)
        context = {
            "program": block_lines(program),
            "action_if_true": args[1].strip(),
            "action_if_false": args[2].strip(),
        }
        if not var:
            # No cache variable: a decision can still stand in for the probe (the checks of its
            # actions, mpfr's MPFR_USE_THREAD_SAFE defines for the TLS run test).
            name = self._anonymous_name(node, ctx, "run")
            decision = self._request(
                f"probe:{ctx.target.name}:{name}",
                "probe",
                ctx,
                "AC_RUN_IFELSE without a cache variable: runtime probes are not modeled",
                context,
                node=node,
            )
            if decision is not None and not decision.ignore:
                self._apply_probe_decision(name, decision, ctx, guard, comment)
            return []
        cross_shell = m4args.quadrigraphs(m4args.strip_m4(cross))
        decisions = df.Evaluator().run(sh.parse(cross_shell)).decisions(var) if cross.strip() else []
        whole = df.disj(*(d.cond for d in decisions)) if decisions else None
        blanket = whole is not None and not df.flag_atoms(whole) and not lower.platform_atoms(whole)
        if not decisions or blanket:
            # No guess, or one guess for every platform (gawk's `has_a_format=no
            # dnl Cross-compiling, assuming the worst`): the reviewer decides.
            context["cross_compiling"] = block_lines(cross_shell)
            decision = self._request(
                f"probe:{var}",
                "probe",
                ctx,
                f"AC_RUN_IFELSE sets {var} at run time; its cross-compiling branch "
                + ("guesses the same value on every platform" if blanket else "gives no value"),
                context,
                node=node,
            )
            if decision is None or decision.ignore:
                ctx.dropped.add(var)
            else:
                self._apply_probe_decision(var, decision, ctx, guard, comment)
            return []
        ctx.probe_vars.add(var)
        provenance = [f"{var}: the m4 runs a program; the value is its cross-compiling guess per platform:"]
        provenance += ["  " + line for line in block_lines(cross_shell)]
        self._decision_checks(var, decisions, ctx, kind="checks.M4_VARIABLE", guess=True, comment="\n".join(provenance))
        return []

    def _m_AC_EGREP_CPP(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """`AC_EGREP_CPP(pattern, program, yes, no)` greps the preprocessed program.

        The gnulib shape `#if !defined X / token / #endif ...` (the token
        appears iff some macro is undefined) becomes a compile probe of
        `int e[] = {X, ...};`; anything else is a decision.
        """
        args = node.args + ["", "", "", ""]
        pattern, program = args[0].strip(), m4args.quadrigraphs(args[1])
        yes, no = args[2].strip(), args[3].strip()
        var = ctx.cache or action_var(yes) or action_var(no)
        blocks = _EGREP_BLOCK_RE.findall(program)
        macros = [name for name, token in blocks if token == pattern]
        rest = _EGREP_BLOCK_RE.sub("", program)
        comment = self._source_comment(node, ctx)
        if var and macros and len(macros) == len(blocks) and pattern not in rest:
            # Found (some macro undefined) <=> the array does not compile.
            found_sets_no = yes == f"{var}=no" and (no == f"{var}=yes" or not no)
            includes = [line.strip() for line in rest.splitlines() if line.strip().startswith("#")]
            if found_sets_no:
                ctx.probe_vars.add(var)
                call = Call(
                    "checks.AC_TRY_COMPILE",
                    [],
                    {
                        "name": var,
                        "code": f"int e[] = {{{', '.join(macros)}}}; (void)e;",
                        "includes": includes,
                    },
                )
                call.comments.append(
                    f"{node.name}([{pattern}]) finds the token when one of the macros is "
                    "undefined: compiled as one array of them"
                )
                return [call]
        if var and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern) and not blocks:
            found_sets_yes = yes == f"{var}=yes" and (no == f"{var}=no" or not no)
            if found_sets_yes:
                # The token appears in the preprocessed program when the
                # headers declare it: close enough to "it compiles as a name".
                ctx.probe_vars.add(var)
                is_type = pattern.endswith("_t")
                body = f"{pattern} probe_var; (void) probe_var;" if is_type else f"(void) {pattern};"
                call = Call(
                    "checks.AC_TRY_COMPILE",
                    [],
                    {
                        "name": var,
                        "code": Call("utils.AC_LANG_PROGRAM", [block_lines(program), [body]]),
                    },
                )
                call.comments.append(
                    f"{node.name}([{pattern}]) in the m4: the preprocessed program mentions the "
                    f"name; compiled here as a {'type' if is_type else 'declaration'} use instead"
                )
                return [call]
        decision = self._request(
            f"probe:{var or f'{ctx.target.name}:egrep_{slug(pattern)}'}",
            "probe",
            ctx,
            f"{node.name}([{pattern}]) greps preprocessor output; needs a compile-time probe",
            {"pattern": pattern, "program": block_lines(program), "action_if_found": yes, "action_if_not_found": no},
            node=node,
        )
        if var and (decision is None or decision.ignore):
            ctx.dropped.add(var)
        elif decision is not None and var:
            self._apply_probe_decision(var, decision, ctx, self._guard(node, ctx), comment)
        return []

    _m_AC_EGREP_HEADER = _m_AC_EGREP_CPP

    def _m_AC_C_BIGENDIAN(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """AC_C_BIGENDIAN([big], [little], [unknown], [universal]): the port (WORDS_BIGENDIAN), and
        the actions when each is one AC_DEFINE (mpfr's HAVE_BIG_ENDIAN / HAVE_LITTLE_ENDIAN), as
        defines on WORDS_BIGENDIAN."""
        resolution = self.index.resolve("AC_C_BIGENDIAN", node.loc_file) if self.index else None
        if resolution is not None:
            self._add_port_dep(resolution, ctx, self._source_comment(node, ctx))
        out: list[CheckItem] = []
        args = node.args + ["", "", "", ""]
        for action, requirement in ((args[0], "WORDS_BIGENDIAN"), (args[1], "!WORDS_BIGENDIAN")):
            action = strip_messages(action)
            if not action or action in ("true", ":"):
                continue
            defines = action_defines(action)
            if not defines:
                self.report.note(
                    f"{ctx.where}: AC_C_BIGENDIAN action `{action.splitlines()[0][:60]}` is not an "
                    "AC_DEFINE; not modeled"
                )
                continue
            for name, value in defines:
                kwargs: dict[str, AttrValue] = {"requires": [requirement]}
                if value != "1":
                    kwargs["value"] = literal(value)
                out.append(
                    Call(
                        "checks.AC_DEFINE",
                        [name],
                        kwargs,
                        comments=[
                            f"AC_C_BIGENDIAN: on a {'big' if requirement == 'WORDS_BIGENDIAN' else 'little'}-endian host"
                        ],
                    )
                )
                ctx.defines.add(name)
        return out

    def _m_AC_LIBOBJ(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        self.report.note(
            f"{ctx.where}: AC_LIBOBJ([{node.args[0] if node.args else ''}]) is a conditional "
            "source; a project-level concern (autoconf_srcs)"
        )
        return []

    def _m_AC_MSG_ERROR(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        """`if test "$ac_cv_prog_cc_c99" = no; then AC_MSG_ERROR(...)`: the script's own stop when
        the compiler is not in the mode it needs, the one fatal part of a standard macro: the
        standard's check gets the message as `error`.  Any other abort is reported, not modeled."""
        guard = self._guard(node, ctx)
        message = m4args.strip_m4(node.args[0]).strip().split("\n")[0] if node.args else ""
        if self._standard_abort(guard, message, ctx.target):
            return []
        self.report.note(
            f"{ctx.where}: configure aborts with AC_MSG_ERROR({message}) when {describe(guard)}; not modeled"
        )
        return []

    def _standard_abort(self, guard: df.Cond, message: str, target: Target) -> bool:
        """An abort under a standard check's failure (`!ac_cv_std_c99`): the check gets `message` as
        its `error`, the one fatal part of AC_PROG_CC_C99 and friends.  False for any other abort."""
        if not (isinstance(guard, df.Not) and isinstance(guard.cond, df.Truthy)):
            return False
        if not guard.cond.name.startswith("ac_cv_std_"):
            return False
        standard = guard.cond.name[len("ac_cv_std_") :]
        for items in [target.checks, *target.arm_lists]:
            for item in items:
                if isinstance(item, Call) and item.fn == "checks.AC_LANG_STANDARD" and item.args[:1] == [standard]:
                    item.kwargs["error"] = message or True
                    return True
        target.checks.append(Call("checks.AC_LANG_STANDARD", [standard], {"error": message or True}))
        return True

    def _script_aborts(self, analysis: df.Analysis, target: Target) -> None:
        """The body's own `AC_MSG_ERROR`s the expansion spelled as `as_fn_error` (`_analyze` records
        them with their path): the ones under a standard check's failure become the check's
        `error`; the rest only bound the dataflow's paths (`fatal`)."""
        for path, message in analysis.aborts:
            self._standard_abort(df.substitute_unset(path, self._never_set), message, target)

    _m_AC_MSG_FAILURE = _m_AC_MSG_ERROR

    def _m_AC_ARG_ENABLE(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        self.report.note(
            f"{ctx.where}: {node.name}([{node.args[0] if node.args else ''}]) is a configure "
            "option; modeled in project mode only"
        )
        return []

    _m_AC_ARG_WITH = _m_AC_ARG_ENABLE

    def _m_AM_CONDITIONAL(self, node: Node, ctx: _Ctx) -> list[CheckItem]:
        self.report.note(
            f"{ctx.where}: AM_CONDITIONAL([{node.args[0] if node.args else ''}]) is an automake "
            "conditional; modeled in project mode only"
        )
        return []

    # -- variables assigned by the shell ---------------------------------------

    def _variables(self, ctx: _Ctx) -> None:
        """Emit the substituted/tracked variables the body assigns."""
        for var in ctx.analysis.order:
            if var in ctx.probe_vars or var in ctx.defines:
                continue
            if (
                self.project is not None
                and _STDC_INDICATOR_RE.match(var)
                and any(
                    d.cond is df.TRUE and isinstance(d.value, df.Lit) and d.value.text == "1"
                    for d in ctx.analysis.decisions(var)
                )
            ):
                # gnulib-comp.m4's `GL_STDC_BIT_WIDTH=1`: a module indicator of stdbit.in.h,
                # assigned directly (no gl_MODULE_INDICATOR_SET_VARIABLE); gl_STDBIT_H substitutes it.
                if var not in self.project.indicator_substs:
                    self.project.indicator_substs.append(var)
                continue
            explicit = ctx.explicit_substs.get(var)
            if explicit is None and not (ctx.auto_variables and _ALLCAPS_RE.match(var)):
                continue  # a local temporary (or configure.ac boilerplate)
            if explicit is not None:
                kind, comment = "checks.AC_SUBST", explicit
            elif var.startswith("GL_GENERATE_"):
                # Consumed by other modules' shell (gl_CONDITIONAL_HEADER
                # substitutes it later, from gl_INIT): tracked, not substituted.
                kind = "checks.M4_VARIABLE"
                comment = f"{var}: shell variable of the m4 that other modules branch on"
            else:
                kind = "checks.AC_SUBST"
                comment = f"{var} is assigned by the shell of the m4 (an AC_SUBST'd gnulib default)"
            decisions = ctx.analysis.decisions(var)
            if self.project is not None and explicit is not None and _TARGET_LIST_RE.match(var):
                # `bin_PROGRAMS` computed by configure: the program list, not a subst.
                self._program_list(var, decisions, ctx)
                continue
            if self.project is not None and explicit is not None:
                words = self._source_words(var, ctx)
                if words:
                    # libpcap's PLATFORM_C_SRC / MODULE_C_SRC: a list of sources configure
                    # computes, which the Makefile compiles; each file under its condition.
                    self.project.subst_sources[var] = words
                    continue
            if var not in ctx.probe_vars and any(df.mentions(d.cond, var) for d in decisions):
                # `test -n "$X" || X=default`: X before the body assigns it is
                # the configure environment, which a Bazel build does not have.
                decisions = [df.Assignment(df.substitute_empty(d.cond, var), d.value, d.text) for d in decisions]
                comment += f"; {var} is unset before the m4 assigns it (no configure environment)"
            decisions = [
                df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text) for d in decisions
            ]
            self._decision_checks(var, decisions, ctx, kind=kind, comment=comment)
        for var, comment in ctx.explicit_substs.items():
            if var not in ctx.analysis.assigns and var not in ctx.probe_vars and var in ctx.analysis.inherited:
                # Assigned only inside a macro the body expands (nasm's PA_ARG_ENABLED([pdf-compression],
                # ..., [PDFOPT='-nocompress']) assigns through PA_ARG_BOOL): the macro's assignments, under
                # the call's condition; a `variable:` decision in the file stands for them.
                decisions = [
                    df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text)
                    for d in ctx.analysis.inherited[var]
                ]
                inherited_comment = f"{comment}; assigned in a macro the body expands"
                if self.decisions.lookup(f"variable:{var}") is not None:
                    self._variable_request(
                        var,
                        "checks.AC_SUBST",
                        ctx,
                        inherited_comment,
                        f"{var}: assigned in a macro the body expands",
                        decisions,
                    )
                else:
                    self._decision_checks(var, decisions, ctx, kind="checks.AC_SUBST", comment=inherited_comment)
                continue
            if var not in ctx.analysis.assigns and var not in ctx.probe_vars:
                decision = self._request(
                    f"variable:{var}",
                    "variable",
                    ctx,
                    f"{comment}: {var} is never assigned in this body; its value is not known here",
                    {},
                    macro=("AC_SUBST", var),
                )
                if decision is None or decision.ignore:
                    ctx.dropped.add(var)
                else:
                    self._apply_variable_decision(var, "checks.AC_SUBST", decision, ctx, comment)

    def _program_list(self, var: str, decisions: list[df.Assignment], ctx: _Ctx) -> None:
        """The programs configure puts in a substituted `*_PROGRAMS` variable, with their conditions.

        coreutils: ``bin_PROGRAMS=`for p in $optional_bin_progs; do echo src/"$p"'$(EXEEXT)'; done` `` lists
        the words of `optional_bin_progs`, which the body appends to (`gl_ADD_PROG`) unconditionally, under
        `if test $ac_cv_sys_posix_termios = yes`, or in a probe's action; `pkglibexec_PROGRAMS='src/libstdbuf.so'`
        under a `case` lists a literal.  A backtick other than that loop (`gl_REMOVE_PROG`) is not followed.
        """
        entries: list[ListedProgram] = []
        for assignment in decisions:
            value = _assignment_value(assignment.text, var)
            if value is None:
                continue
            loop = _FOR_LIST_RE.search(value)
            if loop:
                entries += self._list_words(loop.group(1), ctx, assignment.cond, depth=0)
            elif "$" in value or "`" in value:
                if not re.search(rf"\$\{{?{re.escape(var)}\b", value):  # X=`echo $X` normalizes whitespace
                    self.report.note(
                        f"{var}: `{assignment.text.strip()}` is not followed; the programs it names are unknown"
                    )
            else:
                entries += [ListedProgram(_program_word(w), assignment.cond, f"{var}={value}") for w in value.split()]
        merged: dict[str, ListedProgram] = {}
        for entry in entries:
            if entry.cond is df.FALSE or not entry.name:
                continue
            seen = merged.get(entry.name)
            merged[entry.name] = (
                entry if seen is None else ListedProgram(entry.name, df.disj(seen.cond, entry.cond), seen.source)
            )
        self._state.program_lists[var] = list(merged.values())

    def _source_words(self, var: str, ctx: _Ctx) -> list[tuple[str, df.Cond]] | None:
        """`(file, condition)` for a substituted variable whose every literal assignment or append is
        a list of source or object files; None for any other variable."""
        out: list[tuple[str, df.Cond]] = []
        decisions = ctx.analysis.decisions(var)
        if not decisions:
            return None
        for assignment in decisions:
            value = _assignment_value(assignment.text, var)
            if value is None:
                return None
            rest = _without_self_reference(value, var)
            if "$" in rest or "`" in rest:
                return None
            cond = df.substitute_empty(assignment.cond, var)
            for word in rest.split():
                if not word.endswith((".c", ".cc", ".cpp", ".cxx", ".o", ".lo")):
                    return None
                out.append((word, cond))
        return out or None

    def _list_words(self, listvar: str, ctx: _Ctx, outer: df.Cond, depth: int) -> list[ListedProgram]:
        """The words the body and the probe actions put in shell list `listvar`, each under its condition
        (the list's own emptiness test in `gl_ADD_PROG` substituted away)."""
        out: list[ListedProgram] = []
        for assignment in ctx.analysis.decisions(listvar):
            value = _assignment_value(assignment.text, listvar)
            if value is None:
                continue
            cond = df.conj(outer, df.substitute_empty(assignment.cond, listvar))
            if cond is df.FALSE:
                continue
            loop = _FOR_LIST_RE.search(value)
            if loop and loop.group(1) != listvar and depth < 3:
                out += self._list_words(loop.group(1), ctx, cond, depth + 1)
                continue
            rest = _without_self_reference(value, listvar)
            if "$" in rest or "`" in rest:
                continue  # gl_REMOVE_PROG's filter, or another computed value
            out += [ListedProgram(_program_word(w), cond, assignment.text.strip()) for w in rest.split()]
        for word, cond, where in self._state.shell_list_appends.get(listvar, []):
            out.append(ListedProgram(_program_word(word), df.conj(outer, cond), where))
        return out

    def _validate_conditions(self, ctx: _Ctx) -> None:
        """Checks whose `requires` / `condition` name a variable no check
        produces are turned into guard decisions.

        A name is known to be unproducible when it is a shell variable of the
        configure environment (`cross_compiling`, `CXX`, lower-case temporaries
        that are not cache variables) or one this body assigns without the
        assignment having become a check.  ALLCAPS and `*_cv_*` names the body
        never assigns are left alone: they come from dependencies.
        """
        # `subst:X: IGNORE`: a substitution a dependency already provides
        # (configure.ac's `X=0; AC_SUBST([X])` default next to the gnulib module that sets X),
        # or one a decision's own checks produce instead (mpc's MPC_HAVE_COMPLEX_H, substituted
        # in three branches upstream and by one conditional AC_SUBST here).  The decisions
        # file's calls stay: it is the translated ones that go.
        for items in [ctx.target.checks] + ctx.target.arm_lists:
            for item in list(items):
                if (
                    isinstance(item, Call)
                    and item.fn == "checks.AC_SUBST"
                    and item.args
                    and not item.from_decision
                    and isinstance(item.args[0], str)
                    and self._dropped_by_decision("subst", item.args[0])
                ):
                    items.remove(item)
        bound: set[str] = set(ctx.probe_vars) | set(ctx.defines)
        assigned = set(ctx.analysis.assigns)
        # Dropping a check can leave the checks conditional on its result
        # dangling in turn, so this runs to a fixpoint.
        while self._validate_conditions_once(ctx, bound, assigned):
            pass

    def _validate_conditions_once(self, ctx: _Ctx, bound: set[str], assigned: set[str]) -> bool:
        """One pass of `_validate_conditions`: a check whose `requires` / `condition` names
        something no check of the target produces (the configure environment, a dropped probe, an
        assignment the converter did not translate) is removed and becomes a `guard` decision:
        ALWAYS strips the condition, NEVER drops the check, a requires list or a select replaces
        it. Returns whether anything was dropped, so the caller iterates to a fixpoint.
        """
        # What the target's checks produce: a probe bound to a cache variable
        # counts only once it is placed (a guard decision may have dropped it).
        emitted: set[str] = set()
        lists = [ctx.target.checks] + ctx.target.arm_lists
        for items in lists:
            for item in items:
                if isinstance(item, Call):
                    emitted |= provided_names(item)
        dropped_any = False

        def unproducible(name: str, own: set[str]) -> bool:
            if name in own:
                return True
            if name in emitted:
                return False
            if name in ctx.dropped or name in self.dropped_global:
                return True
            if name in bound:
                return False
            if name in ENVIRONMENT_VARIABLES:
                return True
            if "_cv_" in name:
                return False
            if name in assigned:
                return True
            return not _ALLCAPS_RE.match(name)

        for items in lists:
            for item in list(items):
                if not isinstance(item, Call):
                    continue
                own = provided_names(item)
                requires = strings(item.kwargs.get("requires"))
                condition = item.kwargs.get("condition")
                if not isinstance(condition, str):
                    condition = None
                dangling = sorted(
                    {
                        n
                        for expr in requires + ([condition] if condition else [])
                        for n in condition_names(expr)
                        if unproducible(n, own)
                    }
                )
                if not dangling:
                    continue
                items.remove(item)
                kept = [r for r in requires if not any(n in dangling for n in condition_names(r))]
                decision = self._request(
                    f"guard:{ctx.target.name}:{check_key(item)}",
                    "guard",
                    ctx,
                    f"{check_key(item)} is conditional on {', '.join(dangling)}, which no check produces "
                    "(a variable of the configure environment, or an assignment the converter did not translate)",
                    {"requires": requires, "condition": condition, "unresolved": dangling},
                )
                if decision is None or decision.never:
                    self._drop([item], ctx)
                    dropped_any = True
                    continue
                stripped = clone(item)
                stripped.kwargs.pop("requires", None)
                if kept:
                    stripped.kwargs["requires"] = kept
                if condition is not None and any(n in dangling for n in condition_names(condition)):
                    # `condition = / if_true = / if_false =`: ALWAYS picks the
                    # true branch; an explicit requires list keeps the condition.
                    stripped.kwargs.pop("condition", None)
                    if_true = stripped.kwargs.pop("if_true", None)
                    stripped.kwargs.pop("if_false", None)
                    if decision.requires is not None:
                        stripped.kwargs["condition"] = " && ".join(decision.requires)
                        stripped.kwargs["if_true"] = if_true
                        stripped.kwargs["if_false"] = item.kwargs.get("if_false")
                        items.append(stripped)
                        continue
                    stripped.args = list(stripped.args[:1]) + ([if_true] if if_true is not None else [])
                if decision.always:
                    stripped.comments.append(f"{', '.join(dangling)}: condition dropped (decisions file: ALWAYS)")
                    items.append(stripped)
                    continue
                self._apply_guard_decision([stripped], decision, ctx, items)
        return dropped_any

    def _variable_request(
        self,
        var: str,
        kind: str,
        ctx: _Ctx,
        comment: str,
        summary: str,
        decisions: list[df.Assignment],
        allow_partial: bool = False,
    ) -> dec.Decision | None:
        """Ask for a `variable:` decision about `var`, with its assignments as context, and apply
        the answer. IGNORE or no answer records the variable as dropped, so conditions over it
        become decisions too; ACCEPT_PARTIAL is only valid where the caller allows it.
        """
        context = {
            "assignments": [
                f"{d.text}  when {describe(d.cond)}"
                + ("" if isinstance(d.value, df.Lit) else f"  [value not a literal: {type(d.value).__name__}]")
                + (
                    ""
                    if lower.expression(d.cond).ok
                    else f"  [condition not expressible: {lower.expression(d.cond).unknown}]"
                )
                for d in decisions
            ]
        }
        decision = self._request(
            f"variable:{var}", "variable", ctx, summary, context, shell=[d.text for d in decisions]
        )
        if decision is not None and decision.accept_partial:
            if not allow_partial:
                raise dec.DecisionError(f"variable:{var}: ACCEPT_PARTIAL applies only to a partially followed variable")
            return decision
        if decision is None or decision.ignore:
            ctx.dropped.add(var)  # nothing produces it: conditions over it are decisions too
        else:
            self._apply_variable_decision(var, kind, decision, ctx, comment)
        return decision

    def _decision_checks(
        self, var: str, decisions: list[df.Assignment], ctx: _Ctx, *, kind: str, comment: str, guess: bool = False
    ) -> None:
        """Lower a variable's decision list to one check per select() arm."""
        decisions = _last_wins(decisions, lambda d: d.cond)
        # The raw disjunction: `disj` folds `GCC && !GCC` to TRUE, which would hide the
        # dimension (compiler, platform, option) the arms are split on.
        whole = (
            df.Or(tuple(d.cond for d in decisions))
            if len(decisions) > 1
            else (decisions[0].cond if decisions else df.FALSE)
        )
        has_flag = bool(df.flag_atoms(whole))
        labels = [lower.DEFAULT_ARM]
        split_fn: Callable[[df.Cond, str], df.Cond] | None = None
        if has_flag:
            arms = (
                lower.flag_arms(whole, self.project, [d.cond for d in decisions]) if self.project is not None else None
            )
            if arms is None:
                self._variable_request(
                    var,
                    kind,
                    ctx,
                    comment,
                    f"{var}: assignments depend on configure options in a way select() cannot express",
                    decisions,
                )
                return
            (flag,) = df.option_names(whole)
            if list(arms) == [lower.DEFAULT_ARM]:
                # The invocations agree: plain checks, the conditions read as for the option
                # not given.
                decisions = [
                    df.Assignment(df.substitute_option(d.cond, flag, None), d.value, d.text) for d in decisions
                ]
            else:
                labels = list(arms)
                settings = self._state.option_settings
                split_fn = lambda cond, label: df.substitute_option(
                    cond, flag, None if label == lower.DEFAULT_ARM else settings[label][1]
                )
        elif lower.platform_atoms(whole):
            arms, reason = self._arms(whole)
            if arms is None:
                self._variable_request(var, kind, ctx, comment, f"{var}: {reason}", decisions)
                return
            labels = list(arms)
            if labels == [lower.DEFAULT_ARM]:
                # The patterns matched no modeled platform: one plain check.
                decisions = [df.Assignment(lower.default_residual(d.cond), d.value, d.text) for d in decisions]
            else:
                family = family_of_labels(labels)
                split_fn = lambda cond, label: (
                    lower.default_residual(cond) if label == lower.DEFAULT_ARM else lower.residual(family, label, cond)
                )
        split = split_fn is not None
        family = family_of_labels(labels)
        for label in labels:
            arm_decisions: list[tuple[df.Cond, df.Assignment]] = []
            for decision in decisions:
                cond = split_fn(decision.cond, label) if split_fn is not None else decision.cond
                if cond is df.FALSE:
                    continue
                arm_decisions.append((cond, decision))
            dest = ctx.target.arm(label, family) if split else ctx.target.checks
            check, dropped = self._lower_decisions(var, arm_decisions, kind, guess)
            if check is None:
                if arm_decisions:
                    self._variable_request(
                        var,
                        kind,
                        ctx,
                        comment,
                        f"{var}: the assignments cannot be expressed as one check",
                        [d for _, d in arm_decisions],
                    )
                continue
            if dropped:
                decided = self._variable_request(
                    var,
                    kind,
                    ctx,
                    comment,
                    f"{var}: some assignments have conditions that cannot be expressed",
                    [d for _, d in arm_decisions],
                    allow_partial=True,
                )
                if decided is None or not decided.accept_partial:
                    continue
                check.comments.append(
                    f"partial: `{dropped[0][1].text}` when {describe(dropped[0][0])} "
                    "is not expressed (accepted in the decisions file)"
                )
            check.comments.insert(0, comment)
            _append_unique(dest, check)
        if split:
            ctx.target.arm(lower.DEFAULT_ARM, family)

    def _lower_decisions(
        self, var: str, arm_decisions: list[tuple[df.Cond, df.Assignment]], kind: str, guess: bool
    ) -> tuple[Call | None, list[tuple[df.Cond, df.Assignment]]]:
        """One check for a decision list; also the assignments it had to drop.

        Last assignment wins.  Supported shapes: an unconditional final value;
        one value under one or more (expressible) conditions, with or without
        an unconditional initial value; two values under complementary
        conditions.  Conditions that cannot be expressed are dropped and
        reported so the check stays right where it applies.
        """
        arm_decisions = _last_wins(arm_decisions, lambda pair: pair[0])
        values: list[tuple[df.Cond, str | int, df.Assignment]] = []
        for cond, decision in arm_decisions:
            value = lower.guess_value(decision.value) if guess else None
            if guess:
                if value is not None:
                    values.append((cond, 1 if value == "yes" else 0, decision))
                elif isinstance(decision.value, df.Lit):
                    values.append((cond, literal(decision.value.text), decision))  # `present` / `missing`
                else:
                    return None, []
            elif isinstance(decision.value, df.Lit):
                values.append((cond, literal(decision.value.text), decision))
            else:
                return None, []
        if not values:
            return None, []
        if values[-1][0] is df.TRUE:
            return Call(kind, [var, values[-1][1]]), []
        # Every branch assigns the same value: whether a condition holds
        # changes nothing once an unconditional assignment exists.
        if len({v for _, v, _ in values}) == 1 and any(c is df.TRUE for c, _, _ in values):
            return Call(kind, [var, values[0][1]]), []
        unconditional = [v for c, v, _ in values if c is df.TRUE]
        conditional = [(c, v, d) for c, v, d in values if c is not df.TRUE]
        default = unconditional[-1] if unconditional else None
        distinct = list(dict.fromkeys(v for _, v, _ in conditional))
        if len(distinct) == 2 and default is None:
            first, second = distinct
            cond_first = df.disj(*(c for c, v, _ in conditional if v == first))
            cond_second = df.disj(*(c for c, v, _ in conditional if v == second))
            if lower.expression(cond_first).ok and lower.equivalent(cond_second, df.neg(cond_first)):
                expr = lower.expression(cond_first).expr
                if expr is not None:
                    return Call(kind, [var], {"condition": expr, "if_true": first, "if_false": second}), []
        if len(distinct) != 1:
            return None, []
        (single,) = distinct
        kept = [(c, v, d) for c, v, d in conditional if lower.expression(c).ok]
        dropped = [(c, d) for c, v, d in conditional if not lower.expression(c).ok]
        if not kept:
            return None, []
        cond = df.disj(*(c for c, _, _ in kept))
        expr = lower.expression(cond).expr
        if expr is None:
            return Call(kind, [var, single]), dropped
        if default is None:
            return Call(kind, [var, single], {"requires": [expr]}), dropped
        return Call(kind, [var], {"condition": expr, "if_true": single, "if_false": default}), dropped


# -- helpers -----------------------------------------------------------------


def describe(cond: df.Cond) -> str:
    lowered = lower.expression(cond)
    if lowered.ok:
        return lowered.expr or "always"
    return lowered.unknown or "?"


def check_key(call: Call) -> str:
    """A stable name for a check: the macro and its subject."""
    short = call.fn.split(".")[-1]
    if call.args:
        first = call.args[0]
        subject = " ".join(strings(first)) if isinstance(first, list) else str(first)
    else:
        subject = str(call.kwargs.get("name", ""))
    return f"{short}:{slug(subject)}" if subject else short


_LANG_ABBREV = {"c": "c", "cpp": "cxx"}
_LANG_PREFIX = {"c": "C", "cpp": "CXX"}


_CACHE_HIT_TEXT_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\+y\}")


def cache_node_var(node: Node, language: str = "c") -> str:
    """The cache variable of an AC_CACHE_CHECK / AC_CACHE_VAL node.  The markers keep the argument as
    written; an AS_VAR_PUSHDEF alias (nasm's `AC_CACHE_VAL([cache], ...)` after `AS_VAR_PUSHDEF([cache],
    [pa_cv_$1_$2])`) is spelled out by the expansion's cache-hit test, `if test ${pa_cv_CFLAGS__Wall+y}`."""
    padded = node.args + ["", ""]
    var = m4_cache_name((padded[1] if node.name == "AC_CACHE_CHECK" else padded[0]).strip(), language)
    if "$" in var:
        return var
    hits: list[str] = [
        name for item in node.items if isinstance(item, Text) for name in _CACHE_HIT_TEXT_RE.findall(item.text)
    ]
    if var in hits or not hits:
        return var
    return hits[0]


def m4_cache_name(text: str, language: str = "c") -> str:
    """A cache variable named with m4 in it: `bison_cv_[]_AC_LANG_ABBREV[]_supports_posixly_correct`
    is `bison_cv_c_...` in the C part and `bison_cv_cxx_...` in the C++ part (the expansion
    keeps the call's arguments unexpanded)."""
    text = text.replace("[]", "")
    return text.replace("_AC_LANG_ABBREV", _LANG_ABBREV.get(language, language)).replace(
        "_AC_LANG_PREFIX", _LANG_PREFIX.get(language, language.upper())
    )


def cache_var(macro: str, subject: str) -> str:
    """The cache variable autoconf's singular check macros set."""
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", subject.strip().replace("*", "p")).lower()
    base = macro.replace("_ONCE", "")
    prefix = {
        "AC_CHECK_HEADER": "ac_cv_header_",
        "AC_CHECK_HEADERS": "ac_cv_header_",
        "AC_CHECK_FUNC": "ac_cv_func_",
        "AC_CHECK_FUNCS": "ac_cv_func_",
        "AC_CHECK_DECL": "ac_cv_have_decl_",
        "AC_CHECK_DECLS": "ac_cv_have_decl_",
        "AC_CHECK_TYPE": "ac_cv_type_",
        "AC_CHECK_TYPES": "ac_cv_type_",
        "AC_CHECK_MEMBER": "ac_cv_member_",
        "AC_CHECK_MEMBERS": "ac_cv_member_",
    }.get(base, "ac_cv_")
    return prefix + normalized


def flag_bool_name(option: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", option.strip())


def default_tarname(name: str) -> str:
    """AC_INIT's default PACKAGE_TARNAME for a package name."""
    stripped = name.removeprefix("GNU ")
    return re.sub(r"[^_a-z0-9]", "-", stripped.lower())


def default_from_help(arg: str) -> bool:
    """The option's default as its help string spells it: `--disable-x` and
    `--without-x` document a feature that is on unless turned off."""
    call = m4args.parse_call(arg)
    option = (
        call.args[0] if call is not None and call.name in ("AS_HELP_STRING", "AC_HELP_STRING") and call.args else arg
    )
    return option.strip().startswith(("--disable-", "--without-"))


def help_text(arg: str) -> str:
    """The description of an AS_HELP_STRING argument."""
    call = m4args.parse_call(arg)
    if call is not None and call.name in ("AS_HELP_STRING", "AC_HELP_STRING") and len(call.args) > 1:
        return " ".join(call.args[1].split())
    return " ".join(arg.split())


_MESSAGE_RE = re.compile(r"AC_MSG_(?:RESULT|CHECKING|NOTICE)\(\[?[^()\]]*\]?\)")
_DNL_RE = re.compile(r"\bdnl\b[^\n]*")
_FATAL_RE = re.compile(r"AC_MSG_(?:ERROR|FAILURE)\(.*?\)\s*", re.DOTALL)


_TARGET_LIST_RE = re.compile(r"^[a-z][A-Za-z0-9_]*_(PROGRAMS|LIBRARIES|LTLIBRARIES)$")
_EXPANDED_PROBE_RE = re.compile(r"cat confdefs\.h - <<_ACEOF|ac_fn_c_try_(?:compile|link|cpp|run)")
_EMPTY_MAIN_RE = re.compile(r"int\s+main\s*\(\s*(?:void)?\s*\)\s*\{\s*return\s+0\s*;\s*\}")
_ANY_CALL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\(")
# Checks whose action arguments the dataflow sees as `if test "$cache" = yes; then ...; else ...; fi`
# around the placeholder: macro -> index of its action-if-found argument (the cache variable's
# spelling is `_subject_cache`'s).
_ACTION_PROBES = {
    "AC_CHECK_FUNC": 1,
    "AC_CHECK_FUNCS": 1,
    "AC_CHECK_HEADER": 1,
    "AC_CHECK_HEADERS": 1,
    "AC_CHECK_DECL": 1,
    "AC_CHECK_DECLS": 1,
    "AC_CHECK_TYPE": 1,
    "AC_CHECK_TYPES": 1,
    "AC_CHECK_MEMBER": 1,
    "AC_CHECK_MEMBERS": 1,
    "AC_CHECK_LIB": 2,
    "AC_SEARCH_LIBS": 2,
}


# The checks whose actions `sees_through` hands to the dataflow (see `_singular` and `_m_AC_SEARCH_LIBS`).
_SINGULAR_CHECKS = {
    "AC_CHECK_FUNC",
    "AC_CHECK_HEADER",
    "AC_CHECK_DECL",
    "AC_CHECK_TYPE",
    "AC_CHECK_MEMBER",
    "AC_SEARCH_LIBS",
}


def _subject_cache(node: Node) -> str | None:
    """The cache variable of a singular check, whatever its actions hold."""
    if node.name not in _ACTION_PROBES or not node.args:
        return None
    subjects = m4args.words(node.args[0])
    if len(subjects) != 1:
        return None
    if node.name == "AC_CHECK_LIB":
        if len(node.args) < 2:
            return None
        return (
            "ac_cv_lib_"
            + re.sub(r"[^A-Za-z0-9_]", "_", subjects[0])
            + "_"
            + re.sub(r"[^A-Za-z0-9_]", "_", node.args[1].strip())
        )
    if node.name == "AC_SEARCH_LIBS":
        return "ac_cv_search_" + re.sub(r"[^A-Za-z0-9_]", "_", subjects[0])
    return cache_var(node.name, subjects[0])


# The obsolete spellings a probe may have in the source (libpcap's aclocal.m4 uses AC_TRY_LINK).
_IFELSE_SPELLINGS = {
    "AC_LINK_IFELSE": ["AC_TRY_LINK", "AC_TRY_LINK_FUNC"],
    "AC_COMPILE_IFELSE": ["AC_TRY_COMPILE"],
    "AC_RUN_IFELSE": ["AC_TRY_RUN"],
    "AC_PREPROC_IFELSE": ["AC_TRY_CPP"],
}

# Text of a program argument that says nothing about which call it is: the `int main (void)
# { ... return 0; }` of an AC_LANG_PROGRAM expansion, a closing parenthesis.
_PROGRAM_BOILERPLATE_RE = re.compile(r"^(?:\)*|int|main\s*\((?:void)?\)\)*|\{|\}\)*|;|return\s+0\s*;\)*)$")
_LANG_WRAPPER_RE = re.compile(r"AC_LANG_(?:PROGRAM|SOURCE|CALL|FUNC_LINK_TRY|BOOL_COMPILE_TRY)\s*\(")


# An empty program as the source spells it: `AC_LANG_PROGRAM([[]], [[]])`, `AC_LANG_PROGRAM([], [])`,
# `AC_TRY_LINK([], [], ...)`.
_EMPTY_PROGRAM_RE = re.compile(
    r"(?:AC_LANG_(?:PROGRAM|SOURCE)\s*\(|AC_TRY_(?:LINK|COMPILE|RUN)\s*\()\s*\[*\s*\]*\s*,?\s*\[*\s*\]*\s*[),]"
)


def _probe_needle(arg: str) -> str:
    """A line of the program that the source spells the same way: the first distinctive text
    once the `AC_LANG_PROGRAM([[`, the quotes and the argument commas are set aside
    (`AC_LANG_PROGRAM([[#include <netdb.h>]], [[` -> `#include <netdb.h>`, the way the source's
    `AC_TRY_LINK([#include <netdb.h>], [` carries it)."""
    for line in arg.split("\n"):
        text = line
        if _LANG_WRAPPER_RE.search(line):
            # `AC_LANG_SOURCE([int main(){}])`: the wrapper's own parenthesis goes with it.
            text = _LANG_WRAPPER_RE.sub("", line).rstrip().removesuffix(")")
        text = text.replace("[", "").replace("]", "")
        for segment in text.split(","):
            segment = segment.strip()
            if len(segment) >= 3 and not _PROGRAM_BOILERPLATE_RE.match(segment):
                return segment[:40]
    return ""


def _actions_key(node: Node) -> str:
    """The decision key of a check's action arguments: `actions:AC_CHECK_FUNC:tgetent`."""
    first = node.args[0].split("\n")[0].strip() if node.args else ""
    return f"actions:{node.name}:{slug(first)}"


def sees_through(node: Node) -> bool:
    """A singular check with actions: its expansion is body shell for the dataflow (the actions,
    nested calls included, under `if test "x$cache" = xyes`), see `_placeholder_text`."""
    index = _ACTION_PROBES.get(node.name)
    if node.name not in _SINGULAR_CHECKS or index is None or len(node.args) <= index:
        return False
    yes = node.args[index] if len(node.args) > index else ""
    no = node.args[index + 1] if len(node.args) > index + 1 else ""
    if fold_define(yes) and not no.strip():
        return False  # a plain AC_DEFINE on success folds into `define =`; nothing to see through
    return any(a.strip() for a in (yes, no)) and _subject_cache(node) is not None


def _action_probe_cache(node: Node) -> str | None:
    """The cache variable of a check whose actions are plain shell over one subject, or None."""
    index = _ACTION_PROBES.get(node.name)
    if index is None or len(node.args) <= index:
        return None
    subjects = m4args.words(node.args[0])
    if len(subjects) != 1:
        return None
    actions = [a for a in node.args[index : index + 2] if a.strip()]
    if not actions or any(_ANY_CALL_RE.search(m4args.strip_m4(strip_messages(a))) for a in actions):
        return None
    return _subject_cache(node)


def _action_shell(node: Node, see_through: bool) -> str:
    """The check's action arguments as shell around its placeholder (see `_ACTION_PROBES`); the
    plural forms (`AC_CHECK_FUNCS([strlcat],, [needstrlcat=yes])`), whose expansion loops.
    `see_through`: the actions are already the body's shell (`Translator._sees_through`)."""
    cache = _action_probe_cache(node)
    if cache is None or see_through:
        return ""
    index = _ACTION_PROBES[node.name]
    yes = m4args.strip_m4(strip_messages(node.args[index])) if len(node.args) > index else ""
    no = m4args.strip_m4(strip_messages(node.args[index + 1])) if len(node.args) > index + 1 else ""
    if not yes.strip() and not no.strip():
        return ""
    return f'\nif test "${cache}" = yes; then\n{yes.strip() or ":"}\nelse\n{no.strip() or ":"}\nfi\n'


def pure_assignments(action: str) -> bool:
    """Whether an action is only shell variable assignments (`needstrlcat=yes`), which the dataflow
    models when the check's actions were rendered around its placeholder."""
    text = m4args.strip_m4(strip_messages(action)).strip()
    if not text:
        return False
    for line in re.split(r"[;\n]", text):
        line = line.strip()
        if line and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"$`]*\"|'[^']*'|[^\s\"'$`]*)", line):
            return False
    return True


_STDC_INDICATOR_RE = re.compile(r"^GL_STDC_[A-Z0-9_]+$")
_FOR_LIST_RE = re.compile(r"\bfor\s+\w+\s+in\s+\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?\s*;")
_LIST_APPEND_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)=(?:"\$\{?\1\}?\s+([^"$`]+)"|([^\s"$`;]+))\s*;?\s*$')


def _assignment_value(text: str, var: str) -> str | None:
    """The right-hand side of `var=...` as the analysis recorded it, quotes kept."""
    match = re.match(rf"\s*{re.escape(var)}=(.*)\Z", text.strip(), re.DOTALL)
    if match is None:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _program_word(word: str) -> str:
    """A program as configure spells it in a `*_PROGRAMS` value: `src/"$p"'$(EXEEXT)'` decorations and
    quadrigraphs (`@<:@` is `[`) undone, the directory left to the Makefile."""
    word = m4args.quadrigraphs(word.strip("'\""))
    word = re.sub(r"\$\(EXEEXT\)|\$EXEEXT", "", word)
    return word.rsplit("/", 1)[-1]


def list_append_words(action: str) -> tuple[str, list[str]] | None:
    """`(var, words)` when the action only appends words to shell list `var`: the body of coreutils'
    `gl_ADD_PROG` (`if test -z "$X"; then X=w; else X="$X w"; fi`), or plain `X="$X w"` / `X=w` lines;
    None for anything else."""
    text = action.strip()
    text = re.sub(r"^\{\s*|\s*\}$", "", text)
    text = re.sub(r"\bif\s+test\s+-z\s+\"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?\"?\s*;\s*then\b|\belse\b|\bfi\b", "\n", text)
    var: str | None = None
    words: list[str] = []
    appends = False  # at least one `X="$X w"`: a lone `X=w` is an ordinary assignment, not a list
    for line in text.split("\n"):
        line = line.strip().rstrip(";").strip()
        if not line:
            continue
        match = _LIST_APPEND_RE.match(line)
        if match is None or (var is not None and match.group(1) != var):
            return None
        var = match.group(1)
        appends = appends or match.group(2) is not None
        for word in (match.group(2) or match.group(3)).split():
            if word not in words:
                words.append(word)
    return (var, words) if var and words and appends else None


def strip_messages(action: str) -> str:
    """The action without its AC_MSG_RESULT / AC_MSG_CHECKING calls and `dnl` comments."""
    return _DNL_RE.sub("", _MESSAGE_RE.sub("", action or "")).strip()


def action_defines(action: str) -> list[tuple[str, str]]:
    """`[(NAME, VALUE)]` when `action` is one AC_DEFINE call (`parse_call` accepts nothing else), else `[]`."""
    call = m4args.parse_call((action or "").strip())
    if call is None or call.name != "AC_DEFINE" or not call.args:
        return []
    name = call.args[0].strip()
    value = call.args[1].strip() if len(call.args) > 1 and call.args[1].strip() else "1"
    return [(name, value)]


def action_subst(action: str) -> tuple[str, str] | None:
    """`(NAME, value)` for an action that only substitutes one literal: `AC_SUBST([NAME], [value])`,
    or `NAME=value` followed by `AC_SUBST([NAME])`."""
    text = (action or "").strip()
    call = m4args.parse_call(text)
    if call is not None and call.name == "AC_SUBST" and len(call.args) == 2 and call.args[0].strip():
        value = m4args.unquote_level(call.args[1]).strip()
        return (call.args[0].strip(), value) if "$" not in value and "`" not in value else None
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=\"?([^\s\"$`]*)\"?\s+AC_SUBST\(\[?\1\]?\)", text, re.DOTALL)
    return (match.group(1), match.group(2)) if match else None


def _compiler_option_test(node: Node) -> bool:
    """A probe over an empty program (`AC_LANG_PROGRAM([],[])`, `int main(void) { return 0; }`): it tests the
    compiler options of the moment, not any code."""
    program = (node.args + [""])[0]
    lang = m4args.lang_program(program)
    if lang is not None:
        return not re.sub(r"[\[\]\s]", "", lang.prologue + lang.body)
    source = m4args.lang_source(program)
    text = m4args.quadrigraphs(source if source is not None else program).strip("[] \n\t")
    if not text or _EMPTY_MAIN_RE.fullmatch(text):
        return True
    expanded = node.program() or ""
    return bool(expanded) and re.sub(r"[\s;]", "", expanded) == "intmain(void){return0;}"


def action_var(action: str) -> str | None:
    """`x` for an action that is exactly `x=yes` or `x=no`."""
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)=(yes|no)\s*", action or "")
    return match.group(1) if match else None


def clone(call: Call) -> Call:
    return Call(call.fn, list(call.args), dict(call.kwargs), list(call.comments), call.returns_list)


def basename(path: str) -> str:
    return normalize_path(path).rsplit("/", 1)[-1]


def stem(path: str) -> str:
    name = basename(path)
    return name[:-3] if name.endswith(".m4") else name.rsplit(".", 1)[0]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text.strip()).strip("_") or "x"


def cache_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text.strip().replace("*", "p"))


def shell_ident(text: str) -> str:
    """`text` as the upper-case shell identifier gnulib derives from it (`stdckdint.h` -> `STDCKDINT_H`)."""
    return re.sub(r"[^A-Za-z0-9]", "_", text).upper()


def _includes_arg(arg: str) -> list[str]:
    """An includes argument of a check macro as the lines the `includes =` kwarg takes."""
    return m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(arg)))


def _action_text(arg: str, placeholders: list[tuple[str, str]]) -> str:
    """An action argument as the shell the analysis reads: m4 stripped, quadrigraphs and one quote level
    undone, m4sh spelled out, and the macro calls among it replaced by their `placeholders`."""
    return m4args.neutralize_macros(
        m4args.m4sh_to_shell(m4args.unquote_level(m4args.quadrigraphs(m4args.strip_m4(arg)))), placeholders
    )


# A value that is one shell variable reference: `$x`, `${x}`, `"$x"`.
_VAR_REF_RE = re.compile(r'\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?\s*')


def _without_self_reference(value: str, var: str) -> str:
    """`value` with its references to `var` itself blanked: the words `X="$X a b"` appends."""
    return re.sub(rf"\"?\$\{{?{re.escape(var)}\}}?\"?", " ", value)


def _when(comment: str, cond: df.Cond) -> str:
    """`comment`, with the condition appended when there is one."""
    return comment + (f" (when {describe(cond)})" if cond is not df.TRUE else "")


def _append_unique(dest: list[CheckItem], call: Call) -> None:
    """Append `call` to `dest` unless an identical call is there already."""
    if not any(same_call(call, other) for other in dest if isinstance(other, Call)):
        dest.append(call)


def literal(value: str) -> str | int:
    """Starlark value for an m4 literal: ints stay ints, the rest are strings."""
    text = value.strip()
    if _NUMBER_RE.match(text):
        return int(text)
    return m4args.quadrigraphs(text)


_SHELL_DEFINE_RE = re.compile(
    r"""^\s*(?:printf\s+['"]%s\\n['"]|\$as_echo)\s+"#define\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*?))?"\s*>>\s*confdefs\.h\s*$"""
)


def action_define(action: str) -> tuple[str, str] | None:
    """`(NAME, VALUE)` when `action` is one AC_DEFINE: as m4 (`AC_DEFINE([HAVE_X])`)
    or as the shell it expands to when the action was written unquoted
    (`printf '%s\\n' "#define HAVE_X 1" >>confdefs.h`)."""
    call = m4args.parse_call(action)
    if call is not None:
        if call.name != "AC_DEFINE" or not call.args:
            return None
        name = call.args[0].strip()
        value = call.args[1].strip() if len(call.args) > 1 and call.args[1].strip() else "1"
    else:
        match = _SHELL_DEFINE_RE.match(action.strip())
        if match is None:
            return None
        name, value = match.group(1), (match.group(2) or "1").strip()
    if not name or "$" in name or "(" in name or "$" in value or "`" in value:
        return None
    return name, value


_EXPANDED_DEFINE_RE = re.compile(
    r'#define[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+([^"\n]*?))?[ \t]*"?[ \t]*(?:>>confdefs\.h)?$', re.MULTILINE
)


def expanded_define(node: Node) -> tuple[str, str] | None:
    """`(NAME, VALUE)` from the shell an AC_DEFINE call expanded to: the value
    after m4 did its work (`gl_MODULE_INDICATOR_CONDITION` -> `1`)."""
    match = _EXPANDED_DEFINE_RE.search(node.full_text)
    if match is None:
        return None
    return match.group(1), (match.group(2) or "1").strip()


def fold_define(action: str) -> str | None:
    """`HAVE_X` when `action` is exactly `AC_DEFINE([HAVE_X], [1], ...)`."""
    found = action_define(action)
    if found is None or found[1] != "1":
        return None
    return found[0]


_SHELL_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
_INCLUDES_DEFAULT_VARIABLE = "ac_includes_default"  # configure's AC_INCLUDES_DEFAULT text, as a shell variable


def _shell_refs(value: AttrValue) -> set[str]:
    """The `$var` names left in a program's text (a string, a list of lines, or a `utils.AC_LANG_PROGRAM`
    call; `Raw` parts are Starlark, not text)."""
    if isinstance(value, Call):
        return set().union(*(_shell_refs(arg) for arg in value.args), set())
    if isinstance(value, list):
        return set().union(*(_shell_refs(part) for part in value), set())
    if isinstance(value, str):
        return set(_SHELL_REF_RE.findall(value))
    return set()


# configure's directories: a program reading them (`#include "$srcdir/gmp-h.in"`) wants a file of
# the tree, which no literal stands for (the body's `srcdir=.` is where configure ran).
_TREE_PATH_VARIABLES = frozenset(
    {
        "srcdir",
        "top_srcdir",
        "abs_srcdir",
        "abs_top_srcdir",
        "builddir",
        "top_builddir",
        "abs_builddir",
        "abs_top_builddir",
        "ac_aux_dir",
        "ac_abs_confdir",
        "ac_pwd",
        "ac_confdir",
        "PWD",
    }
)
# Checks whose define carries the recorded value (`SIZEOF_X`), not a yes/no.
_VALUE_DEFINING_CHECKS = ("checks.AC_CHECK_SIZEOF", "checks.AC_CHECK_ALIGNOF", "checks.AC_COMPUTE_INT")


def _program_lines(call: Call) -> list[str]:
    """The program text of a check (its includes and code), as lines for a decision's context."""
    lines: list[str] = []
    for key in ("includes", "code"):
        value = call.kwargs.get(key)
        parts = value.args if isinstance(value, Call) else [value]
        for part in parts:
            if isinstance(part, list):
                lines += [
                    line if isinstance(line, str) else line.text if isinstance(line, Raw) else str(line)
                    for line in part
                ]
            elif isinstance(part, str):
                lines += part.split("\n")
    return lines


def block_lines(text: str) -> list[str]:
    lines = text.replace("\r", "").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return dedent(lines)


def dedent(lines: list[str]) -> list[str]:
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    cut = min(indents) if indents else 0
    return [l[cut:].rstrip() for l in lines]


def same_call(a: Call, b: Call) -> bool:
    return a.fn == b.fn and a.args == b.args and a.kwargs == b.kwargs


def _program(prologue: str, body: str = "") -> Call:
    return Call("utils.AC_LANG_PROGRAM", [block_lines(prologue), block_lines(body)])


# Translations of autoconf builtins that have no port under //autoconf/macros,
# in the shape of the hand ports.  Each entry is (checks, note); the note
# documents what GNU autoconf does that the translation does not.
BUILTIN_CHECKS: dict[str, tuple[list[Call], str | None]] = {
    # AC_HEADER_DIRENT takes the first of dirent.h, sys/ndir.h, sys/dir.h,
    # ndir.h that declares DIR; every supported platform has dirent.h.
    "AC_HEADER_DIRENT": (
        [Call("macros.AC_CHECK_HEADERS", [["dirent.h"]], returns_list=True)],
        (
            "AC_HEADER_DIRENT: only dirent.h is probed (sys/ndir.h, sys/dir.h and ndir.h are "
            "pre-POSIX fallbacks GNU autoconf tries after it)"
        ),
    ),
    # AC_FUNC_GETPGRP: GETPGRP_VOID unless `getpgrp (0)` compiles (the BSD form).
    "AC_FUNC_GETPGRP": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_func_getpgrp_arg",
                    "code": _program("#include <unistd.h>", "getpgrp (0);"),
                },
            ),
            Call("checks.AC_DEFINE", ["GETPGRP_VOID", 1], {"requires": ["!ac_cv_func_getpgrp_arg"]}),
        ],
        (
            "AC_FUNC_GETPGRP: ac_cv_func_getpgrp_arg is the compile test itself (GNU autoconf's "
            "ac_cv_func_getpgrp_void is its negation)"
        ),
    ),
    "AC_FUNC_STRCOLL": (
        [Call("macros.AC_CHECK_FUNCS", [["strcoll"]], returns_list=True)],
        "AC_FUNC_STRCOLL: the run-time test that strcoll orders correctly is assumed to pass",
    ),
    "AC_FUNC_VPRINTF": (
        [Call("macros.AC_CHECK_FUNCS", [["vprintf"]], returns_list=True)],
        "AC_FUNC_VPRINTF: HAVE_DOPRNT (the pre-ANSI fallback) is not probed",
    ),
    "AC_C_PROTOTYPES": (
        [Call("checks.AC_DEFINE", ["PROTOTYPES", 1]), Call("checks.AC_DEFINE", ["__PROTOTYPES", 1])],
        None,
    ),
    "AC_C_CONST": ([], "AC_C_CONST: `const` is only redefined for pre-ANSI compilers; nothing to emit"),
    "AC_C_VOLATILE": ([], "AC_C_VOLATILE: `volatile` is only redefined for pre-ANSI compilers; nothing to emit"),
    "AC_C_STRINGIZE": ([Call("checks.AC_DEFINE", ["HAVE_STRINGIZE", 1])], None),
    "AC_TYPE_SIGNAL": ([Call("checks.AC_DEFINE", ["RETSIGTYPE", "void"])], None),
    "AC_C_CHAR_UNSIGNED": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_c_char_unsigned",
                    "code": _program(
                        "",
                        "static int test_array[1 - 2 * !(((char) -1) > 0)];\ntest_array[0] = 0;\nreturn test_array[0];",
                    ),
                },
            )
        ],
        (
            "AC_C_CHAR_UNSIGNED: GNU autoconf defines __CHAR_UNSIGNED__ only for non-GCC compilers; "
            "the compilers Bazel drives predefine it where it applies"
        ),
    ),
    "AC_C_VARARRAYS": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_c_vararrays",
                    "code": _program(
                        "#ifdef __STDC_NO_VLA__\n#error no variable-length arrays\n#endif",
                        "int n = 1;\nchar a[n];\na[0] = 0;\nreturn a[0];",
                    ),
                    "define": "HAVE_C_VARARRAYS",
                },
            )
        ],
        None,
    ),
    "AC_HEADER_SYS_WAIT": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_header_sys_wait_h",
                    "code": _program(
                        "#include <sys/types.h>\n#include <sys/wait.h>\n#ifndef WEXITSTATUS\n"
                        "# define WEXITSTATUS(stat_val) ((unsigned int) (stat_val) >> 8)\n#endif\n"
                        "#ifndef WIFEXITED\n# define WIFEXITED(stat_val) (((stat_val) & 255) == 0)\n#endif",
                        "int s;\nwait (&s);\ns = WIFEXITED (s) ? WEXITSTATUS (s) : 1;",
                    ),
                    "define": "HAVE_SYS_WAIT_H",
                },
            )
        ],
        None,
    ),
    "AC_HEADER_TIME": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_header_time",
                    "code": _program(
                        "#include <sys/types.h>\n#include <sys/time.h>\n#include <time.h>", "struct tm *tp;\n(void) tp;"
                    ),
                    "define": "TIME_WITH_SYS_TIME",
                },
            )
        ],
        None,
    ),
    "AC_HEADER_TIOCGWINSZ": (
        [
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_sys_tiocgwinsz_in_termios_h",
                    "code": _program(
                        "#include <sys/types.h>\n#include <termios.h>\n#ifndef TIOCGWINSZ\n"
                        "#error TIOCGWINSZ is not in termios.h\n#endif"
                    ),
                },
            ),
            Call(
                "checks.AC_TRY_COMPILE",
                [],
                {
                    "name": "ac_cv_sys_tiocgwinsz_in_sys_ioctl_h",
                    "code": _program(
                        "#include <sys/types.h>\n#include <sys/ioctl.h>\n#ifndef TIOCGWINSZ\n"
                        "#error TIOCGWINSZ is not in sys/ioctl.h\n#endif"
                    ),
                },
            ),
            Call(
                "checks.AC_DEFINE",
                ["GWINSZ_IN_SYS_IOCTL", 1],
                {"requires": ["!ac_cv_sys_tiocgwinsz_in_termios_h", "ac_cv_sys_tiocgwinsz_in_sys_ioctl_h"]},
            ),
        ],
        None,
    ),
}

# What each kind of emitted check provides, for `_validate_conditions`.
_PLURAL_CHECKS = {"AC_CHECK_HEADERS", "AC_CHECK_FUNCS", "AC_CHECK_DECLS", "AC_CHECK_TYPES", "AC_CHECK_MEMBERS"}
# Checks whose first argument is the one subject their key names (`AC_CHECK_HEADER:stdio_h`).
_SUBJECT_CHECKS = {"AC_CHECK_HEADER", "AC_CHECK_FUNC", "AC_CHECK_DECL", "AC_CHECK_TYPE", "AC_CHECK_MEMBER"}
_NAMED_CHECKS = {"AC_SUBST", "M4_VARIABLE", "AC_DEFINE", "AC_DEFINE_UNQUOTED", "AC_COMPUTE_INT"}
_CONDITION_NAME_RE = re.compile(r"(?:^|[\s(!&|])\s*([A-Za-z_][A-Za-z0-9_]*)")
# `x_cv_y=`: an assignment of a cache variable.
_CACHE_ASSIGN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*_cv_[A-Za-z0-9_]*)=")


def iso_standard(standard: str) -> str:
    """The ISO name of a standard: `gnu11` -> `c11`, `gnu++17` -> `c++17` (the dialects report the same value)."""
    standard = standard.lower()
    if standard.startswith("gnu++"):
        return "c" + standard[3:]
    if standard.startswith("gnu"):
        return "c" + standard[3:]
    return standard


# `CFLAGS="$CFLAGS -std=gnu99"`, `CXXFLAGS="-std=c++17 $CXXFLAGS"`: a standard for every check.
_STANDARD_FLAG_RE = re.compile(r'\b(C|CXX)FLAGS="[^"\n]*?(-std=[A-Za-z0-9+:]+)')
# `test "$with_ssl" = openssl`, `test "x$enable_x" != xauto`, `case $with_x in gnutls)`.
_OPTION_COMPARE_RE = re.compile(
    r'"?x?\$\{?(?:enable|with)_([A-Za-z0-9_]+)\}?"?\s*(?:=|==|!=)\s*"?x?([A-Za-z0-9_.+/-]+)"?'
)
# `ISC=yes`, `for X in`, `read X`: ALLCAPS shell variables the script sets.
_ALLCAPS_ASSIGN_RE = re.compile(
    r"(?:\b|^)([A-Z][A-Z0-9_]*)=|\bfor\s+([A-Z][A-Z0-9_]*)\s+in\b|\bread\s+([A-Z][A-Z0-9_]*)\b"
)


def _iter_texts(items: Iterable[Node | Text]) -> Iterator[Text]:
    """The `Text` items of a tree, in order, down through every node."""
    for item in items:
        if isinstance(item, Text):
            yield item
        else:
            yield from _iter_texts(item.items)


# Any shell variable `text` assigns or loops over; `-DFOO=1` and `--enable-x=` are not assignments.
_SHELL_ASSIGN_RE = re.compile(r"(?<![\w$.\-])([A-Za-z_]\w*)=|\bfor\s+([A-Za-z_]\w*)\s+in\b")


def _shell_assigned(text: str) -> set[str]:
    """Every shell variable `text` assigns (a `for` loop's included)."""
    return {a or b for a, b in _SHELL_ASSIGN_RE.findall(text)}


def _last_wins[T](items: list[T], cond: Callable[[T], df.Cond]) -> list[T]:
    """The assignments of a decision list that can be the final one: last assignment wins, so one
    the later assignments cover on every path contributes nothing (`X=$enableval` under the
    option, then `X=yes` or `X=no` by the checks' outcome; `X=""` then `X="$X -flag"` in one
    branch, the second spelled out by the dataflow, whatever opaque condition the branch
    carries)."""
    return [
        item
        for index, item in enumerate(items)
        if not any(cond(item) == cond(later) for later in items[index + 1 :])
        and not df.tautology(df.disj(df.neg(cond(item)), *(cond(later) for later in items[index + 1 :])))
    ]


# The cache variable a standard macro sets and the standard check that stands for it.
_STANDARD_MACRO_VARIABLES = {
    "AC_PROG_CC_C99": ("ac_cv_prog_cc_c99", "ac_cv_std_c99"),
    "AC_PROG_CC_STDC": ("ac_cv_prog_cc_stdc", "ac_cv_std_c99"),
}


def _assigned_names(text: str) -> set[str]:
    """The cache variables (`*_cv_*`) and ALLCAPS shell variables the shell `text` sets."""
    out = set(_CACHE_ASSIGN_RE.findall(text))
    out.update(n for groups in _ALLCAPS_ASSIGN_RE.findall(text) for n in groups if n)
    return out


# gnulib-comp.m4's tests witness: `M4tests_WITNESS=IN_`echo "${PACKAGE-$PACKAGE_TARNAME}"
# | tr a-z A-Z | sed ...`_GNULIB_TESTS`, the package name upper-cased by a pipeline.
_TESTS_WITNESS_RE = re.compile(r'IN_`echo "\$\{PACKAGE-\$PACKAGE_TARNAME\}"[^`\n]*`_GNULIB_TESTS')
# `name=value` lines of the script preamble that are not autoconf's own
# (ac_*, as_*, PACKAGE_*...): what m4_divert_text([INIT_PREPARE]) put there.
_PRELUDE_ASSIGN_RE = re.compile(
    r"^((?!ac_|as_|PACKAGE_|MFLAGS|MAKEFLAGS|SHELL|PATH_SEPARATOR|exec_prefix|prefix|"
    r"program_|bindir|sbindir|libexecdir|datarootdir|datadir|sysconfdir|sharedstatedir|"
    r"localstatedir|runstatedir|includedir|oldincludedir|docdir|infodir|htmldir|dvidir|"
    r"pdfdir|psdir|libdir|localedir|mandir|LIBOBJS|LTLIBOBJS|cross_compiling|subdirs|"
    r"DUALCASE|CONFIG_SHELL|LC_|LANG)[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./-]*)$",
    re.MULTILINE,
)
# `case "$host_os" in mingw* | windows*) true;; *) false;; esac` (and the
# inverse): the test gnulib's AM_CONDITIONALs spell a platform with.
_CASE_CONDITION_RE = re.compile(
    r"^case\s+\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?\"?\s+in\s+(.+?)\)\s*(true|false|:)\s*;;\s*\*\)\s*(true|false|:)\s*;;\s*esac$",
    re.DOTALL,
)


_PROGRAM_LOOKUPS = {
    "AC_CHECK_PROG",
    "AC_CHECK_PROGS",
    "AC_CHECK_TOOL",
    "AC_CHECK_TOOLS",
    "AC_PATH_PROG",
    "AC_PATH_PROGS",
    "AC_PATH_TOOL",
    "AC_PATH_PROGS_FEATURE_CHECK",
}


def _directly_in(child: Node, node: Node) -> bool:
    """True when `child` sits in `node`'s own body, with only cache wrappers between them."""
    # The root body is a synthesized node: its items still point at the
    # original tree's root, so reaching any `defun` counts as reaching it.
    for ancestor in child.ancestors():
        if ancestor is node or ancestor.kind == "defun":
            return True
        if ancestor.name not in TRANSPARENT:
            return False
    return True


def _program_lookup_value(macro: str, args: list[str]) -> tuple[str, str]:
    """The variable a program lookup sets and the value the converter stands in for the
    found program: its plain name (the value-if-found for AC_CHECK_PROG)."""
    padded = [a.strip() for a in args] + ["", "", ""]
    var = padded[0]
    candidates = m4args.words(padded[1])
    if macro == "AC_CHECK_PROG":
        return var, padded[2] or padded[1]
    if macro == "AC_PATH_PROGS_FEATURE_CHECK":
        return f"ac_cv_path_{var}", candidates[-1] if candidates else ""
    if macro in ("AC_CHECK_PROGS", "AC_PATH_PROGS", "AC_CHECK_TOOLS"):
        return var, candidates[0] if candidates else padded[2]
    return var, padded[1]


def produced_keys(call: Call) -> set[tuple[str, str]]:
    """`provided_names` by namespace, as the rules keep them: a define and a substitution of one
    name are two results (lib-link's probe defines HAVE_LIBSSL while `AC_SUBST([HAVE_LIBSSL])`
    substitutes it), so one is not a second producer of the other."""
    if call.fn == "checks.AC_SUBST":
        return {("subst", str(call.args[0]))} if call.args else set()
    out: set[tuple[str, str]] = set()
    for name in provided_names(call):
        kind = "cache" if name == call.kwargs.get("name") or "_cv_" in name else "define"
        out.add((kind, name))
        if kind == "define" and call.kwargs.get("subst") is True:
            out.add(("subst", name))
    return out


def provided_names(call: Call) -> set[str]:
    """The cache variables and defines a check produces."""
    short = call.fn.split(".")[-1]
    out: set[str] = set()
    first = call.args[0] if call.args else None
    if short in _PLURAL_CHECKS and isinstance(first, list):
        for name in first:
            out.add(cache_var(short, str(name)))
            out.add(define_of(short, str(name)))
    elif short in _SUBJECT_CHECKS and isinstance(first, str):
        out.add(cache_var(short, first))  # the HAVE_* define only with `define =` (see the kwargs below)
    elif short in ("AC_CHECK_SIZEOF", "AC_CHECK_ALIGNOF") and isinstance(first, str):
        kind = "sizeof" if short == "AC_CHECK_SIZEOF" else "alignof"
        out.add(f"ac_cv_{kind}_{cache_name(first)}")
        out.add(f"{kind.upper()}_{cache_name(first).upper()}")
    elif short in _NAMED_CHECKS and isinstance(first, str):
        out.add(first)
    elif short == "AC_CHECK_LIB" and len(call.args) >= 2:
        out.add(f"ac_cv_lib_{cache_name(str(call.args[0]))}_{cache_name(str(call.args[1]))}")
        out.add(f"HAVE_LIB{cache_name(str(call.args[0])).upper()}")
    elif short == "AC_SEARCH_LIBS" and isinstance(first, str):
        out.add(f"ac_cv_search_{cache_name(first)}")
    elif short.startswith("GL_CHECK_FUNCS_") and isinstance(first, list):
        for name in first:
            out.add(f"ac_cv_func_{cache_name(str(name))}")
            out.add(f"HAVE_{cache_name(str(name)).upper()}")
    name = call.kwargs.get("name")
    if isinstance(name, str):
        out.add(name)
    define = call.kwargs.get("define")
    if isinstance(define, str):
        out.add(define)
    return out


def define_of(macro: str, subject: str) -> str:
    """The HAVE_* define autoconf's check macros make from a subject."""
    base = macro.replace("_ONCE", "")
    prefix = "HAVE_DECL_" if base in ("AC_CHECK_DECL", "AC_CHECK_DECLS") else "HAVE_"
    return prefix + re.sub(r"[^A-Za-z0-9_]", "_", subject.strip().replace("*", "P")).upper()


def condition_names(expression: str) -> list[str]:
    """The variables a `requires` entry or `condition` string refers to."""
    out: list[str] = []
    for name in _CONDITION_NAME_RE.findall(expression):
        if name not in out:
            out.append(name)
    return out
