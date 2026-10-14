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
from dataclasses import dataclass, field
from pathlib import Path

_MODULE_BEGIN_RE = re.compile(r"^## begin gnulib module (\S+)")
_MODULE_END_RE = re.compile(r"^## end\s+gnulib module")
_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\+?=)\s*(.*)$")
_RULE_RE = re.compile(r"^([^\s:=#][^:=#]*?)\s*:(?!=)\s*(.*)$")
_IF_RE = re.compile(r"^if\s+(!?)\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")
# `bin_PROGRAMS`, `lib_LTLIBRARIES`, `pkgextension_LTLIBRARIES`...: every
# installed or convenience target; `check_` (tests) and `EXTRA_` (built only
# when another variable names them) are not targets of the build.
_TARGET_VAR_RE = re.compile(r"^(?!EXTRA_|check_)[a-z][A-Za-z0-9_]*_(PROGRAMS|LIBRARIES|LTLIBRARIES)$")
_GENERATOR_RE = re.compile(r"\$\((?:YACC|LEX|BISON|FLEX)\)$|(?:^|/)(?:bison|flex|yacc|lex)$")
YACC_SUFFIXES = (".y", ".yy", ".y++", ".yxx", ".ypp")
LEX_SUFFIXES = (".l", ".ll", ".l++", ".lxx", ".lpp")


_INCLUDE_LINE_RE = re.compile(r"^include\s+(\S+)[ \t]*$", re.M)


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
        for variable, base in (("$(top_srcdir)/", top), ("$(top_builddir)/", top),
                               ("$(srcdir)/", makefile_dir), ("$(builddir)/", makefile_dir)):
            if target.startswith(variable):
                candidate = base / target[len(variable):]
                return candidate if candidate.is_file() else None
        if "$(" in target:
            return None
        for base in (here, makefile_dir):
            if (base / target).is_file():
                return base / target
        return None

    def splice(match: re.Match) -> str:
        target = match.group(1)
        file = resolve(target)
        if file is None:
            return match.group(0)
        rel = os.path.relpath(file.parent, makefile_dir).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        canon = re.sub(r"[^A-Za-z0-9_]", "_", rel)
        body = _inline_includes(file.read_text(encoding="utf-8", errors="replace"), file.parent, makefile_dir, top,
                                depth + 1)
        prefix = rel + "/" if rel else ""
        for token in ("%reldir%/", "%D%/"):
            body = body.replace(token, prefix)
        for token in ("%reldir%", "%D%"):
            body = body.replace(token, rel or ".")
        for token in ("%canon_reldir%", "%C%"):
            body = body.replace(token, canon)
        return f"# include {target}\n{body}\n"

    return _INCLUDE_LINE_RE.sub(splice, text)


@dataclass
class Source:
    path: str
    condition: str | None  # automake conditional (None: always)
    module: str | None
    extra: bool = False  # EXTRA_*_SOURCES: compiled only via AC_LIBOBJ / gl_LIBOBJS


@dataclass
class GeneratedHeader:
    out: str  # e.g. "sys/stat.h"
    template: str  # e.g. "sys_stat.in.h"
    condition: str | None  # e.g. "GL_GENERATE_ALLOCA_H"
    extra_inputs: list[str]  # other plain-file prerequisites (assert.h uses verify.h)
    module: str | None


@dataclass
class Program:
    name: str
    sources: list[Source] = field(default_factory=list)
    ldadd: list[str] = field(default_factory=list)  # as written: libver.a, $(CLOCK_TIME_LIB)


@dataclass
class Library:
    name: str  # automake canonical name, e.g. "libgzip_a"
    archive: str  # e.g. "libgzip.a"
    sources: list[Source] = field(default_factory=list)
    libadd: list[str] = field(default_factory=list)
    installed: bool = False  # lib_ / pkglib_ ... rather than noinst_: a shared library when libtool builds it


