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
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import libraries
from tools.convert import decisions as dec
from tools.convert import lower, m4args
from tools.convert import shparse as sh
from tools.convert.index import PortIndex, Resolution
from tools.convert.ir import CPU_FAMILY, OS_FAMILY, PLATFORM_FAMILIES, Call, Loc, Package, Raw, Target, family_of_labels
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

# Calls that carry no information for a port.
IGNORED = {
    "AC_MSG_WARN", "AC_MSG_CHECKING", "AC_MSG_RESULT", "AC_MSG_NOTICE",
    "AC_CONFIG_COMMANDS", "AC_CONFIG_MACRO_DIRS", "AC_CONFIG_MACRO_DIR", "AC_CONFIG_AUX_DIR",
    "AC_CONFIG_SRCDIR", "AC_OUTPUT", "AC_ARG_VAR", "AC_PREREQ",
}

# Compiler-warning machinery: flags belong in copts, not in checks.
WARNING_MACROS = {
    "gl_WARN_ADD", "gl_MANYWARN_ALL_GCC", "gl_MANYWARN_COMPLEMENT", "gl_CC_GNULIB_WARNINGS",
    "gl_CC_ALLOW_WARNINGS", "gl_CXX_ALLOW_WARNINGS", "gl_COMPILER_OPTION_IF", "gl_UNKNOWN_WARNINGS_ARE_ERRORS",
    "gl_GCC_VERSION_IFELSE", "gl_WARN_ADD_IF_SUPPORTED",
}

PROBES = {"AC_COMPILE_IFELSE", "AC_LINK_IFELSE", "AC_PREPROC_IFELSE", "AC_RUN_IFELSE"}

# Configure options automake, libtool and gnulib add for their own machinery.
TOOLING_OPTIONS = {
    "dependency-tracking", "silent-rules", "maintainer-mode", "libtool-lock", "shared", "static",
    "fast-install", "rpath", "nls", "largefile", "year2038", "cross-guesses", "option-checking",
    "pic", "gnu-ld", "sysroot", "aix-soname",
}

# libtool's macro files: everything defined there probes the linker and the
# shared-library conventions of the host, which Bazel's C++ toolchain owns.
# Nothing of it belongs in a port, so the DEFUNs are dropped wholesale.
LIBTOOL_FILES = {"libtool.m4", "ltoptions.m4", "ltsugar.m4", "ltversion.m4", "lt~obsolete.m4", "ltdl.m4"}

