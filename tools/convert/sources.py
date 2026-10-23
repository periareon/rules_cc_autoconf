"""Sources, flags and generated files of a library or program: conditional sources, the
flag variables as defines / includes / copts, recipes that write headers."""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Callable
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk, lower
from tools.convert.common import (
    _SOURCE_SUFFIXES,
    CONFIG_HEADERS,
    NO_WARNINGS,
    TOP_HEADERS,
    _dict_of_lists,
    _list_literal,
    headers_glob,
    source_label,
)
from tools.convert.ir import Assign, Call, Package, Raw, Rule, Target, strings
from tools.convert.makefiles import (
    _am_block,
    _expand_make_text,
    _expand_make_vars,
    _option_switch,
    _shell_unquote,
    make_var_name,
)
from tools.convert.modulebazel import MODULES
from tools.convert.translate import ProjectState, condition_names, describe, iso_standard, slug

# Sources a tool generates C from: the BCR `bison` and `flex` modules' rules
# (they run the module's own binary with m4 configured), and the options
# variable automake reads for them.
_GENERATORS = {
    gnulibmk.YACC_SUFFIXES: ("bison", "@bison//:bison.bzl", "YFLAGS"),
    gnulibmk.LEX_SUFFIXES: ("flex", "@flex//:flex.bzl", "LFLAGS"),
}
_CXX_GENERATED = (".yy", ".y++", ".yxx", ".ypp", ".ll", ".l++", ".lxx", ".lpp")
# What a target compiles: C / C++ sources and the yacc / lex inputs a generator turns into them.
COMPILED_SUFFIXES = _SOURCE_SUFFIXES + gnulibmk.YACC_SUFFIXES + gnulibmk.LEX_SUFFIXES


def _generated_sources(
    package: Package,
    mk: gnulibmk.Makefile,
    canonical: str,
    sources: list[gnulibmk.Source],
    prefix: str,
    *,
    src: Path | None = None,
    state: ProjectState | None = None,
    notes: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """`bison()` / `flex()` rules (the BCR modules' own) for the yacc and lex
    sources of one target: the generated files to compile and the `includes`
    directories that find the generated header.

    Release tarballs ship the generated `foo.c` next to `foo.y`, so the rule
    is named `bison/foo` and writes `bison/foo.c` and `bison/foo.h` where no
    source file is in the way; `bison` among the target's `includes` makes
    `#include "foo.h"` find the generated header."""
    srcs: list[str] = []
    includes: list[str] = []
    for source in sources:
        tool = next((t for suffixes, t in _GENERATORS.items() if source.path.endswith(suffixes)), None)
        if tool is None:
            continue
        kind, load_from, flags_var = tool
        stem = source.path.rsplit(".", 1)[0]
        name = f"{prefix}{kind}/{stem}"
        cxx = source.path.endswith(_CXX_GENERATED)
        shipped = f"{stem}.{'cc' if cxx else 'c'}"
        if (
            state is not None
            and src is not None
            and MODULES[kind].name == state.tarname
            and (src / prefix / shipped).is_file()
        ):
            # flex's scan.l, bison's parse-gram.y: the tool cannot generate
            # itself; the release ships the output, as the bootstrap does.
            if notes is not None:
                notes.append(
                    f"{prefix}Makefile.am: {source.path}: this module is {kind} itself; "
                    f"the shipped {shipped} is compiled instead of running the tool"
                )
            srcs.append(f"{prefix}{shipped}")
            continue
        out_src = f"{name}.{'cc' if cxx else 'c'}"
        out_hdr = None if kind == "flex" and cxx else f"{name}.{'hh' if cxx else 'h'}"
        if not any(isinstance(s, Rule) and s.name == name for s in package.after):
            options = [
                o
                for o in mk.variables.get(f"AM_{flags_var}", [])
                + mk.variables.get(f"{canonical}_{flags_var}", [])
                + mk.generator_flags.get(source.path, [])
                # The header is always generated, under the rule's name; yacc
                # compatibility only renames outputs.
                if o not in ("-d", "--defines", "--header", "-y", "--yacc")
            ]
            generator_input = source_label(prefix, source.path)
            if source.path in mk.subst_templates:
                # config.status writes the grammar from its template (libpcap's
                # grammar.y.in with @REENTRANT_PARSER@): an autoconf_hdr in subst mode.
                template_target = f"{prefix}{stem}_{source.path.rsplit('.', 1)[1]}".replace("/", "_")
                if not any(isinstance(r, Rule) and r.name == template_target for r in package.after):
                    package.after.append(
                        Rule(
                            "autoconf_hdr",
                            template_target,
                            {
                                "out": generator_input,
                                "mode": "subst",
                                "template": f"{prefix}{mk.subst_templates[source.path]}",
                                "deps": [":autoconf"],
                            },
                            comments=[
                                (
                                    f"{prefix}Makefile.in: {source.path} as config.status writes it from "
                                    f"{mk.subst_templates[source.path]}."
                                )
                            ],
                            load_from="//autoconf:autoconf_hdr.bzl",
                        )
                    )
                generator_input = f":{template_target}"
            args = ["-o", f"$(execpath {out_src})"]
            if out_hdr and kind == "bison":
                args.append(f"--defines=$(execpath {out_hdr})")
            elif out_hdr:
                args.append(f"--header-file=$(execpath {out_hdr})")
            if kind == "flex" and cxx:
                args.append("--c++")
            # Quiet, as the C compiles are: a consumer cannot act on the grammar's warnings.
            args += ["-Wnone" if kind == "bison" else "--nowarn"] + options + [f"$(execpath {generator_input})"]
            package.after.append(
                Rule(
                    kind,
                    name,
                    {"srcs": [generator_input], "outs": [out_src] + ([out_hdr] if out_hdr else []), "args": args},
                    comments=[f"{prefix}Makefile.am: {source.path}, run through {kind} at build time."],
                    load_from=load_from,
                )
            )
        srcs.append(out_src)
        if out_hdr:
            srcs.append(out_hdr)
        # `includes` rather than `-I$(BINDIR)/...` copts: Bazel resolves the directory under the
        # owning repository for both source and generated trees (an external repository's
        # generated files live under bazel-out/.../external/<repo>, where $(BINDIR)/<pkg> is wrong).
        include = f"{prefix}{kind}"
        for directory in (include, f"{include}/{stem.rsplit('/', 1)[0]}" if "/" in stem else include):
            if directory not in includes:
                includes.append(directory)
    return srcs, includes


# `.c` files, and the `.def` / `.inc` tables X-macro headers read (bison's timevar.def).
_TEXTUAL_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^">]+\.(?:c|def|inc))[">]', re.MULTILINE)


