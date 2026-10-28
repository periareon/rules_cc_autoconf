"""The gnulib library of a project: `gnulib.mk` and `gnulib-comp.m4` as one cc_library with
its wrapper headers, conditional sources and module indicators."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk, lower, sedtext
from tools.convert.common import (
    _SOURCE_SUFFIXES,
    GNULIB_GENERATED_DIR,
    KNOWN_EXTRA_PLATFORMS,
    NO_WARNINGS,
    _config_deps,
    _dict_literal,
    _dict_of_lists,
    _gnulib_modules,
    _list_literal,
    _platform_exceptions,
    _platform_labels,
    header_patterns,
)
from tools.convert.emit import label_sort_key
from tools.convert.expand import Expansion
from tools.convert.index import PortIndex
from tools.convert.ir import Assign, AttrValue, Call, Package, Raw, Rule, Statement, Target
from tools.convert.makefiles import _am_block, _library_target, _program_names
from tools.convert.results import _port_provided
from tools.convert.sources import (
    _built_sources,
    _generated_files,
    _sed_rewrites,
    _textual_includes,
    am_condition,
    simplified_conditions,
)
from tools.convert.translate import GnulibLibrary, ProjectState, basename, condition_names, describe, slug

# The snippet files gnulib's `make` splices into generated headers, keyed by
# the marker comment each replaces.
GL_INLINES = {
    "/* The definition of _GL_ARG_NONNULL is copied here.  */": "arg-nonnull.h",
    "/* The definition of _GL_WARN_ON_USE is copied here.  */": "warn-on-use.h",
    "/* The definition of _Noreturn is copied here.  */": "_Noreturn.h",
    "/* The definitions of _GL_FUNCDECL_RPL etc. are copied here.  */": "c++defs.h",
}

GNULIB_MK_CANDIDATES = ["lib/gnulib.mk", "gl/gnulib.mk", "gnulib/gnulib.mk", "lib/Makefile.am", "gl/Makefile.am"]
# gl_CONDITIONAL tests over variables gl_INIT computes outside the ports'
# macros, spelled as the hand overlays spell them.
KNOWN_SRC_CONDITIONS = {
    "GL_COND_OBJ_STDIO_CONSOLESAFE": "USES_MSVCRT==1",
}
KNOWN_AM_CONDITIONS = [
    # gl_LIBUNISTRING_VERSION_CMP: compile the bundled libunistring source
    # unless a system libunistring of a sufficient version was found.
    ("gl_LIBUNISTRING_VERSION_CMP", "HAVE_LIBUNISTRING!=yes"),
]
# `_condition_table` values for a condition decided ALWAYS (its sources are plain) and NEVER (they
# are left out, with a note); neither reaches the generated SRC_CONDITIONS table.
ALWAYS_CONDITION = "1"
NEVER_CONDITION = "0"
_INCLUDE_GNULIB_MK_RE = re.compile(r"^include\s+\S*(?:gnulib\.mk|Makefile\.gnulib)\s*$", re.MULTILINE)
# gnulib-comp.m4 says where gnulib-tool put the library and what it named the fragment.
_GL_SOURCE_BASE_RE = re.compile(r"^\s*gl_source_base='([^']*)'", re.MULTILINE)
_GL_MAKEFILE_NAME_RE = re.compile(r"--makefile-name=(\S+)")


def _gnulib_mk_paths(src: Path, expansion: Expansion) -> list[Path]:
    """The gnulib fragment makefiles of the tree: where each gnulib-comp.m4 says its library lives
    (libpaper's `m4/gnulib-comp.m4` with `gl_source_base='libgnu'` and `src/m4/gnulib-comp.m4` with
    `src/libgnu`, both `--makefile-name=Makefile.gnulib`), else `lib/gnulib.mk` and the other usual
    places.  One library per directory; the first is the primary."""
    found: list[Path] = []
    for relative in expansion.m4_files:
        path = expansion.work_dir / relative
        if basename(relative) != "gnulib-comp.m4" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        base = _GL_SOURCE_BASE_RE.search(text)
        if not base:
            continue
        named = _GL_MAKEFILE_NAME_RE.search(text)
        names = ([named.group(1)] if named else []) + ["gnulib.mk", "Makefile.gnulib", "Makefile.am"]
        hit = next((src / f"{base.group(1)}/{n}" for n in names if (src / f"{base.group(1)}/{n}").is_file()), None)
        if hit is not None and hit.parent not in {f.parent for f in found}:
            found.append(hit)
    if not found:
        hit = next((src / c for c in GNULIB_MK_CANDIDATES if (src / c).is_file()), None)
        if hit is not None:
            found.append(hit)
    return found


# `-e 's|@''GUARD_PREFIX''@|GL|g'`: a wrapper-header substitution gnulib.mk
# spells as a literal rather than a configure variable.
_LITERAL_SED_RE = re.compile(r"s\|@''([A-Z][A-Z0-9_]*)''@\|([A-Za-z0-9_./-]*)\|g")


_QUOTED_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.MULTILINE)
_GNULIB_VARIABLE_RE = re.compile(r"^(?:REPLACE|HAVE|GNULIB|GL_GENERATE|NEED|WANT)_[A-Z0-9_]+$")
# `@GNULIB_X@`, and stdbit.in.h's `@GL_STDC_X@` (indicators that do not go through
# gl_MODULE_INDICATOR_INIT_VARIABLE, since the header does not #include_next).
_TEMPLATE_INDICATOR_RE = re.compile(r"@((?:GNULIB|GL_STDC)_[A-Z0-9_]+)@")
_TEMPLATE_VARIABLE_RE = re.compile(r"@((?:HAVE|REPLACE)_[A-Z0-9_]+)@")
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"@([A-Z][A-Z0-9_]*)@")


def _template_indicators(templates: list[Path]) -> set[str]:
    """The @GNULIB_X@ module indicators the wrapper-header templates read."""
    return _template_names(templates, _TEMPLATE_INDICATOR_RE)


def _template_variables(templates: list[Path]) -> set[str]:
    """The @HAVE_X@ / @REPLACE_X@ variables the wrapper-header templates read."""
    return _template_names(templates, _TEMPLATE_VARIABLE_RE)


def _template_placeholders(templates: list[Path]) -> set[str]:
    """Every @NAME@ the wrapper-header templates read."""
    return _template_names(templates, _TEMPLATE_PLACEHOLDER_RE)


def _template_names(templates: list[Path], pattern: re.Pattern[str]) -> set[str]:
    out: set[str] = set()
    for template in templates:
        if template.is_file():
            out |= set(pattern.findall(template.read_text(encoding="utf-8", errors="replace")))
    return out


def _literal_sed_substs(mk_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in _LITERAL_SED_RE.findall(mk_text):
        if not value.startswith("$"):
            out.setdefault(name, value)
    return out


def _header_variables(main: Target, state: ProjectState, index: PortIndex | None, extra_labels: list[str]) -> list[str]:
    """`GL_GENERATE_<HDR>_H` for the gl_CONDITIONAL_HEADER calls of gl_INIT's
    own code (the module's inline code sets it from the header check), unless
    a port among the dependencies provides it already (sigsegv)."""
    notes: list[str] = []
    ported = _port_provided([main], index, extra_labels).variables
    for var, cond in sorted(state.header_conditions.items()):
        if var in ported:
            continue
        lowered = _lower_gnulib(cond, state)
        if not lowered.ok or lowered.expr is None or var in condition_names(lowered.expr):
            # bison's textstyle.h: generated when libtextstyle is not found, which it
            # is not (no registry module); the stub header is the portable answer.
            main.checks.append(
                Call(
                    "checks.M4_VARIABLE",
                    [var, "true"],
                    comments=[
                        (
                            f"gl_CONDITIONAL_HEADER: {var}; its condition ({lowered.unknown or describe(cond)}) cannot be "
                            "expressed, so the header is generated (the optional library is absent)."
                        )
                    ],
                )
            )
            notes.append(
                f"{var}: gl_CONDITIONAL_HEADER's condition cannot be expressed "
                f"({lowered.unknown or describe(cond)}); set to true (the header is generated)"
            )
            continue
        main.checks.append(
            Call(
                "checks.M4_VARIABLE",
                [var],
                {
                    "condition": lowered.expr,
                    "if_true": "true",
                    "if_false": "false",
                },
                comments=[f"gl_CONDITIONAL_HEADER: {var} from gl_INIT's inline module code."],
            )
        )
    return notes


# `$ERRNO_H`, `$STDINT_H`: gnulib's header variables, non-empty exactly when
# gl_CONDITIONAL_HEADER sets GL_GENERATE_<HDR>_H (what the ports provide).
_HEADER_VAR_RE = re.compile(r"^(?!HAVE_|REPLACE_|GNULIB_|GL_|NEXT_)[A-Z][A-Z0-9_]*_H$")


def _gnulib_names(state: ProjectState) -> Callable[[df.Cond], df.Cond]:
    """Spell a gnulib.mk condition the way the ports' results are named:
    a variable that is both a subst and a define of a port by its subst
    result (`ac_cv_subst_HAVE_X`: the rules reject the ambiguous bare name),
    and a header variable (`test -n "$ERRNO_H"`) by `GL_GENERATE_ERRNO_H`."""

    def atom(a: df.Truthy | df.Eq | df.Glob) -> df.Cond:
        if _HEADER_VAR_RE.match(a.name):
            generated = df.Eq(f"GL_GENERATE_{a.name}", "true")
            if isinstance(a, df.Truthy):
                return generated
            if isinstance(a, df.Eq):
                return df.neg(generated) if a.value == "" else generated
            return a
        probe = probe_of(a.name, state)
        if probe is not None and isinstance(a, df.Eq) and a.value in ("0", "1"):
            # `test $HAVE_WCWIDTH = 0`: the probe's failure.  The bare name would find the
            # header port's default (`HAVE_WCWIDTH=1` in wchar_h's) where the failed probe
            # defined nothing, and `ac_cv_subst_HAVE_X` names that default outright.
            return df.Truthy(probe) if a.value == "1" else df.neg(df.Truthy(probe))
        if a.name in state.port_ambiguous:
            name = f"ac_cv_subst_{a.name}"
            if isinstance(a, df.Truthy):
                return df.Truthy(name)
            if isinstance(a, df.Eq):
                return df.Eq(name, a.value)
            return df.Glob(name, a.pattern)
        return a

    return lambda cond: df.map_atoms(cond, atom)


def _lower_gnulib(cond: df.Cond, state: ProjectState) -> lower.Lowered:
    """A gnulib.mk condition lowered to the rules' expression language, its atoms respelled as
    the ports name their results (`_gnulib_names`)."""
    return lower.expression(_gnulib_names(state)(cond))


def probe_of(name: str, state: ProjectState) -> str | None:
    """The cache variable of the port probe that alone produces HAVE_* `name`, for a name only a
    define and a header default carry (wcwidth's); None where a port substitutes the name as a
    result (the probe's `subst =`, an `AC_SUBST`, a platform arm's constant) or holds it as an
    M4 variable: that result exists on every platform, where the probe may not run."""
    if name in state.port_result_substs or name in state.port_plain_substs or name in state.port_variables:
        return None
    return state.port_probes.get(name)


def _echo_wrapper_headers(
    mk: gnulibmk.Makefile, prefix: str, state: ProjectState, notes: list[str]
) -> list[gnulibmk.GeneratedHeader]:
    """gnulib.mk rules that write a header with echo lines (libgmp-mpz's `lib/gmp.h`: `#include
    "mini-gmp.h"` under `if GL_GENERATE_GMP_H` + `if GL_GENERATE_MINI_GMP_H`, `#include <gmp/gmp.h>`
    under `if GL_GENERATE_GMP_GMP_H`): one template per header, written next to the BUILD file,
    the variants chosen by `#if` on the substituted true/false of their own conditionals, the
    header itself generated under the conditional the variants share, like the `.in.h` wrappers."""
    by_out: dict[str, list[gnulibmk.EchoHeader]] = {}
    for header in mk.echo_headers:
        by_out.setdefault(header.out, []).append(header)
    out: list[gnulibmk.GeneratedHeader] = []
    for path, variants in sorted(by_out.items()):
        rel = path[len(prefix) :] if prefix and path.startswith(prefix) else path
        conditions = [h.condition for h in variants if h.condition is not None]
        if len(conditions) != len(variants):
            notes.append(f"{path}: written by an unconditional echo recipe; not generated")
            continue
        conjuncts = [c.split(" && ") for c in conditions]
        common = [c for c in conjuncts[0] if all(c in cs for cs in conjuncts[1:])]
        if len(common) != 1 or not common[0].startswith("GL_GENERATE_"):
            notes.append(
                f"{path}: echo-written header whose rules share no single GL_GENERATE_* conditional "
                f"({'; '.join(conditions)}); not generated"
            )
            continue
        lines = [
            f"/* {rel}, as {prefix}gnulib.mk writes it: generated under {common[0]}, the variant chosen",
            "   by the conditionals each rule is under (substituted as the words true / false). */",
            "#define _gl_echo_true 1",
            "#define _gl_echo_false 0",
        ]
        for header, condition in zip(variants, conditions):
            residual = [c for c in condition.split(" && ") if c != common[0]]
            if residual:
                lines.append("#if " + " && ".join(f"_gl_echo_@{c}@" for c in residual))
            lines += header.lines
            if residual:
                lines.append("#endif")
        lines += ["#undef _gl_echo_true", "#undef _gl_echo_false", ""]
        stem = gnulibmk.header_stem(rel)
        template = f"_echo/{stem}.in.h"
        state.extra_templates[f"{prefix}{template}"] = "\n".join(lines)
        out.append(gnulibmk.GeneratedHeader(rel, template, common[0], [], variants[0].module))
        notes.append(
            f"{path}: written by echo recipes in {prefix}gnulib.mk; rendered as the template {prefix}{template}"
        )
    return out


_SHARED_NAMES_RE = re.compile(r"\b(GL_INLINES|GL_SUBSTITUTIONS|GNULIB_COND_HDR_[A-Z]+|SRC_CONDITIONS)\b")


def _prefix_shared_names(items: list[Statement], prefix: str) -> None:
    """A second gnulib library's top-level names (`SRC_CONDITIONS`, `GNULIB_COND_HDR_STEMS`, ...) carry
    the library's prefix, so the two do not collide in the BUILD file."""

    def rewrite(value: AttrValue) -> AttrValue:
        """An attribute value with the shared names prefixed in its Raw texts, lists and dicts through."""
        if isinstance(value, Raw):
            return Raw(_SHARED_NAMES_RE.sub(lambda m: prefix + m.group(1), value.text))
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        return value

    for item in items:
        if isinstance(item, Assign):
            item.name = _SHARED_NAMES_RE.sub(lambda m: prefix + m.group(1), item.name)
            item.value = rewrite(item.value)
        elif isinstance(item, Raw):
            item.text = _SHARED_NAMES_RE.sub(lambda m: prefix + m.group(1), item.text)
        elif isinstance(item, Rule):
            item.attrs = {k: rewrite(v) for k, v in item.attrs.items()}


def _gnulib_library(
    package: Package,
    mk_path: Path,
    src: Path,
    state: ProjectState,
    config_targets: list[str],
    notes: list[str],
    decisions: dec.Decisions,
) -> GnulibLibrary | None:
    """The gnulib library of `lib/gnulib.mk` (together with the Makefile.am that includes it): the
    generated and conditional wrapper headers (`autoconf_hdr`, `cc_gnulib_conditional_hdrs`), the
    private headers library, `autoconf_srcs` for the sources under gnulib's conditionals (host-OS
    ones as a platform `select()`), and the `cc_library` itself, named per `_library_target`.
    Records the library on `state` for the programs that link it; a second library's shared names
    are prefixed with its own (`GNUPAPER_SRC_CONDITIONS`).
    """
    start = len(package.after)
    library = _emit_gnulib_library(package, mk_path, src, state, config_targets, notes, decisions)
    if library is None:
        return None
    if state.gnulib_libraries:
        upper = re.sub(r"[^A-Za-z0-9]", "_", library.label.rsplit("/", 1)[-1].lstrip(":")).upper()
        _prefix_shared_names(package.after[start:], f"{upper}_")
    state.gnulib_libraries.append(library)
    return library


def _emit_gnulib_library(
    package: Package,
    mk_path: Path,
    src: Path,
    state: ProjectState,
    config_targets: list[str],
    notes: list[str],
    decisions: dec.Decisions,
) -> GnulibLibrary | None:
    lib_dir = mk_path.parent.relative_to(src).as_posix()
    mk_text = mk_path.read_text(encoding="utf-8", errors="replace")
    # gnulib.mk is a fragment: the library it fills (`noinst_LIBRARIES = libfoo.a`)
    # is declared by the Makefile.am that includes it.
    parse_text = mk_text
    # ... or by the lib/local.mk a non-recursive top Makefile.am includes (bison's
    # adds lib/path-join.c to the library gnulib.mk fills).
    sibling = next(
        (mk_path.parent / name for name in ("Makefile.am", "local.mk") if (mk_path.parent / name).is_file()),
        mk_path.parent / "Makefile.am",
    )
    if mk_path.name != "Makefile.am" and sibling.is_file():
        am_text = sibling.read_text(encoding="utf-8", errors="replace")
        if _INCLUDE_GNULIB_MK_RE.search(am_text):
            parse_text = _INCLUDE_GNULIB_MK_RE.sub(lambda _: mk_text, am_text, count=1)
        else:
            parse_text = am_text + "\n" + mk_text
    mk = gnulibmk.parse(parse_text)
    notes.extend(f"{mk_path.name}: {n}" for n in mk.notes)
    prefix = f"{lib_dir}/" if lib_dir not in ("", ".") else ""
    if prefix and gnulibmk.strip_directory(mk, lib_dir):
        notes.append(
            f"{mk_path.name}: paths carry `{lib_dir}/` (a non-recursive Makefile.am); the directory is "
            "stripped and added back once"
        )
    libraries = [l for l in mk.libraries.values() if l.sources or l.libadd]
    if not libraries:
        notes.append(f"{mk_path}: no library with sources")
        return None
    library = libraries[0]
    if len(libraries) > 1:
        notes.append(
            f"{mk_path}: several libraries ({', '.join(l.archive for l in libraries)}); "
            f"only {library.archive} is emitted"
        )
    lib_name = library.archive.rsplit("/", 1)[-1]  # a non-recursive Makefile.am names it `lib/libsed.a`
    lib_name = re.sub(r"\.l?a$", "", lib_name)  # `libgnu.a`, libtool's `libgnu.la`
    lib_name = lib_name.removeprefix("lib")
    upper = re.sub(r"[^A-Za-z0-9]", "_", lib_name).upper()

    private = _wrapper_header_rules(package, mk, mk_path, src, prefix, lib_name, upper, state, notes)

    # -- sources ------------------------------------------------------------------
    src_conditions, platform_conditions, platform_exceptions = _condition_table(
        library, src, state, notes, decisions, mk_path, mk_text
    )
    conditional_srcs, platform_srcs, excluded_srcs, extra_unknown, plain_srcs = _classify_sources(
        library,
        src,
        prefix,
        state,
        notes,
        decisions,
        mk_path,
        mk_text,
        src_conditions,
        platform_conditions,
        platform_exceptions,
    )
    # The EXTRA sources no rule places (`extra_unknown`) are decided before SRC_CONDITIONS and
    # the conditional-sources rule are rendered below: a `src_condition:` decided as an
    # expression joins both (coreutils' asprintf.c under REPLACE_VASPRINTF); appended after
    # the literals were written, it was silently dropped while the decision counted as used.
    if extra_unknown:
        # Sources other compiles include textually are not compiled on their own.
        included = set(
            _textual_includes(src, prefix.rstrip("/"), [p[len(prefix) :] for p in plain_srcs + list(conditional_srcs)])
        )
        for path, source, cond_name in extra_unknown:
            if path in included:
                continue
            if source.path in KNOWN_EXTRA_PLATFORMS:
                for label in KNOWN_EXTRA_PLATFORMS[source.path]:
                    if path not in platform_srcs.setdefault(label, []):
                        platform_srcs[label].append(path)
                notes.append(
                    f"{source.path}: EXTRA source of module {source.module}, compiled "
                    f"{'on ' + ', '.join(KNOWN_EXTRA_PLATFORMS[source.path]) if KNOWN_EXTRA_PLATFORMS[source.path] else 'nowhere'} "
                    "(the ported macro's host_os case)"
                )
                continue
            decision = decisions.resolve(
                dec.Request(
                    f"src_condition:{cond_name}",
                    "src_condition",
                    mk_path.name,
                    f"{source.path} is an EXTRA source compiled by AC_LIBOBJ inside the ported macro of module "
                    f"{source.module}; neither the port nor gnulib's replacement rule says when",
                    {"module": source.module},
                )
            )
            if decision is None or decision.never:
                notes.append(f"{source.path}: EXTRA source of module {source.module}; left out (undecided or never)")
            elif decision.always:
                if path not in plain_srcs:
                    plain_srcs.append(path)
            elif decision.condition is not None:
                src_conditions[cond_name] = decision.condition
                state.src_condition_exprs[cond_name] = decision.condition
                conditional_srcs[path] = cond_name
    if conditional_srcs:
        package.after.append(
            Assign(
                "SRC_CONDITIONS",
                dict(sorted((name, cond) for name, cond in src_conditions.items() if cond != NEVER_CONDITION)),
                comments=[
                    "Conditions attached to each conditionally-compiled gnulib source: gl_INIT's",
                    "gl_CONDITIONAL tests and the `if`s around AC_LIBOBJ, over the rules' results.",
                ],
            )
        )
        package.after.append(
            Rule(
                "autoconf_srcs",
                f"{prefix}{lib_name}_gnulib_conditional_srcs",
                {
                    "srcs": Raw(
                        "{\n        src: SRC_CONDITIONS[name]\n        for src, name in "
                        + _dict_literal(dict(sorted(conditional_srcs.items())), "        ")
                        + ".items()\n    }"
                    ),
                    # `include`: each generated file includes its source by path, so
                    # the source's own `#include "sibling.h"` (lib/unictype/*.c and
                    # bitmap.h) resolves where the source lives; the library lists the
                    # target in `deps` too, which is how the sources reach the compiler.
                    "wrap": "include",
                    # The <header>_h defaults (REPLACE_X=0, HAVE_X=1) come with the main
                    # target's GNULIB_M4_DEFAULT_DEPS.
                    "deps": [":autoconf"],
                },
                load_from="//autoconf:autoconf_srcs.bzl",
            )
        )
    configmake = [s for s in _built_sources(mk, []) if s.path.endswith("configmake.h")]
    # `nodist_libgreputils_a_SOURCES = colorize.c` with a recipe: a source the
    # tree does not ship, written by its rule (or a `generated:` decision).
    missing = [gnulibmk.Source(p[len(prefix) :], None, None) for p in plain_srcs if not (src / p).is_file()]
    written, excluded = (
        _generated_files(package, mk, prefix, configmake + missing, src, notes, decisions, library.archive, state)
        if configmake or missing
        else ([], [])
    )
    plain_srcs = [p for p in plain_srcs if p not in excluded]
    written_sources = [w for w in written if w.endswith(_SOURCE_SUFFIXES)]
    written = [w for w in written if w not in written_sources]
    package.after.append(
        Assign(
            f"{upper}_SRCS",
            sorted(set(plain_srcs) | set(written_sources)),
            comments=[f"{mk_path.name}: {library.archive}'s unconditional sources."],
        )
    )
    srcs_expr = f"{upper}_SRCS"
    if conditional_srcs:
        srcs_expr += f' + [":{prefix}{lib_name}_gnulib_conditional_srcs"]'
    # gnulib.mk lists a source under every module that compiles it (localename-
    # unsafe.c under GL_COND_OBJ_LOCALENAME_UNSAFE and _LIMITED): once it is
    # unconditional, the conditional and platform copies would duplicate it.
    for label in list(platform_srcs):
        platform_srcs[label] = [q for q in platform_srcs[label] if q not in plain_srcs]
    excluded_srcs = [(q, labels) for q, labels in excluded_srcs if q not in plain_srcs]
    for q in [q for q in conditional_srcs if q in plain_srcs]:
        del conditional_srcs[q]
    if excluded_srcs:
        # A source compiled everywhere but on a few platforms (localename's
        # limited variants: not on OpenBSD, AIX, Android): it is in every arm
        # of the select except those platforms'.
        for label in {label for _path, labels in excluded_srcs for label in labels}:
            platform_srcs.setdefault(label, [])
        for path, labels in excluded_srcs:
            for label in [*platform_srcs, lower.DEFAULT_ARM]:
                if label not in labels and path not in platform_srcs.setdefault(label, []):
                    platform_srcs[label].append(path)
    if platform_srcs:
        arms = {label: sorted(paths) for label, paths in sorted(platform_srcs.items()) if label != lower.DEFAULT_ARM}
        arms[lower.DEFAULT_ARM] = sorted(platform_srcs.get(lower.DEFAULT_ARM, []))
        package.after.append(
            Assign(
                f"{upper}_PLATFORM_SRCS",
                Raw(f"select({_dict_of_lists(arms, '')})"),
                comments=[
                    (
                        f"{mk_path.name}: sources compiled only on some platforms (`case $host_os` conditionals "
                        "such as OS_IS_NATIVE_WINDOWS and GL_COND_OBJ_WINDOWS_*)."
                    )
                ],
            )
        )
        srcs_expr += f" + {upper}_PLATFORM_SRCS"
    lib_attrs: dict[str, AttrValue] = {
        "srcs": Raw(srcs_expr),
        "copts": Raw(NO_WARNINGS),
        "implementation_deps": [f":{private}"],
        "local_defines": ["HAVE_CONFIG_H"],
    }
    # configmake.h (`#define BINDIR "..."` per installation directory) is a
    # BUILT_SOURCES of gnulib.mk the library's own sources include.
    # regex.c includes regex_internal.c, strtoimax.c includes strtol.c: sources
    # read by other compiles, not compiled on their own.
    all_sources = [
        p[len(prefix) :]
        for p in plain_srcs + list(conditional_srcs) + [q for paths in platform_srcs.values() for q in paths]
    ]
    textual = sorted(
        set(_textual_includes(src, prefix.rstrip("/"), all_sources))
        | set(written)
        | {p for p in state.generated_includes if p.startswith(prefix)}
    )
    if textual:
        lib_attrs["textual_hdrs"] = textual
    if conditional_srcs:
        lib_attrs["deps"] = [f":{prefix}{lib_name}_gnulib_conditional_srcs"]
    name, alias = _library_target(lib_dir, library.archive, _program_names(src, set(state.config_subdirs)))
    package.after.append(
        Rule(
            "cc_library",
            name,
            lib_attrs,
            comments=[f"{library.archive} ({mk_path.name})."],
            load_from="@rules_cc//cc:cc_library.bzl",
        )
    )
    if alias:
        package.after.append(
            Rule(
                "alias",
                alias,
                {"actual": f":{name}"},
                comments=[f"{library.archive} under its archive name."],
            )
        )
    return GnulibLibrary(lib_dir, f":{name}", library.archive, f":{private}")


def _wrapper_header_rules(
    package: Package,
    mk: gnulibmk.Makefile,
    mk_path: Path,
    src: Path,
    prefix: str,
    lib_name: str,
    upper: str,
    state: ProjectState,
    notes: list[str],
) -> str:
    """The wrapper headers of the gnulib library: `GL_INLINES`, the `.gl.h` sed rewrites, the
    `autoconf_hdr` comprehensions over the unconditionally generated headers (`<UPPER>_GENERATED_HDRS`)
    and the conditional ones (`GNULIB_COND_HDR_STEMS`, picked by `cc_gnulib_conditional_hdrs`), and
    the private headers library that carries them all.  Returns that library's name."""
    inlines = {marker: f"{prefix}{file}" for marker, file in GL_INLINES.items() if (src / prefix / file).is_file()}
    if inlines:
        package.after.append(
            Assign(
                "GL_INLINES",
                inlines,
                comments=["Snippet files gnulib's `make` splices into each generated header."],
            )
        )
    inlines_expr = "GL_INLINES" if inlines else "{}"

    generated = {h.out: h for h in mk.headers if h.condition is None}
    conditional = [h for h in mk.headers if h.condition is not None]
    conditional += _echo_wrapper_headers(mk, prefix, state, notes)
    state.templates += [src / prefix / h.template for h in mk.headers]
    state.generated_headers.update(f"{prefix}{h.out}" for h in mk.headers + conditional)
    # The `.gl.h` headers are glibc files rewritten by the recipe's sed script
    # (`s|__glibc_likely|_GL_LIKELY|g`, `/libc_hidden_proto/d`): the same
    # rewrites as substitutions, a deleted line commented out, applied to every
    # generated header as the hand overlays do (the strings occur nowhere else).
    rewrites: dict[str, str] = {}
    for header in mk.headers:
        if header.out.endswith(".gl.h"):
            rewrites.update(_sed_rewrites(mk.recipes.get(header.out, [])))
    substitutions_expr = "GL_SUBSTITUTIONS" if rewrites else None
    if rewrites:
        package.after.append(
            Assign(
                "GL_SUBSTITUTIONS",
                dict(sorted(rewrites.items())),
                comments=[f"{mk_path.name}: the sed rewrites of the glibc-derived `.gl.h` headers."],
            )
        )
    generated_dir = f"{prefix}{GNULIB_GENERATED_DIR}"
    if generated:
        package.after.append(
            Assign(
                f"{upper}_GENERATED_HDRS",
                {f"{generated_dir}/{out}": f"{prefix}{h.template}" for out, h in sorted(generated.items())},
                comments=[
                    (
                        f"{mk_path.name} BUILT_SOURCES: the wrapper headers generated unconditionally, written to "
                        f"{generated_dir}/: a tree that ships a maintainer's copy of a header next to its .in.h "
                        "template (grub's gnulib) keeps its file, and the rendered one comes first on the "
                        "include path."
                    )
                ],
            )
        )
        package.after.append(
            Raw(
                "[\n    autoconf_hdr(\n"
                f'        name = hdr.replace("{GNULIB_GENERATED_DIR}/", "").replace("/", "_").replace(".", "_"),\n'
                "        out = hdr,\n"
                f'        inlines = {inlines_expr},\n        mode = "subst",\n'
                + (f"        substitutions = {substitutions_expr},\n" if substitutions_expr else "")
                + "        template = template,\n"
                '        deps = [":autoconf"],\n    )\n'
                f"    for hdr, template in {upper}_GENERATED_HDRS.items()\n]"
            )
        )
    conditions: dict[str, str] = {}
    outs: dict[str, str] = {}  # stem -> header path, where it is not `<stem>.h` (selinux/context.h)
    for header in conditional:
        # bison's stdbool.h: an older gnulib's AM_CONDITIONAL([GL_GENERATE_STDBOOL_H])
        # inside the ported macro, which the port (a newer gnulib) no longer has:
        # the system header suffices on every supported compiler.
        if _header_condition(header).startswith("GL_GENERATE_"):
            state.generate_defaults.add(_header_condition(header))
    templates: dict[str, str] = {}  # stem -> template, where it is not `<stem>.in.h` (se-context.in.h)
    appended: dict[str, dict[str, Rule]] = {}  # stem -> marker -> the rule rendering a second input file
    dir_slug = (prefix.rstrip("/") or "lib").replace("/", "_")
    if conditional:
        stems = []
        for header in conditional:
            stem = gnulibmk.header_stem(header.out)
            stems.append(stem)
            if header.out != f"{stem}.h":
                outs[stem] = header.out
            if header.template != f"{stem}.in.h":
                templates[stem] = header.template
            expected_var = gnulibmk.generate_variable(header.out)
            header_condition = _header_condition(header)
            if header_condition != expected_var:
                # make's glob.h under the `else` of `if USE_SYSTEM_GLOB`: an AM_CONDITIONAL the
                # script lowered is spelled over configure's results; a gnulib switch
                # (GL_GENERATE_X_H, gl_GNULIB_ENABLED_x) is the substituted true/false.
                known = am_condition(header_condition, state)
                lowered = _lower_gnulib(known, state) if known is not None else None
                if lowered is not None and lowered.ok and lowered.expr:
                    conditions[stem] = lowered.expr
                elif header_condition.startswith("!"):
                    conditions[stem] = f"{header_condition[1:]}!=true"
                else:
                    conditions[stem] = f"{header_condition}==true"
                if header_condition.startswith("gl_GNULIB_ENABLED_"):
                    state.enabled_switches.add(header_condition)
            if header.extra_inputs:
                appended[stem] = _appended_inputs(mk, mk_path, src, prefix, header, stem, dir_slug, notes)
        package.after.append(
            Assign(
                "GNULIB_COND_HDR_STEMS",
                stems,
                comments=[
                    "The wrapper headers generated only when the matching `GL_GENERATE_*_H` says the",
                    "system header is missing or unusable.  Each is processed unconditionally into",
                    "`_gnulib_cond/` and `cc_gnulib_conditional_hdrs` picks the ones whose condition holds,",
                    "placing them at their path under `_gnulib_cond/` (`selinux/context.h` included).",
                ],
            )
        )
        if outs:
            package.after.append(
                Assign(
                    "GNULIB_COND_HDR_OUTS",
                    outs,
                    comments=["Headers in a subdirectory: their path, where it is not `<stem>.h`."],
                )
            )
        if templates:
            package.after.append(
                Assign(
                    "GNULIB_COND_HDR_TEMPLATES",
                    templates,
                    comments=["Templates not named `<stem>.in.h` (gnulib's se-context.in.h for selinux/context.h)."],
                )
            )
        out_expr = 'GNULIB_COND_HDR_OUTS.get(stem, stem + ".h")' if outs else 'stem + ".h"'
        template_expr = (
            f'"{prefix}" + GNULIB_COND_HDR_TEMPLATES.get(stem, stem + ".in.h")'
            if templates
            else f'"{prefix}{{}}.in.h".format(stem)'
        )
        cond_inlines_expr = inlines_expr
        if any(appended.values()):
            for halves in appended.values():
                package.after.extend(halves.values())
            package.after.append(
                Assign(
                    "GNULIB_COND_HDR_INLINES",
                    {
                        stem: {marker: f":{rule.name}" for marker, rule in halves.items()}
                        for stem, halves in appended.items()
                        if halves
                    },
                    comments=[
                        "Snippets spliced into one particular wrapper on top of GL_INLINES: a second file",
                        f"{mk_path.name}'s recipe appends to the header, rendered through the recipe's sed.",
                    ],
                )
            )
            cond_inlines_expr = (
                f"{inlines_expr} | GNULIB_COND_HDR_INLINES.get(stem, {{}})"
                if inlines
                else "GNULIB_COND_HDR_INLINES.get(stem, {})"
            )
        package.after.append(
            Raw(
                f'[\n    autoconf_hdr(\n        name = "{dir_slug}_{{}}_h_processed".format(stem.replace("-", "_")),\n'
                f'        out = "_gnulib_cond/" + {out_expr},\n'
                f'        inlines = {cond_inlines_expr},\n        mode = "subst",\n'
                f'        template = {template_expr},\n        deps = [":autoconf"],\n    )\n'
                "    for stem in GNULIB_COND_HDR_STEMS\n]"
            )
        )
        if conditions:
            package.after.append(
                Assign(
                    "GNULIB_COND_HDR_CONDITIONS",
                    conditions,
                    comments=["Headers whose automake conditional is not the usual GL_GENERATE_<STEM>_H."],
                )
            )
        cond_expr = (
            'GNULIB_COND_HDR_CONDITIONS.get(stem, "GL_GENERATE_{}_H".format(stem.upper().replace("-", "_")))'
            if conditions
            else '"GL_GENERATE_{}_H".format(stem.upper().replace("-", "_"))'
        )
        package.after.append(
            Rule(
                "cc_gnulib_conditional_hdrs",
                f"{prefix}{lib_name}_gnulib_conditional_hdrs",
                {
                    "hdrs": Raw(
                        f'{{\n        ":{dir_slug}_{{}}_h_processed".format(stem.replace("-", "_")): '
                        + cond_expr
                        + "\n        for stem in GNULIB_COND_HDR_STEMS\n    }"
                    ),
                    # GL_GENERATE_ICONV_H and the like: the header ports' :defaults the
                    # main target reads (GNULIB_M4_DEFAULT_DEPS) where the project
                    # configures no such header itself.
                    "deps": [":autoconf"],
                    # `_gnulib_cond/selinux/context.h` is included as <selinux/context.h>.
                    "strip_include_prefix": "_gnulib_cond",
                },
                load_from="//gnulib:conditional_hdrs.bzl",
            )
        )

    private = f"{prefix}{lib_name}_private_headers"
    # The glob leaves out the templates and any shipped copy of a header the library generates
    # (both generated unconditionally and conditionally): the rendered one is the header.
    shipped = sorted({f"{prefix}{out}" for out in generated} | {f"{prefix}{h.out}" for h in conditional})
    template_patterns = header_patterns(src, [prefix or "."], ".in.h")
    exclude = _list_literal(template_patterns + [p for p in shipped if (src / p).is_file()], "        ")
    hdrs = Raw(
        f"glob(\n        {_list_literal(header_patterns(src, [prefix or '.']), '        ')},\n"
        f"        # The templates are inputs of the autoconf_hdr targets, not headers.\n"
        f"        exclude = {exclude},\n    )"
    )
    generated_lib = f"{prefix}{lib_name}_gnulib_generated_hdrs"
    if generated:
        # The rendered headers under their own include root, as the conditional ones: a `-I`
        # that comes before the source directory's `-isystem`, so a stale shipped copy never
        # shadows them.
        package.after.append(
            Rule(
                "cc_library",
                generated_lib,
                {
                    "hdrs": Raw(f"{upper}_GENERATED_HDRS.keys()"),
                    "strip_include_prefix": generated_dir,
                    "visibility": ["//:__subpackages__"],
                },
                load_from="@rules_cc//cc:cc_library.bzl",
            )
        )
    private_attrs: dict[str, AttrValue] = {
        "hdrs": hdrs,
        "includes": [prefix.rstrip("/") or "."],
        "visibility": ["//:__subpackages__"],
    }  # the module's own test packages
    private_deps = sorted(
        _config_deps(state)
        + ([f":{generated_lib}"] if generated else [])
        + ([f":{prefix}{lib_name}_gnulib_conditional_hdrs"] if conditional else []),
        key=label_sort_key,
    )
    private_attrs["deps"] = private_deps
    package.after.append(Rule("cc_library", private, private_attrs, load_from="@rules_cc//cc:cc_library.bzl"))
    return private


# `< $(srcdir)/verify.h`; a non-recursive gnulib.mk reads `$(top_srcdir)/lib/verify.h`.
_SRCDIR_INPUT_RE = re.compile(r"<\s*\$\((?:top_)?srcdir\)/(\S+)")


def _appended_inputs(
    mk: gnulibmk.Makefile,
    mk_path: Path,
    src: Path,
    prefix: str,
    header: gnulibmk.GeneratedHeader,
    stem: str,
    dir_slug: str,
    notes: list[str],
) -> dict[str, Rule]:
    """The second input file a wrapper header's recipe appends after the template (gnulib's
    `assert.h`: `verify.h` through a sed that keeps its `static_assert` half), as an
    `autoconf_hdr` rendering that file with the sed as substitutions, keyed by the template's
    closing comment where it is spliced in (`/* The definition of static_assert is copied
    here.  */`, the template's last line).  A recipe the converter cannot follow is noted."""
    halves: dict[str, Rule] = {}
    recipe = " ".join(line.rstrip("\\").strip() for line in mk.recipes.get(header.out, []))
    # `{ sed ... < $(srcdir)/assert.in.h && sed ... < $(srcdir)/verify.h; } > $@-t`: the
    # program for each input is what precedes its redirection.
    parts = _SRCDIR_INPUT_RE.split(recipe)
    programs = {parts[i + 1].rstrip(";"): parts[i] for i in range(0, len(parts) - 1, 2)}
    template = src / prefix / header.template
    marker = next((line for line in reversed(template.read_text(errors="replace").splitlines()) if line.strip()), "")
    for extra in header.extra_inputs:
        program = next((p for name, p in programs.items() if name.rsplit("/", 1)[-1] == extra), None)
        commands = sedtext.parse(program) if program is not None and "sed" in program else None
        source = src / prefix / extra
        reason = None
        if commands is None:
            reason = "its recipe is not a sed the converter follows"
        elif not source.is_file():
            reason = f"{prefix}{extra} is not in the tree"
        elif not sedtext.is_comment_line(marker) or marker in GL_INLINES or marker in halves:
            reason = f"{header.template} does not end with a comment line to splice it at"
        else:
            substitutions = sedtext.substitutions(commands, source.read_text(errors="replace"))
            if substitutions is None:
                reason = "the recipe's sed has no equivalent as exact substitutions"
        if reason is not None:
            notes.append(f"{prefix}{header.out}: also assembled from {prefix}{extra}, left out: {reason}")
            continue
        assert substitutions is not None
        extra_stem = extra.rsplit(".", 1)[0]
        attrs: dict[str, AttrValue] = {"out": f"_gnulib_cond/{stem}-{extra}", "mode": "subst"}
        if substitutions:
            attrs["substitutions"] = substitutions
        attrs["template"] = f"{prefix}{extra}"
        attrs["deps"] = [":autoconf"]
        halves[marker] = Rule(
            "autoconf_hdr",
            f"{dir_slug}_{stem.replace('-', '_')}_h_{slug(extra_stem)}",
            attrs,
            comments=[
                f"{header.out} is assembled from two files: {header.template}, then {extra} through",
                f"the sed of {mk_path.name}'s recipe, here as substitutions (a range the sed",
                "deletes is fenced off with `#if 0`).  For assert.h that half supplies a one-argument",
                "`_Static_assert` to compilers predating C23 (gcc < 9).",
            ],
            load_from="//autoconf:autoconf_hdr.bzl",
        )
    return halves


def _apply_src_decision(
    decision: dec.Decision, cond_name: str, src_conditions: dict[str, str], state: ProjectState
) -> str:
    """Record a `src_condition:` decision for `cond_name` in the condition table: ALWAYS as
    `ALWAYS_CONDITION`, NEVER as `NEVER_CONDITION`, an expression as itself (and as an expression
    whose providers `_condition_providers` pulls in).  Returns the word or expression for a
    note."""
    if decision.condition is not None:
        src_conditions[cond_name] = decision.condition
        state.src_condition_exprs[cond_name] = decision.condition
        return decision.condition
    if decision.always:
        src_conditions[cond_name] = ALWAYS_CONDITION
        return "ALWAYS"
    src_conditions[cond_name] = NEVER_CONDITION
    return "NEVER"


def _header_condition(header: gnulibmk.GeneratedHeader) -> str:
    """The automake conditional a conditionally generated wrapper header is under (the
    `conditional` list of `_wrapper_header_rules` holds no header without one)."""
    if header.condition is None:
        raise ValueError(f"{header.out}: generated unconditionally, not a conditional wrapper header")
    return header.condition


def _condition_table(
    library: gnulibmk.Library,
    src: Path,
    state: ProjectState,
    notes: list[str],
    decisions: dec.Decisions,
    mk_path: Path,
    mk_text: str,
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    """gl_INIT's conditions (`state.src_conditions`) as the library spells them: an expression over
    the rules' results per condition name (`1` for one that always holds), the constraint labels a
    purely host-OS condition holds on (`platform_conditions`), or the ones it does not hold on
    (`platform_exceptions`); what none of those express is a `src_condition:` decision when a
    source is under it."""
    src_conditions: dict[str, str] = {}
    platform_conditions: dict[str, list[str]] = {}  # cond name -> constraint labels where it holds
    platform_exceptions: dict[str, list[str]] = {}  # cond name -> constraint labels where it does NOT hold
    # gl_INIT declares a gl_CONDITIONAL for every module's object whether or
    # not this Makefile compiles one under it (LIBC_IS_GNU, LIBC_IS_MUSL and
    # PLATFORM_IS_MINGW guard nothing in most trees): only a condition a
    # source is under is worth a decision.
    guarding = {source.condition for source in library.sources if source.condition}
    # gl_ERROR: `m4_ifdef([gl_HAVE_MODULE_VERROR], [COMPILE_ERROR_C=1], ...)`: with
    # the verror module (m4), error.c is compiled whatever the system has; the
    # error_h port's COMPILE_ERROR_C knows only the system's entry points.
    if any(source.path.endswith("verror.c") for source in library.sources) or "verror" in _gnulib_modules(src):
        src_conditions["GL_COND_OBJ_ERROR"] = "1"
        notes.append(
            "GL_COND_OBJ_ERROR: the verror module is present, so error.c is compiled unconditionally "
            "(gl_HAVE_MODULE_VERROR)"
        )
    for cond_name, conds in state.src_conditions.items():
        # A hand-added `src_condition:` decision overrides the derived condition, the verror
        # heuristic above included (the docs' promise): a gnulib.mk older than the ports' gnulib
        # tests a cache variable the port no longer sets, or spells the condition in a way the
        # lowering cannot follow.  The note keeps the override auditable once a newer gnulib.mk
        # derives the right condition itself.
        decided = decisions.optional(f"src_condition:{cond_name}", "src_condition")
        if decided is not None:
            applied = _apply_src_decision(decided, cond_name, src_conditions, state)
            derived = " || ".join(describe(c) for c in conds)
            notes.append(f"{cond_name}: condition decided {applied} (gl_INIT's: {derived})")
            continue
        if cond_name in src_conditions:
            continue  # settled above
        if cond_name.startswith("gl_GNULIB_ENABLED_"):
            src_conditions[cond_name] = "1"  # conditional dependencies: every module is enabled
            continue
        cond = df.disj(*conds)
        lowered = _lower_gnulib(cond, state)
        if lowered.ok and lowered.expr is None:
            src_conditions[cond_name] = "1"  # grep's `AC_LIBOBJ([mbsrtoc32s-state])` at gl_INIT's top level
            continue
        if lowered.ok and lowered.expr:
            src_conditions[cond_name] = lowered.expr
            state.src_condition_exprs[cond_name] = lowered.expr
            continue
        labels = _platform_labels(cond)
        if labels is not None:
            platform_conditions[cond_name] = labels
            unmatched = lower.unmatched_patterns(cond)
            if unmatched:
                notes.append(f"{cond_name}: host_os patterns without a Bazel constraint: {', '.join(unmatched)}")
            continue
        everywhere_but = _platform_exceptions(cond)
        if everywhere_but is not None:
            platform_exceptions[cond_name] = everywhere_but
            continue
        spelled = next((expr for macro, expr in KNOWN_AM_CONDITIONS if any(macro in describe(c) for c in conds)), None)
        if spelled is None:
            spelled = KNOWN_SRC_CONDITIONS.get(cond_name)
        if spelled is not None:
            src_conditions[cond_name] = spelled
            state.src_condition_exprs[cond_name] = spelled
            continue
        if cond_name not in guarding:
            continue
        decision = decisions.resolve(
            dec.Request(
                f"src_condition:{cond_name}",
                "src_condition",
                mk_path.name,
                f"the condition for the sources under {cond_name} cannot be expressed ({lowered.unknown or 'always'})",
                {**_am_block(mk_text, mk_path.name, cond_name), "conditions": [describe(c) for c in conds]},
            )
        )
        if decision is not None:
            _apply_src_decision(decision, cond_name, src_conditions, state)
    return src_conditions, platform_conditions, platform_exceptions


def _classify_sources(
    library: gnulibmk.Library,
    src: Path,
    prefix: str,
    state: ProjectState,
    notes: list[str],
    decisions: dec.Decisions,
    mk_path: Path,
    mk_text: str,
    src_conditions: dict[str, str],
    platform_conditions: dict[str, list[str]],
    platform_exceptions: dict[str, list[str]],
) -> tuple[
    dict[str, str], dict[str, list[str]], list[tuple[str, list[str]]], list[tuple[str, gnulibmk.Source, str]], list[str]
]:
    """Every source of the library by how it is compiled: under a condition expression
    (path -> condition name), on some platforms (constraint label -> paths), everywhere but on
    some (path, labels), an EXTRA source of a used module no rule places yet (for the caller to
    decide), or unconditionally.  Conditions met on the way (a gnulib.mk `else` branch, an
    automake conditional of the Makefile.am, gnulib's replacement rule) join the tables."""
    conditional_srcs: dict[str, str] = {}
    platform_srcs: dict[str, list[str]] = {}  # constraint label -> sources
    excluded_srcs: list[tuple[str, list[str]]] = []  # (source, constraint labels it is left out on)
    extra_unknown: list[tuple[str, gnulibmk.Source, str]] = []  # EXTRA sources of used modules, no rule applies
    plain_srcs: list[str] = []
    for source in simplified_conditions(library.sources):  # a file under X and under !X is unconditional
        if source.path.endswith(gnulibmk.YACC_SUFFIXES):
            # parse-datetime.y: the tarball ships the bison output next to it
            # (automake distributes it); compile that, as the hand overlays do.
            shipped = source.path.rsplit(".", 1)[0] + ".c"
            if (src / prefix / shipped).is_file():
                source = gnulibmk.Source(shipped, source.condition, source.module, source.extra)
            else:
                notes.append(f"{source.path}: yacc source without a shipped .c; left out")
                continue
        if not source.path.endswith((".c", ".cc", ".cpp")):
            continue
        path = f"{prefix}{source.path}"
        if source.condition:
            cond_name = source.condition
            if cond_name.startswith("gl_GNULIB_ENABLED_"):
                src_conditions.setdefault(cond_name, "1")  # conditional dependencies: every module is enabled
            if src_conditions.get(cond_name) == NEVER_CONDITION:
                notes.append(f"{source.path}: left out (src_condition:{cond_name} is NEVER)")
                continue
            if (
                cond_name.startswith("!")
                and " && " not in cond_name
                and cond_name not in src_conditions
                and cond_name not in platform_conditions
                and cond_name not in platform_exceptions
            ):
                # gnulib.mk's `else` branch (m4's localename-unsafe.c, under `if
                # GL_COND_OBJ_LOCALENAME_UNSAFE_LIMITED` else): the conditional's negation.
                base = cond_name[1:]
                if src_conditions.get(base) == ALWAYS_CONDITION:
                    notes.append(f"{source.path}: under the else of `if {base}`, which always holds here; left out")
                    continue
                if src_conditions.get(base) == NEVER_CONDITION:
                    # The `if` arm never compiles: the else arm always does.
                    if path not in plain_srcs:
                        plain_srcs.append(path)
                    continue
                decided_base = decisions.optional(f"src_condition:{base}", "src_condition")
                if decided_base is not None and decided_base.condition is not None:
                    # The decided expression, not the derived one, is what the else arm negates.
                    src_conditions[cond_name] = f"!({decided_base.condition})"
                    state.src_condition_exprs[cond_name] = src_conditions[cond_name]
                elif base in state.src_conditions:
                    negated = _lower_gnulib(df.neg(df.disj(*state.src_conditions[base])), state)
                    if negated.ok and negated.expr:
                        src_conditions[cond_name] = negated.expr
                        state.src_condition_exprs[cond_name] = negated.expr
                    elif negated.ok:
                        if path not in plain_srcs:
                            plain_srcs.append(path)
                        continue
                if base in platform_conditions:
                    platform_exceptions[cond_name] = platform_conditions[base]
                elif base in platform_exceptions:
                    platform_conditions[cond_name] = platform_exceptions[base]
            if cond_name in platform_exceptions:
                excluded_srcs.append((path, platform_exceptions[cond_name]))
                continue
            if cond_name not in src_conditions and cond_name not in platform_conditions:
                known = am_condition(cond_name, state)
                labels = _platform_labels(known) if known is not None else None
                if cond_name.startswith("GL_GENERATE_"):
                    src_conditions[cond_name] = f"{cond_name}==true"
                elif labels is not None:
                    platform_conditions[cond_name] = labels
                else:
                    # gnulib's HAVE_* shell variables are substs with a define of
                    # the same name; the rules want the subst named outright.
                    lowered = _lower_gnulib(known, state) if known is not None else None
                    spelled = next(
                        (expr for macro, expr in KNOWN_AM_CONDITIONS if known is not None and macro in describe(known)),
                        None,
                    )
                    if lowered is not None and lowered.ok and lowered.expr:
                        src_conditions[cond_name] = lowered.expr
                    elif spelled is not None:
                        src_conditions[cond_name] = spelled
                    elif cond_name in state.port_conditionals:
                        # AM_CONDITIONAL in a ported m4: the port's <NAME>_TRUE subst says when.
                        src_conditions[cond_name] = state.port_conditionals[cond_name]
                    else:
                        decision = decisions.resolve(
                            dec.Request(
                                f"src_condition:{cond_name}",
                                "src_condition",
                                mk_path.name,
                                f"{source.path} is compiled only `if {cond_name}`, an automake conditional "
                                "the converter did not follow",
                                {
                                    **_am_block(mk_text, mk_path.name, cond_name),
                                    "am_conditional": describe(known) if known is not None else None,
                                },
                            )
                        )
                        if decision is None or decision.never:
                            notes.append(f"{source.path}: left out (`if {cond_name}` undecided or never)")
                            continue
                        if decision.always:
                            src_conditions[cond_name] = "1"
                        elif decision.condition is not None:
                            src_conditions[cond_name] = decision.condition
                            state.src_condition_exprs[cond_name] = decision.condition
            if src_conditions.get(cond_name) == "1":
                # Decided ALWAYS: an ordinary source, not a wrapper under a condition.
                if path not in plain_srcs:
                    plain_srcs.append(path)
                continue
            if cond_name in platform_conditions:
                for label in platform_conditions[cond_name]:
                    platform_srcs.setdefault(label, []).append(path)
                continue
            conditional_srcs[path] = cond_name
        elif source.extra:
            cond_name = "GL_COND_OBJ_" + re.sub(r"[^A-Za-z0-9]", "_", source.path[:-2]).upper()
            if cond_name in platform_exceptions:
                excluded_srcs.append((path, platform_exceptions[cond_name]))
                continue
            if cond_name in platform_conditions:
                # bison's `case "$host_os" in mingw*) AC_LIBOBJ([windows-once]) ;; esac` in gl_INIT:
                # the older gnulib's spelling of a platform arm.
                for label in platform_conditions[cond_name]:
                    platform_srcs.setdefault(label, []).append(path)
                continue
            if cond_name in src_conditions:
                if src_conditions[cond_name] == "1":
                    if path not in plain_srcs:
                        plain_srcs.append(path)
                else:
                    conditional_srcs[path] = cond_name
            elif source.module and f"gnulib/m4/{source.module}" in state.gnulib_packages:
                # Older gnulib-tool output: the AC_LIBOBJ is inside the ported macro.
                # gnulib's rule for a replacement is `HAVE_X = 0 || REPLACE_X = 1`
                # over the header's substitutions, which the ports provide; a
                # source outside that rule (vasnprintf.c, compiled whenever the
                # module is used) is the project's decision, unless another source
                # includes it textually (regex.c reads regcomp.c).
                fn_upper = re.sub(r"[^A-Za-z0-9]", "_", source.path[:-2]).upper()

                def result_name(name: str) -> str:
                    # A subst that is also a define of another check (HAVE_VASPRINTF)
                    # is named by its cache name, as the rules require.
                    return f"ac_cv_subst_{name}" if name in state.port_ambiguous else name

                # The port may track HAVE_<X> as an M4 variable rather than a subst (obstack):
                # a result either way.
                provided = state.port_substs | state.port_variables
                parts = [f"{result_name(f'REPLACE_{fn_upper}')}==1"] if f"REPLACE_{fn_upper}" in provided else []
                if f"HAVE_{fn_upper}" in provided:
                    probe = probe_of(f"HAVE_{fn_upper}", state)
                    parts.append(f"!{probe}" if probe is not None else f"{result_name(f'HAVE_{fn_upper}')}==0")
                if parts:
                    src_conditions[cond_name] = " || ".join(parts)
                    state.src_condition_exprs[cond_name] = src_conditions[cond_name]
                    conditional_srcs[path] = cond_name
                    notes.append(
                        f"{source.path}: EXTRA source of module {source.module}, compiled when "
                        f"{src_conditions[cond_name]} (gnulib's replacement rule)"
                    )
                else:
                    extra_unknown.append((path, source, cond_name))
            else:
                notes.append(f"{source.path}: EXTRA source without an AC_LIBOBJ condition; left out")
        else:
            if path not in plain_srcs:
                plain_srcs.append(path)
    return conditional_srcs, platform_srcs, excluded_srcs, extra_unknown, plain_srcs
