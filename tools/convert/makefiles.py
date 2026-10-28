"""The automake tree: finding the Makefile.am files, parsing their libraries and programs
(automake's and hand-written Makefile.in's), and make-variable expansion."""

from __future__ import annotations

import re
from collections.abc import Set as AbstractSet
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk
from tools.convert.common import _LIBRARY_RE, _SOURCE_SUFFIXES, makefile_relative
from tools.convert.ir import Call, Package, Rule, Target
from tools.convert.translate import ProjectState


def _am_block(text: str, name: str, conditional: str) -> dict[str, dec.ContextValue]:
    """`{"<file>:<lines>": [...]}`: the `if COND ... endif` block of an automake file, as the
    excerpt of a decision request's context."""
    lines = text.replace("\r\n", "\n").split("\n")
    for start, line in enumerate(lines, 1):
        if line.strip() != f"if {conditional}":
            continue
        depth = 0
        for end in range(start, len(lines) + 1):
            word = lines[end - 1].strip().split(" ")[0]
            if word == "if":
                depth += 1
            elif word == "endif":
                depth -= 1
                if depth == 0:
                    return {f"{name}:{start}-{end}": [l.rstrip() for l in lines[start - 1 : end]]}
        break
    return {}


# Directories whose Makefile.am builds nothing a consumer links or runs.
_SKIP_SUBDIRS = {"po", "doc", "man", "tests", "gnulib-tests", "testsuite", "test", "m4", "build-aux", "gnulib"}


_COMPUTED_SUBDIRS_RE = re.compile(r"^(?:DIST_)?SUBDIRS\s*\+?=.*\$\(", re.MULTILINE)


def _configured_subdirs(src: Path, known: set[str]) -> list[str]:
    """Sub-configures a project names only at configure time.

    texlive's `libs/Makefile.am` says `SUBDIRS = . $(MAKE_SUBDIRS)` and configure fills the
    variable from a list in its own m4; `AC_CONFIG_SUBDIRS` never sees literal names.  When a
    Makefile.am's SUBDIRS is computed like that, the immediate child directories that carry
    a configure.ac of their own are those sub-configures.
    """
    makefile = src / "Makefile.am"
    if not makefile.is_file() or not _COMPUTED_SUBDIRS_RE.search(
        makefile.read_text(encoding="utf-8", errors="replace")
    ):
        return []
    found = []
    for child in sorted(src.iterdir()):
        if (
            child.is_dir()
            and child.name not in known
            and child.name not in _SKIP_SUBDIRS
            and any((child / c).is_file() for c in ("configure.ac", "configure.in"))
        ):
            found.append(child.name)
    return found


_OBJECT_SUFFIXES = (".o", ".lo", ".obj")
# `$(CC) ... -o $@`, `-o $(PROG)`, or `-o tftp$(X)`: a link line (a compile line's target carries a suffix).
_LINK_RE = re.compile(r"\$[({](?:CC|CXX|LINK|CCLD|CXXLD|LIBTOOL)[)}].*\s-o\s*\S")
# An install command in a recipe: `$(INSTALL_DATA) libreadline.a $(DESTDIR)$(libdir)/...`,
# `$(INSTALL_PROGRAM) tcpdump ...`, a bare `install -m 644 ...`.
_INSTALL_CMD_RE = re.compile(r"\$[({]INSTALL(?:_PROGRAM|_DATA|_SCRIPT)?[)}]|(?<![\w./-])install\s+-")
_SUBST_REF_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)@")


_COMPILER_OVERRIDE_RE = re.compile(r"^\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]$")


def _other_compiler(mk: gnulibmk.Makefile) -> str | None:
    """The variable a Makefile.am sets `CC` (or `CXX`) to when it is not the configured host
    compiler: grub's `grub-core/Makefile.am` begins with `CC=$(TARGET_CC)`, and everything it
    builds (the kernel, the modules) is firmware code for another toolchain."""
    for var in ("CC", "CXX"):
        words = mk.variables.get(var, [])
        match = _COMPILER_OVERRIDE_RE.match(words[0]) if len(words) == 1 else None
        if match and match.group(1) not in (var, "CC", "CXX"):
            return match.group(1)
    return None


