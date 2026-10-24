"""Parse the automake fragments a gnulib project ships: lib/gnulib.mk and
Makefile.am.

Only the shapes gnulib-tool and ordinary GNU packages produce are
understood: `VAR = ...` / `VAR += ...` with `\\` continuations, `if COND` /
`else` / `endif`, rules `target: deps` with tab-indented recipes, and
`## begin gnulib module X` / `## end` markers.  The result says which
sources are compiled unconditionally, which only under an automake
conditional, and which headers are generated from `*.in.h` templates (and
under which `GL_GENERATE_*` conditional).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

_MODULE_BEGIN_RE = re.compile(r"^## begin gnulib module (\S+)")
_MODULE_END_RE = re.compile(r"^## end\s+gnulib module")
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\+?=)\s*(.*)$")
_RULE_RE = re.compile(r"^([^\s:=#][^:=#]*?)\s*:(?!=)\s*(.*)$")
_IF_RE = re.compile(r"^if\s+(!?)\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")
# Automake lets `else` and `endif` repeat the condition (`endif COND_X`, `else !COND_X`);
# grub's gentpl-generated Makefile.util.am closes every block that way.
_ELSE_RE = re.compile(r"^else(?:\s+!?\s*[A-Za-z_][A-Za-z0-9_]*)?\s*$")
_ENDIF_RE = re.compile(r"^endif(?:\s+!?\s*[A-Za-z_][A-Za-z0-9_]*)?\s*$")
# `bin_PROGRAMS`, `lib_LTLIBRARIES`, `pkgextension_LTLIBRARIES`...: every
# installed or convenience target; `check_` (tests) and `EXTRA_` (built only
# when another variable names them) are not targets of the build.
_TARGET_VAR_RE = re.compile(r"^(?!EXTRA_|check_)[a-z][A-Za-z0-9_]*_(PROGRAMS|LIBRARIES|LTLIBRARIES)$")
# `bin_PROGRAMS = @bin_PROGRAMS@`: configure computes the list (coreutils); automake
# requires every candidate to be in `EXTRA_PROGRAMS` ("Conditional compilation of programs").
_SUBST_WORD_RE = re.compile(r"^@([A-Za-z_][A-Za-z0-9_]*)@$")
# `$(YACC)`, `$(LEX)`, libpcap's `$(BISON_BYACC)`, or the tool by name.
_GENERATOR_RE = re.compile(r"\$\(\w*(?:YACC|LEX|BISON|FLEX)\w*\)$|(?:^|/)(?:bison|flex|yacc|lex)$")
YACC_SUFFIXES = (".y", ".yy", ".y++", ".yxx", ".ypp")
LEX_SUFFIXES = (".l", ".ll", ".l++", ".lxx", ".lpp")


# automake's `include`, GNU make's `-include` / `sinclude` (tftp-hpa's `-include ../config/MCONFIG`).
_INCLUDE_LINE_RE = re.compile(r"^-?s?include\s+(\S+)[ \t]*$", re.MULTILINE)


def read_makefile(makefile: Path, top: Path, depth: int = 0) -> str:
    """A Makefile.am with its automake `include` fragments inlined.

    `include src/Makemodule.am` (nvc), `include Makefile.inc` (libssh2) and
    `include $(top_srcdir)/...` splice another file into the Makefile.am at
    automake time; a fragment's `%reldir%` / `%D%` is its directory relative
    to the Makefile.am's and `%canon_reldir%` / `%C%` the same with the
    characters automake canonicalizes.  A plain path is tried relative to the
    including file, then to the Makefile.am; an unresolved include is left as
    it is.  `top` is the project root (`$(top_srcdir)`).
    """
    text = makefile.read_text(encoding="utf-8", errors="replace")
    return _inline_includes(text, makefile.parent, makefile.parent, top, depth)


def _inline_includes(text: str, here: Path, makefile_dir: Path, top: Path, depth: int) -> str:
    if depth > 8:
        return text

    def resolve(target: str) -> Path | None:
        for variable, base in (
            ("$(top_srcdir)/", top),
            ("$(top_builddir)/", top),
            ("$(srcdir)/", makefile_dir),
            ("$(builddir)/", makefile_dir),
        ):
            if target.startswith(variable):
                candidate = base / target[len(variable) :]
                return candidate if candidate.is_file() else None
        if "$(" in target:
            return None
        for base in (here, makefile_dir):
            if (base / target).is_file():
                return base / target
        # A file configure writes (`config/MCONFIG` from AC_CONFIG_FILES): its template, whose `@VAR@`
        # references the reader resolves as it does a Makefile.in's.
        for base in (here, makefile_dir):
            if (base / f"{target}.in").is_file():
                return base / f"{target}.in"
        return None

    def splice(match: re.Match[str]) -> str:
        target = match.group(1)
        file = resolve(target)
        if file is None:
            return match.group(0)
        rel = os.path.relpath(file.parent, makefile_dir).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        canon = re.sub(r"[^A-Za-z0-9_]", "_", rel)
        body = file.read_text(encoding="utf-8", errors="replace")
        # The fragment's own tokens first, so that an include it makes through them
        # (newlib's libc/Makefile.inc: `include %D%/argz/Makefile.inc`) names a real path.
        prefix = rel + "/" if rel else ""
        for token in ("%reldir%/", "%D%/"):
            body = body.replace(token, prefix)
        for token in ("%reldir%", "%D%"):
            body = body.replace(token, rel or ".")
        for token in ("%canon_reldir%", "%C%"):
            body = body.replace(token, canon)
        body = _inline_includes(body, file.parent, makefile_dir, top, depth + 1)
        return f"# include {target}\n{body}\n"

    return _INCLUDE_LINE_RE.sub(splice, text)


@dataclass
class EchoHeader:
    """A header a recipe writes with `echo` lines (gnulib's libgmp-mpz: `lib/gmp.h` wrapping
    mini-gmp.h or gmp/gmp.h), under the automake conditionals of its rule."""

    out: str
    """E.g. "gmp.h", relative to the Makefile's directory as the rule names it."""
    lines: list[str]
    """The header's text, one entry per echo."""
    condition: str | None
    """"GL_GENERATE_GMP_H && GL_GENERATE_MINI_GMP_H"."""
    module: str | None
    """The gnulib module whose `## begin` block the rule is in, if any."""