@dataclass
class Makefile:
    libraries: dict[str, Library] = field(default_factory=dict)
    programs: dict[str, Program] = field(default_factory=dict)
    headers: list[GeneratedHeader] = field(default_factory=list)
    built_sources: list[tuple[str, str | None]] = field(default_factory=list)  # (name, condition)
    # `include_HEADERS`, `nobase_pkginclude_HEADERS`, ...: the headers automake installs (the
    # library's public interface), and `noinst_HEADERS`: the ones it does not.
    installed_headers: list[Source] = field(default_factory=list)
    private_headers: list[Source] = field(default_factory=list)
    conditionals: set[str] = field(default_factory=set)
    variables: dict[str, list[str]] = field(default_factory=dict)  # last unconditional value
    # Options a hand-written rule passes to yacc or lex for one source
    # (`$(YACC) -o $@ -p zz $<`), besides AM_YFLAGS and <target>_YFLAGS.
    generator_flags: dict[str, list[str]] = field(default_factory=dict)
    subdirs: list[str] = field(default_factory=list)  # SUBDIRS, conditional ones included
    recipes: dict[str, list[str]] = field(default_factory=dict)  # rule target -> its recipe lines
    rules: dict[str, list[str]] = field(default_factory=dict)  # rule target -> its prerequisites, as written
    notes: list[str] = field(default_factory=list)

    def ldadd_vars(self, program: str) -> list[str]:
        """`$(X_LIB)` style variables a program links against."""
        out = []
        for item in self.programs.get(program, Program(program)).ldadd:
            match = re.fullmatch(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", item)
            if match and match.group(1) not in out:
                out.append(match.group(1))
        return out


def _logical_lines(text: str):
    """Join `\\` continuations; yield (line, is_recipe)."""
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


def parse(text: str) -> Makefile:  # noqa: C901 - a line-oriented state machine
    """Parse a Makefile.am or gnulib.mk fragment line by line: the declared programs and libraries
    with their sources (conditional ones tagged with the automake conditional in effect), LDADD /
    LIBADD, BUILT_SOURCES, the `foo.h: foo.in.h` wrapper-header rules with their conditions,
    SUBDIRS, the variables' last unconditional values, every rule's recipe lines, and the options
    of yacc / lex recipes. `$(var)` references in source lists are expanded at the end, since
    make evaluates them late.
    """
    mk = Makefile()
    module: str | None = None
    stack: list[str | None] = []  # current conditional (None for negations we cannot name)
    in_recipe = False
    current_rule: tuple[str, list[str]] | None = None

    def condition() -> str | None:
        active = [c for c in stack if c is not None]
        if len(active) == 1:
            return active[0]
        if not active:
            return None
        return " && ".join(active)

    seen_vars: dict[str, list[str]] = {}  # unconditional assignments, in file order

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

    def declare(kind_var: str, names: list[str]) -> None:
        for name in target_names(kind_var, names):
            canonical = _canonical(name)
            if kind_var.endswith("LIBRARIES") or kind_var.endswith("LTLIBRARIES"):
                mk.libraries.setdefault(canonical, Library(canonical, name, installed=not kind_var.startswith("noinst_")))
            elif kind_var.endswith("PROGRAMS"):
                mk.programs.setdefault(canonical, Program(name))

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

    for line in _logical_lines(text):
        if line.startswith("\t"):
            in_recipe = True
            if current_rule is not None:
                target, deps = current_rule
                mk.recipes.setdefault(target, []).append(line.strip())
                # Recipes that read a second template file (assert.h splices verify.h).
                for extra in re.findall(r"\$\(srcdir\)/([A-Za-z0-9_./-]+)", line):
                    if extra not in deps and extra != target:
                        deps.append(extra)
                if deps and deps[0].endswith(YACC_SUFFIXES + LEX_SUFFIXES):
                    flags = _generator_flags(line)
                    if flags is not None:
                        mk.generator_flags[deps[0]] = flags
            continue
        in_recipe = False
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
            stack.append(None if if_match.group(1) else name)
            continue
        if stripped == "else":
            if stack:
                top = stack.pop()
                stack.append(None if top is not None else None)
            continue
        if stripped == "endif":
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
                owner, kind = ldadd_match.groups()
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
            # (a glibc header rewritten by sed; the BCR patch overlay renders it as a template).
            if not target.startswith("$(") and target.endswith(".h") and deps and (
                    deps[0].endswith(".in.h") or (target.endswith(".gl.h") and deps[0].endswith(".h"))):
                plain = [d for d in deps[1:] if not d.startswith("$(") and "/" not in d.lstrip("./")]
                mk.headers.append(GeneratedHeader(target, deps[0], condition(), plain, module))
            continue
        current_rule = None
    # `gawk_SOURCES = $(base_sources)`: sources named through variables.
    for target in [*mk.programs.values(), *mk.libraries.values()]:
        target.sources = [
            Source(path, s.condition, s.module, s.extra)
            for s in target.sources
            for path in (_expand_variables([s.path], mk.variables) if s.path.startswith("$(") else [s.path])
        ]
    return mk


def strip_directory(mk: Makefile, directory: str) -> bool:
    """A gnulib.mk written for a non-recursive Makefile.am (sed: `lib_libsed_a_SOURCES += lib/x.c`)
    names every file with its directory; strip it so the fragment reads like one of its own
    directory.  True when the paths carried it."""
    prefix = directory.rstrip("/") + "/"
    paths = [s.path for lib in mk.libraries.values() for s in lib.sources] + [s.path for p in mk.programs.values()
                                                                             for s in p.sources]
    if not paths or sum(p.startswith(prefix) for p in paths) * 2 <= len(paths):
        return False

    def cut(path: str) -> str:
        return path[len(prefix):] if path.startswith(prefix) else path

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
                expanded = [w[:-len(old)] + new if old and w.endswith(old) else w for w in expanded]
            out.extend(expanded)
        else:
            out.append(word)
    return out


_expand_variables = expand_variables


def _generator_flags(recipe: str) -> list[str] | None:
    """The options of a `$(YACC) -o $@ -p zz $<` recipe line: what the
    rule passes besides the output, the input and the flag variables."""
    words = recipe.strip().split()
    start = next((i for i, w in enumerate(words) if _GENERATOR_RE.search(w)), None)
    if start is None:
        return None
    flags: list[str] = []
    skip = False
    for word in words[start + 1:]:
        if skip:
            skip = False
            continue
        if word in ("&&", "||", ";", "|", ">", "<", ">>") or word.endswith(";"):
            break
        if word in ("-o", "--output"):
            skip = True
            continue
        if word.startswith("$") or word.startswith("-o") or word.startswith("--output="):
            continue  # $@, $<, $(AM_YFLAGS), $(YFLAGS), -o$@
        flags.append(word)
    return flags


def header_stem(out: str) -> str:
    """`sys/stat.h` -> `sys_stat`, `getopt-cdefs.h` -> `getopt-cdefs`."""
    name = out[:-2] if out.endswith(".h") else out
    return name.replace("/", "_")


def generate_variable(out: str) -> str:
    """The gnulib `GL_GENERATE_*` variable of a conditional header."""
    return "GL_GENERATE_" + re.sub(r"[^A-Za-z0-9]", "_", out).upper()