def _parse_tree_makefile(
    directory: str,
    makefile: Path,
    text: str,
    src: Path | None,
    state: ProjectState | None = None,
    notes: list[str] | None = None,
) -> gnulibmk.Makefile:
    """Parse one Makefile of the tree; a hand-written Makefile.in (an autoconf project
    without automake) gets its programs and libraries from its rules.  A Makefile that
    switches to another compiler builds nothing for the host: its programs and libraries
    are left out and reported, for the hand-written BUILD.bazel to cover with rules of its own."""
    mk = gnulibmk.parse(text)
    # teckit's `teckit_compile_SOURCES = @top_builddir@/source/Sample-tools/TECkit_Compile.cpp`:
    # sources and link items named from the tree's root are paths of the tree.
    owners: list[gnulibmk.Library | gnulibmk.Program] = [*mk.libraries.values(), *mk.programs.values()]
    for target in owners:
        for source in target.sources:
            source.path = makefile_relative(source.path, directory)
        for attr in ("libadd", "ldadd"):
            items = getattr(target, attr, None)
            if items:
                items[:] = [makefile_relative(item, directory) for item in items]
    if makefile.name in ("Makefile.in", "Makefile") and src is not None:
        _discover_makefile_in_targets(mk, directory, src, state, notes if notes is not None else [], makefile.name)
    other = _other_compiler(mk)
    if other and (mk.programs or mk.libraries):
        where = f"{directory}/{makefile.name}" if directory else makefile.name
        if notes is not None:
            notes.append(
                f"{where}: CC = $({other}): its {len(mk.programs)} programs and {len(mk.libraries)} "
                "libraries are built with another compiler than the host's (firmware or cross-compiled "
                "code); none is emitted. Rules for them belong in the hand-written BUILD.bazel."
            )
        mk.programs.clear()
        mk.libraries.clear()
    return mk