@dataclass
class Source:
    path: str
    """As the Makefile names it, relative to the Makefile's directory."""
    condition: str | None
    """Automake conditional (None: always)."""
    module: str | None
    """The gnulib module whose `## begin` block lists it, if any."""
    extra: bool = False
    """EXTRA_*_SOURCES: compiled only via AC_LIBOBJ / gl_LIBOBJS."""


@dataclass
class GeneratedHeader:
    out: str
    """E.g. "sys/stat.h"."""
    template: str
    """E.g. "sys_stat.in.h"."""
    condition: str | None
    """E.g. "GL_GENERATE_ALLOCA_H"."""
    extra_inputs: list[str]
    """Other plain-file prerequisites (assert.h uses verify.h)."""
    module: str | None
    """The gnulib module whose `## begin` block the rule is in, if any."""


@dataclass
class Program:
    name: str
    """As listed in `*_PROGRAMS` (`gzip`, `src/grep`)."""
    sources: list[Source] = field(default_factory=list)
    """Its `_SOURCES` (and `EXTRA_*_SOURCES`, tagged `extra`)."""
    ldadd: list[str] = field(default_factory=list)
    """As written: libver.a, $(CLOCK_TIME_LIB)."""
    substituted: bool = False
    """An `EXTRA_PROGRAMS` candidate: built only when configure's value of a substituted
    `*_PROGRAMS` variable (`bin_PROGRAMS = @bin_PROGRAMS@`) names it."""

    default_sources: bool = False
    """No `_SOURCES` variable: the source is automake's default, the program's name with its
    extension replaced by `.c` (`src/hostid` -> `src/hostid.c`, `src/libstdbuf.so` -> `src/libstdbuf.c`)."""

    installed: bool = True
    """`bin_` / `sbin_` / `libexec_` ... rather than `noinst_`: a program `make install` ships, public
    in the generated module; a `noinst_` helper stays the module's own."""


@dataclass
class Library:
    name: str
    """Automake canonical name, e.g. "libgzip_a"."""
    archive: str
    """E.g. "libgzip.a"."""
    sources: list[Source] = field(default_factory=list)
    """Its `_SOURCES` (and `EXTRA_*_SOURCES`, tagged `extra`)."""
    libadd: list[str] = field(default_factory=list)
    """Its unconditional `_LIBADD` words, as written."""
    installed: bool = False
    """lib_ / pkglib_ ... rather than noinst_: a shared library when libtool builds it."""
    include_prefix: str | None = None
    """The subdirectory of `$(includedir)` the headers are installed in (readline's
    `$(includedir)/readline`): consumers include them as `<readline/readline.h>`."""