def _textual_includes(src: Path, directory: str, sources: list[str]) -> list[str]:
    """`.c` files the sources `#include` (gnulib's regex.c pulls in regcomp.c):
    not compiled on their own, but inputs of the compiles that read them."""
    found: list[str] = []
    queue = [f"{directory}/{s}" if directory else s for s in sources]
    compiled = set(queue)
    seen = set(queue)
    while queue:
        path = queue.pop(0)
        file = src / path
        if not file.is_file():
            continue
        for name in _TEXTUAL_INCLUDE_RE.findall(file.read_text(encoding="utf-8", errors="replace")):
            # Next to the including file, else from the tree's root (bison's
            # src/scan-skel-c.c includes "src/scan-skel.c" through -I$(top_srcdir)).
            included = os.path.normpath(os.path.join(os.path.dirname(path), name)).replace(os.sep, "/")
            if included.startswith("..") or not (src / included).is_file():
                included = os.path.normpath(name).replace(os.sep, "/")
            if included.startswith("..") or not (src / included).is_file():
                continue
            if included in seen:
                # A source compiled on its own that another source also includes
                # (mpfr's pow_uj.c reads pow_ui.c): a textual header as well.
                if included in compiled and included not in found:
                    found.append(included)
                continue
            seen.add(included)
            found.append(included)
            queue.append(included)
    return sorted(found)


def _cross_package_includes(src: Path, root: Path) -> list[str]:
    """Files under `src` that sources in a subdirectory with a configure.ac of its own include through
    `../` (relative paths out of their package): textual headers of the root config headers library."""
    if src != root:
        return []
    out: set[str] = set()
    for sub in sorted(p.parent for p in src.glob("*/configure.ac")):
        for source in sub.rglob("*.c"):
            for name in _TEXTUAL_INCLUDE_RE.findall(
                source.read_text(encoding="utf-8", errors="replace")
            ) + _PARENT_HEADER_RE.findall(source.read_text(encoding="utf-8", errors="replace")):
                if not name.startswith("../"):
                    continue
                target = os.path.normpath(os.path.join(source.parent.relative_to(src).as_posix(), name)).replace(
                    os.sep, "/"
                )
                if (
                    not target.startswith("..")
                    and (src / target).is_file()
                    and not target.startswith(sub.relative_to(src).as_posix() + "/")
                ):
                    out.add(target)
    return sorted(out)


_PARENT_HEADER_RE = re.compile(r'^\s*#\s*include\s+"(\.\./[^"]+\.h)"', re.MULTILINE)


def _root_textual(package: Package, files: list[str], notes: list[str]) -> None:
    """Top-level sources read by other compiles ride along with the config headers."""
    rule = next((s for s in package.after if isinstance(s, Rule) and s.name == TOP_HEADERS), None)
    if rule is None:
        rule = next((s for s in package.after if isinstance(s, Rule) and s.name == CONFIG_HEADERS), None)
    if rule is None:
        notes.append(f"{', '.join(files)}: included by other sources but no {CONFIG_HEADERS} library to carry them")
        return
    rule.attrs["textual_hdrs"] = sorted(set(strings(rule.attrs.get("textual_hdrs"))) | set(files))


_FLAG_VARIABLES = ("DEFS", "AM_CPPFLAGS", "{}_CPPFLAGS", "AM_CFLAGS", "{}_CFLAGS")
# `-f` options that are the build's choice, not the sources' requirement.
_OPTIMIZATION_FLAGS = {
    "-fomit-frame-pointer",
    "-fno-omit-frame-pointer",
    "-fPIC",
    "-fpic",
    "-fPIE",
    "-fpie",
    "-fstack-protector",
    "-fstack-protector-strong",
    "-fstack-protector-all",
    "-fno-common",
}


def _require_standards(main: Target, state: ProjectState, notes: list[str]) -> None:
    """Requirement checks for the standards the script or a Makefile forces
    with `-std=` (`CFLAGS="$CFLAGS -std=gnu11"`, `AM_CFLAGS = -std=gnu99`).

    configure would compile everything in that mode; a Bazel build's standard
    is the root module's (--copt / --cxxopt or a toolchain feature), so the
    main target checks the mode the compiler is in and stops the build with
    the flag to add when it is older.  Standards a macro already checks
    (AC_PROG_CC_C99, AX_CXX_COMPILE_STDCXX) are not checked twice."""
    wanted = {"C": state.c_standard, "CXX": state.cxx_standard}
    for lang, standard in state.makefile_standards.items():
        if not wanted[lang]:
            wanted[lang] = standard
            notes.append(
                f"{standard}: a Makefile.am passes -std={standard}; the main target checks for it instead "
                "of pinning it (a Bazel build's standard is the root module's)"
            )
    for lang in ("C", "CXX"):
        chosen = wanted[lang]
        if not chosen or iso_standard(chosen) in state.required_standards:
            continue
        state.required_standards.add(iso_standard(chosen))
        call = Call("checks.AC_LANG_STANDARD", [chosen], {"error": True})
        call.comments.append(f"The script compiles everything with -std={chosen}.  A Bazel build's standard is the")
        call.comments.append(
            "root module's (--copt / --cxxopt), so it is checked here rather than pinned on the targets."
        )
        main.checks.append(call)


def _record_makefile_standards(options: list[str], state: ProjectState) -> None:
    """`-std=` options of a Makefile's flags, for `_require_standards`: per language, the last one wins."""
    for option in options:
        value = option[len("-std=") :]
        state.makefile_standards["CXX" if "++" in value else "C"] = value


"""A make word: the text, or the text with the automake conditional it is under."""


def _joined_options[Word: (str, tuple[str, str])](words: list[Word]) -> list[Word]:
    """`-I $(top_srcdir)/src/w32/include` (make): the option and its separate argument as one
    word; for (word, condition) pairs, the pair's condition is the option's."""
    out: list[Word] = []
    skip = False
    for index, word in enumerate(words):
        if skip:
            skip = False
            continue
        text = word[0] if isinstance(word, tuple) else word
        following = words[index + 1] if index + 1 < len(words) else None
        if text in ("-I", "-D") and following is not None:
            next_text = following[0] if isinstance(following, tuple) else following
            if not isinstance(word, tuple) or following[1] == word[1]:
                out.append((text + next_text, word[1]) if isinstance(word, tuple) else text + next_text)
                skip = True
                continue
        out.append(word)
    return out