def _discover_makefile_in_targets(
    mk: gnulibmk.Makefile,
    directory: str,
    src: Path,
    state: ProjectState | None,
    notes: list[str],
    makefile_name: str = "Makefile.in",
) -> None:
    """Programs and libraries of a hand-written Makefile.in (readline, libpcap,
    tcpdump: autoconf without automake), or of a plain Makefile that includes a
    file configure writes (tftp-hpa's `-include ../config/MCONFIG`), from its rules.

    A rule whose target is an archive (`libreadline.a: $(OBJECTS)`,
    `$(LIBNETDISSECT): $(LIBNETDISSECT_OBJ)`) is a library; one whose recipe
    links with `$(CC) ... -o $@` (`$(PROG): $(OBJ) $(LIBNETDISSECT)`) is a
    program, with the archives among its prerequisites as what it links.  The
    objects become the sources of the same name next to the Makefile
    (`tcpdump.o` -> `tcpdump.c`).  `$(VAR)` comes from the Makefile's own
    assignments, `@VAR@` from configure's literal substitutions; what neither
    knows (`@PLATFORM_C_SRC@`, `@LIBOBJS@`) is reported and left out.
    """
    here = src / directory if directory else src
    where = f"{directory}/{makefile_name}" if directory else makefile_name
    # `tftp$(X)` / `tftp.$(O)` with `X = @EXEEXT@`, `O = @OBJEXT@` from the included configured file: the
    # names on a POSIX host, which is where the sources are looked up.
    substs = {"EXEEXT": "", "OBJEXT": "o", **(state.literal_substs if state is not None else {})}
    conditions: dict[str, str | None] = {}  # expanded word -> the source condition it is under

    def computed(name: str) -> list[str] | None:
        """The words of a configure-computed list (`@PLATFORM_C_SRC@`, `@LIBOBJS@`), each
        registered under the condition configure lists it; None when the converter knows nothing."""
        if state is None:
            return None
        if name in state.subst_sources:
            words = []
            for file, cond in state.subst_sources[name]:
                cond_name = None
                if cond is not df.TRUE:
                    cond_name = f"COND_{name}_{re.sub(r'[^A-Za-z0-9]', '_', file).upper()}"
                    state.src_conditions.setdefault(cond_name, []).append(cond)
                conditions[file] = cond_name
                words.append(file)
            return words
        if name in ("LIBOBJS", "LTLIBOBJS") and state.libobj_names:
            words = []
            for cond_name, function in state.libobj_names.items():
                # AC_LIBOBJ names an object: its source is in AC_CONFIG_LIBOBJ_DIR, next to the
                # Makefile, or wherever the Makefile's own rule for the object says
                # (tcpdump's `strlcpy.o: $(srcdir)/missing/strlcpy.c`).
                candidates = ([f"{state.libobj_dir}/{function}.c"] if state.libobj_dir else []) + [f"{function}.c"]
                for prerequisite in mk.rules.get(f"{function}.o", []) + mk.rules.get(f"{function}.$(OBJEXT)", []):
                    source = re.sub(r"^\$\((?:top_)?srcdir\)/", "", prerequisite)
                    if source.endswith(_SOURCE_SUFFIXES):
                        candidates.append(source)
                libobj_source = next((c for c in candidates if (here / c).is_file()), None)
                if libobj_source is None:
                    notes.append(
                        f"{where}: AC_LIBOBJ([{function}]): no {function}.c in the libobj directory, next "
                        "to the Makefile, or named by a rule for the object; left out"
                    )
                    continue
                conditions[libobj_source] = cond_name
                words.append(libobj_source)
            return words
        return None

    def expand(words: list[str]) -> list[str]:
        out = []
        for word in gnulibmk.expand_variables(words, mk.variables):
            if re.search(r"\$[({]", word):
                # `tftp.$(O)`, `tftp$(X)`: a variable inside the word (`O = @OBJEXT@` from the
                # included configured file); a make function (`$(filter ...)`) stays unknown.
                (word,) = embedded([word])
            if "@" in word:
                whole = re.fullmatch(r"@([A-Za-z_][A-Za-z0-9_]*)@(?::\.[a-z]+=\.[a-z]+)?", word)
                listed = computed(whole.group(1)) if whole else None
                if listed is not None:
                    out.extend(listed)
                    continue
                missing = [name for name in _SUBST_REF_RE.findall(word) if name not in substs]
                if missing:
                    notes.append(f"{where}: @{missing[0]}@ is computed by configure; its contribution is left out")
                    continue
                word = _SUBST_REF_RE.sub(lambda m: substs[m.group(1)], word)
                if not word.strip():
                    continue
                out.extend(word.split())
                continue
            if word.startswith(("$(", "${")):
                continue  # an unknown make variable
            out.append(word)
        return out

    def sources_of(objects: list[str], owner: str) -> list[gnulibmk.Source]:
        found = []
        for obj in objects:
            stem = re.sub(r"^(?:\$\(srcdir\)/|\./)", "", obj)
            condition = conditions.get(obj)
            if stem.endswith(_SOURCE_SUFFIXES) and (here / stem).is_file():
                found.append(gnulibmk.Source(stem, condition, None))  # a computed list names sources
                continue
            stem = stem.rsplit(".", 1)[0]
            for suffix in _SOURCE_SUFFIXES + gnulibmk.LEX_SUFFIXES + gnulibmk.YACC_SUFFIXES:
                if (here / f"{stem}{suffix}").is_file():
                    found.append(gnulibmk.Source(f"{stem}{suffix}", condition, None))
                    break
            else:
                template = next(
                    (
                        f"{stem}{sfx}.in"
                        for sfx in gnulibmk.YACC_SUFFIXES + gnulibmk.LEX_SUFFIXES
                        if (here / f"{stem}{sfx}.in").is_file()
                    ),
                    None,
                )
                if template is not None:
                    # libpcap's grammar.y: config.status writes it from grammar.y.in.
                    source = template[: -len(".in")]
                    mk.subst_templates[source] = template
                    found.append(gnulibmk.Source(source, condition, None))
                else:
                    notes.append(f"{where}: {owner}: no source for {obj} next to the Makefile; left out")
        return found

    def embedded(words: list[str]) -> list[str]:
        """`$(VAR)` inside a word (`-DRL_LIBRARY_VERSION='"$(RL_LIBRARY_VERSION)"'`): the Makefile's
        own variables, then configure's literal substitutions."""
        out = []
        for word in words:
            for _ in range(4):
                replaced = re.sub(
                    r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]",
                    lambda m: " ".join(mk.variables[m.group(1)]) if m.group(1) in mk.variables else m.group(0),
                    word,
                )
                replaced = _SUBST_REF_RE.sub(lambda m: substs.get(m.group(1), m.group(0)), replaced)
                if replaced == word:
                    break
                word = replaced
            out.append(word)
        return out

    def installed_names() -> set[str]:
        """What `make install` ships, by basename: the files the `install*` rules' install commands
        copy (readline's `$(INSTALL_DATA) libreadline.a $(DESTDIR)$(libdir)/libreadline.a`, with
        `$(STATIC_LIBS)` and the like expanded).  A Makefile without an install rule ships what
        `all` builds: its prerequisites."""
        names: set[str] = set()
        seen = False
        for target, recipe in mk.recipes.items():
            if not target.startswith("install"):
                continue
            for line in recipe:
                if not _INSTALL_CMD_RE.search(line):
                    continue
                seen = True
                for word in embedded(expand(line.split())):
                    names.add(re.sub(r"\$\(EXEEXT\)|@EXEEXT@", "", word).rsplit("/", 1)[-1])
        if not seen:
            names = {word.rsplit("/", 1)[-1] for word in embedded(expand(mk.rules.get("all", [])))}
        return names

    installed = installed_names()
    # What `make` builds: the prerequisites of `all`, by basename.  A program rule outside them
    # (readline's rlptytest, in OTHEREXE alone) is a candidate as an EXTRA_PROGRAMS entry is:
    # no target unless a `program:` decision says so.  No `all` rule: every rule counts.
    reachable = (
        {re.sub(r"\$\(EXEEXT\)|@EXEEXT@", "", w).rsplit("/", 1)[-1] for w in embedded(expand(mk.rules["all"]))}
        if "all" in mk.rules
        else None
    )
    archive_objects: dict[str, list[str]] = {}  # archive -> its objects, for the convenience rules
    rules = list(mk.rules.items())
    # Archives first: a program rule may link one, and a convenience rule (readline's
    # `readline: $(OBJECTS) ... ./libreadline.a`, built by hand from an example) repeats its objects.
    for target, deps in sorted(rules, key=lambda item: 0 if _LIBRARY_RE.match(item[0]) else 1):
        names = expand([target]) if "$" in target else [target]
        if len(names) != 1:
            continue
        name = re.sub(r"\$\(EXEEXT\)|@EXEEXT@", "", names[0])
        recipe = mk.recipes.get(target, [])
        prerequisites = expand(deps)
        # Objects, and the sources a configure-computed list names (`@PLATFORM_C_SRC@`).
        objects = [d for d in prerequisites if d.endswith(_OBJECT_SUFFIXES + _SOURCE_SUFFIXES)]
        base = name.rsplit("/", 1)[-1]
        if _LIBRARY_RE.match(name) and objects:
            canonical = re.sub(r"[^A-Za-z0-9_@]", "_", base)
            archive_objects[base] = objects
            if canonical not in mk.libraries:
                mk.libraries[canonical] = gnulibmk.Library(
                    canonical, base, sources_of(objects, base), installed=base in installed
                )
        elif any(_LINK_RE.search(line) for line in recipe) and objects and "." not in base:
            # The archives among the prerequisites, and the ones the link line names through a
            # variable the prerequisites filter (`$(filter %.a,$(TFTP_LIBS))`).
            linked = [w for line in recipe if _LINK_RE.search(line) for w in embedded(expand(line.split()))]
            archives = list(
                dict.fromkeys(
                    [d for d in prerequisites if _LIBRARY_RE.match(d)] + [w for w in linked if _LIBRARY_RE.match(w)]
                )
            )
            repeated = [a for a in archives if set(objects) <= set(archive_objects.get(a.rsplit("/", 1)[-1], []))]
            if repeated:
                notes.append(
                    f"{where}: `{name}` links {repeated[0]} from the archive's own objects (a convenience "
                    "target built by hand); not a program"
                )
                continue
            # The configure substitutions the link line names besides the archives
            # (`$(TERMCAP_LIB)` with `TERMCAP_LIB = @TERMCAP_LIB@`): link variables, as a
            # Makefile.am's `$(X_LIB)` in LDADD.  A variable the Makefile never defines
            # (readline's `$(LIBUTIL)`) is empty under make and nothing here either.
            link_vars = []
            for word in (w for line in recipe if _LINK_RE.search(line) for w in line.split()):
                var = make_var_name(word)
                if var is not None and mk.variables.get(var) == [f"@{var}@"] and word not in link_vars:
                    link_vars.append(word)
            canonical = re.sub(r"[^A-Za-z0-9_@]", "_", base)
            if canonical not in mk.programs:
                mk.programs[canonical] = gnulibmk.Program(
                    base,
                    sources_of(objects, base),
                    ldadd=archives + link_vars,
                    installed=base in installed,
                    substituted=reachable is not None and base not in reachable,
                )
    # `tilde.o: tilde.c` with `$(CC) ... -DREADLINE_LIBRARY -c`: the rule's own -D options,
    # applied to the target that compiles the object (one object's flag on the whole target).
    for target, deps in rules:
        if not target.endswith(_OBJECT_SUFFIXES) or "$" in target:
            continue
        flags = [w for line in mk.recipes.get(target, []) for w in line.split() if w.startswith("-D")]
        if not flags:
            continue
        source = sources_of([target], target)
        owners_of_objects: list[gnulibmk.Library | gnulibmk.Program] = [*mk.libraries.values(), *mk.programs.values()]
        for owner in owners_of_objects:
            if source and any(s.path == source[0].path for s in owner.sources):
                variable = f"{owner.name}_CPPFLAGS"
                mk.variables.setdefault(variable, []).extend(f for f in flags if f not in mk.variables[variable])
                shown = owner.archive if isinstance(owner, gnulibmk.Library) else owner.name
                notes.append(f"{where}: {target}'s rule compiles with {' '.join(flags)}; applied to {shown} as a whole")
    # The compile rule's flags (`.c.o: $(CC) -c $(CCFLAGS) $<`): the -D and -I options of the
    # variables it names are the directory's preprocessor flags.
    for suffix_rule in (".c.o", ".c.lo", "%.o"):
        for line in mk.recipes.get(suffix_rule, []):
            if not re.search(r"\$[({](?:CC|CXX)[)}]", line):
                continue
            words = embedded(expand(re.findall(r"\$[({][A-Za-z_][A-Za-z0-9_]*[)}]", line)))
            flags = [w for w in words if w.startswith(("-D", "-I")) and w not in ("-D", "-I")]
            if flags:
                mk.variables.setdefault("AM_CPPFLAGS", []).extend(
                    f for f in flags if f not in mk.variables["AM_CPPFLAGS"]
                )
    # `for i in $(PUBHDR); do $(INSTALL_DATA) $(srcdir)/$$i $(DESTDIR)$(includedir)/$$i; done`: the
    # headers the install rule copies under includedir are the public ones (libpcap's PUBHDR;
    # readline's INSTALLED_HEADERS already parse as a `*_HEADERS` variable).
    known_headers = {h.path for h in mk.installed_headers}
    for lines in mk.recipes.values():
        for line in lines:
            loop = re.search(r"\bfor\s+\w+\s+in\s+\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}].*\$\((?:pkg)?includedir\)", line)
            if loop:
                for header in expand(mk.variables.get(loop.group(1), [])):
                    if header.endswith(".h") and header not in known_headers and (here / header).is_file():
                        mk.installed_headers.append(gnulibmk.Source(header, None, None))
                        known_headers.add(header)
    # `$(INSTALL_DATA) $(srcdir)/$$f $(DESTDIR)$(includedir)/readline`: headers installed into a
    # subdirectory of includedir, which consumers spell in their includes.  libpcap's
    # `$(includedir)/$$i` keeps each header's own path (`pcap/pcap.h`): no prefix.
    for lines in mk.recipes.values():
        for line in lines:
            # The directory is the last word of the install command (`... ; done` may follow on a
            # joined logical line); a header installed by name (`.../foo.h`) is not a directory.
            match = re.search(r"\bINSTALL(?:_DATA)?\b[^;]*\$\((?:pkg)?includedir\)/([A-Za-z0-9_+-]+)(?=\s|;|$)", line)
            if match:
                for library in mk.libraries.values():
                    library.include_prefix = library.include_prefix or match.group(1)