@dataclass
class Makefile:
    libraries: dict[str, Library] = field(default_factory=dict)
    """Automake canonical name -> library."""
    programs: dict[str, Program] = field(default_factory=dict)
    """Automake canonical name -> program."""
    subst_templates: dict[str, str] = field(default_factory=dict)
    """Sources config.status writes from a template (libpcap's `grammar.y` from `grammar.y.in`): source -> template."""

    substituted_targets: dict[str, str] = field(default_factory=dict)
    """`*_PROGRAMS` / `*_LIBRARIES` variables whose value is a configure substitution
    (`bin_PROGRAMS = @bin_PROGRAMS@`): variable -> substitution name."""

    headers: list[GeneratedHeader] = field(default_factory=list)
    """The wrapper headers generated from `.in.h` templates (`stdio.h: stdio.in.h`)."""
    echo_headers: list[EchoHeader] = field(default_factory=list)
    """The headers written by echo recipes."""
    built_sources: list[tuple[str, str | None]] = field(default_factory=list)
    """BUILT_SOURCES as (name, condition)."""
    installed_headers: list[Source] = field(default_factory=list)
    """`include_HEADERS`, `nobase_pkginclude_HEADERS`, ...: the headers automake installs (the
    library's public interface)."""
    private_headers: list[Source] = field(default_factory=list)
    """`noinst_HEADERS`: the headers automake does not install."""
    conditionals: set[str] = field(default_factory=set)
    """Every automake conditional an `if` names."""
    variables: dict[str, list[str]] = field(default_factory=dict)
    """Last unconditional value."""
    conditional_variables: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    """Words added to a variable inside `if COND` blocks (`AM_CPPFLAGS += -DENABLE_MERGE`
    under `if ENABLE_MERGE`): variable -> [(word, condition)]."""
    generator_flags: dict[str, list[str]] = field(default_factory=dict)
    """Options a hand-written rule passes to yacc or lex for one source
    (`$(YACC) -o $@ -p zz $<`), besides AM_YFLAGS and <target>_YFLAGS."""
    subdirs: list[str] = field(default_factory=list)
    """SUBDIRS, conditional ones included."""
    recipes: dict[str, list[str]] = field(default_factory=dict)
    """Rule target -> its recipe lines."""
    rules: dict[str, list[str]] = field(default_factory=dict)
    """Rule target -> its prerequisites, as written."""
    notes: list[str] = field(default_factory=list)
    """What the parser left out, for the report."""


def _logical_lines(text: str) -> Iterator[str]:
    """Join `\\` continuations; yield the logical lines."""
    pending = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        if pending:
            raw = pending + raw.lstrip()
            pending = ""
        if raw.endswith("\\"):
            pending = raw[:-1] + " "
            continue
        yield raw
    if pending:
        yield pending


def _canonical(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_@]", "_", name)