def _preprocessor_flags(
    mk: gnulibmk.Makefile,
    canonical: str,
    directory: str,
    notes: list[str],
    state: ProjectState,
    decisions: dec.Decisions,
) -> tuple[
    list[str], list[str], list[str], list[str], dict[str, list[str]], dict[str, list[str]], dict[str, list[str]]
]:
    """`-D` and `-I` options of DEFS / AM_CPPFLAGS / <target>_CPPFLAGS (and the
    CFLAGS variables) as local_defines, copts and includes. `$(VAR)` in a define's
    value is expanded from the Makefile.am and automake's default
    directories (`-DDEFPATH=$(DEFPATH)`); what configure alone knows
    (`-DSHLIBEXT=$(SHLIBEXT)`) is reported and left out."""
    defines: list[str] = []
    copts: list[str] = []
    copt_arms: dict[str, list[str]] = {}  # select() arm label -> machine options under that conditional
    define_arms: dict[str, list[str]] = {}  # select() arm label -> defines under that conditional
    includes: list[str] = []  # `-I` options, package-relative: a `-I` copt would be execroot-relative

    def define_of(item: str, variable: str) -> str | None:
        """A `-D` option as a local_defines entry, its configure-computed value decided
        (`cppflag:`); None when it is left out."""
        name, eq, value = item[2:].partition("=")
        value = _shell_unquote(_expand_make_text(value, mk, state))
        if "$" in value or "@" in value or "$" in name:
            # `-DSHLIBEXT=$(SHLIBEXT)`: a value only configure computes.
            decision = decisions.resolve(
                dec.Request(
                    f"cppflag:{directory or '.'}:{name}",
                    "cppflag",
                    f"{directory or '.'}/Makefile.am",
                    f"{item} ({variable}) has a value only configure computes",
                    {"expanded": f"-D{name}={value}"},
                )
            )
            if decision is None or decision.ignore:
                notes.append(
                    f"{directory or '.'}/Makefile.am: {variable} {item} left out (cppflag decision missing or IGNORE)"
                )
                return None
            value = str(decision.value)  # a cppflag decision is a string
        if len(value) >= 2 and value[0] == value[-1] == '"':
            # Bazel tokenizes local_defines like a shell: a C string
            # literal keeps its quotes only when they are escaped.
            value = '"\\"' + value[1:-1] + '\\""'
        define = f"{name}={value}" if eq else name
        return define if name and name != "HAVE_CONFIG_H" else None

    def include_dir(item: str, variable: str) -> str | None:
        """A `-I` option as a package-relative includes entry; None when it is left out."""
        option = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", r"$(\1)", item)  # `-I${top_srcdir}` (ppp)
        path = _expand_make_text(option[2:].strip("\"'"), mk, state)
        base = directory
        for variable_, replacement in (
            ("$(top_srcdir)", ""),
            ("$(srcdir)", directory),
            ("$(top_builddir)", "$(BINDIR)"),
            ("$(builddir)", f"$(BINDIR)/{directory}"),
        ):
            if path.startswith(variable_):
                path, base = path[len(variable_) :].lstrip("/"), replacement
                break
        if "$(" in path or "@" in path:
            notes.append(f"{directory or '.'}/Makefile.am: {variable} {item} needs make's expansion; left out")
            return None
        joined = os.path.normpath(os.path.join(base, path) if path else base or ".").replace(os.sep, "/")
        if joined.startswith(".."):
            notes.append(f"{directory or '.'}/Makefile.am: {variable} {item} leaves the tree; left out")
            return None
        if joined.startswith("$(BINDIR)"):
            joined = joined[len("$(BINDIR)") :].lstrip("/") or "."
        return joined

    include_arms: dict[str, list[str]] = {}  # select() arm label -> `-I` directories under that conditional
    standards: list[str] = []  # `-std=` options: a requirement check, not a pin (see _require_standards)
    unknown_vars: set[tuple[str, str]] = set()  # (flag variable, the `$(VAR)` word only configure sets)
    for variable in (v.format(canonical) for v in _FLAG_VARIABLES):
        # `libgrubgcry_a_CPPFLAGS = $(AM_CPPFLAGS) $(CPPFLAGS_GCRY)`: the flag classes a Makefile
        # defines (grub's conf/Makefile.common) are spelled out; a variable only configure sets
        # (`$(HOST_CPPFLAGS)`) stays a reference, reported below.
        items = _expand_make_vars(list(mk.variables.get(variable, [])), mk)
        # `$(FUSE_CFLAGS)`: a substitution configure sets to one literal (a pkg-config package's
        # found action, `-DFUSE_USE_VERSION=32`) is spelled out too.
        spelled_out: list[str] = []
        for item in items:
            name = make_var_name(item)
            if name is not None and name in state.literal_substs:
                spelled_out += state.literal_substs[name].split()
            else:
                spelled_out.append(item)
        items = spelled_out
        # `AM_CPPFLAGS += -DENABLE_MERGE` inside `if ENABLE_MERGE`: the words
        # count when the conditional is decided ALWAYS (the same decision that
        # compiles the sources under it); otherwise they are reported.
        for word, cond in _joined_options(mk.conditional_variables.get(variable, [])):
            decision = am_decision(cond, decisions)
            # The arm label: a decided select(), or the one arm a flag-driven (or host-OS)
            # conditional lowers to on its own (`if PPP_WITH_PLUGINS` over `--enable-plugins`),
            # as `_conditional_sources` resolves it for sources.  Unlike automake's
            # `_resolve_conditional`, a complement arm (`!<label>`) is accepted here: `arm_dict`
            # renders it as the default arm.
            label = decision.arm if decision is not None else None
            if decision is None:
                label = conditional_arm(cond, state)
            if decision is not None and decision.always:
                items.append(word)
            elif label is not None and word.startswith("-I"):
                # make's `AM_CPPFLAGS += -I $(top_srcdir)/src/w32/include` under `if WINDOWSENV`:
                # the directory goes in that arm of includes.
                included = include_dir(word, variable)
                if included is not None and included not in include_arms.setdefault(label, []):
                    include_arms[label].append(included)
            elif label is not None and word.startswith("-D"):
                # ppp's `pppd_CPPFLAGS += -DPPPD_PLUGIN_DIR=...` under `if PPP_WITH_PLUGINS`: the
                # define goes in that arm of local_defines.
                define = define_of(word, variable)
                if define is not None and define not in define_arms.setdefault(label, []):
                    define_arms[label].append(define)
            elif label is not None and re.fullmatch(r"-m[a-z0-9][a-z0-9+=.-]*", word):
                # `src_libcksum_avx2_a_CFLAGS = -mavx2` under a conditional decided as a
                # select() arm: the machine options go in that arm of copts.
                copt_arms.setdefault(label, []).append(word)
            elif decision is None or not decision.never:
                notes.append(
                    f"{directory or '.'}/Makefile.am: {variable} {word} under `if {cond}` left out "
                    f"(decide am_conditional:{cond} ALWAYS to keep it)"
                )
        for item in _joined_options(items):
            option = item
            if option.startswith("-D"):
                define = define_of(item, variable)
                if define is not None and define not in defines:
                    defines.append(define)
            elif option.startswith("-std="):
                if option not in standards:
                    standards.append(option)
            elif re.fullmatch(r"-m[a-z0-9][a-z0-9+=.-]*", option):
                # Machine options (`-mavx2`, `-march=armv8-a+crypto`): the SIMD variants of
                # coreutils' cksum and wc are compiled with them, under a conditional
                # that selects the CPU.
                if option not in copts:
                    copts.append(option)
            elif re.fullmatch(r"-f[a-z][a-z0-9-]*(?:=[A-Za-z0-9_.-]+)?", option) and option not in _OPTIMIZATION_FLAGS:
                # Code generation options the sources rely on (grub's `-fno-builtin`,
                # `-fno-strict-aliasing`); warnings (`-W...`) and optimization levels are the
                # build's own business and are left out.
                if option not in copts:
                    copts.append(option)
            elif make_var_name(option) is not None:
                unknown_vars.add((variable, option))
            elif option.startswith("-I"):
                joined = include_dir(item, variable)
                if joined is not None and joined not in includes:
                    includes.append(joined)
    for option in sorted({option for _variable, option in unknown_vars}):
        note = (
            f"{directory or '.'}/Makefile.am: {option} in the flag variables: a value only configure "
            "computes; its options are left out (a `variable:` decision gives the substitution, not these flags)"
        )
        if note not in notes:  # once per Makefile, not once per target
            notes.append(note)
    return defines, copts, standards, includes, copt_arms, define_arms, include_arms