def _project_shape(src: Path, package: Package) -> str:
    """Which kind of autoconf project this is, and what to expect of the output (docs/src/convert.md,
    "Which projects"): automake file lists (1), hand-written declarative Makefile.in (2), or make logic
    the converter does not follow, so configuration targets only (3)."""
    targets = sum(1 for r in package.after if isinstance(r, Rule) and r.kind in ("cc_library", "cc_binary", "cc_test"))
    automake = any(src.rglob("Makefile.am"))
    makefile_ins = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("Makefile.in")
        if not any(part.startswith(".") for part in p.relative_to(src).parts)
    )
    if automake:
        return (
            "Project shape: autoconf + automake (class 1); the programs and libraries come from the Makefile.am files."
        )
    if not makefile_ins:
        makefile_ins = [f"{d}/Makefile" if d else "Makefile" for d in _plain_makefile_dirs(src)]
    if makefile_ins and targets:
        return (
            "Project shape: autoconf + hand-written Makefiles (class 2); the programs and libraries come from "
            f"the rules of {', '.join(makefile_ins[:4])}{', ...' if len(makefile_ins) > 4 else ''}. Check them "
            "against the Makefiles: the reader follows literal lists, not make functions."
        )
    if makefile_ins:
        return (
            "Project shape: autoconf with a build defined in make logic the converter does not follow "
            f"(class 3): {', '.join(makefile_ins[:4])} yielded no programs or libraries. The output is the "
            "configuration (the autoconf target and its headers); write the build targets by hand."
        )
    return (
        "Project shape: autoconf without Makefiles (class 3): the output is the configuration only; "
        "write the build targets by hand."
    )