def parse(text: str) -> Makefile:
    """Parse a Makefile.am or gnulib.mk fragment line by line: the declared programs and libraries
    with their sources (conditional ones tagged with the automake conditional in effect), LDADD /
    LIBADD, BUILT_SOURCES, the `foo.h: foo.in.h` wrapper-header rules with their conditions,
    SUBDIRS, the variables' last unconditional values, every rule's recipe lines, and the options
    of yacc / lex recipes. `$(var)` references in source lists are expanded at the end, since
    make evaluates them late.
    """
    mk = Makefile()
    module: str | None = None
    stack: list[str | None] = []  # current conditionals (`!NAME` for a negation; None for nested ones not named)
    current_rule: tuple[str, list[str]] | None = None

    def condition() -> str | None:
        active = [c for c in stack if c is not None]
        if len(active) == 1:
            return active[0]
        if not active:
            return None
        return " && ".join(active)

    seen_vars: dict[str, list[str]] = {}  # unconditional assignments, in file order
    declared_sources: set[str] = set()  # targets with a `_SOURCES` variable, even an empty one

    def target_names(kind_var: str, words: list[str], depth: int = 0) -> list[str]:
        """`$(API_TEST_PROGS)` in a target list: the variable's words (pkgconf)."""
        out: list[str] = []
        for word in words:
            ref = _VARIABLE_REF_RE.match(word)
            if ref is None:
                out.append(word)
            elif ref.group(1) in seen_vars and depth < 5:
                out += target_names(kind_var, seen_vars[ref.group(1)], depth + 1)
            else:
                mk.notes.append(f"{kind_var}: {word} is a variable the Makefile does not define; left out")
        return out

    def declare(kind_var: str, names: list[str], substituted: bool = False) -> None:
        for name in target_names(kind_var, names):
            subst = _SUBST_WORD_RE.match(name)
            if subst:
                mk.substituted_targets[kind_var] = subst.group(1)
                continue
            canonical = _canonical(name)
            if kind_var.endswith(("LIBRARIES", "LTLIBRARIES")):
                mk.libraries.setdefault(
                    canonical, Library(canonical, name, installed=not kind_var.startswith("noinst_"))
                )
            elif kind_var.endswith("PROGRAMS"):
                mk.programs.setdefault(
                    canonical, Program(name, substituted=substituted, installed=not kind_var.startswith("noinst_"))
                )

    def declare_candidates() -> None:
        """The `EXTRA_*` candidates of every substituted target variable."""
        for kind_var in list(mk.substituted_targets):
            primary = kind_var.rsplit("_", 1)[1]
            declare(f"EXTRA_{primary}", seen_vars.get(f"EXTRA_{primary}", []), substituted=True)

    # Targets first: automake does not care whether `libfoo_a_SOURCES` comes
    # before or after `lib_LIBRARIES = libfoo.a` (libssh2's src/Makefile.am
    # lists the sources, through an included fragment, above the declaration).
    for line in _logical_lines(text):
        assign = _ASSIGN_RE.match(line.strip())
        if assign is None:
            continue
        if assign.group(2) == "=":
            seen_vars[assign.group(1)] = assign.group(3).split()
        else:
            seen_vars.setdefault(assign.group(1), []).extend(assign.group(3).split())
        if _TARGET_VAR_RE.match(assign.group(1)):
            declare(assign.group(1), assign.group(3).split())
    declare_candidates()

    echo_rule: EchoHeader | None = None  # a `.h` rule whose recipe so far is only echo lines
    for line in _logical_lines(text):
        if line.startswith("\t"):
            if current_rule is not None:
                target, deps = current_rule
                mk.recipes.setdefault(target, []).append(line.strip())
                if echo_rule is not None:
                    echo_rule = _echo_step(mk, echo_rule, line.strip())
                # Recipes that read a second template file (assert.h splices verify.h).
                for extra in re.findall(r"\$\(srcdir\)/([A-Za-z0-9_./-]+)", line):
                    if extra not in deps and extra != target:
                        deps.append(extra)
                if deps and deps[0].endswith(YACC_SUFFIXES + LEX_SUFFIXES):
                    flags = _generator_flags(line)
                    if flags is not None:
                        mk.generator_flags[re.sub(r"^\$\((?:top_)?srcdir\)/", "", deps[0])] = flags
            continue
        stripped = line.strip()
        begin = _MODULE_BEGIN_RE.match(stripped)
        if begin:
            module = begin.group(1)
            continue
        if _MODULE_END_RE.match(stripped):
            module = None
            continue
        if not stripped or stripped.startswith("#"):
            current_rule = None
            continue
        if_match = _IF_RE.match(stripped)
        if if_match:
            name = if_match.group(2)
            mk.conditionals.add(name)
            # `if !COND` and the `else` branch: the negation, `!COND` (make's `src/posixos.c`
            # under `else` of `if WINDOWSENV` is for every other platform).
            stack.append(f"!{name}" if if_match.group(1) else name)
            continue
        if _ELSE_RE.match(stripped):
            if stack:
                top = stack.pop()
                stack.append(None if top is None else top[1:] if top.startswith("!") else f"!{top}")
            continue
        if _ENDIF_RE.match(stripped):
            if stack:
                stack.pop()
            continue
        assign = _ASSIGN_RE.match(stripped)
        if assign:
            current_rule = None
            var, op, value = assign.group(1), assign.group(2), assign.group(3)
            words = value.split()
            cond = condition()
            if cond is None:
                if op == "=":
                    mk.variables[var] = list(words)
                else:
                    mk.variables.setdefault(var, []).extend(words)
            else:
                mk.conditional_variables.setdefault(var, []).extend((w, cond) for w in words)
            if var == "SUBDIRS":
                mk.subdirs.extend(w for w in words if w not in mk.subdirs)
            if _TARGET_VAR_RE.match(var):
                declare(var, words)
                continue
            if var == "BUILT_SOURCES":
                mk.built_sources.extend((w, cond) for w in words)
                continue
            headers_match = re.fullmatch(r"(?:nobase_|dist_|nodist_)*([A-Za-z][A-Za-z0-9_]*?)_HEADERS", var)
            if headers_match:
                where = headers_match.group(1)
                bucket = mk.private_headers if where in ("noinst", "check", "EXTRA") else mk.installed_headers
                bucket.extend(Source(w, cond, module) for w in words)
                continue
            source_match = re.fullmatch(r"(EXTRA_|nodist_|dist_)?([A-Za-z0-9_@]+?)_SOURCES", var)
            if source_match:
                prefix, owner = source_match.group(1), source_match.group(2)
                declared_sources.add(owner)
                sources = [Source(w, cond, module, extra=prefix == "EXTRA_") for w in words]
                if owner in mk.libraries:
                    mk.libraries[owner].sources.extend(sources)
                elif owner in mk.programs:
                    mk.programs[owner].sources.extend(sources)
                else:
                    mk.notes.append(f"{var}: sources of an undeclared target")
                continue
            ldadd_match = re.fullmatch(r"([A-Za-z0-9_@]+?)_(LDADD|LIBADD)", var)
            if ldadd_match:
                owner, _kind = ldadd_match.groups()
                if cond is not None:
                    continue  # kept in `conditional_variables`: the target resolves the arm
                if owner in mk.programs:
                    mk.programs[owner].ldadd.extend(words)
                elif owner in mk.libraries:
                    mk.libraries[owner].libadd.extend(words)
                continue
            continue
        rule = _RULE_RE.match(stripped)
        # `$(srcdir)/awkgram.c: awkgram.y`: a rule for a shipped generated file.
        target = re.sub(r"^\$\((?:top_)?srcdir\)/", "", rule.group(1).strip()) if rule else ""
        if rule:
            deps_text = rule.group(2)
            deps = deps_text.split()
            current_rule = (target, deps)
            # `$(PROG): $(OBJ)` (a hand-written Makefile.in) is a rule too; only
            # the wrapper-header recognition below wants a plain file name.
            mk.rules.setdefault(target, list(deps))
            # `stdio.h: stdio.in.h`, and `malloc/scratch_buffer.gl.h: malloc/scratch_buffer.h`
            # (a glibc header rewritten by sed; the BCR patch overlay renders it as a template;
            # `malloc/dynarray-skeleton.gl.h` is rewritten from the `.c` of the same name).
            if (
                not target.startswith("$(")
                and target.endswith(".h")
                and deps
                and (deps[0].endswith(".in.h") or (target.endswith(".gl.h") and deps[0].endswith((".h", ".c"))))
            ):
                # The other file inputs (`verify.h`; `lib/verify.h` in a non-recursive gnulib.mk,
                # which `strip_directory` cuts like every other path), not `$(top_builddir)/config.status`.
                plain = [d for d in deps[1:] if not d.startswith("$(")]
                mk.headers.append(GeneratedHeader(target, deps[0], condition(), plain, module))
            # `lib/gmp.h: $(top_builddir)/config.status` with an echo recipe: candidate.
            echo_rule = (
                EchoHeader(target, [], condition(), module)
                if not target.startswith("$(") and target.endswith(".h") and not any(d.endswith(".in.h") for d in deps)
                else None
            )
            continue
        current_rule = None
        echo_rule = None
    # `gawk_SOURCES = $(base_sources)`: sources named through variables.
    owners: list[Program | Library] = [*mk.programs.values(), *mk.libraries.values()]
    for owner_target in owners:
        owner_target.sources = [
            Source(path, s.condition, s.module, s.extra)
            for s in owner_target.sources
            for path in (expand_variables([s.path], mk.variables) if s.path.startswith("$(") else [s.path])
        ]
    # Automake's default `_SOURCES`: the target's name with its extension replaced by `.c`
    # (coreutils declares none for most of its programs).
    for canonical, program in mk.programs.items():
        if canonical not in declared_sources and not program.sources:
            program.sources.append(Source(re.sub(r"\.[^./]*$", "", program.name) + ".c", None, None))
            program.default_sources = True
    return mk