def am_condition(name: str, state: ProjectState) -> df.Cond | None:
    """The condition of automake conditional `name` as AM_CONDITIONAL's test lowered; `!NAME`
    (an `else` branch, `if !NAME`) is its negation, `A && B` (nested blocks) their conjunction."""
    conds = []
    for part in name.split(" && "):
        cond = state.am_conditionals.get(part.lstrip("!"))
        if cond is None:
            return None
        conds.append(df.neg(cond) if part.startswith("!") else cond)
    return df.conj(*conds) if len(conds) > 1 else conds[0]


def am_decision(name: str, decisions: dec.Decisions) -> dec.Decision | None:
    """The `am_conditional:` decision for `name`; for `!NAME`, NAME's decision inverted (ALWAYS and
    NEVER swap, a select() arm becomes its complement, `!<label>`); for `A && B`, the whole's
    decision, else the parts' combined (a NEVER anywhere, ALWAYS throughout, or one part's arm)."""
    parts = name.split(" && ")
    if len(parts) == 1:
        return invert_am_decision(decisions.optional(f"am_conditional:{name.lstrip('!')}", "am_conditional"), name)
    whole = decisions.optional(f"am_conditional:{name}", "am_conditional")
    if whole is not None:
        return whole
    results = [am_decision(part, decisions) for part in parts]
    key = f"am_conditional:{name}"
    if any(r is not None and r.never for r in results):
        return dec.Decision("am_conditional", key, word="NEVER")
    settled = [r for r in results if r is not None]
    if len(settled) == len(results):
        arms = [r.arm for r in settled if r.arm is not None]
        if not arms:
            return dec.Decision("am_conditional", key, word="ALWAYS")
        if len(arms) == 1:
            return dec.Decision("am_conditional", key, arm=arms[0])
    return None


def simplified_conditions(sources: list[gnulibmk.Source]) -> list[gnulibmk.Source]:
    """ppp's tls.c under `if PPP_WITH_EAPTLS` and, in its `else`, under `if PPP_WITH_PEAP`
    (`!PPP_WITH_EAPTLS && PPP_WITH_PEAP`): a source that is also compiled under the negated
    conditional itself is compiled under the union, so the negation drops out of the compound."""
    by_path: dict[str, set[str]] = {}
    for source in sources:
        if source.condition:
            by_path.setdefault(source.path, set()).add(source.condition)
    out: list[gnulibmk.Source] = []
    for source in sources:
        if not source.condition or "!" not in source.condition:
            out.append(source)
            continue
        parts = source.condition.split(" && ")
        kept = [p for p in parts if not (p.startswith("!") and p[1:] in by_path.get(source.path, set()))]
        out.append(source if kept == parts else dataclasses.replace(source, condition=" && ".join(kept) or None))
    return out


def invert_am_decision(decision: dec.Decision | None, name: str) -> dec.Decision | None:
    """NAME's `am_conditional:` decision read for `!NAME`: ALWAYS and NEVER swap, a select() arm
    becomes its complement (`!<label>`); any other name's decision is returned as it is."""
    if decision is None or not name.startswith("!"):
        return decision
    if decision.always:
        return dataclasses.replace(decision, word="NEVER")
    if decision.never:
        return dataclasses.replace(decision, word="ALWAYS")
    if decision.arm is not None:
        return dataclasses.replace(decision, arm=f"!{decision.arm}")
    return decision


def single_arm(split: dict[str, df.Cond] | None) -> str | None:
    """The one select() arm a split condition holds in: the label whose arm is TRUE while every
    other arm is FALSE.  The default arm is spelled by the labels it excludes (`!L1,L2`), which
    `arm_dict` renders as `{L1: [], L2: [], default: words}`."""
    if not split or not all(c is df.TRUE or c is df.FALSE for c in split.values()):
        return None
    true = [label for label, c in split.items() if c is df.TRUE]
    if len(true) != 1:
        return None
    if true[0] != lower.DEFAULT_ARM:
        return true[0]
    excluded = sorted(label for label in split if label != lower.DEFAULT_ARM)
    return "!" + ",".join(excluded) if excluded else None