def _configured_makefile_dirs(state: ProjectState) -> tuple[str, ...]:
    """The directories of the Makefiles AC_CONFIG_FILES writes (`examples/Makefile`), top level excluded."""
    out = []
    for entry in state.config_files:
        output = entry.partition(":")[0].strip()
        if output.rsplit("/", 1)[-1] == "Makefile" and "/" in output:
            directory = output.rsplit("/", 1)[0].strip("/")
            if directory and directory not in out:
                out.append(directory)
    return tuple(out)


def _automake_tree(
    src: Path, skip: AbstractSet[str] = frozenset(), extra: tuple[str, ...] = ()
) -> list[tuple[str, Path, str]]:
    """`(directory, Makefile.am or Makefile.in, text)` for the top level and
    every SUBDIRS directory under it, build-only directories left out.  A
    directory without automake (a hand-written Makefile.in) is read for its
    rules instead; `extra` names the directories whose Makefile AC_CONFIG_FILES
    writes (readline's examples/), which no SUBDIRS reaches."""
    out: list[tuple[str, Path, str]] = []
    queue = [""] + [d for d in extra if d and d not in skip and d.split("/")[0] not in _SKIP_SUBDIRS]
    if not (src / "Makefile.am").is_file() and not (src / "Makefile.in").is_file():
        # Plain Makefiles that include a file configure writes (tftp-hpa): the
        # top one recurses with `$(MAKE) -C`, not SUBDIRS, so each is found by its include.
        queue += [d for d in _plain_makefile_dirs(src) if d not in skip]
    seen: set[str] = set()
    while queue:
        directory = queue.pop(0)
        base = src / directory if directory else src
        makefile = next(
            (base / name for name in ("Makefile.am", "Makefile.in") if (base / name).is_file()), base / "Makefile"
        )
        if directory in seen or not makefile.is_file():
            continue
        if makefile.name == "Makefile" and not _includes_configured_file(makefile):
            continue
        seen.add(directory)
        text = gnulibmk.read_makefile(makefile, src)  # `include` fragments spliced in
        out.append((directory, makefile, text))
        mk = gnulibmk.parse(text)
        # Conditional SUBDIRS too (`if ENABLE_EXTENSIONS`): their targets exist either way.
        for sub in mk.subdirs + mk.variables.get("DIST_SUBDIRS", []):
            sub = sub.strip("/")
            if sub in ("", ".") or sub.startswith("$") or sub.split("/")[0] in _SKIP_SUBDIRS:
                continue
            path = f"{directory}/{sub}" if directory else sub
            if path in skip:
                continue  # an AC_CONFIG_SUBDIRS directory: a package of its own (gawk's extension/)
            queue.append(path)
    return out