# Variables of the configure environment and the compiler driver: no check
# ever produces them, so a condition over one of them has no translation.
ENVIRONMENT_VARIABLES = {
    "CC", "CXX", "CPP", "CXXCPP", "GCC", "GXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "LIBS",
    "LIBOBJS", "LTLIBOBJS", "EXEEXT", "OBJEXT", "AR", "ARFLAGS", "RANLIB", "LD", "NM", "STRIP", "INSTALL",
    "MAKE", "SHELL", "CONFIG_SHELL", "PATH", "IFS", "YACC", "LEX", "PKG_CONFIG", "DEFS", "ECHO_N", "ECHO_C",
    "ECHO_T", "LN_S", "SED", "GREP", "EGREP", "FGREP", "AWK", "MKDIR_P",
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
# AC_EGREP_CPP programs of the form `#if !defined X` / token / `#endif`,
# repeated: the token appears iff some macro is undefined.
# `#if !defined X` and `#ifndef X` around a token: the token appears iff X is undefined.
_EGREP_BLOCK_RE = re.compile(
    r"#\s*(?:if\s*!\s*defined|ifndef)\s*\(?\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)?\s*\n\s*(\S+)\s*\n\s*#\s*endif")


@dataclass
class Options:
    repo: str = ""  # label prefix for ports: "" (this repo) or "@rules_cc_autoconf"
    in_repo: bool = True  # load `autoconf_cache` as the hand ports do
    layout: str = "per-file"  # "per-file" | "package"
    package_prefix: str = "m4/"  # per-file layout: packages land at <prefix><stem>
    gnulib_commit: str | None = None  # docstring URL for unported gnulib files


@dataclass
class FlagInfo:
    macro: str  # AC_ARG_ENABLE | AC_ARG_WITH
    option: str  # as written: "dfltcc", "gcc-warnings"
    help: str
    default: bool = False


@dataclass
class ProjectState:
    """What project mode collects beyond the targets."""

    flatten_files: set[str] = field(default_factory=set)  # DEFUNs expanded inline (gnulib-comp.m4)
    gnulib_packages: dict[str, str] = field(default_factory=dict)  # package -> aggregator label
    gnulib_defaults: dict[str, str] = field(default_factory=dict)
    """Header ports whose `_DEFAULTS` the script AC_REQUIREs: package -> its `:defaults` label (default_deps)."""
    src_conditions: dict[str, list[df.Cond]] = field(default_factory=dict)  # GL_COND_OBJ_X -> conds
    cond_headers: dict[str, str] = field(default_factory=dict)  # header -> GL_GENERATE var
    header_conditions: dict[str, df.Cond] = field(default_factory=dict)  # GL_GENERATE var -> when it is true
    src_condition_exprs: dict[str, str] = field(default_factory=dict)  # GL_COND_OBJ_X -> its lowered expression
    indicator_defines: list[str] = field(default_factory=list)
    indicator_test_defines: dict[str, str] = field(default_factory=dict)  # GNULIB_X -> IN_PKG_GNULIB_TESTS
    indicator_substs: list[str] = field(default_factory=list)
    flags: dict[str, FlagInfo] = field(default_factory=dict)  # bool_flag name -> info
    init_args: list[str] | None = None
    automake: bool = False
    config_headers: list[str] = field(default_factory=list)
    config_subdirs: list[str] = field(default_factory=list)  # AC_CONFIG_SUBDIRS: sub-configures, literal names
    config_files: list[str] = field(default_factory=list)
    am_conditionals: dict[str, df.Cond] = field(default_factory=dict)
    library_label: str | None = None
    library_archive: str | None = None  # e.g. "libgreputils.a", for LDADD lines that name it
    private_headers_label: str | None = None
    private_header_libs: dict[str, str] = field(default_factory=dict)  # library label -> its _private_headers label
    config_targets: list[str] = field(default_factory=list)  # autoconf_hdr target names, first is "config"
    tarname: str = ""  # PACKAGE_TARNAME: automake's $(PACKAGE) in installation directories
    header_libraries: list[str] = field(default_factory=list)  # config_headers (+ top_headers) labels
    literal_substs: dict[str, str] = field(default_factory=dict)  # unconditional AC_SUBST literals
    port_ambiguous: set[str] = field(default_factory=set)  # names that are a port subst and a define (index)
    port_substs: set[str] = field(default_factory=set)  # substitutions the ports provide (index)
    port_conditionals: dict[str, str] = field(default_factory=dict)  # automake conditionals the ports spell
    # A standard the script forces with CFLAGS=-std=... / CXXFLAGS=-std=...: a requirement check
    # (checks.AC_LANG_STANDARD) of the main target, since a Bazel build's standard is the root module's.
    c_standard: str | None = None
    cxx_standard: str | None = None
    # Standards already covered by a requirement check (AC_PROG_CC_C99, AX_CXX_COMPILE_STDCXX, or one
    # added for the above), by their ISO name (`gnu11` -> `c11`): a second one would be a duplicate.
    required_standards: set[str] = field(default_factory=set)
    makefile_standards: dict[str, str] = field(default_factory=dict)  # `-std=` found in a Makefile's flags, per language
    templates: list[Path] = field(default_factory=list)  # the .in.h templates of the gnulib library

    # Configure options as `select()` keys: the config_setting labels the
    # output uses (only those get a flag rendered) and the values the
    # project compares each option with (`--with-ssl=openssl`).
    option_settings: dict[str, tuple[str, str]] = field(default_factory=dict)  # label -> (option, value)
    flag_values: dict[str, set[str]] = field(default_factory=dict)
    flag_defaults: dict[str, str] = field(default_factory=dict)  # literal the not-given branch assigns
    external_modules: dict[str, str] = field(default_factory=dict)
    """Registry modules (tools/convert/libraries.py) MODULE.bazel declares: module -> version."""

    configure_libs: dict[str, str] = field(default_factory=dict)
    """Registry libraries configure puts in LIBS (AC_CHECK_LIB, AC_SEARCH_LIBS, `-l` by hand), which
    every target links: `-l` name -> label."""

    def label(self, name: str, value: str) -> str:
        label = f":{name}_enabled" if value == "yes" else f":{name}_{slug(value)}"
        self.option_settings[label] = (name, value)
        return label

    def values(self, name: str) -> set[str]:
        return self.flag_values.get(name, set())


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
    package: Package
    lang: list[str]
    analysis: df.Analysis
    file: str | None = None
    locator: CallLocator | None = None
    cache: str | None = None
    cache_comment: str | None = None  # AC_CACHE_CHECK text, attached to its probe
    guard: df.Cond = df.TRUE  # inherited by the children of a transparent call
    probe_count: int = 0
    explicit_substs: dict[str, str] = field(default_factory=dict)  # var -> source comment
    probe_vars: set[str] = field(default_factory=set)  # cache vars bound to probes
    defines: set[str] = field(default_factory=set)  # names AC_DEFINE'd in this body
    dropped: set[str] = field(default_factory=set)  # cache vars whose probe was left out (undecided / IGNORE)
    auto_variables: bool = True  # emit ALLCAPS shell variables as substs
    root: bool = False  # the configure.ac top level

    @property
    def language(self) -> str:
        return self.lang[-1] if self.lang else "c"

    @property
    def where(self) -> str:
        return f"{self.target.name} ({self.file or '?'})"

    def inner(self, cache: str | None, cache_comment: str | None, guard: df.Cond) -> "_Ctx":
        return _Ctx(target=self.target, package=self.package, lang=self.lang, analysis=self.analysis,
                    file=self.file, locator=self.locator, cache=cache, cache_comment=cache_comment,
                    guard=guard, probe_count=self.probe_count, explicit_substs=self.explicit_substs,
                    probe_vars=self.probe_vars, defines=self.defines, dropped=self.dropped,
                    auto_variables=self.auto_variables, root=self.root)


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
        # Every cache variable and ALLCAPS shell variable something in the
        # whole configure script sets. One outside this set is never set:
        # configure reads it as empty, and so does the translation.
        self.cache_vars_set = self._collect_cache_vars(tree)
        if self.project is not None:
            for option, values in self._collect_option_values(tree).items():
                self.project.flag_values.setdefault(option, set()).update(values)
            for lang, flag in self._collect_standard_flags(tree):
                self._set_standard(lang, flag[len("-std="):])
        for text in m4_sources.values():  # ports' m4 files, which the expansion may not spell out
            self.cache_vars_set.update(_CACHE_ASSIGN_RE.findall(text))
            self.cache_vars_set.update(n for groups in _ALLCAPS_ASSIGN_RE.findall(text) for n in groups if n)

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

        def texts(items) -> None:
            for item in items:
                if isinstance(item, Text):
                    out.update(_CACHE_ASSIGN_RE.findall(item.text))
                    out.update(n for groups in _ALLCAPS_ASSIGN_RE.findall(item.text) for n in groups if n)
                else:
                    texts(item.items)

        texts(tree.items)
        for node in tree.walk():
            if node.kind != "call":
                continue
            args = node.args + ["", "", ""]
            if node.name == "AC_CACHE_CHECK":
                out.update(m4_cache_name(args[1].strip(), lang) for lang in ("c", "cpp"))
            elif node.name == "AC_CACHE_VAL":
                out.update(m4_cache_name(args[0].strip(), lang) for lang in ("c", "cpp"))
            elif node.name in PROBES:
                out.update(v for v in (action_var(args[1]), action_var(args[2])) if v)
            elif node.name in ("AC_CHECK_HEADERS", "AC_CHECK_HEADERS_ONCE", "AC_CHECK_FUNCS", "AC_CHECK_FUNCS_ONCE",
                               "AC_REPLACE_FUNCS", "AC_CHECK_DECLS", "AC_CHECK_DECLS_ONCE", "AC_CHECK_TYPES",
                               "AC_CHECK_MEMBERS"):
                words = args[0].split(",") if node.name in ("AC_CHECK_DECLS", "AC_CHECK_DECLS_ONCE", "AC_CHECK_TYPES",
                                                            "AC_CHECK_MEMBERS") else m4args.words(args[0])
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

        def texts(items) -> None:
            for item in items:
                if isinstance(item, Text):
                    for option, value in _OPTION_COMPARE_RE.findall(item.text):
                        if value not in df.YES and value not in df.NO:
                            out.setdefault(option, set()).add(value)
                else:
                    texts(item.items)

        texts(tree.items)
        return out

    @staticmethod
    def _collect_standard_flags(tree: Tree) -> list[tuple[str, str]]:
        """`CFLAGS="$CFLAGS -std=gnu99"` in the script: a language standard every
        later check (and the project's compiles) runs under."""
        out: list[tuple[str, str]] = []

        def texts(items) -> None:
            for item in items:
                if isinstance(item, Text):
                    for lang, flag in _STANDARD_FLAG_RE.findall(item.text):
                        if (lang, flag) not in out:
                            out.append((lang, flag))
                else:
                    texts(item.items)

        texts(tree.items)
        return out

    def _never_set(self, name: str) -> bool:
        """A cache variable or ALLCAPS variable (`$ISC`) nothing in the
        configure script sets (project mode: the expansion is the whole
        script; a single m4 file's expansion lacks the other modules that set
        its inputs). Variables of the environment stay unknown."""
        if self.project is None or name in self.cache_vars_set or name in ENVIRONMENT_VARIABLES:
            return False
        return "_cv_" in name or bool(_ALLCAPS_RE.match(name))

    # -- decisions ------------------------------------------------------------

    def _request(self, key: str, kind: str, ctx: _Ctx, summary: str, context: dict | None = None, *,
                 node: Node | None = None, macro: tuple[str, str | None] | None = None,
                 shell: list[str] | None = None) -> dict | None:
        """The decision for `key`, or None after recording the request.

        `node` or `macro` (name, first argument) names the m4 call the decision
        replaces; `shell` lists shell statements of the m4 to point at.  Their
        source text leads the request's context so the decisions file shows
        what is being decided.
        """
        full: dict = {}
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
            candidates += [(file, source, CallLocator(source, Span(1, len(source.lines))))
                           for file, source in self._other_sources(ctx)]
        for file, source, locator in candidates:
            line = locator.find(macro, first_arg)
            if line is None:
                continue
            call = source.defun_span(line)
            label = f"{file}:{call.start}" + (f"-{call.end}" if call.end != call.start else "")
            return label, [l.rstrip() for l in source.lines[call.start - 1:call.end]]
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
                    hit = next((n for n in range(span.start, span.end + 1)
                                if needle in " ".join(source.lines[n - 1].split())), None)
                    if hit is not None:
                        entry = f"{file}:~{hit}: {source.lines[hit - 1].strip()}"
                        break
                if entry:
                    break
            if entry and entry not in out:
                out.append(entry)
        return out

    # -- selection ----------------------------------------------------------

    def _is_flattened(self, node: Node) -> bool:
        return (self.project is not None and node.loc_file is not None
                and basename(node.loc_file) in self.project.flatten_files)

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
        start = next((i for i, it in enumerate(items)
                      if isinstance(it, Node) and it.kind == "call" and it.name == "AC_INIT"), None)
        end = next((i for i, it in enumerate(items)
                    if isinstance(it, Node) and it.kind == "call" and it.name == "AC_OUTPUT"), None)
        prelude: list[str] = []
        if start is not None:
            # `m4_divert_text([INIT_PREPARE], [gl_printf_safe=yes])` lands ahead
            # of the body: keep such plain assignments as the body's prelude.
            for item in items[:start]:
                if isinstance(item, Text):
                    prelude += _PRELUDE_ASSIGN_RE.findall(item.text)
            items = items[start:end] if end is not None and end > start else items[start:]
        if prelude:
            items = [Text("\n".join(prelude) + "\n", 0)] + items
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
            target.comments.append(
                f"{node.name}({', '.join('[' + ' '.join(a.split()) + ']' for a in node.args)})")

        analysis = self._analyze(node)
        ctx = _Ctx(target=target, package=package, lang=["c"], analysis=analysis, file=node.loc_file,
                   auto_variables=not root_level, root=root_level)
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
                name, ctx,
                f"{name} is not defined by any m4 file the expansion saw (a missing macro directory, an "
                "autoconf-archive macro, or a tool's .m4 not shipped with the project); its calls were left out",
                {})
        for unknown in analysis.unknowns:
            self.report.note(f"{ctx.where}: shell not followed: {unknown}")
        if target.kind == "autoconf_defaults":
            self._finish_defaults(target)
        return target

    def _finish_defaults(self, target: Target) -> None:
        """A `_DEFAULTS` macro assigns literals: an `autoconf_defaults` target, whose `deps` are
        the other defaults it AC_REQUIREs.  A probe, a conditional value or an ordinary
        dependency in it makes it a plain `autoconf` target instead (its values are then
        results, not defaults), with a note."""
        literal = all(isinstance(c, Call) and c.fn in ("checks.AC_SUBST", "checks.M4_VARIABLE", "checks.AC_DEFINE")
                      and len(c.args) == 2 and not c.kwargs and isinstance(c.args[1], (str, int))
                      for items in [target.checks] + target.arm_lists for c in items)
        if literal and not target.deps and not target.deps_suffix:
            return
        self.report.note(f"{target.name}: not only literal defaults (a probe, a conditional value or an "
                         "ordinary dependency): an autoconf target, its values results rather than defaults")
        target.kind = "autoconf"

    def _analyze(self, node: Node, outer: df.Analysis | None = None) -> df.Analysis:
        """The dataflow of `node`'s shell; `outer` is the caller's analysis,
        whose settled literals (`M4tests_WITNESS=IN_M4_GNULIB_TESTS` at the
        top level) the body sees as configure would at the call."""
        text, undefined = m4args.neutralize_unexpanded(self._placeholder_text(node))
        if self.tarname and "_GNULIB_TESTS" in text:
            witness = "IN_" + re.sub(r"[^A-Z0-9_]", "_", self.tarname.upper()) + "_GNULIB_TESTS"
            text = _TESTS_WITNESS_RE.sub(witness, text)
        for name in undefined:
            self.undefined_macros.setdefault(name, node.name)
        seq = sh.parse(text)
        bindings: dict[str, df.Value] = {}
        if outer is not None and outer.evaluator is not None:
            for var, value in outer.evaluator.state.items():
                if isinstance(value, df.Lit):
                    bindings[var] = value
        bindings.update(self._probe_bindings(node))
        analysis = df.Evaluator(bindings).run(seq)
        # The program lookups' stand-in values win over the lookups' own
        # expansion (`ac_cv_path_M4=$ac_path_M4` inside the loop).
        analysis.program_literals = {var: value.text for var, value in bindings.items()
                                     if isinstance(value, df.Lit) and var in self._program_vars(node)}
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
            parts.append(f"\n{sh.placeholder(item.id)}\n")
            if self.project is not None and item.kind == "call" and item.name in ("AC_ARG_ENABLE", "AC_ARG_WITH"):
                parts.append(self._flag_shell(item))
        return "".join(parts)

    def _placeholder_ids(self, node: Node) -> set[int]:
        """The ids `_placeholder_text` put into the text (through cache bodies)."""
        out: set[int] = set()
        for item in node.items:
            if isinstance(item, Text):
                continue
            if item.kind == "call" and item.name in TRANSPARENT and not self._decided_probe(item):
                out |= self._placeholder_ids(item)
            else:
                out.add(item.id)
        return out

    def _decided_probe(self, node: Node) -> bool:
        """The decisions file has a value for this cache check's variable."""
        padded = node.args + ["", ""]
        var = (padded[1] if node.name == "AC_CACHE_CHECK" else padded[0]).strip()
        value = self.decisions.data.get(f"probe:{var}") if var else None
        return value is not None and not (isinstance(value, dec.Word) and value.name == "UNDECIDED")

    def _flag_shell(self, node: Node) -> str:
        """The shell that stands in for an AC_ARG_ENABLE / AC_ARG_WITH in the analysis: `if <flag
        command>` running the given-branch actions with the option's value as the symbolic
        `$__rccac_optval_<flag>`, else the not-given actions, so later tests of `$enable_x`
        resolve to option atoms. Registers the option as a flag, records the literal values its
        actions compare it with and the default the not-given branch assigns, and honours a
        `flag:` decision (IGNORE drops the option).
        """
        args = node.args + ["", "", "", ""]
        option = args[0].strip()
        if option in TOOLING_OPTIONS:
            return ""  # automake / libtool machinery, not a build option
        flag_name = flag_bool_name(option)
        var_prefix = "enable" if node.name == "AC_ARG_ENABLE" else "with"
        given = m4args.neutralize_macros(m4args.quadrigraphs(m4args.strip_m4(args[2])))
        not_given = m4args.neutralize_macros(m4args.quadrigraphs(m4args.strip_m4(args[3])))
        info = self.project.flags.setdefault(
            flag_name, FlagInfo(node.name, option, help_text(args[1]), default=default_from_help(args[1])))
        decision = self.decisions.optional(f"flag:{flag_name}", "flag")
        if decision:
            info.default = bool(decision.get("default", False))
            if decision.get("ignore"):
                del self.project.flags[flag_name]
                return ""
        # `$enableval` / `$withval` inside the option's own actions compare its value.
        for value in re.findall(r'"?x?\$\{?(?:enable|with)val\}?"?\s*(?:=|==|!=)\s*"?x?([A-Za-z0-9_.+/-]+)"?', given):
            if value not in df.YES and value not in df.NO:
                self.project.flag_values.setdefault(flag_name, set()).add(value)
        default = re.search(rf"\b{var_prefix}_{flag_name}=([A-Za-z0-9_.+/-]+)", not_given)
        if default:
            self.project.flag_defaults[flag_name] = default.group(1)
        optval = f"${df.OPTVAL_PREFIX}{flag_name}"
        return (
            f"\nif {df.flag_command(flag_name)}; then\n"
            f"  {var_prefix}val={optval}\n  {var_prefix}_{flag_name}={optval}\n{given}\nelse\n"
            f"  {var_prefix}_{flag_name}=no\n{not_given}\nfi\n"
        )

    @staticmethod
    def _program_vars(node: Node) -> set[str]:
        """The variables the body's own program lookups set."""
        return {_program_lookup_value(child.name, child.args)[0] for child in node.walk()
                if child.kind == "call" and child.name in _PROGRAM_LOOKUPS and child.args and _directly_in(child, node)}

    def _probe_bindings(self, node: Node) -> dict[str, df.Value]:
        """Cache variables set by probes with synthesized check names."""
        bindings: dict[str, df.Value] = {}
        for child in node.walk():
            if child.kind == "call" and child.name in PROBES and child.name != "AC_RUN_IFELSE":
                cache = child.enclosing("AC_CACHE_CHECK", "AC_CACHE_VAL")
                if cache is None:
                    var = action_var(child.args[1] if len(child.args) > 1 else "")
                    if var:
                        bindings[var] = df.Probe(var)
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

    def _walk(self, items, ctx: _Ctx) -> None:
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
            guard = df.Unknown("shell around the call could not be parsed") \
                if node.id in ctx.analysis.placeholders else df.TRUE
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
        dimensions = [name for name, atoms in (("host OS", df.host_os_atoms(guard)), ("host CPU", df.host_cpu_atoms(guard)),
                                               ("compiler", df.gnuc_atoms(guard))) if atoms]
        if len(dimensions) > 1:
            return None, f"condition mixes {' and '.join(dimensions)} (one select() dimension at a time)"
        if df.host_cpu_atoms(guard):
            return lower.cpu_arms(guard), ""
        if df.gnuc_atoms(guard):
            return lower.compiler_arms(guard), ""
        return lower.os_arms(guard), ""

    def _place(self, items: list, guard: df.Cond, ctx: _Ctx) -> None:
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

    def _place_arm(self, calls: list[Call], cond: df.Cond, dest: list, ctx: _Ctx) -> None:
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
                existing = list(call.kwargs.get("requires", []))
                call.kwargs["requires"] = existing + [r for r in reqs if r not in existing]
            if not any(same_call(call, other) for other in dest if isinstance(other, Call)):
                dest.append(call)

    def _guard_decision(self, calls: list[Call], cond: df.Cond, ctx: _Ctx, reason: str) -> dict | None:
        key = f"guard:{ctx.target.name}:{check_key(calls[0])}"
        return self._request(
            key, "guard", ctx,
            f"the condition under which {check_key(calls[0])} runs cannot be expressed ({reason})",
            {"checks": [check_key(c) for c in calls], "condition": describe(cond)})

    def _drop(self, calls: list[Call], ctx: _Ctx) -> None:
        """Checks left out: what they would have produced is known to be missing,
        so conditions over it become decisions instead of dangling requires."""
        for call in calls:
            ctx.dropped |= provided_names(call)

    def _apply_guard_decision(self, calls: list[Call], decision: dict, ctx: _Ctx, dest: list | None = None) -> None:
        """Apply a `guard` decision to `calls`: NEVER drops them (recording what they would have
        produced), a select() puts copies in each arm (platform arms other conditions opened take
        the default arm's value), a requires list is attached to them, ALWAYS appends them as
        they are.
        """
        if decision.get("never"):
            self._drop(calls, ctx)
            return
        if "select" in decision:
            family = family_of_labels(decision["select"])
            select = dict(decision["select"])
            if family in PLATFORM_FAMILIES and lower.DEFAULT_ARM in select:
                # The default arm is "every other platform": host-OS arms other
                # conditions opened take the default value too.
                for label in ctx.target.selects.get(family, {}):
                    select.setdefault(label, select[lower.DEFAULT_ARM])
            for label, value in select.items():
                arm = ctx.target.arm(label, family)
                copies = [clone(c) for c in calls]
                if isinstance(value, list):
                    for call in copies:
                        call.kwargs["requires"] = list(call.kwargs.get("requires", [])) + value
                for call in copies:
                    if not any(same_call(call, other) for other in arm if isinstance(other, Call)):
                        arm.append(call)
            ctx.target.arm(lower.DEFAULT_ARM, family)
            return
        target_list = dest if dest is not None else ctx.target.checks
        for call in calls:
            if "requires" in decision:
                call.kwargs["requires"] = list(call.kwargs.get("requires", [])) + list(decision["requires"])
            if not any(same_call(call, other) for other in target_list if isinstance(other, Call)):
                target_list.append(call)

    # -- deps -----------------------------------------------------------------

    def _loc(self, ctx: _Ctx, macro: str, first_arg: str | None) -> Loc | None:
        if ctx.locator is None or ctx.file is None:
            return None
        line = ctx.locator.find(macro, first_arg)
        if line is None:
            return None
        return Loc(ctx.file, line, approx=True)

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
            port = self.index.by_package[resolution.package]
            self.project.gnulib_packages[resolution.package] = f"{self.options.repo}{port.label()}"
            return
        ctx.target.add_dep(self._port_label(resolution), comment)

    def _macro_decision(self, name: str, ctx: _Ctx, summary: str, context: dict,
                        macro: tuple[str, str | None] | None = None) -> None:
        """Apply the `macro:<name>` decision (dep / checks / ignore) or record the request."""
        decision = self._request(f"macro:{name}", "macro", ctx, summary, context, macro=macro or (name, None))
        if decision is None:
            self.report.unresolved.add(name)
            return
        if decision.get("ignore"):
            return
        if "dep" in decision:
            ctx.target.add_dep(decision["dep"], f"{name} (decisions file)")
        ctx.target.checks.extend(decision.get("checks", []))

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
        comment = f"AC_REQUIRE([{name}])" + (f" via {via}" if via else "")
        if when is not df.TRUE:
            comment += f" (when {describe(when)})"
        if name in self._local_names(file):
            if self.project is None:
                if name.endswith("_DEFAULTS"):
                    ctx.target.add_default_dep(self._local_label(name, file, ctx), comment)
                else:
                    ctx.target.add_dep(self._local_label(name, file, ctx), comment)
            return  # project mode: the required body is expanded where m4 hoisted it
        if self.project is not None and file is not None and basename(file) in self.project.flatten_files:
            return  # gl_EARLY / gl_INIT: expanded inline where they are called
        resolution = self.index.resolve(name, file) if self.index else None
        if resolution is None:
            if via is not None:
                return
            if _BUILTIN_RE.match(name):
                self.report.note(f"{ctx.target.name}: AC_REQUIRE([{name}]) has no port (autoconf builtin); dropped")
            else:
                self._macro_decision(name, ctx, f"AC_REQUIRE([{name}]) has no port", {"defined_in": file},
                                     macro=("AC_REQUIRE", name))
            return
        if via is not None and not (resolution.exact or resolution.reason == "file"):
            return
        if name.endswith("_DEFAULTS"):
            # The `_DEFAULTS` targets are private to their package: its public
            # `:defaults`, read through default_deps, stands for them.
            resolution = self.index.by_package[resolution.package].defaults_resolution()
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

    def _nested_defun(self, node: Node, ctx: _Ctx) -> None:  # noqa: C901
        """A macro call expanded inside the body being translated, dispatched by what it is: libtool
        noise, a gnulib module macro in project mode (a dep on its port or inlined), warning-flag
        macros (noted), shared probe helpers (their own target or a port), inline helper macros
        (list calls with the guard as condition), a ported macro (a dep), a selected macro (a
        dep, or inlined in project mode), anything else a `macro` decision.
        """
        name = node.name
        if node.is_internal:
            return
        if node.loc_file and basename(node.loc_file) in LIBTOOL_FILES:
            self._libtool_note(name)
            return
        guard = self._guard(node, ctx)
        if self.project is not None and self._gnulib_project_macro(node, ctx, guard):
            return
        if name in WARNING_MACROS:
            self.report.note(f"{name}: compiler warning flags are not modeled (set copts instead)")
            return
        if name in HELPER_TARGET_MACROS:
            self._helper_target(node, ctx, guard)
            return
        if name in HELPER_INLINE_MACROS:
            call = Call(HELPER_INLINE_MACROS[name], [m4args.words(node.args[0])] if node.args else [[]],
                        comments=[self._source_comment(node, ctx)], returns_list=True)
            lowered = lower.expression(guard)
            if lowered.ok and lowered.expr:
                call.kwargs["condition"] = lowered.expr
            elif not lowered.ok:
                decision = self._guard_decision([call], guard, ctx, lowered.unknown or "?")
                if decision is None:
                    return
                if decision.get("never"):
                    return
                if "requires" in decision:
                    call.kwargs["condition"] = " && ".join(decision["requires"])
            ctx.target.checks.append(call)
            self._harvest_requires(node, ctx)
            return
        comment = self._source_comment(node, ctx)
        if guard is not df.TRUE:
            comment += f" (when {describe(guard)})"
        resolution = self.index.resolve(name, node.loc_file) if self.index else None
        if self._is_flattened(node):
            self._inline(node, ctx, guard)
            return
        if resolution and resolution.exact and not self._is_selected(node):
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
        self._walk(node.items, inner)
        ctx.probe_count = inner.probe_count

    def _gnulib_project_macro(self, node: Node, ctx: _Ctx, guard: df.Cond) -> bool:
        """gl_INIT conventions: conditionals, conditional headers, indicators."""
        name = node.name
        args = node.args + ["", ""]
        state = self.project
        if name == "gl_CONDITIONAL":
            cond_name = args[0].strip()
            cond = self._test_condition(args[1], ctx, guard)
            state.src_conditions.setdefault(cond_name, []).append(df.conj(guard, cond))
            return True
        if name == "gl_CONDITIONAL_HEADER":
            # gl_CONDITIONAL_HEADER([stdckdint.h]): GL_GENERATE_STDCKDINT_H is
            # true when the shell variable STDCKDINT_H is non-empty (a module
            # whose code gnulib-comp.m4 carries inline, as the newer header
            # modules do: no m4 file, so no port provides the variable).
            header = args[0].strip()
            shell_var = re.sub(r"[^A-Za-z0-9]", "_", header).upper()
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
                state.header_conditions[var] = df.disj(state.header_conditions[var], cond) \
                    if var in state.header_conditions else cond
            return True
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
                state.indicator_defines.append("GNULIB_" + re.sub(r"[^A-Za-z0-9]", "_", args[0].strip()).upper())
            return True
        if name in ("gl_MODULE_INDICATOR_INIT_VARIABLE", "gl_MODULE_INDICATOR_SET_VARIABLE"):
            return True
        if _MODULE_INDICATOR_RE.match(name):
            state.indicator_substs.append("GNULIB_" + re.sub(r"[^A-Za-z0-9]", "_", args[0].strip()).upper())
            # gl_<HEADER>_MODULE_INDICATOR also runs gl_MODULE_INDICATOR_FOR_TESTS:
            # GNULIB_TEST_<MODULE> in config.h.
            for define in node.calls("AC_DEFINE") + node.calls("AC_DEFINE_UNQUOTED"):
                expanded = expanded_define(define)
                if expanded is not None and expanded[0].startswith("GNULIB_TEST_"):
                    state.indicator_test_defines[expanded[0]] = literal(expanded[1])
            return True
        if name in ("gl_LIBOBJ", "gl_REPLACE_FUNCS", "AC_LIBOBJ", "AC_REPLACE_FUNCS"):
            # Inside gl_INIT, AC_LIBOBJ is pushdef'd to gl_LIBOBJ: the range is
            # named after the macro as called.
            for word in m4args.words(args[0]):
                self._libobj(word, guard)
            return True
        if name == "AM_INIT_AUTOMAKE":
            state.automake = True
            return True
        if name == "AM_CONDITIONAL":
            if args[0].strip():
                state.am_conditionals[args[0].strip()] = df.conj(guard, self._test_condition(args[1], ctx, guard))
            return True
        if name in ("gl_COMMON", "gl_COMMON_BODY", "gl_ASSERT_NO_GNULIB_POSIXCHECK", "gl_ASSERT_NO_GNULIB_TESTS",
                    "gl_PROG_AR_RANLIB", "gl_FILE_LIST", "gl_LIBSOURCES", "AM_COND_IF", "AM_MISSING_PROG",
                    "AM_SILENT_RULES", "AM_MAINTAINER_MODE"):
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
                evaluator = ctx.analysis.evaluator if ctx is not None and ctx.analysis.evaluator else None
                if evaluator is not None:
                    return evaluator.condition_of(expr, guard)
                return df.Evaluator().expr(expr)
        return df.Unknown(shell)

    def _libobj(self, name: str, guard: df.Cond) -> None:
        cond_name = "GL_COND_OBJ_" + re.sub(r"[^A-Za-z0-9]", "_", name.strip()).upper()
        self.project.src_conditions.setdefault(cond_name, []).append(guard)

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

    def _add_check_port_dep(self, resolution: Resolution, ctx: _Ctx, comment: str) -> None:
        self._add_port_dep(resolution, ctx, comment)

    def _helper_target(self, node: Node, ctx: _Ctx, guard: df.Cond) -> None:
        """A shared probe macro (HELPER_TARGET_MACROS, AC_FUNC_X style): a dep on the port that
        carries the probe when one does, else one helper target per (macro, function) in the
        package, so several modules can depend on the same probe and define.
        """
        func = node.args[0].strip() if node.args else ""
        name = f"{node.name}_{slug(func)}"
        port = self._port_for_check([name, f"HAVE_{cache_name(func).upper()}", f"ac_cv_func_{cache_name(func)}"])
        if port is not None:
            comment = self._source_comment(node, ctx) + " (the port's shared probe)"
            if guard is not df.TRUE:
                comment += f" (when {describe(guard)})"
            self._add_check_port_dep(port, ctx, comment)
            return
        helper = ctx.package.target(name)
        if helper is None:
            kwargs = {}
            if len(node.args) > 1 and node.args[1].strip():
                kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(node.args[1])))
            call = Call(HELPER_TARGET_MACROS[node.name], [[func]], kwargs, returns_list=True)
            helper = Target(name=name, checks=[call], loc=Loc(node.loc_file or "?", node.loc_line),
                            helper=True)
            helper.comments.append(
                f"{node.name}([{func}]{', [...]' if len(node.args) > 1 else ''}): shared probe and define; "
                "its own target so other modules can depend on it.")
            ctx.package.targets.append(helper)
        comment = self._source_comment(node, ctx)
        if guard is not df.TRUE:
            comment += f" (when {describe(guard)})"
        ctx.target.add_dep(f":{name}", comment)

    # -- leaf calls -----------------------------------------------------------

    def _call(self, node: Node, ctx: _Ctx) -> None:  # noqa: C901 - a dispatch table
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
            resolution = self.index.resolve(name) if self.index else None
            if resolution is not None and resolution.exact:
                guard = self._guard(node, ctx)
                self._add_port_dep(resolution, ctx, comment + (f" (when {describe(guard)})" if guard is not df.TRUE else ""))
                return
            if name in BUILTIN_CHECKS:
                calls, note = BUILTIN_CHECKS[name]
                items = [clone(c) for c in calls]
                for item in items:
                    item.comments.insert(0, f"{comment}: GNU autoconf builtin, translated by the converter")
                if note:
                    self.report.note(note)
                self._place(items, self._guard(node, ctx), ctx)
                return
            if _BUILTIN_RE.match(name) and not _CHECK_BUILTIN_RE.match(name):
                # A library macro without a port (AC_PROG_CPP, AC_PROG_GREP...):
                # its internals were recorded under this call and are not
                # the project's own checks.
                self.report.note(f"{name}: no port in rules_cc_autoconf; dropped ({comment})")
                return
            summary = (f"{name} is an autoconf builtin without a port in rules_cc_autoconf ({comment})"
                       if _BUILTIN_RE.match(name) else f"no translation for {comment}")
            self._macro_decision(name, ctx, summary, {"arguments": args}, macro=(name, args[0] if args else None))
            return
        items = handler(node, ctx) or []
        for item in items:
            if isinstance(item, Call):
                item.comments.insert(0, comment)
                if ctx.cache_comment:
                    item.comments.insert(0, ctx.cache_comment)
                    ctx.cache_comment = None
        self._place(items, self._guard(node, ctx), ctx)
        self._harvest_requires(node, ctx)

    def _cache_check(self, node: Node, ctx: _Ctx) -> None:
        """AC_CACHE_CHECK / AC_CACHE_VAL: look through to the probe inside."""
        padded = node.args + ["", "", ""]
        var, commands = (padded[1], padded[2]) if node.name == "AC_CACHE_CHECK" else (padded[0], padded[1])
        var = m4_cache_name(var.strip(), ctx.language)
        comment = self._source_comment(node, ctx)
        guard = self._guard(node, ctx)
        if "$" in var:
            # `AC_CACHE_CHECK([...], [$gt_func_gnugettext_libc], ...)`: the
            # variable is named by the shell; no check can be attached to it.
            decision = self._request(
                f"probe:{ctx.target.name}:{slug(var)}", "probe", ctx,
                f"{comment}: the cache variable's name is computed by the shell ({var}); "
                "only replacement checks (or IGNORE) can stand for it",
                {"cache_variable": var, "commands": block_lines(m4args.quadrigraphs(commands))}, node=node)
            if decision is not None:
                ctx.target.checks.extend(decision.get("checks", []))
                if not decision.get("ignore") and "checks" not in decision:
                    self.report.note(f"{comment}: the decision for {var} must be IGNORE or checks; left out")
            return
        inner = ctx.inner(var or None, comment, guard)
        probes = [c for c in node.children if c.kind == "call" and c.name in PROBES]
        if m4args.parse_call(commands) is None and commands.strip() and var and self._cache_from_shell(node, ctx, var, guard, comment):
            return
        if m4args.parse_call(commands) is None and commands.strip():
            # The commands do more than one probe call (save/restore flags,
            # several probes, shell logic): the probe alone may not be it.
            key = f"probe:{var or ctx.target.name}"
            decision = self._request(
                key, "probe", ctx,
                f"{comment}: the cache value comes from shell around the probe, not from one probe",
                {"commands": block_lines(m4args.quadrigraphs(commands)),
                 "programs": [block_lines(p.program() or "") for p in probes]}, node=node)
            if decision is None or decision.get("ignore"):
                if var:
                    ctx.dropped.add(var)
                return
            if not decision.get("accept"):
                if var:
                    self._apply_probe_decision(var, decision, ctx, guard, comment)
                return
        self._walk([i for i in node.items if not isinstance(i, Text)], inner)
        ctx.probe_count = inner.probe_count

    def _cache_from_shell(self, node: Node, ctx: _Ctx, var: str, guard: df.Cond, comment: str) -> bool:
        """`AC_CACHE_VAL([var], [shell that calls other macros and assigns var])`:
        translate the nested calls, then `var` from the body's assignments
        (`M4_VARIABLE` with the conditions over the nested results).  False
        when the body assigns `var` nowhere the dataflow can follow."""
        decisions = ctx.analysis.decisions(var)
        if not decisions or any(not isinstance(d.value, df.Lit) for d in decisions):
            return False
        # The nested checks first (the probes the assignments branch on).
        inner = ctx.inner(None, None, guard)
        self._walk([i for i in node.items if not isinstance(i, Text)], inner)
        ctx.probe_count = inner.probe_count
        ctx.probe_vars.add(var)
        decisions = [df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text) for d in decisions]
        check, dropped = self._lower_decisions(var, [(d.cond, d) for d in decisions], "checks.M4_VARIABLE", False)
        if check is not None and not dropped:
            check.comments.insert(0, f"{comment}: {var} from the shell of the cache commands")
            if not any(same_call(check, other) for other in ctx.target.checks if isinstance(other, Call)):
                ctx.target.checks.append(check)
            return True
        # Several values on several paths: no single check spells it, but every
        # `test $var = value` in this body already became the matching
        # condition over the probes.  Uses from other bodies become decisions.
        ctx.probe_vars.discard(var)
        self.dropped_global.add(var)
        self.report.note(f"{comment}: {var} takes {len(decisions)} values on different paths; the tests on it "
                         "in this body were rewritten to the probes it depends on")
        return True

    def _project_call(self, node: Node, ctx: _Ctx) -> bool:
        """Top-level configure.ac macros that shape the project (project mode)."""
        name = node.name
        args = node.args
        state = self.project
        if name == "AC_INIT":
            state.init_args = [m4args.quadrigraphs(a) for a in args]
            return True
        if name == "AM_INIT_AUTOMAKE":
            state.automake = True
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
            for word in words:
                if "$" in word:
                    self.report.note(f"AC_CONFIG_SUBDIRS({word}): computed at configure time; not followed")
            return True
        if name in ("AC_ARG_ENABLE", "AC_ARG_WITH"):
            return True  # modeled by the synthesized shell around the placeholder
        if name == "AC_LIBOBJ":
            if args:
                self._libobj(args[0], self._guard(node, ctx))
            return True
        if name == "AM_CONDITIONAL":
            if args:
                guard = self._guard(node, ctx)
                state.am_conditionals[args[0].strip()] = df.conj(
                    guard, self._test_condition(args[1] if len(args) > 1 else "", ctx, guard))
            return True
        return False

    # -- decided checks -------------------------------------------------------

    def _apply_probe_decision(self, var: str, decision: dict, ctx: _Ctx, guard: df.Cond, comment: str) -> None:
        """A decided value or replacement probe for cache variable `var`."""
        if decision.get("ignore"):
            return
        ctx.probe_vars.add(var)
        if "values" in decision:
            family = family_of_labels(decision["values"])
            for label, value in decision["values"].items():
                call = Call("checks.M4_VARIABLE", [var, value], comments=[f"{comment} (decisions file)"])
                dest = ctx.target.checks if label == lower.DEFAULT_ARM and len(decision["values"]) == 1 \
                    else ctx.target.arm(label, family)
                dest.append(call)
            if len(decision["values"]) > 1:
                ctx.target.arm(lower.DEFAULT_ARM, family)
            return
        if "value" in decision:
            self._place([Call("checks.M4_VARIABLE", [var, decision["value"]], comments=[f"{comment} (decisions file)"])],
                        guard, ctx)
            return
        for kind in ("try_compile", "try_link"):
            if kind in decision:
                call = clone(decision[kind])
                call.comments = [f"{comment} (decisions file)"]
                self._place([call], guard, ctx)
                return
        ctx.target.checks.extend(decision.get("checks", []))
        if not any(k in decision for k in ("values", "value", "try_compile", "try_link", "checks", "accept", "ignore")):
            raise ValueError(f"decision for probe:{var} has no known form: {decision}")

    def _apply_variable_decision(self, var: str, kind: str, decision: dict, ctx: _Ctx, comment: str) -> None:
        """Emit the checks a `variable:` decision asks for: a literal per select arm (`values`), a
        condition / if_true / if_false subst, one literal (optionally under requires), or
        verbatim checks; IGNORE emits nothing.
        """
        if decision.get("ignore"):
            return
        comments = [f"{comment} (decisions file)"]
        if "values" in decision:
            family = family_of_labels(decision["values"])
            for label, value in decision["values"].items():
                dest = ctx.target.checks if label == lower.DEFAULT_ARM and len(decision["values"]) == 1 \
                    else ctx.target.arm(label, family)
                dest.append(Call(kind, [var, literal(str(value))], comments=comments))
            if len(decision["values"]) > 1:
                ctx.target.arm(lower.DEFAULT_ARM, family)
            return
        if "condition" in decision:
            kwargs = {"condition": decision["condition"]}
            for key in ("if_true", "if_false"):
                if key in decision:
                    kwargs[key] = literal(str(decision[key]))
            ctx.target.checks.append(Call(kind, [var], kwargs, comments=comments))
            return
        if "value" in decision:
            kwargs = {}
            if "requires" in decision:
                kwargs["requires"] = list(decision["requires"])
            ctx.target.checks.append(Call(kind, [var, literal(str(decision["value"]))], kwargs, comments=comments))
            return
        ctx.target.checks.extend(decision.get("checks", []))
        if not any(k in decision for k in ("values", "condition", "value", "checks", "accept_partial")):
            raise ValueError(f"decision for variable:{var} has no known form: {decision}")

    def _actions_decision(self, node: Node, ctx: _Ctx, actions: list[tuple[str, str]]) -> list:
        """Action arguments with no mechanical translation: a decision or nothing."""
        first = node.args[0].split("\n")[0].strip() if node.args else ""
        key = f"actions:{node.name}:{slug(first)}"
        decision = self._request(
            key, "actions", ctx, f"{node.name}([{first}]) has actions the converter does not translate",
            {label: text.strip() for label, text in actions}, node=node)
        if decision is None or decision.get("ignore"):
            return []
        return list(decision.get("checks", []))

    def _action_defines(self, node: Node, cache: str, actions: list[tuple[str, str]]) -> tuple[list, list]:
        """`AC_DEFINE([X], [v])` in an action argument -> a define that requires the
        probe's cache variable (negated for the not-found action)."""
        checks: list = []
        leftover = []
        for label, text in actions:
            found = action_define(text)
            if found is not None:
                define, value = found
                requires = cache if label == "action-if-found" else f"!{cache}"
                checks.append(Call("checks.AC_DEFINE", [define, literal(value)], {"requires": [requires]}))
            else:
                leftover.append((label, text))
        return checks, leftover

    # Each handler returns a list of Call / Raw items.

    def _m_AC_DEFINE(self, node: Node, ctx: _Ctx, unquoted: bool = False):
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
                decision = self._request(
                    f"variable:{name}", "variable", ctx,
                    f"{fn.split('.')[1]}([{name}]) takes its value from shell: {value.strip()}",
                    {"value_expression": value.strip()})
                if decision is None or decision.get("ignore"):
                    ctx.dropped.add(name)
                else:
                    self._apply_variable_decision(name, fn, decision, ctx, self._source_comment(node, ctx))
                return []
            stripped = value.strip()
            if isinstance(resolved, str) and len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
                resolved = f'"{resolved}"'  # `["$M4"]`: a C string literal, quotes and all
            # The shell expansion is done here: a plain define keeps the value as written.
            call = Call("checks.AC_DEFINE", [name, resolved])
            call.comments.append(f"value {value.strip()} in the m4")
            return [call]
        return [Call(fn, [name, literal(value)])]

    def _m_AC_DEFINE_UNQUOTED(self, node: Node, ctx: _Ctx):
        return self._m_AC_DEFINE(node, ctx, unquoted=True)

    def _define_from_assignments(self, name: str, value: str, fn: str, ctx: _Ctx, node: Node) -> bool:
        """`AC_DEFINE_UNQUOTED([X], [$var])` where the body assigns `var` a
        literal on each path: the define takes the same conditions."""
        match = re.fullmatch(r'\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?\s*', value)
        if not match:
            return False
        decisions = ctx.analysis.decisions(match.group(1))
        if not decisions or any(not isinstance(d.value, df.Lit) for d in decisions):
            return False
        decisions = [df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text) for d in decisions]
        ctx.defines.add(name.split("(")[0])
        before = len(self.decisions.pending)
        self._decision_checks(name, decisions, ctx, kind=fn,
                              comment=f"{self._source_comment(node, ctx)}: value {value.strip()} in the m4")
        return len(self.decisions.pending) == before  # a decision request means it did not lower

    def _shell_literal(self, text: str, ctx: _Ctx, depth: int = 0) -> str | None:
        """The literal `$var` stands for when the body assigns `var` once,
        possibly through another variable (`x=$y`, `y=literal`)."""
        match = re.fullmatch(r'\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?\s*', text)
        if not match or depth > 3:
            return None
        program = getattr(ctx.analysis, "program_literals", {}).get(match.group(1))
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

    def _shell_value(self, text: str, ctx: _Ctx):
        """The literal a `$var` value has when the body assigns it unconditionally."""
        match = re.fullmatch(r'\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?\s*', text)
        if not match:
            return None
        program = getattr(ctx.analysis, "program_literals", {}).get(match.group(1))
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

    def _m_AC_SUBST(self, node: Node, ctx: _Ctx):
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
                        f"variable:{var}", "variable", ctx,
                        f"AC_SUBST([{var}]) takes its value from shell: {value.strip()}",
                        {"value_expression": value.strip()})
                    if decision is None or decision.get("ignore"):
                        ctx.dropped.add(var)
                    else:
                        self._apply_variable_decision(var, "checks.AC_SUBST", decision, ctx,
                                                      self._source_comment(node, ctx))
                    return []
                self._seed(ctx, var, str(resolved))
                return [Call("checks.AC_SUBST", [var, resolved])]
            self._seed(ctx, var, value.strip())
            return [Call("checks.AC_SUBST", [var, literal(value)])]
        # One-argument form: the value is whatever the shell assigned; the
        # variable pass emits the check from the assignments' decision list.
        ctx.explicit_substs[var] = self._source_comment(node, ctx)
        return []

    def _m_AC_CHECK_HEADERS(self, node: Node, ctx: _Ctx):
        return self._plural(node, ctx, "macros.AC_CHECK_HEADERS", includes_index=3, comma=False)

    _m_AC_CHECK_HEADERS_ONCE = _m_AC_CHECK_HEADERS

    def _m_AC_CHECK_FUNCS(self, node: Node, ctx: _Ctx):
        return self._plural(node, ctx, "macros.AC_CHECK_FUNCS", includes_index=None, comma=False)

    _m_AC_CHECK_FUNCS_ONCE = _m_AC_CHECK_FUNCS

    def _m_AC_REPLACE_FUNCS(self, node: Node, ctx: _Ctx):
        items = self._plural(node, ctx, "macros.AC_CHECK_FUNCS", includes_index=None, comma=False)
        words = m4args.words(node.args[0] if node.args else "")
        if self.project is not None:
            for word in words:
                self.project.src_conditions.setdefault(
                    "GL_COND_OBJ_" + re.sub(r"[^A-Za-z0-9]", "_", word).upper(), []
                ).append(df.conj(self._guard(node, ctx), df.neg(df.Truthy(f"ac_cv_func_{word}"))))
        else:
            self.report.note(f"{ctx.where}: AC_REPLACE_FUNCS([{' '.join(words)}]) AC_LIBOBJs the missing "
                             "functions' sources; conditional sources are a project-level concern (autoconf_srcs)")
        return items

    def _m_AC_CHECK_DECLS(self, node: Node, ctx: _Ctx):
        return self._plural(node, ctx, "macros.AC_CHECK_DECLS", includes_index=3, comma=True)

    _m_AC_CHECK_DECLS_ONCE = _m_AC_CHECK_DECLS

    def _m_AC_CHECK_TYPES(self, node: Node, ctx: _Ctx):
        return self._plural(node, ctx, "macros.AC_CHECK_TYPES", includes_index=3, comma=True)

    def _m_AC_CHECK_MEMBERS(self, node: Node, ctx: _Ctx):
        return self._plural(node, ctx, "macros.AC_CHECK_MEMBERS", includes_index=3, comma=True)

    def _plural(self, node: Node, ctx: _Ctx, fn: str, *, includes_index: int | None, comma: bool):
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
                f"macro:{node.name}:{slug(first)}", "macro", ctx,
                f"{node.name} over a computed list: {first}", {"arguments": node.args}, node=node)
            if decision is None or decision.get("ignore"):
                return []
            return list(decision.get("checks", []))
        names = [n.strip() for n in args[0].split(",")] if comma else m4args.words(args[0])
        names = [n for n in names if n and not self._dropped_by_decision("check", cache_var(node.name, n))]
        names = [n for n in names if not self._check_from_port(node, n, ctx)]
        if not names:
            return []
        kwargs = {}
        if includes_index is not None and args[includes_index].strip():
            kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(args[includes_index])))
        if ctx.language != "c":
            kwargs["language"] = ctx.language
        items: list = [Call(fn, [names], kwargs, returns_list=True)]
        actions = [(label, args[i]) for label, i in (("action-if-found", 1), ("action-if-not-found", 2))
                   if args[i].strip()]
        if actions:
            if len(names) == 1:
                defines, actions = self._action_defines(node, cache_var(node.name, names[0]), actions)
                items += defines
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _dropped_by_decision(self, kind: str, name: str) -> bool:
        """`define:<NAME>` / `check:<cache variable>` IGNORE: a check a
        dependency already provides (never requested, applied when present)."""
        decision = self.decisions.optional(f"{kind}:{name}", kind)
        return bool(decision and decision.get("ignore"))

    def _check_from_port(self, node: Node, subject: str, ctx: _Ctx) -> bool:
        """Project mode: a `*_ONCE` check (or any check a port carries under its
        own target) becomes a dep on that port instead of a second probe."""
        if self.project is None or not self.project.flatten_files:
            return False  # only gl_INIT repeats the modules' own checks
        candidates = [f"{node.name}_{slug(subject)}", f"{node.name.replace('_ONCE', '')}_ONCE_{slug(subject)}",
                      f"{node.name}_{slug(subject).upper()}", f"{node.name.replace('_ONCE', '')}_ONCE_{slug(subject).upper()}",
                      cache_var(node.name, subject), define_of(node.name, subject)]
        port = self._port_for_check(list(dict.fromkeys(candidates)))
        if port is None:
            return False
        self._add_check_port_dep(port, ctx, f"{self._source_comment(node, ctx)}: {subject} (the port's shared check)")
        return True

    def _singular(self, node: Node, ctx: _Ctx, fn: str, *, includes_index: int | None):
        """The single-subject check macros (AC_CHECK_HEADER, AC_CHECK_FUNC, AC_CHECK_TYPE, ...):
        `checks.X(subject, ...)` with the define folded in from a plain AC_DEFINE action, the
        includes and the language; other actions become defines or an `actions` decision.
        """
        args = node.args + ["", "", "", ""]
        subject = m4args.quadrigraphs(args[0].strip())
        if self._dropped_by_decision("check", cache_var(node.name, subject)) or self._check_from_port(node, subject, ctx):
            return []
        kwargs = {}
        define = fold_define(args[1])
        if define:
            kwargs["define"] = define
            ctx.defines.add(define)
        if includes_index is not None and args[includes_index].strip():
            kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(args[includes_index])))
        if ctx.language != "c":
            kwargs["language"] = ctx.language
        items: list = [Call(fn, [subject], kwargs)]
        leftover = [(label, args[i]) for label, i in (("action-if-found", 1), ("action-if-not-found", 2))
                    if args[i].strip() and not (i == 1 and define)]
        if leftover:
            defines, leftover = self._action_defines(node, cache_var(node.name, subject), leftover)
            items += defines
            if leftover:
                items += self._actions_decision(node, ctx, leftover)
        return items

    def _m_AC_CHECK_HEADER(self, node: Node, ctx: _Ctx):
        return self._singular(node, ctx, "checks.AC_CHECK_HEADER", includes_index=3)

    def _m_AC_CHECK_FUNC(self, node: Node, ctx: _Ctx):
        return self._singular(node, ctx, "checks.AC_CHECK_FUNC", includes_index=None)

    def _m_AC_CHECK_DECL(self, node: Node, ctx: _Ctx):
        return self._singular(node, ctx, "checks.AC_CHECK_DECL", includes_index=3)

    def _m_AC_CHECK_MEMBER(self, node: Node, ctx: _Ctx):
        return self._singular(node, ctx, "checks.AC_CHECK_MEMBER", includes_index=3)

    def _m_AC_CHECK_TYPE(self, node: Node, ctx: _Ctx):
        """AC_CHECK_TYPE, including the obsolete two-argument form `AC_CHECK_TYPE([pid_t], [int])`,
        which defines the type to the fallback when it is missing.
        """
        args = node.args + ["", "", "", ""]
        default = args[1].strip()
        if len(node.args) == 2 and default and "(" not in default and "=" not in default and "$" not in default \
                and not re.match(r"^[A-Z][A-Z0-9_]*$", default):
            # Obsolete two-argument form: AC_CHECK_TYPE([pid_t], [int]).
            type_name = args[0].strip()
            return [
                Call("checks.AC_CHECK_TYPE", [type_name]),
                Call("checks.AC_DEFINE", [type_name, args[1].strip()],
                     {"requires": [f"!ac_cv_type_{cache_name(type_name)}"]}),
            ]
        return self._singular(node, ctx, "checks.AC_CHECK_TYPE", includes_index=3)

    # -- language standards: a requirement, not a flag -------------------------
    #
    # configure switches the compiler into the mode a macro asks for and
    # stops when it cannot.  A Bazel build's standard is the root module's
    # (--copt / --cxxopt or a toolchain feature), so the macros become checks
    # that probe the mode the compiler is in and stop the build with the
    # flag to add when it is not the one asked for.

    def _set_standard(self, lang: str, standard: str) -> None:
        """A `CFLAGS="$CFLAGS -std=..."` of the script: the last one wins (configure appends in order)."""
        if lang == "CXX":
            self.project.cxx_standard = standard
        else:
            self.project.c_standard = standard

    def _standard(self, lang: str, standard: str, node: Node, ctx: _Ctx) -> list:
        """`checks.AC_LANG_STANDARD(standard, error = True)` for a macro that insists on a standard."""
        if self.project is not None:
            self.project.required_standards.add(iso_standard(standard))
        self.report.note(f"{node.name}: the {lang} compiler must be in {standard} mode or later; checked, "
                         "not set (a Bazel build's standard is the root module's)")
        return [Call("checks.AC_LANG_STANDARD", [standard], {"error": True})]

    def _cxx_standard(self, version: str, ext: str, mandatory: str, node: Node, ctx: _Ctx) -> list:
        """AX_CXX_COMPILE_STDCXX: `checks.AX_CXX_COMPILE_STDCXX`, which defines HAVE_CXX<N> and, when
        mandatory, stops the build when the compiler is not in that mode."""
        ext = ext.strip() if ext.strip() in ("ext", "noext") else "ext"
        optional = mandatory.strip() == "optional"
        if self.project is not None and not optional:
            self.project.required_standards.add(f"c++{version}")
        self.report.note(f"{node.name}: the C++ compiler must be in C++{version} mode or later; checked, "
                         "not set (a Bazel build's standard is the root module's)")
        return [Call("checks.AX_CXX_COMPILE_STDCXX", [version, ext], {"mandatory": False} if optional else {})]

    def _m_AX_CXX_COMPILE_STDCXX(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", ""]
        return self._cxx_standard(args[0].strip() or "11", args[1], args[2], node, ctx)

    def _m_AX_CXX_COMPILE_STDCXX_11(self, node: Node, ctx: _Ctx):
        args = node.args + ["", ""]
        return self._cxx_standard("11", args[0], args[1], node, ctx)

    def _m_AX_CXX_COMPILE_STDCXX_14(self, node: Node, ctx: _Ctx):
        args = node.args + ["", ""]
        return self._cxx_standard("14", args[0], args[1], node, ctx)

    def _m_AX_CXX_COMPILE_STDCXX_17(self, node: Node, ctx: _Ctx):
        args = node.args + ["", ""]
        return self._cxx_standard("17", args[0], args[1], node, ctx)

    def _m_AC_PROG_CC_C99(self, node: Node, ctx: _Ctx):
        return self._standard("C", "c99", node, ctx)

    def _m_AC_CHECK_SIZEOF(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", ""]
        kwargs = {}
        if args[2].strip():
            kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(args[2])))
        # The rules define nothing unless asked; autoconf always defines SIZEOF_<TYPE>.
        kwargs["define"] = f"SIZEOF_{cache_name(args[0].strip()).upper()}"
        return [Call("checks.AC_CHECK_SIZEOF", [args[0].strip()], kwargs)]

    def _m_AC_CHECK_ALIGNOF(self, node: Node, ctx: _Ctx):
        args = node.args + ["", ""]
        kwargs = {}
        if args[1].strip():
            kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(args[1])))
        kwargs["define"] = f"ALIGNOF_{cache_name(args[0].strip()).upper()}"
        return [Call("checks.AC_CHECK_ALIGNOF", [args[0].strip()], kwargs)]

    def _m_AC_COMPUTE_INT(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", "", ""]
        kwargs = {}
        if args[2].strip():
            kwargs["includes"] = m4args.includes_lines(m4args.quadrigraphs(m4args.unquote(args[2])))
        call = Call("checks.AC_COMPUTE_INT", [args[0].strip(), m4args.quadrigraphs(args[1].strip())], kwargs)
        call.comments.append(f"{args[0].strip()} is a shell variable in the m4; consumers of it need "
                             "a define or subst of that name")
        return [call]

    def _registry_library(self, lib: str, what: str) -> bool:
        """Project mode, a library the registry provides: the module becomes a bazel_dep, its
        target a `cc_deps` entry of the main autoconf target (so the probe compiles against its
        headers and links its archive, as configure does with the library installed) and a dep of
        everything the project links, as LIBS would.  The check itself stays a real probe."""
        found = libraries.known(lib)
        if found is None or self.project is None:
            return False
        if lib not in self.project.configure_libs:
            self.project.external_modules[found.module] = found.version
            self.project.configure_libs[lib] = found.label
            self.report.note(f"{what}: -l{lib} is the registry module {found.module} ({found.label}): declared in "
                             "MODULE.bazel, probed through the main target's cc_deps and linked by the project's "
                             "targets")
        return True

    def _m_AC_CHECK_LIB(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", "", "", ""]
        lib, function = args[0].strip(), args[1].strip()
        cache = f"ac_cv_lib_{cache_name(lib)}_{cache_name(function)}"
        self._registry_library(lib, f"AC_CHECK_LIB([{lib}], [{function}])")
        items: list = [Call("checks.AC_CHECK_LIB", [lib, function])]
        actions = [(label, args[i]) for label, i in (("action-if-found", 2), ("action-if-not-found", 3))
                   if args[i].strip()]
        if actions:
            defines, actions = self._action_defines(node, cache, actions)
            items += defines
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _m_AC_SEARCH_LIBS(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", "", "", ""]
        function, candidates = args[0].strip(), m4args.words(args[1])
        for lib in candidates:
            self._registry_library(lib, f"AC_SEARCH_LIBS([{function}], [{' '.join(candidates)}])")
        items: list = [Call("checks.AC_SEARCH_LIBS", [function, candidates])]
        actions = [(label, args[i]) for label, i in (("action-if-found", 2), ("action-if-not-found", 3))
                   if args[i].strip()]
        if actions:
            defines, actions = self._action_defines(node, f"ac_cv_search_{cache_name(args[0])}", actions)
            items += defines
            if actions:
                items += self._actions_decision(node, ctx, actions)
        return items

    def _m_AC_PATH_PROGS_FEATURE_CHECK(self, node: Node, ctx: _Ctx):
        """AC_PATH_PROGS_FEATURE_CHECK(VAR, [progs], feature-test, ...): sets ac_cv_path_VAR
        (configure.ac substitutes or defines from it); the last, plainest candidate stands in."""
        var, value = _program_lookup_value(node.name, node.args)
        call = Call("checks.M4_VARIABLE", [var, value])
        call.comments.append(f"{node.name}: program lookups are not modeled; the plain program name stands in")
        return [call]

    def _m_AC_CHECK_PROG(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", "", ""]
        var, value = args[0].strip(), (args[2] or args[1]).strip()
        call = Call("checks.AC_SUBST", [var, value])
        call.comments.append(f"{node.name}: program lookups are not modeled; the value-if-found is substituted")
        return [call]

    def _m_AC_CHECK_TOOL(self, node: Node, ctx: _Ctx):
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

    def _m_AC_CHECK_PROGS(self, node: Node, ctx: _Ctx):
        args = node.args + ["", "", "", ""]
        var, programs = args[0].strip(), m4args.words(args[1])
        call = Call("checks.AC_SUBST", [var, programs[0] if programs else args[2].strip()])
        call.comments.append(f"{node.name}: program lookups are not modeled; the first candidate is substituted")
        return [call]

    _m_AC_PATH_PROGS = _m_AC_CHECK_PROGS

    def _probe(self, node: Node, ctx: _Ctx, fn: str, note: str | None = None):
        """AC_COMPILE_IFELSE / AC_LINK_IFELSE / AC_PREPROC_IFELSE and the AC_TRY_* forms: a
        `checks.AC_TRY_COMPILE` / `AC_TRY_LINK` named after the enclosing cache variable, the
        action variable, or a synthesized probe name; the program keeps its AC_LANG_PROGRAM parts
        when it was written that way. Canonical yes/no actions bind the cache variable; other
        actions become defines or decisions.
        """
        args = node.args + ["", "", ""]
        program = args[0]
        kwargs: dict = {}
        yes, no = args[1].strip(), args[2].strip()
        if ctx.cache:
            kwargs["name"] = ctx.cache
        elif action_var(yes):
            kwargs["name"] = action_var(yes)
        else:
            ctx.probe_count += 1
            kwargs["name"] = f"{ctx.target.name.lower()}_probe_{ctx.probe_count}"
        var = kwargs["name"]
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
        canonical = yes == f"{var}=yes" and no == f"{var}=no"
        if canonical or (not yes and not no):
            ctx.probe_vars.add(var)
            return [call]
        define = fold_define(yes)
        if define and not no:
            kwargs["define"] = define
            ctx.defines.add(define)
            ctx.probe_vars.add(var)
            return [call]
        # The actions do something other than record the result: the probe's
        # outcome is not the cache value.
        context = {"program": block_lines(node.program() or m4args.quadrigraphs(program)),
                   "action_if_true": yes, "action_if_false": no}
        if yes == f"{var}=no" and no == f"{var}=yes":
            context["note"] = f"inverted: {var} is yes when the program does NOT compile/link"
        decision = self._request(f"probe:{var}", "probe", ctx,
                                 f"{node.name}: the actions decide {var}, not the probe's success",
                                 context, node=node)
        if decision is None or decision.get("ignore"):
            ctx.dropped.add(var)
            return []
        if decision.get("accept"):
            ctx.probe_vars.add(var)
            return [call]
        self._apply_probe_decision(var, decision, ctx, self._guard(node, ctx), self._source_comment(node, ctx))
        return []

    def _m_AC_COMPILE_IFELSE(self, node: Node, ctx: _Ctx):
        return self._probe(node, ctx, "checks.AC_TRY_COMPILE")

    def _m_AC_LINK_IFELSE(self, node: Node, ctx: _Ctx):
        return self._probe(node, ctx, "checks.AC_TRY_LINK")

    def _m_AC_PREPROC_IFELSE(self, node: Node, ctx: _Ctx):
        return self._probe(node, ctx, "checks.AC_TRY_COMPILE",
                           note="AC_PREPROC_IFELSE in the m4: modeled as a compile probe")

    def _m_AC_RUN_IFELSE(self, node: Node, ctx: _Ctx):
        """Runtime probes are not modeled: the cross-compiling branch decides.

        The m4's own `case $host_os` guesses become `M4_VARIABLE` values per
        platform (1 for "yes", 0 for "no"); the program stays in a comment so
        the reviewer sees what the guess stands for.  Without a usable guess
        the value is a decision.
        """
        args = node.args + ["", "", "", ""]
        var = ctx.cache or action_var(args[1].strip())
        program = node.program() or m4args.quadrigraphs(args[0])
        cross = args[3]
        comment = self._source_comment(node, ctx)
        guard = self._guard(node, ctx)
        context = {"program": block_lines(program), "action_if_true": args[1].strip(),
                   "action_if_false": args[2].strip()}
        if not var:
            self._request(f"probe:{ctx.target.name}:run_{ctx.probe_count + 1}", "probe", ctx,
                          "AC_RUN_IFELSE without a cache variable: runtime probes are not modeled",
                          context, node=node)
            return []
        cross_shell = m4args.quadrigraphs(m4args.strip_m4(cross))
        decisions = df.Evaluator().run(sh.parse(cross_shell)).decisions(var) if cross.strip() else []
        if not decisions:
            context["cross_compiling"] = block_lines(cross_shell)
            decision = self._request(
                f"probe:{var}", "probe", ctx,
                f"AC_RUN_IFELSE sets {var} at run time and its cross-compiling branch gives no value",
                context, node=node)
            if decision is None or decision.get("ignore"):
                ctx.dropped.add(var)
            else:
                self._apply_probe_decision(var, decision, ctx, guard, comment)
            return []
        ctx.probe_vars.add(var)
        provenance = [f"{var}: the m4 runs a program; the value is its cross-compiling guess per platform:"]
        provenance += ["  " + line for line in block_lines(cross_shell)]
        self._decision_checks(var, decisions, ctx, kind="checks.M4_VARIABLE", guess=True,
                              comment="\n".join(provenance))
        return []

    def _m_AC_EGREP_CPP(self, node: Node, ctx: _Ctx):
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
            found_sets_yes = yes == f"{var}=yes" and (no == f"{var}=no" or not no)
            includes = [line.strip() for line in rest.splitlines() if line.strip().startswith("#")]
            if found_sets_no or (found_sets_yes and False):
                ctx.probe_vars.add(var)
                call = Call("checks.AC_TRY_COMPILE", [], {
                    "name": var,
                    "code": f"int e[] = {{{', '.join(macros)}}}; (void)e;",
                    "includes": includes,
                })
                call.comments.append(f"{node.name}([{pattern}]) finds the token when one of the macros is "
                                     "undefined: compiled as one array of them")
                return [call]
        if var and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pattern) and not blocks:
            found_sets_yes = yes == f"{var}=yes" and (no == f"{var}=no" or not no)
            if found_sets_yes:
                # The token appears in the preprocessed program when the
                # headers declare it: close enough to "it compiles as a name".
                ctx.probe_vars.add(var)
                is_type = pattern.endswith("_t")
                body = f"{pattern} probe_var; (void) probe_var;" if is_type else f"(void) {pattern};"
                call = Call("checks.AC_TRY_COMPILE", [], {
                    "name": var,
                    "code": Call("utils.AC_LANG_PROGRAM", [block_lines(program), [body]]),
                })
                call.comments.append(f"{node.name}([{pattern}]) in the m4: the preprocessed program mentions the "
                                     f"name; compiled here as a {'type' if is_type else 'declaration'} use instead")
                return [call]
        decision = self._request(
            f"probe:{var or f'{ctx.target.name}:egrep_{slug(pattern)}'}", "probe", ctx,
            f"{node.name}([{pattern}]) greps preprocessor output; needs a compile-time probe",
            {"pattern": pattern, "program": block_lines(program), "action_if_found": yes,
             "action_if_not_found": no}, node=node)
        if var and (decision is None or decision.get("ignore")):
            ctx.dropped.add(var)
        elif decision is not None and var:
            self._apply_probe_decision(var, decision, ctx, self._guard(node, ctx), comment)
        return []

    _m_AC_EGREP_HEADER = _m_AC_EGREP_CPP

    def _m_AC_LIBOBJ(self, node: Node, ctx: _Ctx):
        self.report.note(f"{ctx.where}: AC_LIBOBJ([{node.args[0] if node.args else ''}]) is a conditional "
                         "source; a project-level concern (autoconf_srcs)")
        return []

    def _m_AC_MSG_ERROR(self, node: Node, ctx: _Ctx):
        self.report.note(f"{ctx.where}: configure aborts with AC_MSG_ERROR({node.args[0] if node.args else ''}) "
                         f"when {describe(self._guard(node, ctx))}; not modeled")
        return []

    _m_AC_MSG_FAILURE = _m_AC_MSG_ERROR

    def _m_AC_ARG_ENABLE(self, node: Node, ctx: _Ctx):
        self.report.note(f"{ctx.where}: {node.name}([{node.args[0] if node.args else ''}]) is a configure "
                         "option; modeled in project mode only")
        return []

    _m_AC_ARG_WITH = _m_AC_ARG_ENABLE

    def _m_AM_CONDITIONAL(self, node: Node, ctx: _Ctx):
        self.report.note(f"{ctx.where}: AM_CONDITIONAL([{node.args[0] if node.args else ''}]) is an automake "
                         "conditional; modeled in project mode only")
        return []

    # -- variables assigned by the shell ---------------------------------------

    def _variables(self, ctx: _Ctx) -> None:
        """Emit the substituted/tracked variables the body assigns."""
        for var in ctx.analysis.order:
            if var in ctx.probe_vars or var in ctx.defines:
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
            if var not in ctx.probe_vars and any(df.mentions(d.cond, var) for d in decisions):
                # `test -n "$X" || X=default`: X before the body assigns it is
                # the configure environment, which a Bazel build does not have.
                decisions = [df.Assignment(df.substitute_empty(d.cond, var), d.value, d.text) for d in decisions]
                comment += f"; {var} is unset before the m4 assigns it (no configure environment)"
            decisions = [df.Assignment(df.substitute_unset(d.cond, self._never_set), d.value, d.text)
                         for d in decisions]
            self._decision_checks(var, decisions, ctx, kind=kind, comment=comment)
        for var, comment in ctx.explicit_substs.items():
            if var not in ctx.analysis.assigns and var not in ctx.probe_vars:
                decision = self._request(
                    f"variable:{var}", "variable", ctx,
                    f"{comment}: {var} is never assigned in this body; its value is not known here",
                    {}, macro=("AC_SUBST", var))
                if decision is None or decision.get("ignore"):
                    ctx.dropped.add(var)
                else:
                    self._apply_variable_decision(var, "checks.AC_SUBST", decision, ctx, comment)

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
        # (configure.ac's `X=0; AC_SUBST([X])` default next to the gnulib module that sets X).
        for items in [ctx.target.checks] + ctx.target.arm_lists:
            for item in list(items):
                if isinstance(item, Call) and item.fn == "checks.AC_SUBST" and item.args \
                        and isinstance(item.args[0], str) and self._dropped_by_decision("subst", item.args[0]):
                    items.remove(item)
        bound: set[str] = set(ctx.probe_vars) | set(ctx.defines)
        assigned = set(ctx.analysis.assigns)
        # Dropping a check can leave the checks conditional on its result
        # dangling in turn, so this runs to a fixpoint.
        while self._validate_conditions_once(ctx, bound, assigned):
            pass

    def _validate_conditions_once(self, ctx: _Ctx, bound: set[str], assigned: set[str]) -> bool:  # noqa: C901
        # What the target's checks produce: a probe bound to a cache variable
        # counts only once it is placed (a guard decision may have dropped it).
        """One pass of `_validate_conditions`: a check whose `requires` / `condition` names
        something no check of the target produces (the configure environment, a dropped probe, an
        assignment the converter did not translate) is removed and becomes a `guard` decision:
        ALWAYS strips the condition, NEVER drops the check, a requires list or a select replaces
        it. Returns whether anything was dropped, so the caller iterates to a fixpoint.
        """
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
                requires = [r for r in item.kwargs.get("requires", []) if isinstance(r, str)]
                condition = item.kwargs.get("condition") if isinstance(item.kwargs.get("condition"), str) else None
                dangling = sorted({n for expr in requires + ([condition] if condition else [])
                                   for n in condition_names(expr) if unproducible(n, own)})
                if not dangling:
                    continue
                items.remove(item)
                kept = [r for r in requires if not any(n in dangling for n in condition_names(r))]
                decision = self._request(
                    f"guard:{ctx.target.name}:{check_key(item)}", "guard", ctx,
                    f"{check_key(item)} is conditional on {', '.join(dangling)}, which no check produces "
                    "(a variable of the configure environment, or an assignment the converter did not translate)",
                    {"requires": requires, "condition": condition, "unresolved": dangling})
                if decision is None or decision.get("never"):
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
                    if "requires" in decision:
                        stripped.kwargs["condition"] = " && ".join(decision["requires"])
                        stripped.kwargs["if_true"] = if_true
                        stripped.kwargs["if_false"] = item.kwargs.get("if_false")
                        items.append(stripped)
                        continue
                    stripped.args = list(stripped.args[:1]) + ([if_true] if if_true is not None else [])
                if decision.get("always"):
                    stripped.comments.append(f"{', '.join(dangling)}: condition dropped (decisions file: ALWAYS)")
                    items.append(stripped)
                    continue
                self._apply_guard_decision([stripped], decision, ctx, items)
        return dropped_any

    def _variable_request(self, var: str, kind: str, ctx: _Ctx, comment: str, summary: str,
                          decisions: list, allow_partial: bool = False) -> dict | None:
        """Ask for a `variable:` decision about `var`, with its assignments as context, and apply
        the answer. IGNORE or no answer records the variable as dropped, so conditions over it
        become decisions too; ACCEPT_PARTIAL is only valid where the caller allows it.
        """
        context = {"assignments": [f"{d.text}  when {describe(d.cond)}" for d in decisions]}
        decision = self._request(f"variable:{var}", "variable", ctx, summary, context,
                                 shell=[d.text for d in decisions])
        if decision is not None and decision.get("accept_partial"):
            if not allow_partial:
                raise dec.DecisionError(f"variable:{var}: ACCEPT_PARTIAL applies only to a partially followed variable")
            return decision
        if decision is None or decision.get("ignore"):
            ctx.dropped.add(var)  # nothing produces it: conditions over it are decisions too
        else:
            self._apply_variable_decision(var, kind, decision, ctx, comment)
        return decision

    def _decision_checks(self, var: str, decisions: list[df.Assignment], ctx: _Ctx, *, kind: str,
                         comment: str, guess: bool = False) -> None:
        """Lower a variable's decision list to one check per select() arm."""
        whole = df.disj(*(d.cond for d in decisions))
        has_flag = bool(df.flag_atoms(whole))
        labels = [lower.DEFAULT_ARM]
        split_fn = None
        if has_flag:
            arms = lower.flag_arms(whole, self.project) if self.project is not None else None
            if arms is None:
                self._variable_request(var, kind, ctx, comment,
                                       f"{var}: assignments depend on configure options in a way select() "
                                       "cannot express", decisions)
                return
            (flag,) = df.option_names(whole)
            labels = list(arms)
            settings = self.project.option_settings
            split_fn = lambda cond, label: df.substitute_option(  # noqa: E731
                cond, flag, None if label == lower.DEFAULT_ARM else settings[label][1])
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
                split_fn = lambda cond, label: (  # noqa: E731
                    lower.default_residual(cond) if label == lower.DEFAULT_ARM else lower.residual(family, label, cond))
        split = split_fn is not None
        family = family_of_labels(labels)
        for label in labels:
            arm_decisions = []
            for decision in decisions:
                cond = split_fn(decision.cond, label) if split else decision.cond
                if cond is df.FALSE:
                    continue
                arm_decisions.append((cond, decision))
            dest = ctx.target.arm(label, family) if split else ctx.target.checks
            check, dropped = self._lower_decisions(var, arm_decisions, kind, guess)
            if check is None:
                if arm_decisions:
                    self._variable_request(var, kind, ctx, comment,
                                           f"{var}: the assignments cannot be expressed as one check",
                                           [d for _, d in arm_decisions])
                continue
            if dropped:
                decided = self._variable_request(
                    var, kind, ctx, comment,
                    f"{var}: some assignments have conditions that cannot be expressed",
                    [d for _, d in arm_decisions], allow_partial=True)
                if decided is None or not decided.get("accept_partial"):
                    continue
                check.comments.append(f"partial: `{dropped[0][1].text}` when {describe(dropped[0][0])} "
                                      "is not expressed (accepted in the decisions file)")
            check.comments.insert(0, comment)
            if not any(same_call(check, other) for other in dest if isinstance(other, Call)):
                dest.append(check)
        if split:
            ctx.target.arm(lower.DEFAULT_ARM, family)

    def _lower_decisions(self, var: str, arm_decisions, kind: str, guess: bool):
        """One check for a decision list; also the assignments it had to drop.

        Last assignment wins.  Supported shapes: an unconditional final value;
        one value under one or more (expressible) conditions, with or without
        an unconditional initial value; two values under complementary
        conditions.  Conditions that cannot be expressed are dropped and
        reported so the check stays right where it applies.
        """
        values = []
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
        if len({v for _, v, _ in values}) == 1:
            # Every branch assigns the same value: whether a condition holds
            # changes nothing once an unconditional assignment exists.
            if any(c is df.TRUE for c, _, _ in values):
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
        (value,) = distinct
        kept = [(c, v, d) for c, v, d in conditional if lower.expression(c).ok]
        dropped = [(c, d) for c, v, d in conditional if not lower.expression(c).ok]
        if not kept:
            return None, []
        cond = df.disj(*(c for c, _, _ in kept))
        expr = lower.expression(cond).expr
        if expr is None:
            return Call(kind, [var, value]), dropped
        if default is None:
            return Call(kind, [var, value], {"requires": [expr]}), dropped
        return Call(kind, [var], {"condition": expr, "if_true": value, "if_false": default}), dropped


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
        subject = " ".join(first) if isinstance(first, list) else str(first)
    else:
        subject = str(call.kwargs.get("name", ""))
    return f"{short}:{slug(subject)}" if subject else short


_LANG_ABBREV = {"c": "c", "cpp": "cxx"}
_LANG_PREFIX = {"c": "C", "cpp": "CXX"}


def m4_cache_name(text: str, language: str = "c") -> str:
    """A cache variable named with m4 in it: `bison_cv_[]_AC_LANG_ABBREV[]_supports_posixly_correct`
    is `bison_cv_c_...` in the C part and `bison_cv_cxx_...` in the C++ part (the expansion
    keeps the call's arguments unexpanded)."""
    text = text.replace("[]", "")
    return text.replace("_AC_LANG_ABBREV", _LANG_ABBREV.get(language, language)) \
        .replace("_AC_LANG_PREFIX", _LANG_PREFIX.get(language, language.upper()))


def cache_var(macro: str, subject: str) -> str:
    """The cache variable autoconf's singular check macros set."""
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", subject.strip().replace("*", "p")).lower()
    base = macro.replace("_ONCE", "")
    prefix = {
        "AC_CHECK_HEADER": "ac_cv_header_", "AC_CHECK_HEADERS": "ac_cv_header_",
        "AC_CHECK_FUNC": "ac_cv_func_", "AC_CHECK_FUNCS": "ac_cv_func_",
        "AC_CHECK_DECL": "ac_cv_have_decl_", "AC_CHECK_DECLS": "ac_cv_have_decl_",
        "AC_CHECK_TYPE": "ac_cv_type_", "AC_CHECK_TYPES": "ac_cv_type_",
        "AC_CHECK_MEMBER": "ac_cv_member_", "AC_CHECK_MEMBERS": "ac_cv_member_",
    }.get(base, "ac_cv_")
    return prefix + normalized


def flag_bool_name(option: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", option.strip())


def default_tarname(name: str) -> str:
    """AC_INIT's default PACKAGE_TARNAME for a package name."""
    stripped = name[4:] if name.startswith("GNU ") else name
    return re.sub(r"[^_a-z0-9]", "-", stripped.lower())


def default_from_help(arg: str) -> bool:
    """The option's default as its help string spells it: `--disable-x` and
    `--without-x` document a feature that is on unless turned off."""
    call = m4args.parse_call(arg)
    option = call.args[0] if call is not None and call.name in ("AS_HELP_STRING", "AC_HELP_STRING") and call.args \
        else arg
    return option.strip().startswith(("--disable-", "--without-"))


def help_text(arg: str) -> str:
    """The description of an AS_HELP_STRING argument."""
    call = m4args.parse_call(arg)
    if call is not None and call.name in ("AS_HELP_STRING", "AC_HELP_STRING") and len(call.args) > 1:
        return " ".join(call.args[1].split())
    return " ".join(arg.split())


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


def literal(value: str):
    """Starlark value for an m4 literal: ints stay ints, the rest are strings."""
    text = value.strip()
    if _NUMBER_RE.match(text):
        return int(text)
    return m4args.quadrigraphs(text)


_SHELL_DEFINE_RE = re.compile(
    r"""^\s*(?:printf\s+['"]%s\\n['"]|\$as_echo)\s+"#define\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s+(.*?))?"\s*>>\s*confdefs\.h\s*$""")


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


_EXPANDED_DEFINE_RE = re.compile(r'#define[ \t]+([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+([^"\n]*?))?[ \t]*"?[ \t]*(?:>>confdefs\.h)?$', re.M)


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
    "AC_HEADER_DIRENT": ([Call("macros.AC_CHECK_HEADERS", [["dirent.h"]], returns_list=True)],
                         "AC_HEADER_DIRENT: only dirent.h is probed (sys/ndir.h, sys/dir.h and ndir.h are "
                         "pre-POSIX fallbacks GNU autoconf tries after it)"),
    # AC_FUNC_GETPGRP: GETPGRP_VOID unless `getpgrp (0)` compiles (the BSD form).
    "AC_FUNC_GETPGRP": ([Call("checks.AC_TRY_COMPILE", [], {
        "name": "ac_cv_func_getpgrp_arg",
        "code": _program("#include <unistd.h>", "getpgrp (0);"),
    }), Call("checks.AC_DEFINE", ["GETPGRP_VOID", 1], {"requires": ["!ac_cv_func_getpgrp_arg"]})],
                        "AC_FUNC_GETPGRP: ac_cv_func_getpgrp_arg is the compile test itself (GNU autoconf's "
                        "ac_cv_func_getpgrp_void is its negation)"),
    "AC_FUNC_STRCOLL": ([Call("macros.AC_CHECK_FUNCS", [["strcoll"]], returns_list=True)],
                        "AC_FUNC_STRCOLL: the run-time test that strcoll orders correctly is assumed to pass"),
    "AC_FUNC_VPRINTF": ([Call("macros.AC_CHECK_FUNCS", [["vprintf"]], returns_list=True)],
                        "AC_FUNC_VPRINTF: HAVE_DOPRNT (the pre-ANSI fallback) is not probed"),
    "AC_C_PROTOTYPES": ([Call("checks.AC_DEFINE", ["PROTOTYPES", 1]), Call("checks.AC_DEFINE", ["__PROTOTYPES", 1])],
                        None),
    "AC_C_CONST": ([], "AC_C_CONST: `const` is only redefined for pre-ANSI compilers; nothing to emit"),
    "AC_C_VOLATILE": ([], "AC_C_VOLATILE: `volatile` is only redefined for pre-ANSI compilers; nothing to emit"),
    "AC_C_STRINGIZE": ([Call("checks.AC_DEFINE", ["HAVE_STRINGIZE", 1])], None),
    "AC_TYPE_SIGNAL": ([Call("checks.AC_DEFINE", ["RETSIGTYPE", "void"])], None),
    "AC_C_CHAR_UNSIGNED": ([Call("checks.AC_TRY_COMPILE", [], {
        "name": "ac_cv_c_char_unsigned",
        "code": _program("", "static int test_array[1 - 2 * !(((char) -1) > 0)];\ntest_array[0] = 0;\n"
                             "return test_array[0];"),
    })], "AC_C_CHAR_UNSIGNED: GNU autoconf defines __CHAR_UNSIGNED__ only for non-GCC compilers; "
         "the compilers Bazel drives predefine it where it applies"),
    "AC_C_VARARRAYS": ([Call("checks.AC_TRY_COMPILE", [], {
        "name": "ac_cv_c_vararrays",
        "code": _program("#ifdef __STDC_NO_VLA__\n#error no variable-length arrays\n#endif",
                         "int n = 1;\nchar a[n];\na[0] = 0;\nreturn a[0];"),
        "define": "HAVE_C_VARARRAYS",
    })], None),
    "AC_HEADER_SYS_WAIT": ([Call("checks.AC_TRY_COMPILE", [], {
        "name": "ac_cv_header_sys_wait_h",
        "code": _program("#include <sys/types.h>\n#include <sys/wait.h>\n#ifndef WEXITSTATUS\n"
                         "# define WEXITSTATUS(stat_val) ((unsigned int) (stat_val) >> 8)\n#endif\n"
                         "#ifndef WIFEXITED\n# define WIFEXITED(stat_val) (((stat_val) & 255) == 0)\n#endif",
                         "int s;\nwait (&s);\ns = WIFEXITED (s) ? WEXITSTATUS (s) : 1;"),
        "define": "HAVE_SYS_WAIT_H",
    })], None),
    "AC_HEADER_TIME": ([Call("checks.AC_TRY_COMPILE", [], {
        "name": "ac_cv_header_time",
        "code": _program("#include <sys/types.h>\n#include <sys/time.h>\n#include <time.h>",
                         "struct tm *tp;\n(void) tp;"),
        "define": "TIME_WITH_SYS_TIME",
    })], None),
    "AC_HEADER_TIOCGWINSZ": ([
        Call("checks.AC_TRY_COMPILE", [], {
            "name": "ac_cv_sys_tiocgwinsz_in_termios_h",
            "code": _program("#include <sys/types.h>\n#include <termios.h>\n#ifndef TIOCGWINSZ\n"
                             "#error TIOCGWINSZ is not in termios.h\n#endif"),
        }),
        Call("checks.AC_TRY_COMPILE", [], {
            "name": "ac_cv_sys_tiocgwinsz_in_sys_ioctl_h",
            "code": _program("#include <sys/types.h>\n#include <sys/ioctl.h>\n#ifndef TIOCGWINSZ\n"
                             "#error TIOCGWINSZ is not in sys/ioctl.h\n#endif"),
        }),
        Call("checks.AC_DEFINE", ["GWINSZ_IN_SYS_IOCTL", 1],
             {"requires": ["!ac_cv_sys_tiocgwinsz_in_termios_h", "ac_cv_sys_tiocgwinsz_in_sys_ioctl_h"]}),
    ], None),
}

# What each kind of emitted check provides, for `_validate_conditions`.
_PLURAL_CHECKS = {"AC_CHECK_HEADERS", "AC_CHECK_FUNCS", "AC_CHECK_DECLS", "AC_CHECK_TYPES", "AC_CHECK_MEMBERS"}
_SINGULAR_CHECKS = {"AC_CHECK_HEADER", "AC_CHECK_FUNC", "AC_CHECK_DECL", "AC_CHECK_TYPE", "AC_CHECK_MEMBER"}
_NAMED_CHECKS = {"AC_SUBST", "M4_VARIABLE", "AC_DEFINE", "AC_DEFINE_UNQUOTED", "AC_COMPUTE_INT"}
_CONDITION_NAME_RE = re.compile(r"(?:^|[\s(!&|])\s*([A-Za-z_][A-Za-z0-9_]*)")
# `case "$host_os" in mingw* | windows*) true;; *) false;; esac` (and the
# inverse): the test gnulib's AM_CONDITIONALs spell a platform with.
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
    r'"?x?\$\{?(?:enable|with)_([A-Za-z0-9_]+)\}?"?\s*(?:=|==|!=)\s*"?x?([A-Za-z0-9_.+/-]+)"?')
# `ISC=yes`, `for X in`, `read X`: ALLCAPS shell variables the script sets.
_ALLCAPS_ASSIGN_RE = re.compile(r"(?:\b|^)([A-Z][A-Z0-9_]*)=|\bfor\s+([A-Z][A-Z0-9_]*)\s+in\b|\bread\s+([A-Z][A-Z0-9_]*)\b")
# gnulib-comp.m4's tests witness: `M4tests_WITNESS=IN_`echo "${PACKAGE-$PACKAGE_TARNAME}"
# | tr a-z A-Z | sed ...`_GNULIB_TESTS`, the package name upper-cased by a pipeline.
_TESTS_WITNESS_RE = re.compile(r'IN_`echo "\$\{PACKAGE-\$PACKAGE_TARNAME\}"[^`\n]*`_GNULIB_TESTS')
# `name=value` lines of the script preamble that are not autoconf's own
# (ac_*, as_*, PACKAGE_*...): what m4_divert_text([INIT_PREPARE]) put there.
_PRELUDE_ASSIGN_RE = re.compile(r"^((?!ac_|as_|PACKAGE_|MFLAGS|MAKEFLAGS|SHELL|PATH_SEPARATOR|exec_prefix|prefix|"
                                r"program_|bindir|sbindir|libexecdir|datarootdir|datadir|sysconfdir|sharedstatedir|"
                                r"localstatedir|runstatedir|includedir|oldincludedir|docdir|infodir|htmldir|dvidir|"
                                r"pdfdir|psdir|libdir|localedir|mandir|LIBOBJS|LTLIBOBJS|cross_compiling|subdirs|"
                                r"DUALCASE|CONFIG_SHELL|LC_|LANG)[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_./-]*)$", re.M)
_CASE_CONDITION_RE = re.compile(
    r"^case\s+\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?\"?\s+in\s+(.+?)\)\s*(true|false|:)\s*;;\s*\*\)\s*(true|false|:)\s*;;\s*esac$",
    re.S)


_PROGRAM_LOOKUPS = {"AC_CHECK_PROG", "AC_CHECK_PROGS", "AC_CHECK_TOOL", "AC_CHECK_TOOLS", "AC_PATH_PROG",
                    "AC_PATH_PROGS", "AC_PATH_TOOL", "AC_PATH_PROGS_FEATURE_CHECK"}


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


def provided_names(call: Call) -> set[str]:
    """The cache variables and defines a check produces."""
    short = call.fn.split(".")[-1]
    out: set[str] = set()
    first = call.args[0] if call.args else None
    if short in _PLURAL_CHECKS and isinstance(first, list):
        for name in first:
            out.add(cache_var(short, str(name)))
            out.add(define_of(short, str(name)))
    elif short in _SINGULAR_CHECKS and isinstance(first, str):
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