def conditional_arm(name: str, state: ProjectState) -> str | None:
    """The one arm automake conditional `name` lowers to on its own: over a configure option
    (`flag_arms`), or over the host OS (make's `if WINDOWSENV` is the Windows arm, its `else`
    every other platform)."""
    cond = am_condition(name, state)
    if cond is None:
        return None
    return single_arm(am_arms(cond, state))


def am_arms(cond: df.Cond, state: ProjectState) -> dict[str, df.Cond] | None:
    """The select() arms a lowered automake conditional splits into: over the host OS when it is
    a platform condition, else over a configure option (`flag_arms`)."""
    if lower.platform_atoms(cond) and not df.flag_atoms(cond):
        return lower.os_arms(cond)
    return lower.flag_arms(cond, state)


def arm_dict(label: str, words: list[str]) -> dict[str, list[str]]:
    """One `select()`'s arms for words that apply under `label`: the arm and an empty default,
    or, for `!L1,L2`, those arms empty and the words in the default."""
    if label.startswith("!"):
        return {**{l: [] for l in label[1:].split(",")}, lower.DEFAULT_ARM: words}
    return {label: words, lower.DEFAULT_ARM: []}


def _arm_selects(arms: dict[str, list[str]], transform: Callable[[list[str]], list[str]]) -> list[str]:
    """One rendered `select()` per arm label, in label order, with the label's words put through
    `transform` (the caller's ordering, de-duplication or escaping); joined with ` + ` after a list."""
    return [
        f"select({_dict_of_lists(arm_dict(label, transform(words)), '    ')})" for label, words in sorted(arms.items())
    ]


def includes_value(includes: list[str], arms: dict[str, list[str]]) -> list[str] | Raw:
    """The includes of a target: the list, plus one `select()` per conditional whose `-I`
    directories apply in one arm (make's `-I src/w32/include` under `if WINDOWSENV`)."""
    if not arms:
        return includes
    return Raw(_list_literal(includes, "    ") + " + " + " + ".join(_arm_selects(arms, lambda dirs: sorted(set(dirs)))))


def _defines_value(defines: list[str], arms: dict[str, list[str]]) -> list[str] | Raw:
    """The local_defines of a target: the list, plus one `select()` per conditional whose
    defines apply in one arm."""
    if not arms:
        return defines

    def escaped(words: list[str]) -> list[str]:
        # `_list_literal` writes the words between quotes verbatim: a define's own quotes and
        # backslashes (`X="\"/run/pppd\""`) are escaped here as a list attribute's would be.
        return [w.replace("\\", "\\\\").replace('"', '\\"') for w in words]

    return Raw(
        _list_literal(escaped(defines), "    ")
        + " + "
        + " + ".join(_arm_selects(arms, lambda words: escaped(list(words))))
    )


def _copts_value(copts: list[str], arms: dict[str, list[str]]) -> Raw:
    """The copts of a target: `NO_WARNINGS`, then the target's own options, then one `select()`
    per conditional whose flags apply in one arm."""
    parts = [NO_WARNINGS]
    if copts:
        parts.append(_list_literal(copts, "    "))
    parts += _arm_selects(arms, lambda words: sorted(set(words)))
    return Raw(" + ".join(parts))


_WRITE_RE = re.compile(r"^(?:\$\(AM_V_\w+\))?\s*(printf|echo)\s+'([^']*)'\s*(>>?)\s*(?:\"?\$@\"?[-a-z]*|\S+)\s*$")
_RECIPE_NOISE_RE = re.compile(r"^(?:\$\(AM_V_\w+\))?\s*(?:rm|chmod|mv|touch)\b")


_CONFIGMAKE_ECHO_RE = re.compile(r"""echo\s+'([^']*)'""")
_MAKE_VAR_REF_RE = re.compile(r"\$\([A-Za-z_][A-Za-z0-9_]*\)")
_CONFIGMAKE_PRINTF_RE = re.compile(r"""printf\s+'#define\s+([A-Z_][A-Z0-9_]*)\s+%s\\n'\s+\$\(([a-z_]+)_c_make\)""")


def _configmake_content(recipe: list[str], mk: gnulibmk.Makefile, state: ProjectState) -> list[str] | None:
    """gnulib's `configmake.h`: one `#define BINDIR "..."` per installation
    directory, each `$(x_c_make)` being the directory as a C string.  The
    directories are automake's defaults (`--prefix=/usr/local`), as the
    converter assumes everywhere."""
    lines: list[str] = []
    for line in recipe:
        for echo in _CONFIGMAKE_ECHO_RE.finditer(line):
            text = echo.group(1)
            if "$(" in text:  # bison's gnulib: `echo '#define PKGDATADIR "$(pkgdatadir)"'`
                text = _expand_make_text(text, mk, state)
                # A variable no Makefile defines (`$(manext)`) is empty, as make expands it.
                text = _MAKE_VAR_REF_RE.sub("", text)
                if "$" in text or "@" in text:
                    return None
            lines.append(text)
        for define in _CONFIGMAKE_PRINTF_RE.finditer(line):
            name, variable = define.groups()
            # `$(bindir_c_make)` is `$(bindir)` as a C string literal (gnulib.mk
            # builds it with make functions the converter does not run).
            variable = variable.removesuffix("_c_make")
            value = _expand_make_text(f"$({variable})", mk, state)
            if "$" in value or "@" in value:
                return None
            lines.append(f'#define {name} "{value}"')
    return lines if any(l.startswith("#define") for l in lines) else None