_PLAIN_INCLUDE_RE = re.compile(r"^-?s?include\s+(\S+)[ \t]*$", re.MULTILINE)


def _includes_configured_file(makefile: Path) -> bool:
    """A plain Makefile is part of the configured build when it includes a file configure writes:
    an `include` whose target is absent but has a `.in` template (`-include ../config/MCONFIG`)."""
    text = makefile.read_text(encoding="utf-8", errors="replace")
    for target in _PLAIN_INCLUDE_RE.findall(text):
        if "$(" in target:
            continue
        path = makefile.parent / target
        if not path.is_file() and path.with_name(path.name + ".in").is_file():
            return True
    return False


def _plain_makefile_dirs(src: Path) -> list[str]:
    """The directories of the plain Makefiles (no Makefile.am / Makefile.in beside them) that include
    a file configure writes, nearest the root first; build-only directories left out."""
    out = []
    for makefile in sorted(src.rglob("Makefile"), key=lambda p: (len(p.parts), p.as_posix())):
        directory = makefile.parent.relative_to(src).as_posix()
        directory = "" if directory == "." else directory
        parts = directory.split("/") if directory else []
        if any(part.startswith(".") or part in _SKIP_SUBDIRS for part in parts):
            continue
        if (makefile.parent / "Makefile.am").is_file() or (makefile.parent / "Makefile.in").is_file():
            continue
        if _includes_configured_file(makefile):
            out.append(directory)
    return out