def strip_directory(mk: Makefile, directory: str) -> bool:
    """A gnulib.mk written for a non-recursive Makefile.am (sed: `lib_libsed_a_SOURCES += lib/x.c`)
    names every file with its directory; strip it so the fragment reads like one of its own
    directory.  True when the paths carried it."""
    prefix = directory.rstrip("/") + "/"
    paths = [s.path for lib in mk.libraries.values() for s in lib.sources] + [
        s.path for p in mk.programs.values() for s in p.sources
    ]
    if not paths or sum(p.startswith(prefix) for p in paths) * 2 <= len(paths):
        return False

    def cut(path: str) -> str:
        return path.removeprefix(prefix)

    for owner in list(mk.libraries.values()) + list(mk.programs.values()):
        for source in owner.sources:
            source.path = cut(source.path)
    for library in mk.libraries.values():
        library.archive = cut(library.archive)
    for source in mk.installed_headers + mk.private_headers:
        source.path = cut(source.path)
    for header in mk.headers:
        header.out, header.template = cut(header.out), cut(header.template)
        header.extra_inputs = [cut(p) for p in header.extra_inputs]
    mk.built_sources = [(cut(name), cond) for name, cond in mk.built_sources]
    mk.recipes = {cut(k): v for k, v in mk.recipes.items()}
    mk.rules = {cut(k): [cut(p) for p in v] for k, v in mk.rules.items()}
    mk.generator_flags = {cut(k): v for k, v in mk.generator_flags.items()}
    return True