def _written_content(recipe: list[str]) -> list[str] | None:
    """The lines a `printf '...' > $@` / `echo '...' >> $@` recipe writes,
    with configure's `$(PACKAGE_VERSION)` as the `@PACKAGE_VERSION@` an
    `autoconf_hdr(mode = "subst")` fills in; None for any other recipe."""
    text = ""
    for line in recipe:
        if _RECIPE_NOISE_RE.match(line):
            continue
        match = _WRITE_RE.match(line)
        if match is None:
            return None
        command, body, op = match.group(1), match.group(2), match.group(3)
        if op == ">":
            text = ""
        if command == "printf":
            body = body.replace("%%", "%").replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")
        else:
            body += "\n"
        text += body
    if not text:
        return None
    # `"$(PACKAGE_VERSION)"`: the substitution is the quoted C literal.
    text = re.sub(r'"?\$\((PACKAGE(?:_[A-Z]+)?)\)"?', r"@\1@", text)
    text = re.sub(r"\$\(([A-Z][A-Z0-9_]*)\)", r"@\1@", text)
    if "$(" in text or "${" in text:
        return None
    return text.split("\n")


def _subst_values(package: Package, name: str) -> list[str]:
    """The literal values the package's autoconf targets substitute `name` with (one per select arm)."""
    out: list[str] = []
    for target in package.targets:
        for items in [target.checks, *target.arm_lists]:
            for call in items:
                if (
                    isinstance(call, Call)
                    and call.fn == "checks.AC_SUBST"
                    and len(call.args) >= 2
                    and call.args[0] == name
                    and isinstance(call.args[1], str)
                ):
                    out.append(call.args[1].strip("'"))
    return out


_SED_SUBST_RE = re.compile(r"-e\s+'s\|([^|]*)\|([^|]*)\|g?'")
_SED_DELETE_RE = re.compile(r"-e\s+'/([^/]*)/d'")


def _sed_rewrites(recipe: list[str]) -> dict[str, str]:
    """The `s|A|B|g` and `/P/d` expressions of a header recipe's sed as substitutions: A -> B, and
    a line with P commented out (`// P`), which is how the overlays delete it."""
    out: dict[str, str] = {}
    for line in recipe:
        for old, new in _SED_SUBST_RE.findall(line):
            if "@" not in old and "$(" not in new:  # @X@ placeholders are the rule's own
                out[old] = new
        for pattern in _SED_DELETE_RE.findall(line):
            if re.fullmatch(r"[A-Za-z0-9_ ]+", pattern):
                out[pattern] = f"// {pattern}"
    return out


def _generated_files(
    package: Package,
    mk: gnulibmk.Makefile,
    prefix: str,
    sources: list[gnulibmk.Source],
    src: Path,
    notes: list[str],
    decisions: dec.Decisions,
    owner: str,
    state: ProjectState | None = None,
) -> tuple[list[str], list[str]]:
    """Sources that are not in the tree: a `printf` / `echo` recipe becomes a
    `write_file` (through an `autoconf_hdr(mode = "subst")` when the text
    carries configure's values); any other recipe, or none, is a `generated`
    decision (the rule that writes the file, or IGNORE).  Returns the paths
    of the generated files and the paths left out."""
    out: list[str] = []
    excluded: list[str] = []
    for source in sources:
        path = source_label(prefix, source.path)
        if (src / path).is_file() or path.endswith(gnulibmk.YACC_SUFFIXES + gnulibmk.LEX_SUFFIXES):
            continue  # yacc / lex inputs go through `_generated_sources`
        if state is not None and path in state.generated_headers:
            continue  # a gnulib wrapper header: the gnulib library generates it
        name = slug(path)
        if any(isinstance(s, Rule) and s.name in (name, f"{name}_in") for s in package.after) or any(
            isinstance(s, Raw) and f'"{path}"' in s.text for s in package.after
        ):
            out.append(path)
            continue
        content = None
        if source.path in mk.recipes:
            content = (
                _configmake_content(mk.recipes[source.path], mk, state)
                if source.path.endswith("configmake.h") and state is not None
                else _written_content(mk.recipes[source.path])
            )
        if content is None:
            decision = decisions.resolve(
                dec.Request(
                    f"generated:{path}",
                    "generated",
                    f"{prefix}Makefile.am ({owner})",
                    f"{source.path} is not in the tree and "
                    + (
                        "its recipe is not one the converter translates"
                        if source.path in mk.recipes
                        else "no rule of the Makefile.am writes it"
                    ),
                    {"recipe": mk.recipes.get(source.path)},
                )
            )
            if decision is None or decision.ignore:
                notes.append(f"{prefix}Makefile.am: {source.path} left out (generated decision missing or IGNORE)")
                excluded.append(path)
                continue
            if decision.label is not None:
                # A rule of the hand-written BUILD.bazel writes it; the consumer names the file.
                label = decision.label
                if state is not None and label.startswith(":") and label[1:] not in state.hand_targets:
                    notes.append(
                        f"{prefix}Makefile.am: {source.path} is expected from {label}, which the hand-written "
                        "BUILD file does not define"
                    )
                out.append(path)
                continue
            if decision.rule is not None:
                package.after.append(Raw(decision.rule))
                package.loads += [line for line in decision.loads or [] if line not in package.loads]
            out.append(path)
            continue
        if state is not None:
            # What the written file includes from the tree (grep's colorize.c
            # is one `#include <colorize-posix.c>`): inputs of its compile.
            directory = path.rsplit("/", 1)[0] if "/" in path else ""
            for line in content:
                match = re.match(r'\s*#\s*include\s*[<"]([^>"]+)[>"]', line)
                if not match:
                    continue
                # `#include <@COLORIZE_SOURCE@>`: every value the substitution takes.
                names = [match.group(1)]
                subst = re.fullmatch(r"@([A-Za-z_][A-Za-z0-9_]*)@", match.group(1))
                if subst:
                    names = _subst_values(package, subst.group(1))
                for included in names:
                    if (src / directory / included).is_file():
                        state.generated_includes.add(f"{directory}/{included}" if directory else included)
        if any("@" in line for line in content):
            package.after.append(
                Rule(
                    "write_file",
                    f"{name}_in",
                    {"out": f"{path}.in", "content": content, "newline": "unix"},
                    comments=[
                        (
                            f"{prefix}Makefile.am: {source.path}, written by its recipe; configure's values "
                            "come from the autoconf results (@PACKAGE_VERSION@ is the quoted C literal)."
                        )
                    ],
                    load_from="@bazel_skylib//rules:write_file.bzl",
                )
            )
            package.after.append(
                Rule(
                    "autoconf_hdr",
                    name,
                    {"out": path, "mode": "subst", "template": f"{path}.in", "deps": [":autoconf"]},
                    load_from="//autoconf:autoconf_hdr.bzl",
                )
            )
        else:
            package.after.append(
                Rule(
                    "write_file",
                    name,
                    {"out": path, "content": content, "newline": "unix"},
                    comments=[f"{prefix}Makefile.am: {source.path}, written by its recipe."],
                    load_from="@bazel_skylib//rules:write_file.bzl",
                )
            )
        out.append(path)
    return out, excluded