def _program_names(src: Path, skip: AbstractSet[str] = frozenset()) -> set[str]:
    """Every program of the tree, as the cc_binary names they take."""
    out: set[str] = set()
    for directory, makefile, text in _automake_tree(src, skip):
        prefix = f"{directory}/" if directory else ""
        out |= {f"{prefix}{p.name}" for p in _parse_tree_makefile(directory, makefile, text, src).programs.values()}
    return out


def _library_target(directory: str, archive: str, taken: set[str]) -> tuple[str, str | None]:
    """The `cc_library` name for an automake library and, when needed, the
    alias that carries its archive name.

    rules_cc names the output `lib<name>.a`, so `libfoo.a` is a rule called
    `foo`. When a program `foo` already takes that name the library becomes
    `lib/foo` (output `lib/libfoo.a`) and an alias `libfoo` keeps the archive
    name addressable. Archives without the prefix (automake modules such as
    `plugin.la`) keep their name.
    """
    # `lib/libnvc.a` (automake subdir-objects): the directory stays in front of
    # the name, the `lib` prefix logic applies to the file name.
    subdir, _, stem = archive.rpartition("/")
    for suffix in (".a", ".la"):
        stem = stem.removesuffix(suffix)
    prefix = f"{directory}/" if directory not in ("", ".") else ""
    if subdir:
        prefix += subdir.strip("/") + "/"
    if not stem.startswith("lib") or stem == "lib":
        return f"{prefix}{stem}", None
    name = f"{prefix}{stem[3:]}"
    if name not in taken:
        return name, None
    return f"{prefix}lib/{stem[3:]}", f"{prefix}{stem}"


def _option_switch(state: ProjectState, name: str) -> str:
    """`--enable-usb` / `--with-pcap` for a flag atom's option name."""
    flag = state.flags.get(name)
    if flag is None:
        return f"--enable-{name.replace('_', '-')}"
    return f"--{'with' if flag.macro == 'AC_ARG_WITH' else 'enable'}-{flag.option}"