_ECHO_RE = re.compile(r"^echo '([^']*)' (>>?) ?\$@-t$")
_RECIPE_SILENCERS_RE = re.compile(r"^(?:\$\((?:AM_V_GEN|AM_V_at|gl_V_at|AM_V_P)\)\s*)+")


def _echo_step(mk: Makefile, rule: EchoHeader, line: str) -> EchoHeader | None:
    """One recipe line of a candidate echo-written header: an echo adds a line, `mv $@-t $@`
    completes the header, a `mkdir` is ignored, anything else disqualifies the rule."""
    line = _RECIPE_SILENCERS_RE.sub("", line).strip()
    if not line or line.startswith(("$(MKDIR_P)", "mkdir")):
        return rule
    echo = _ECHO_RE.match(line)
    if echo:
        if echo.group(2) == ">":
            rule.lines.clear()
        rule.lines.append(echo.group(1))
        return rule
    if re.fullmatch(r"mv(?: -f)? \$@-t \$@", line):
        if rule.lines:
            mk.echo_headers.append(rule)
        return None
    return None


_VARIABLE_REF_RE = re.compile(r"^\$[({]([A-Za-z_][A-Za-z0-9_]*)(?::([^=]*)=([^)}]*))?[)}]$")


def expand_variables(words: list[str], variables: dict[str, list[str]], depth: int = 0) -> list[str]:
    """`$(VAR)` and `${VAR}` spelled out from the Makefile's own assignments, and the
    suffix substitution `$(SRC:.c=.o)` applied (`$(CSRC:.c=.o)` in tcpdump's Makefile.in);
    a word that is not a known variable stays as written."""
    out: list[str] = []
    for word in words:
        match = _VARIABLE_REF_RE.match(word)
        if match and match.group(1) in variables and depth < 6:
            expanded = expand_variables(variables[match.group(1)], variables, depth + 1)
            old, new = match.group(2), match.group(3)
            if old is not None:
                expanded = [w[: -len(old)] + new if old and w.endswith(old) else w for w in expanded]
            out.extend(expanded)
        else:
            out.append(word)
    return out


def _generator_flags(recipe: str) -> list[str] | None:
    """The options of a `$(YACC) -o $@ -p zz $<` recipe line: what the
    rule passes besides the output, the input and the flag variables."""
    words = recipe.strip().split()
    start = next((i for i, w in enumerate(words) if _GENERATOR_RE.search(w)), None)
    if start is None:
        return None
    flags: list[str] = []
    skip = False
    for word in words[start + 1 :]:
        if skip:
            skip = False
            continue
        if word in ("&&", "||", ";", "|", ">", "<", ">>") or word.endswith(";"):
            break
        if word in ("-o", "--output"):
            skip = True
            continue
        if word.startswith(("$", "-o", "--output=", "--header-file=", "--defines=")):
            continue  # $@, $<, $(AM_YFLAGS), $(YFLAGS), -o$@, the outputs the rule names
        flags.append(word)
    return flags


def header_stem(out: str) -> str:
    """`sys/stat.h` -> `sys_stat`, `getopt-cdefs.h` -> `getopt-cdefs`."""
    name = out.removesuffix(".h")
    return name.replace("/", "_")


def generate_variable(out: str) -> str:
    """The gnulib `GL_GENERATE_*` variable of a conditional header."""
    return "GL_GENERATE_" + re.sub(r"[^A-Za-z0-9]", "_", out).upper()