def _unproduced(expr: str | None, state: ProjectState) -> list[str]:
    """The result names a lowered condition reads that no check produces (`produced_results`);
    nothing while the produced results are not known yet."""
    if not state.produced_results:
        return []
    return [n for n in condition_names(expr or "") if n not in state.produced_results]


def _conditional_sources(
    sources: list[gnulibmk.Source],
    state: ProjectState,
    decisions: dec.Decisions,
    makefile_name: str,
    owner: str,
    am_text: str,
    prefix: str,
    notes: list[str],
) -> tuple[list[gnulibmk.Source], dict[str, list[str]], dict[str, str], dict[str, dict[str, str]]]:
    """Sources under an automake conditional: the ones a configure option
    switches on go in a `select()` arm, the rest are `am_conditional`
    decisions (ALWAYS, NEVER or a config_setting label).  Sources under a
    condition over configure's results come back as `probe` (path -> expression)."""
    probe: dict[str, str] = {}
    probe_arms: dict[str, dict[str, str]] = {}  # platform arm label -> path -> probe expression
    included: list[gnulibmk.Source] = []
    arms: dict[str, list[str]] = {}
    for source in simplified_conditions(sources):
        if not source.condition or not source.path.endswith(COMPILED_SUFFIXES):
            continue
        path = source_label(prefix, source.path)
        override = (
            decisions.optional(f"src_condition:{source.condition}", "src_condition")
            if source.condition in state.src_conditions
            else None
        )
        if override is not None:
            # The decisions file overrides a condition the converter derived (a gnulib.mk older
            # than the ports' gnulib tests a variable the port no longer sets): ALWAYS, NEVER or
            # an expression, as for an asked `src_condition:`.
            if override.always:
                included.append(source)
            elif override.never:
                notes.append(f"{owner}: {source.path} left out (src_condition:{source.condition} is NEVER)")
            elif override.condition is not None:
                probe[path] = override.condition
            continue
        if source.condition in state.src_conditions:
            # A condition over configure's results (AC_LIBOBJ's `if`, a computed source list):
            # a probe expression (autoconf_srcs), platform arms, or TRUE.
            cond = df.disj(*state.src_conditions[source.condition])
            lowered = lower.expression(cond)
            if not lowered.ok and df.flag_atoms(cond):
                # libpcap's `--with-pcap=TYPE` overrides the capture type the probes pick:
                # the sources as configure chooses them when the option is not given; the
                # override itself is not a select() dimension of the sources.
                reduced = cond
                for option in df.option_names(cond):
                    reduced = df.substitute_option(reduced, option, None)
                if not df.flag_atoms(reduced):
                    # The reduced condition is a probe expression or platform arms (pcap-usb-linux.c
                    # under `host_os in linux*` once --enable-usb's default is applied).
                    lowered = lower.expression(reduced)
                    cond = reduced
                    options = df.option_names(df.disj(*state.src_conditions[source.condition]))
                    notes.append(
                        f"{owner}: {source.path}: compiled as when {' / '.join(_option_switch(state, o) for o in options)} "
                        "is not given (the option's override of configure's choice is not modeled)"
                    )
            missing = _unproduced(lowered.expr, state) if lowered.ok and lowered.expr else []
            if lowered.ok and lowered.expr is None:
                included.append(source)
            elif lowered.ok and lowered.expr and missing:
                # pcap-dag.c when `ac_cv_header_dagapi_h`: the check was left out by decision.
                notes.append(
                    f"{owner}: {source.path} left out (its condition needs {', '.join(missing)}, "
                    "which no check produces)"
                )
            elif lowered.ok and lowered.expr:
                probe[path] = lowered.expr
            elif lower.platform_atoms(cond):
                # One arm per platform: a constant residual puts the source in the arm outright; a
                # probe residual (libpcap's pcap-netfilter-linux.c under `host_os in linux*` and
                # `ac_cv_netfilter_can_compile`) makes it a probe-conditioned source of that arm.
                placed = True
                for label, residual in lower.os_arms(cond).items():
                    if residual is df.FALSE:
                        continue
                    if residual is df.TRUE:
                        if label != lower.DEFAULT_ARM:
                            arms.setdefault(label, []).append(path)
                        else:
                            included.append(source)
                        continue
                    lowered_arm = lower.expression(residual)
                    arm_missing = _unproduced(lowered_arm.expr, state)
                    if lowered_arm.ok and lowered_arm.expr and arm_missing:
                        notes.append(
                            f"{owner}: {source.path} left out on {label} (its condition needs "
                            f"{', '.join(arm_missing)}, which no check produces)"
                        )
                    elif lowered_arm.ok and lowered_arm.expr:
                        probe_arms.setdefault(label, {})[path] = lowered_arm.expr
                    else:
                        placed = False
                if not placed:
                    notes.append(
                        f"{owner}: {source.path} left out (its condition, {describe(cond)}, cannot be expressed)"
                    )
            else:
                notes.append(
                    f"{owner}: {source.path} left out (its condition, {lowered.unknown or describe(cond)}, "
                    "cannot be expressed)"
                )
            continue
        am_cond = am_condition(source.condition, state)
        arm_label = conditional_arm(source.condition, state)
        if arm_label is not None:
            arms.setdefault(arm_label, []).append(path)
            continue
        decision = am_decision(source.condition, decisions)
        if (
            decision is None
            and am_cond is not None
            and not lower.platform_atoms(am_cond)
            and not df.flag_atoms(am_cond)
        ):
            # A conditional over configure's results (ppp's `else` of `if WITH_LIBATM`, with
            # `with_atm` a decided variable): a probe-conditioned source, as a gl_CONDITIONAL's,
            # when the results it names exist (grep's USE_PCRE reads a shell variable: a decision).
            lowered = lower.expression(am_cond)
            missing = _unproduced(lowered.expr, state) if lowered.ok and lowered.expr else []
            if lowered.ok and lowered.expr is None:
                included.append(source)
                continue
            if lowered.ok and lowered.expr and not missing:
                probe[path] = lowered.expr
                continue
        if decision is None:
            single = " && " not in source.condition
            key = source.condition.lstrip("!") if single else source.condition
            decision = decisions.resolve(
                dec.Request(
                    f"am_conditional:{key}",
                    "am_conditional",
                    f"{makefile_name} ({owner})",
                    f"{source.path} is compiled only `if {source.condition}`"
                    + (f" ({describe(am_cond)})" if am_cond is not None else ""),
                    {**_am_block(am_text, makefile_name, key), "sources": [source.path]},
                )
            )
            if single:
                decision = invert_am_decision(decision, source.condition)
        if decision is None or decision.never:
            notes.append(f"{owner}: {source.path} left out (`if {source.condition}` undecided or never)")
        elif decision.always:
            included.append(source)
        elif decision.arm is not None:
            arms.setdefault(decision.arm, []).append(path)
    return included, arms, probe, probe_arms