# Automake's installation directories at configure's defaults (`--prefix=/usr/local`).
INSTALL_DIRS = {
    "prefix": "/usr/local",
    "exec_prefix": "$(prefix)",
    "bindir": "$(exec_prefix)/bin",
    "sbindir": "$(exec_prefix)/sbin",
    "libexecdir": "$(exec_prefix)/libexec",
    "libdir": "$(exec_prefix)/lib",
    "datarootdir": "$(prefix)/share",
    "datadir": "$(datarootdir)",
    "sysconfdir": "$(prefix)/etc",
    "sharedstatedir": "$(prefix)/com",
    "localstatedir": "$(prefix)/var",
    "runstatedir": "$(localstatedir)/run",
    "includedir": "$(prefix)/include",
    "oldincludedir": "/usr/include",
    "localedir": "$(datarootdir)/locale",
    "infodir": "$(datarootdir)/info",
    "mandir": "$(datarootdir)/man",
    "docdir": "$(datarootdir)/doc/$(PACKAGE)",
    "pkgdatadir": "$(datadir)/$(PACKAGE)",
    "pkglibdir": "$(libdir)/$(PACKAGE)",
    "pkglibexecdir": "$(libexecdir)/$(PACKAGE)",
    "pkgincludedir": "$(includedir)/$(PACKAGE)",
    "htmldir": "$(docdir)",
    "dvidir": "$(docdir)",
    "pdfdir": "$(docdir)",
    "psdir": "$(docdir)",
    "lispdir": "$(datarootdir)/emacs/site-lisp",
    "PATH_SEPARATOR": ":",
}
_MAKE_VAR_RE = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")
_MAKE_VAR_WORD_RE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")


def make_var_name(word: str) -> str | None:
    """The variable name of a word that is one `$(VAR)` reference and nothing else; None otherwise."""
    match = _MAKE_VAR_WORD_RE.fullmatch(word)
    return match.group(1) if match else None


def _expand_make_text(text: str, mk: gnulibmk.Makefile, state: ProjectState, depth: int = 0) -> str:
    """`$(VAR)` references in a define's value: the Makefile.am's own
    variables, configure's unconditional literal substitutions
    (`AC_SUBST([pkgextensiondir], ['${pkglibdir}'])`), then automake's
    directories at their defaults."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "PACKAGE":
            return state.tarname
        if name in mk.variables:
            value = " ".join(mk.variables[name])
        elif name in state.literal_substs:
            value = state.literal_substs[name]
        elif name in INSTALL_DIRS:
            value = INSTALL_DIRS[name]
        else:
            return match.group(0)
        return _expand_make_text(value, mk, state, depth + 1) if depth < 8 else match.group(0)

    return _MAKE_VAR_RE.sub(one, text)


def _literal_substs(main: Target) -> dict[str, str]:
    """The substitutions configure sets to one literal unconditionally."""
    out: dict[str, str] = {}
    for item in main.checks:
        if (
            isinstance(item, Call)
            and item.fn == "checks.AC_SUBST"
            and len(item.args) == 2
            and isinstance(item.args[0], str)
            and isinstance(item.args[1], str)
            and not any(k in item.kwargs for k in ("requires", "condition"))
        ):
            out[item.args[0]] = item.args[1].strip("'")
    return out


def _subst_values(main: Target) -> dict[str, list[str]]:
    """Every literal the main target's AC_SUBSTs give a variable, whatever the condition: the
    plain value, a `condition` check's `if_true` and `if_false`, the values of the select() arms."""
    out: dict[str, list[str]] = {}
    for items in [main.checks, *main.arm_lists]:
        for item in items:
            if not (
                isinstance(item, Call) and item.fn == "checks.AC_SUBST" and item.args and isinstance(item.args[0], str)
            ):
                continue
            values = [item.args[1]] if len(item.args) > 1 else [item.kwargs.get("if_true"), item.kwargs.get("if_false")]
            for value in values:
                if isinstance(value, (str, int)) and str(value) not in out.setdefault(item.args[0], []):
                    out[item.args[0]].append(str(value))
    return out


def _shell_unquote(text: str) -> str:
    """The C token a Makefile's shell quoting spells: `'"x"'` and `"\\"x\\""` are `"x"`."""
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"')
    return text


def _expand_make_vars(items: list[str], mk: gnulibmk.Makefile, depth: int = 0) -> list[str]:
    """`$(LDADD)` and other variables of the same Makefile.am, spelled out."""
    out: list[str] = []
    for item in items:
        name = make_var_name(item)
        if name is not None and name in mk.variables and depth < 4:
            out.extend(_expand_make_vars(mk.variables[name], mk, depth + 1))
        else:
            out.append(item)
    return out