def _probe_conditioned_srcs(
    package: Package, target: str, probe: dict[str, str], probe_arms: dict[str, dict[str, str]] | None = None
) -> tuple[str | None, dict[str, str]]:
    """`autoconf_srcs` targets for the sources compiled when a probe expression holds: one for
    the sources of every platform, and one per platform arm (`probe_arms`) for the sources a
    platform case and a probe both gate.  Returns the first's label and the arms' (arm label ->
    target label); the owner lists them in `srcs` (the arm ones in that arm of its select) and
    all of them in `deps`, through which the sources reach the compiler.  Off its platform an
    arm target has nothing to say: its conditions are not evaluated there."""

    def rule(name: str, sources: dict[str, str], comment: str) -> str:
        package.after.append(
            Rule(
                "autoconf_srcs",
                name,
                {"srcs": dict(sorted(sources.items())), "wrap": "include", "deps": [":autoconf"]},
                comments=[comment],
                load_from="//autoconf:autoconf_srcs.bzl",
            )
        )
        return f":{name}"

    label = (
        rule(
            f"{target}_conditional_srcs", probe, f"{target}: sources configure compiles when a check's result says so."
        )
        if probe
        else None
    )
    arm_labels = {
        arm: rule(
            f"{target}_conditional_srcs_{slug(arm.split(':')[-1])}",
            sources,
            f"{target}: sources configure compiles on {arm} when a check's result says so.",
        )
        for arm, sources in sorted((probe_arms or {}).items())
    }
    return label, arm_labels


def _record_generated_headers(written: list[str], state: ProjectState) -> None:
    """Remember the `.h` files a target's recipes or `generated:` decisions write, by directory."""
    for path in written:
        if path.endswith(".h"):
            directory = os.path.dirname(path)
            if path not in state.generated_dir_headers.setdefault(directory, []):
                state.generated_dir_headers[directory].append(path)


def _directory_generated_headers(srcs: list[str], state: ProjectState) -> set[str]:
    """The generated headers of the directories `srcs` are in (automake's -I. reaches them)."""
    dirs = {os.path.dirname(p) for p in srcs}
    return {h for d in dirs for h in state.generated_dir_headers.get(d, [])}


def _srcs_value(srcs: list[str], arms: dict[str, list[str]], package: Package | None = None) -> list[str] | Raw:
    """A srcs list, plus one `select()` per configure option for conditional sources.  A source
    two options both add (ppp's tls.c under `if PPP_WITH_EAPTLS` and `if PPP_WITH_PEAP`) would be
    listed twice with both on, which Bazel rejects: it moves to one select() over a
    `config_setting_group` matching any of them, declared in `package`."""
    if not arms:
        return srcs
    arms = {label: [p for p in paths if p not in srcs] for label, paths in arms.items()}
    arms = {label: paths for label, paths in arms.items() if paths}
    if package is not None:
        by_labels: dict[tuple[str, ...], list[str]] = {}
        for path in sorted({p for paths in arms.values() for p in paths}):
            labels = tuple(
                sorted(label for label, paths in arms.items() if path in paths and not label.startswith("!"))
            )
            if len(labels) > 1:
                by_labels.setdefault(labels, []).append(path)
        for labels, paths in by_labels.items():
            name = "_or_".join(slug(label.split(":")[-1]).removesuffix("_enabled") for label in labels) + "_any"
            if not any(isinstance(st, Rule) and st.name == name for st in package.after):
                package.after.append(
                    Rule(
                        "selects.config_setting_group",
                        name,
                        {"match_any": list(labels)},
                        comments=["Sources two options both compile: either setting adds them once."],
                        load_from="@bazel_skylib//lib:selects.bzl",
                        load_symbol="selects",
                    )
                )
            for label in labels:
                arms[label] = [p for p in arms[label] if p not in paths]
            arms = {label: ps for label, ps in arms.items() if ps}
            arms[f":{name}"] = paths
    # One select() per option: two options can be on at once, which Bazel
    # rejects as an ambiguous match within one select().
    selects = " + ".join(_arm_selects(arms, sorted))
    return Raw(f"{_list_literal(srcs, '    ')} + {selects}")


def _with_headers(package: Package, srcs_value: list[str] | Raw, dirs: list[str]) -> Raw:
    """The headers next to a program's sources in subdirectories of a
    non-recursive Makefile.am (make's `src/`, pkgconf's `cli/`): no headers
    library serves them, so they are private sources of the program.  The
    glob is one top-level constant shared by the programs of those
    directories (coreutils has a hundred), named after them."""
    name = "_".join(slug(d).upper() for d in dirs) + "_HDRS"
    if not any(isinstance(st, Assign) and st.name == name for st in [*package.before, *package.after]):
        package.after.append(
            Assign(
                name,
                headers_glob([f"{d}/**/*.h" for d in dirs], indent=""),
                comments=[
                    (
                        f"The headers under {', '.join(dirs)}/: private sources of each program there, which "
                        "no headers library serves."
                    )
                ],
            )
        )
    text = _list_literal(srcs_value, "    ") if isinstance(srcs_value, list) else srcs_value.text
    return Raw(f"{text} + {name}")


def _built_sources(mk: gnulibmk.Makefile, included: list[gnulibmk.Source]) -> list[gnulibmk.Source]:
    """Unconditional BUILT_SOURCES (headers the sources include) not already among a target's sources."""
    listed = {s.path for s in included}
    return [
        gnulibmk.Source(name, None, None)
        for name, cond in mk.built_sources
        if cond is None and not name.startswith("$") and name not in listed
    ]
