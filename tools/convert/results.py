"""What the configure body's checks produce and what the ports already provide: duplicate
results, confdefs.h, the registry libraries configure asks for."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from tools.convert import decisions as dec
from tools.convert import libraries as registry_libraries
from tools.convert.common import _dict_of_lists, _list_literal
from tools.convert.index import PortIndex
from tools.convert.ir import (
    DEFAULT_ARM,
    PLATFORM_FAMILIES,
    Assign,
    AttrValue,
    Call,
    CheckItem,
    Dep,
    Package,
    Raw,
    Target,
    select_family,
    strings,
)
from tools.convert.translate import ProjectState, basename, check_key, clone, condition_names, provided_names, slug

_LIST_CHECKS = {
    "macros.AC_CHECK_HEADERS",
    "macros.AC_CHECK_FUNCS",
    "macros.AC_CHECK_DECLS",
    "macros.AC_CHECK_TYPES",
    "macros.AC_CHECK_MEMBERS",
}
# The define-less single check that keeps a list subject's cache variable.
_SINGULAR_OF = {
    "macros.AC_CHECK_HEADERS": "checks.AC_CHECK_HEADER",
    "macros.AC_CHECK_FUNCS": "checks.AC_CHECK_FUNC",
    "macros.AC_CHECK_DECLS": "checks.AC_CHECK_DECL",
    "macros.AC_CHECK_TYPES": "checks.AC_CHECK_TYPE",
    "macros.AC_CHECK_MEMBERS": "checks.AC_CHECK_MEMBER",
}

# Checks that compile a program and so see configure's confdefs.h: in the
# rules a probe sees an earlier define only when it lists it in
# `compile_defines`, and only results of *dependencies* are available.
_COMPILING_CHECKS = {
    "checks.AC_TRY_COMPILE",
    "checks.AC_TRY_LINK",
    "checks.AC_CHECK_HEADER",
    "checks.AC_CHECK_FUNC",
    "checks.AC_CHECK_DECL",
    "checks.AC_CHECK_TYPE",
    "checks.AC_CHECK_MEMBER",
} | _LIST_CHECKS
# Where the feature-test macros of AC_USE_SYSTEM_EXTENSIONS change the
# answer: what a header declares.  Header existence and function linkage
# do not depend on them, and those checks stay identical to the ports'
# (the toolchain cache then shares the result instead of seeing a duplicate).
_DECLARATION_CHECKS = {
    "checks.AC_TRY_COMPILE",
    "checks.AC_TRY_LINK",
    "checks.AC_CHECK_DECL",
    "checks.AC_CHECK_TYPE",
    "checks.AC_CHECK_MEMBER",
    "macros.AC_CHECK_DECLS",
    "macros.AC_CHECK_TYPES",
    "macros.AC_CHECK_MEMBERS",
}
# What the ports of the two macros every configure.ac starts with define:
# listed on each probe the way confdefs.h carries them in configure.
SYSTEM_EXTENSIONS_PORTS = ("autoconf/macros/AC_USE_SYSTEM_EXTENSIONS", "gnulib/m4/extensions")
SYSTEM_EXTENSION_DEFINES = [
    "_ALL_SOURCE",
    "_COSMO_SOURCE",
    "_DARWIN_C_SOURCE",
    "_GNU_SOURCE",
    "_HPUX_ALT_XOPEN_SOCKET_API",
    "_NETBSD_SOURCE",
    "_OPENBSD_SOURCE",
    "_POSIX_PTHREAD_SEMANTICS",
    "_TANDEM_SOURCE",
    "__EXTENSIONS__",
    "__STDC_WANT_MATH_SPEC_FUNCS__",
]
INCLUDES_DEFAULT_PORT = "autoconf/macros/AC_CHECK_INCLUDES_DEFAULT"
INCLUDES_DEFAULT_DEFINES = {
    "STDC_HEADERS",
    "HAVE_SYS_TYPES_H",
    "HAVE_SYS_STAT_H",
    "HAVE_STRINGS_H",
    "HAVE_INTTYPES_H",
    "HAVE_STDINT_H",
    "HAVE_UNISTD_H",
    "HAVE_STDIO_H",
    "HAVE_STDLIB_H",
    "HAVE_STRING_H",
    "HAVE_WCHAR_H",
}
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def all_provided_names(targets: list[Target]) -> set[str]:
    """Every result name the checks of `targets` produce, select() arms included."""
    return set().union(
        *(
            provided_names(c)
            for t in targets
            for items in [t.checks, *t.arm_lists]
            for c in items
            if isinstance(c, Call)
        )
    )


def _loc_file(target: Target) -> str:
    """The file a target's checks come from, for the report (configure.ac unless the target says)."""
    return target.loc.file if target.loc else "configure.ac"


_LINK_TOKEN_RE = re.compile(r"(?<![\w./-])-l([A-Za-z0-9_+.-]+)\b")
_INCLUDE_RE = re.compile(r'#\s*include\s*[<"]([A-Za-z0-9_./-]+\.h)[>"]')
# The headers that name a registry library (a probe that includes one links it) are the
# table's `[headers]`: `registry_libraries.headers()`.


def _registry_libraries_in_configure(
    sources: dict[str, str], selected: set[str], state: ProjectState, decisions: dec.Decisions, scope: str
) -> list[str]:
    """Registry libraries the configure script links by hand: `LIBS="-lgmp $LIBS"` in its own
    shell (mpfr's MPFR_CHECK_LIB) or a probe that includes the library's header.  Recorded as
    AC_CHECK_LIB would be: the module a bazel_dep, the target a dep of everything the project
    links."""
    notes: list[str] = []
    for file, text in sorted(sources.items()):
        if file not in selected and basename(file) not in selected:
            continue
        found: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("dnl"):
                continue
            # A `#` line is an m4 comment, except the `#include` of a probe's program.
            if not stripped.startswith("#"):
                for name in _LINK_TOKEN_RE.findall(line):
                    if registry_libraries.known(name):
                        found.setdefault(name, f"-l{name}")
            for header in _INCLUDE_RE.findall(line):
                if header in registry_libraries.headers():
                    found.setdefault(registry_libraries.headers()[header], f"#include <{header}>")
        for name, how in sorted(found.items()):
            if name in state.configure_libs:
                continue
            # `library:<package>:<name>` IGNORE: the project is built without the
            # library (gawk's extensions without MPFR, as the BCR module), as a
            # configure run on a host that lacks it.
            decision = decisions.optional(f"library:{scope}:{name}", "library")
            if decision is not None and decision.ignore:
                notes.append(
                    f"{file}: {how}: left out (library:{scope}:{name} is IGNORE); the project builds "
                    "as on a host without the library"
                )
                continue
            library = registry_libraries.known(name)
            if library is None:
                # A header whose library the table does not list: no module to declare.
                notes.append(f"{file}: {how}: no registry module is known for {name}; left out")
                continue
            state.external_modules[library.module] = library.version
            state.configure_libs[name] = library.label
            notes.append(
                f"{file}: {how}: the registry module {library.module} ({library.label}) is declared in "
                "MODULE.bazel, probed through the main target's cc_deps and linked by the project's targets"
            )
    return notes


_LINK_FLAG_RE = r"(?<![\w-])-l{name}\b"


def _needs_library(item: Call, name: str, headers: set[str]) -> bool:
    """Whether a check compiles or links against the registry library `name`: a library check
    naming it, a header check for one of its headers, or a probe whose program includes one."""
    flat = [str(v) for a in item.args for v in (a if isinstance(a, list) else [a])]
    if item.fn in ("checks.AC_CHECK_LIB", "checks.AC_HAVE_LIBRARY"):
        return bool(flat) and flat[0] in (name, f"lib{name}")
    if item.fn == "checks.AC_SEARCH_LIBS":
        return name in flat[1:]
    if any(h in flat for h in headers):
        return True
    # tcpdump's AC_CHECK_FUNCS(pcap_findalldevs ...) after AC_LBL_LIBPCAP put -lpcap in LIBS: a
    # function or declaration named with the library's prefix links against it.
    if item.fn in (
        "checks.AC_CHECK_FUNC",
        "macros.AC_CHECK_FUNCS",
        "checks.AC_CHECK_DECL",
        "macros.AC_CHECK_DECLS",
    ) and any(f.lower().startswith(name.lower() + "_") for f in flat):
        return True
    text = _probe_text(item) + "\n" + "\n".join(str(v) for v in item.kwargs.values() if isinstance(v, (str, list)))
    return (
        any(re.search(rf'[<"]{re.escape(h)}[>"]', text) for h in headers)
        or re.search(_LINK_FLAG_RE.format(name=re.escape(name)), text) is not None
    )


def _library_check_targets(main: Target, segments: list[Target], state: ProjectState, notes: list[str]) -> list[Target]:
    """The checks that need a registry library, each library's in an `autoconf` target of its
    own with the library as its one `cc_deps` entry; the other targets stay toolchain-only, so
    their results do not depend on the dependency graph and are shared across modules.

    A library's checks found in body `k` of the chain (`autoconf_1`, ..., `autoconf`) go to
    `autoconf_<lib>` (a second body's to `autoconf_<lib>_<k>`), which depends on the bodies
    before `k` as those checks did; body `k` and the bodies after it depend on it, so
    `requires` and `compile_defines` still reach its results.  A check under a select() arm
    (libssh2's openssl probe under `--with-crypto=openssl`) moves to the same arm of the
    library target; a check against two libraries at once (`-lssl -lcrypto`) gets both as
    `cc_deps`.

    A library of `configure_libs` that only ever gets probed under one configure option's arm
    (gawk's `-lmpfr -lgmp`, linked by the one probe `--enable-mpfr` runs) is found only there:
    `state.library_arms` records the arm, and the Makefile targets depend on the library in
    that arm (`automake._link_items`) instead of unconditionally."""
    libraries: dict[str, str] = dict(state.configure_libs)
    for label in sorted(state.pkg_cc_deps):  # pkg-config libraries: by their target name
        libraries.setdefault(label.rsplit(":", 1)[-1] if ":" in label else label.lstrip("@").split("/")[0], label)
    if not libraries:
        return []
    known_headers = registry_libraries.headers()
    headers_of = {name: {h for h, lib in known_headers.items() if lib == name} for name in libraries}
    bodies = segments + [main]
    port_deps = [d for d in main.deps if not d.label.startswith(":autoconf_")]
    out: list[Target] = []
    probed_unconditionally: set[str] = set()  # labels a check outside every select() needs
    probed_in_arms: dict[str, set[str]] = {}  # label -> the arm labels of the checks that need it

    def take(items: list[CheckItem], name: str) -> list[Call]:
        """The checks of `items` that need library `name`, with the ones they require (readline's
        AC_CHECK_LIB([termcap]) runs under `!ac_cv_func_tgetent`, a result of the same list: the
        list comes to depend on the library target, so what the taken checks require of it moves
        along, where the list still sees it)."""
        taken = [i for i in items if isinstance(i, Call) and _needs_library(i, name, headers_of[name])]
        moved = bool(taken)
        while moved:
            moved = False
            needed = {n for i in taken for n in _required_names(i)}
            for i in items:
                if isinstance(i, Call) and not any(i is t for t in taken) and provided_names(i) & needed:
                    taken.append(i)
                    moved = True
        return taken

    for name, label in sorted(libraries.items()):
        first = True
        for k, body in enumerate(bodies):
            taken = take(body.checks, name)
            arm_taken = {
                (family, arm_label): picked
                for family, arms in body.selects.items()
                for arm_label, items in arms.items()
                for picked in [take(items, name)]
                if picked
            }
            if not taken and not arm_taken:
                continue
            body.checks = [i for i in body.checks if not any(i is t for t in taken)]
            for (family, arm_label), picked in arm_taken.items():
                body.selects[family][arm_label] = [
                    i for i in body.selects[family][arm_label] if not any(i is t for t in picked)
                ]
            target_name = f"autoconf_{slug(name)}" if first else f"autoconf_{slug(name)}_{k + 1}"
            first = False
            target = Target(name=target_name, checks=list(taken), visibility="//visibility:private")
            for (family, arm_label), picked in arm_taken.items():
                target.arm(arm_label, family).extend(picked)
                target.arm(DEFAULT_ARM, family)
            everything = taken + [i for picked in arm_taken.values() for i in picked]
            needed_labels = sorted(
                {
                    lbl
                    for lib, lbl in libraries.items()
                    if any(_needs_library(i, lib, headers_of[lib]) for i in everything)
                }
            )
            for lib, lbl in libraries.items():
                if any(_needs_library(i, lib, headers_of[lib]) for i in taken):
                    probed_unconditionally.add(lbl)
                for (_family, arm_label), picked in arm_taken.items():
                    if any(_needs_library(i, lib, headers_of[lib]) for i in picked):
                        probed_in_arms.setdefault(lbl, set()).add(arm_label)
            target.attrs["cc_deps"] = needed_labels
            target.deps = list(port_deps) + [Dep(f":autoconf_{i}") for i in range(1, k + 1)]
            target.deps_suffix = list(main.deps_suffix)
            target.comments.append(
                f"configure.ac: the checks that compile or link against {', '.join(needed_labels)} "
                f"({name}); the other targets stay toolchain-only."
            )
            out.append(target)
            # Body k's remaining checks and every later body may `requires` these results.
            for later in bodies[k:]:
                later.add_dep(f":{target_name}", f"the {name} checks")
            where = "" if not arm_taken else f" ({sum(len(p) for p in arm_taken.values())} under the "
            where += ", ".join(sorted(l for _f, l in arm_taken)) + " arm)" if arm_taken else ""
            notes.append(
                f"{name}: {len(everything)} check(s) probe against {', '.join(needed_labels)} in {target_name}"
                f"{where}; the main target has no cc_deps"
            )
    for lib, label in sorted(state.configure_libs.items()):
        arms = probed_in_arms.get(label, set())
        if label in probed_unconditionally or len(arms) != 1:
            continue
        (arm,) = arms
        # One configure option's arm only: a platform arm would make the library a host's
        # business, and a negated conditional (`!L1,L2`) is no single arm to link it in.
        if arm == DEFAULT_ARM or arm.startswith("!") or select_family(arm) in PLATFORM_FAMILIES:
            continue
        state.library_arms[label] = arm
        notes.append(
            f"-l{lib}: probed only under the {arm} arm; the targets that link {label} depend on it in "
            "that arm, as configure finds it there"
        )
    # The library targets' cc_deps follow: a library only probed in an arm is a cc_dep in that
    # arm, so the option off keeps the module out of the graph (the checks are in the arm too).
    for target in out:
        labels = strings(target.attrs["cc_deps"])
        unconditional = [lbl for lbl in labels if lbl not in state.library_arms]
        by_arm: dict[str, list[str]] = {}
        for lbl in labels:
            if lbl in state.library_arms:
                by_arm.setdefault(state.library_arms[lbl], []).append(lbl)
        if by_arm:
            selects = [
                f"select({_dict_of_lists({arm: arm_labels, DEFAULT_ARM: []}, '    ')})"
                for arm, arm_labels in sorted(by_arm.items())
            ]
            parts = ([_list_literal(unconditional, "    ")] if unconditional else []) + selects
            target.attrs["cc_deps"] = Raw(" + ".join(parts))
    return out


def _merge_outer_substs(target: Target) -> list[str]:
    """One producer per substitution: a constant `AC_SUBST(NAME, v)` emitted on the branch where
    a probe does not run (mpc's `MPC_HAVE_COMPLEX_H=0` when complex.h is missing) next to the
    probe's own conditional `AC_SUBST(NAME, condition = <probe>, if_true, if_false)`.  The probe
    requires what that branch negates, so its condition is false there and `if_false` already
    applies: when `v` equals `if_false`, the constant is dropped."""
    notes: list[str] = []
    for items in [target.checks] + target.arm_lists:
        conditional = {
            c.args[0]: c
            for c in items
            if isinstance(c, Call) and c.fn == "checks.AC_SUBST" and c.args and "condition" in c.kwargs
        }
        for item in list(items):
            if not (
                isinstance(item, Call)
                and item.fn == "checks.AC_SUBST"
                and len(item.args) == 2
                and item.args[0] in conditional
                and "condition" not in item.kwargs
            ):
                continue
            probe = conditional[item.args[0]]
            requires = strings(item.kwargs.get("requires"))
            negated = {r[1:] for r in requires if r.startswith("!")}
            probe_requires = set()
            for other in items:
                if isinstance(other, Call) and other.kwargs.get("name") == probe.kwargs["condition"]:
                    probe_requires = set(strings(other.kwargs.get("requires")))
            if negated and negated <= probe_requires and str(item.args[1]) == str(probe.kwargs.get("if_false")):
                items.remove(item)
                notes.append(
                    f"configure.ac: AC_SUBST({item.args[0]}) on the branch without the probe is the "
                    f"probe's own if_false; one producer kept"
                )
    return notes


def _merge_list_checks(targets: list[Target]) -> int:
    """One call per plural macro and keyword set in each check list: the `macros.AC_CHECK_FUNCS`
    (`_HEADERS`, `_DECLS`, `_TYPES`, `_MEMBERS`) calls of a target with the same `includes`,
    `compile_defines`, `requires` and the rest merge into the first of them, their names appended
    in order; a call that carries anything different keeps its own group.  Within one target no
    check depends on another's result (the body was split where one would), so the order of the
    names does not matter.  Returns the number of calls merged away."""
    merged = 0
    for target in targets:
        for items in [target.checks, *[arm for family in target.selects.values() for arm in family.values()]]:
            first: dict[tuple[str, str], tuple[Call, list[AttrValue]]] = {}  # the head call and its names list
            kept: list[CheckItem] = []
            for item in items:
                if isinstance(item, Call) and item.fn in _LIST_CHECKS and item.args and isinstance(item.args[0], list):
                    key = (item.fn, repr(sorted(item.kwargs.items())))
                    found = first.get(key)
                    if found is not None:
                        head, names = found
                        names += [n for n in item.args[0] if n not in names]
                        head.comments += [c for c in item.comments if c not in head.comments]
                        merged += 1
                        continue
                    first[key] = (item, item.args[0])
                kept.append(item)
            items[:] = kept
    return merged


def _underscore_twins(package: Package, state: ProjectState, index: PortIndex | None) -> list[str]:
    """gnulib.mk conditions over a cache variable the ports spell with a leading underscore (bison's
    gl_cv_lib_readline is the readline port's _gl_cv_lib_readline): the expressions are respelled."""
    if index is None:
        return []
    provided = index.provided | {n for port in index.ports for n in port.cache}
    twins = {
        n: f"_{n}"
        for expr in state.src_condition_exprs.values()
        for n in condition_names(expr)
        if n not in provided and f"_{n}" in provided
    }
    if not twins:
        return []
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in twins) + r")\b")
    for name, expr in list(state.src_condition_exprs.items()):
        state.src_condition_exprs[name] = pattern.sub(lambda m: twins[m.group(1)], expr)
    for item in package.after:
        if isinstance(item, Assign) and item.name.endswith("SRC_CONDITIONS") and isinstance(item.value, dict):
            item.value = {
                k: pattern.sub(lambda m: twins[m.group(1)], v) if isinstance(v, str) else v
                for k, v in item.value.items()
            }
    return [
        f"{n}: spelled {t} by the port that provides it; the gnulib.mk conditions use that name"
        for n, t in sorted(twins.items())
    ]


def _produced_results(main: Target, package: Package, state: ProjectState, index: PortIndex | None) -> set[str]:
    """The result names the project's targets and the ports produce (see ProjectState.produced_results)."""
    targets = [t for t in package.targets if t is not main] + [main]
    labels = list(state.gnulib_packages.values()) + list(state.gnulib_defaults.values())
    out = set(_port_provided(targets, index, labels).variables)
    out |= all_provided_names(targets)
    if index is not None:
        out |= index.provided | {n for port in index.ports for n in port.cache}
    return out


def _condition_providers(main: Target, package: Package, state: ProjectState, index: PortIndex | None) -> list[str]:
    """A gnulib.mk condition over a result none of the project's modules provides, but another
    port does: the project's gnulib snapshot computed it in a macro the ports since moved it out
    of (bison's gl_ERROR once recorded `ac_cv_lib_error_at_line`; the error_h port does now).
    That port joins GNULIB_M4_DEPS, with a note; two candidates are a note only."""
    notes: list[str] = []
    if index is None or not state.src_condition_exprs:
        return notes
    others = [t for t in package.targets if t is not main]
    labels = list(state.gnulib_packages.values()) + list(state.gnulib_defaults.values())
    provided = set(_port_provided(others + [main], index, labels).variables)
    provided |= all_provided_names(others + [main])
    for name in sorted({n for expr in state.src_condition_exprs.values() for n in condition_names(expr)} - provided):
        candidates = [p for p in index.providers_of(name) if p.package.startswith("gnulib/m4/")]
        if len(candidates) == 1 and candidates[0].package not in state.gnulib_packages:
            port = candidates[0]
            label = f"@rules_cc_autoconf{port.label()}"
            state.gnulib_packages[port.package] = label
            assign = next((s for s in package.before if isinstance(s, Assign) and s.name == "GNULIB_M4_DEPS"), None)
            if assign is not None:
                assign.value = sorted(set(strings(assign.value)) | {label})
            else:
                main.add_dep(label, f"{name} for gnulib.mk")
            notes.append(
                f"gnulib.mk reads {name}, which none of the project's gnulib modules provides; "
                f"{port.label()} does (the project's gnulib snapshot computed it elsewhere): added to "
                "GNULIB_M4_DEPS"
            )
        elif len(candidates) > 1:
            notes.append(
                f"gnulib.mk reads {name}, which none of the project's gnulib modules provides; "
                f"{', '.join(p.label() for p in candidates)} do: add the right one to the deps"
            )
    return notes


@dataclass
class PortProvided:
    """What the port dependencies of some targets provide, each name with the label of the
    dependency that provides it (the first one found, through the ports' own deps)."""

    defines: dict[str, str] = field(default_factory=dict)
    """The defines of the direct dependencies."""
    substs: dict[str, str] = field(default_factory=dict)
    """The substitutions, through the ports' own deps too."""
    caches: dict[str, str] = field(default_factory=dict)
    """The cache variables the direct dependencies' probes record."""
    variables: dict[str, str] = field(default_factory=dict)
    """The M4 variables and the defaults (substs, variables and defines read beneath the results),
    with the defines over them: GL_GENERATE_X_H is spelled either way in the ports."""


def _port_provided(targets: list[Target], index: PortIndex | None, extra_labels: Sequence[str] = ()) -> PortProvided:
    """The defines and the substitutions the port dependencies of `targets`
    (and `extra_labels`: the gnulib modules, appended through a list
    variable) provide, each with the dependency's label.  The two are kept
    apart: the rules do (string_h substs HAVE_DECL_MEMEQ for its template
    while configure.ac defines it).  `caches` are the cache variables the
    direct dependencies' probes record instead (`ac_cv_type_gid_t` of
    AC_TYPE_UID_T), which a project probe of the same subject would produce
    a second time."""
    out = PortProvided()
    defines, substs, caches, m4_variables = out.defines, out.substs, out.caches, out.variables
    if index is None:
        return out
    labels = [dep.label for target in targets for dep in target.deps + target.default_deps] + list(extra_labels)
    seen: set[str] = set()
    queue = [(label.split("//", 1)[1].split(":")[0], label, True) for label in labels if "//" in label]
    while queue:  # the ports' own deps too: wint_t comes in through wctype_h
        package_name, label, direct = queue.pop(0)
        port = index.by_package.get(package_name)
        if port is None or package_name in seen:
            continue
        seen.add(package_name)
        # A package's defines count only when it is depended on directly: the
        # index knows deps by package, and dirfd reaches dirent_h for its
        # gl_DIRENT_DIR target alone, not the gl_DIRENT_H that defines
        # HAVE_DIRENT_H.  Dropping a define that is not there breaks a compile;
        # a duplicate that stays is an analysis error a `define:` decision ends.
        if direct:
            for name in sorted(port.defines):
                defines.setdefault(name, label)
            for name in sorted(port.cache):
                caches.setdefault(name, label)
        for name in sorted(port.substs):
            substs.setdefault(name, label)
        for name in sorted(port.variables):
            m4_variables.setdefault(name, label)
        # A port's defaults are read beneath the results: a condition may read
        # them, a local result of the name overrides them (no duplicate).
        for name in sorted(port.default_substs | port.default_variables | port.default_defines):
            m4_variables.setdefault(name, label)
        queue += [
            (dep, f"{label} (through {dep})" if dep != package_name else label, False) for dep in sorted(port.deps)
        ]
    m4_variables.update(defines)  # GL_GENERATE_X_H is spelled either way in the ports
    return out


def _local_provided(item: Call) -> tuple[set[str], set[str]]:
    """The defines and the substs one check produces (cache variables aside)."""
    if item.fn == "checks.AC_SUBST":
        return set(), {str(item.args[0])} if item.args else set()
    return {n for n in provided_names(item) if not n.startswith(("ac_cv_", "gl_cv_"))}, set()


def _drop_duplicate_defines(
    main: Target, others: list[Target], index: PortIndex | None = None, extra_labels: Sequence[str] = ()
) -> list[str]:
    """configure.ac's `AC_CHECK_FUNCS([lstat])` next to a project macro that
    defines HAVE_LSTAT itself: configure defines it twice, consistently; the
    rules reject two producers.  The plain list entry goes, the macro's
    probe (which knows more) stays.  Likewise a check whose define (or
    subst) a port dependency already provides: the port's probe stays
    (`AC_CHECK_FUNCS([fchown])` next to gl_FUNC_CHOWN, which checks fchown
    itself)."""
    ported = _port_provided(others + [main], index, extra_labels)
    ported_defines, ported_substs, ported_cache = ported.defines, ported.substs, ported.caches
    produced: dict[str, str] = {}
    for target in others + [main]:
        for items in [target.checks] + target.arm_lists:
            for item in items:
                if isinstance(item, Call) and item.fn not in _LIST_CHECKS:
                    for name in provided_names(item):
                        produced.setdefault(name, target.name)
    notes: list[str] = []
    notes += _merge_duplicate_defines(main)
    notes += _drop_arm_constant_duplicates(others + [main])
    for target in others + [main]:
        for items in [target.checks] + target.arm_lists:
            for item in list(items):
                if not isinstance(item, Call) or item.fn in _LIST_CHECKS:
                    continue
                defines, substs = _local_provided(item)
                taken = {n: ported_defines[n] for n in defines if n in ported_defines}
                taken.update({n: ported_substs[n] for n in substs if n in ported_substs})
                if taken:
                    items.remove(item)
                    notes.append(
                        f"{_loc_file(target)}: {check_key(item)} left out: "
                        f"{', '.join(sorted(set(taken.values())))} already provides "
                        f"{', '.join(sorted(taken))} (configure produces it twice, consistently)"
                    )
                    continue
                # `AC_CHECK_TYPE(gid_t, int)` beside AC_TYPE_UID_T: the port's probe records
                # ac_cv_type_gid_t already, and the rules reject a second producer of a result
                # unless the two checks are the same text, which a project's own (its
                # compile_defines, its includes) rarely is.
                if item.fn in _SINGULAR_OF.values() and "define" not in item.kwargs:
                    recorded = {n: ported_cache[n] for n in provided_names(item) if n in ported_cache}
                    if recorded:
                        items.remove(item)
                        notes.append(
                            f"{_loc_file(target)}: {check_key(item)} left out: "
                            f"{', '.join(sorted(set(recorded.values())))} already records "
                            f"{', '.join(sorted(recorded))} (configure probes it twice, consistently)"
                        )
    for items in [main.checks] + main.arm_lists:
        for item in list(items):
            if not isinstance(item, Call) or item.fn not in _LIST_CHECKS or not item.args:
                continue
            kept: list[tuple[str, str | None]] = []
            dropped: list[tuple[str, str | None]] = []
            for subject in strings(item.args[0]):
                names = provided_names(Call(item.fn, [[subject]]))
                owner = next(
                    (produced.get(n) or ported_defines.get(n) for n in names if n in produced or n in ported_defines),
                    None,
                )
                (dropped if owner else kept).append((subject, owner))
            if not dropped:
                continue
            singular = _SINGULAR_OF[item.fn]
            carried = {
                k: v for k, v in item.kwargs.items() if k in ("includes", "compile_defines", "language", "copts")
            }
            at = items.index(item)
            for subject, owner in dropped:
                if any(n in ported_cache for n in provided_names(Call(singular, [subject]))):
                    # The port records the cache variable too: nothing is left to keep.
                    notes.append(
                        f"configure.ac: {item.fn.split('.')[1]}([{subject}]) left out: {owner} already "
                        "defines the result and records its cache variable (configure defines it twice, "
                        "consistently)"
                    )
                    continue
                notes.append(
                    f"configure.ac: {item.fn.split('.')[1]}([{subject}]) left out: {owner} already "
                    "defines the result (configure defines it twice, consistently); its cache variable "
                    "stays as a define-less check"
                )
                # gnulib.mk conditions read `ac_cv_func_memrchr` even where the
                # port defines HAVE_MEMRCHR without it: the probe stays, defining nothing.
                at += 1
                items.insert(at, Call(singular, [subject], dict(carried)))
            if kept:
                item.args[0] = [subject for subject, _ in kept]
            else:
                items.remove(item)
    return notes


def _probe_text(call: Call) -> str:
    """The C text a check compiles: its program and its includes."""
    parts: list[str] = []
    for value in (call.kwargs.get("code"), call.kwargs.get("includes")):
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif isinstance(value, Call):
            for arg in value.args:
                parts.extend(str(v) for v in (arg if isinstance(arg, list) else [arg]))
    return "\n".join(parts)


def _confdefs(main: Target) -> tuple[list[Target], bool]:
    """Give every compiling check of the configure.ac target the defines it
    could see in confdefs.h, and split the target where a probe needs one of
    the target's own earlier results.

    In the rules only dependency results reach a probe, and only the ones
    its `compile_defines` names.  Defines known to come from the system
    extensions and includes-default ports are listed from a table; a define
    the target itself produced earlier closes a segment: the checks so far
    become `autoconf_N`, which the rest depends on.  Returns the earlier
    segments and whether the extensions list is used.
    """
    labels = {d.label for d in main.deps}
    extensions = any(label.endswith(port) for label in labels for port in SYSTEM_EXTENSIONS_PORTS)
    dep_defines = INCLUDES_DEFAULT_DEFINES if any(l.endswith(INCLUDES_DEFAULT_PORT) for l in labels) else set()
    segments: list[list[CheckItem]] = [[]]
    produced: list[set[str]] = [set()]  # per segment
    # What a select() arm or a later check produces is not visible to an earlier segment: a check
    # reading it (make's FILE_TIMESTAMP_HI_RES define, conditioned on a select()-decided probe)
    # goes to the last segment, the one target that sees every result.
    arm_names = set().union(
        *(provided_names(c) for items in main.arm_lists for c in items if isinstance(c, Call)), set()
    )
    first_producer: dict[str, int] = {}
    for position, item in enumerate(main.checks):
        if isinstance(item, Call):
            for produced_name in provided_names(item):
                first_producer.setdefault(produced_name, position)
    deferred: list[Call] = []
    for position, item in enumerate(main.checks):
        if isinstance(item, Call):
            seen = set().union(*produced)
            reads = _required_names(item)
            if any((n in arm_names and n not in seen) or first_producer.get(n, -1) > position for n in reads):
                deferred.append(item)
                continue
        if isinstance(item, Call) and item.fn in _COMPILING_CHECKS:
            names = set(_IDENTIFIER_RE.findall(_probe_text(item)))
            earlier = set().union(*produced[:-1]) if len(produced) > 1 else set()
            if names & produced[-1]:
                segments.append([])
                produced.append(set())
                earlier = set().union(*produced[:-1])
            local = sorted(names & (earlier | dep_defines))
            with_extensions = extensions and (item.fn in _DECLARATION_CHECKS or names & set(SYSTEM_EXTENSION_DEFINES))
            if with_extensions and local:
                item.kwargs["compile_defines"] = Raw("SYSTEM_EXTENSIONS + " + _list_literal(local, "            "))
            elif with_extensions:
                item.kwargs["compile_defines"] = Raw("SYSTEM_EXTENSIONS")
            elif local:
                item.kwargs["compile_defines"] = local
        segments[-1].append(item)
        if isinstance(item, Call):
            produced[-1] |= provided_names(item)
    segments[-1] += deferred
    if len(segments) == 1:
        main.checks = segments[-1]
        return [], extensions
    earlier_targets: list[Target] = []
    for index, checks in enumerate(segments[:-1], start=1):
        target = Target(name=f"autoconf_{index}", checks=checks, visibility="//visibility:private")
        target.comments.append(
            f"configure.ac, part {index}: the checks before a probe that compiles against "
            "one of their results (`compile_defines` reaches dependencies only)."
        )
        target.deps = list(main.deps) + [Dep(f":autoconf_{i}") for i in range(1, index)]
        target.deps_suffix = list(main.deps_suffix)
        earlier_targets.append(target)
    main.checks = segments[-1]
    for index in range(1, len(segments)):
        main.add_dep(f":autoconf_{index}", f"configure.ac, part {index}")
    return earlier_targets, extensions


_REQUIRES_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _required_names(item: Call) -> set[str]:
    """The result names a check's `requires` and `condition` read."""
    texts = strings(item.kwargs.get("requires")) + (
        [str(item.kwargs["condition"])] if "condition" in item.kwargs else []
    )
    return {n for text in texts for n in _REQUIRES_NAME_RE.findall(text)}


def _rename_repeated_probes(target: Target) -> list[str]:
    """libpcap probes `ether_hostton`'s declaration in five headers in a row, each
    `AC_CHECK_DECL` after an `unset ac_cv_have_decl_ether_hostton` and a `!= yes` test of the
    previous answer: the same cache variable, re-probed.  The rules want one producer per
    name, so the later probes get `name = <cache>_<n>` and run only when the previous one
    failed (the chain's `!= yes`), and what follows reads the latest name, as the shell did."""
    notes: list[str] = []
    for items in [target.checks] + target.arm_lists:
        latest: dict[str, str] = {}  # base cache variable -> the name its latest probe carries
        seen: dict[str, tuple[int, str]] = {}  # base -> (count, first probe's key)
        for item in items:
            if not isinstance(item, Call):
                continue
            # References read the latest probe of a renamed variable.
            for attr in ("requires", "condition"):
                value = item.kwargs.get(attr)
                if isinstance(value, list):
                    rewritten = [_rename_in(str(v), latest) for v in value]
                    item.kwargs[attr] = list(dict.fromkeys(rewritten))  # a duplicate entry says nothing twice
                elif isinstance(value, str):
                    item.kwargs[attr] = _rename_in(value, latest)
            if item.fn not in _SINGULAR_OF.values() or "name" in item.kwargs:
                continue
            base = next((n for n in provided_names(item) if n.startswith(("ac_cv_", "gl_cv_"))), None)
            if base is None:
                continue
            key = repr(sorted((k, v) for k, v in item.kwargs.items() if k != "requires"))
            if base not in seen:
                seen[base] = (1, key)
                continue
            count, first_key = seen[base]
            if key == first_key:
                continue  # the same probe again: one producer, shared by content
            count += 1
            seen[base] = (count, first_key)
            previous = latest.get(base, base)
            name = f"{base}_{count}"
            item.kwargs["name"] = name
            requires = strings(item.kwargs.get("requires"))
            if f"!{previous}" not in requires:
                requires.append(f"!{previous}")
            item.kwargs["requires"] = requires
            latest[base] = name
            notes.append(
                f"{check_key(item)}: probed again with other includes; named {name}, run when {previous} "
                "failed (the chain's `!= yes` test), and read by the checks after it"
            )
    return notes


def _rename_in(text: str, latest: dict[str, str]) -> str:
    """`text` with every renamed cache variable replaced by its latest name."""
    for base, name in latest.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(base)}(?![A-Za-z0-9_])", name, text)
    return text


def _sink_into_producing_arms(target: Target) -> list[str]:
    """A check placed unconditionally whose `requires` names a result only a select() arm
    produces (gawk's HAVE_ADDR_NO_RANDOMIZE, whose probe sits in the `:pma_linux` arm) would
    fail the build in the other arms, where the result does not exist.  It moves into the arms
    that produce the result."""
    notes: list[str] = []
    unconditional = set().union(*(provided_names(c) for c in target.checks if isinstance(c, Call)), set())
    producers: dict[str, dict[str, set[str]]] = {}  # name -> family -> labels
    for family, arms in target.selects.items():
        for label, items in arms.items():
            for item in items:
                if isinstance(item, Call):
                    for name in provided_names(item) - unconditional:
                        producers.setdefault(name, {}).setdefault(family, set()).add(label)
    for item in list(target.checks):
        if not isinstance(item, Call):
            continue
        # `requires` and a subst's `condition` alike (make's `-linet` LIBS word, conditioned on
        # the inet probe that runs only under --with-customs).
        needed = {n for n in _required_names(item) if n in producers}
        if not needed:
            continue
        families = {f for n in needed for f in producers[n]}
        if len(families) != 1:
            continue  # two select() dimensions: not expressible; left as it is
        (family,) = families
        labels = set.intersection(*(producers[n][family] for n in needed))
        if not labels:
            continue
        target.checks.remove(item)
        for label in sorted(labels):
            target.arm(label, family).append(clone(item))
        target.arm(DEFAULT_ARM, family)
        notes.append(
            f"configure.ac: {check_key(item)} requires {', '.join(sorted(needed))}, produced only in the "
            f"{', '.join(sorted(labels))} arm; placed there"
        )
    return notes


_DEFINE_FNS = ("checks.AC_DEFINE", "checks.AC_DEFINE_UNQUOTED")


def _define_value(item: Call) -> str:
    """The value a `checks.AC_DEFINE` / `AC_DEFINE_UNQUOTED` call defines its name to (1 when unsaid)."""
    if len(item.args) > 1:
        return str(item.args[1])
    return str(item.kwargs.get("value", 1))


def _drop_arm_constant_duplicates(targets: list[Target]) -> list[str]:
    """make's `AS_CASE([$host], [*-*-mingw32], [AC_DEFINE([HAVE_DOS_PATHS])])` beside
    `AS_IF([test "$PATH_SEPARATOR" = ';'], [AC_DEFINE([HAVE_DOS_PATHS])])`: a platform
    arm's constant and a probe's conditional define of the same name and value.  configure
    writes the define once either way; the rules take one producer.  The probe's define
    stays (it decides on every platform), the arm's constant goes."""
    conditional: dict[tuple[str, str], str] = {}
    for target in targets:
        for item in target.checks:
            if isinstance(item, Call) and item.fn in _DEFINE_FNS and item.args and item.kwargs.get("requires"):
                conditional.setdefault((str(item.args[0]), _define_value(item)), target.name)
    notes: list[str] = []
    for target in targets:
        for items in target.arm_lists:
            for item in list(items):
                if not (isinstance(item, Call) and item.fn in _DEFINE_FNS and item.args) or item.kwargs.keys() & {
                    "requires",
                    "condition",
                }:
                    continue
                owner = conditional.get((str(item.args[0]), _define_value(item)))
                if owner is None:
                    continue
                items.remove(item)
                notes.append(
                    f"{_loc_file(target)}: {item.args[0]} is defined by a "
                    f"platform arm of {target.name} and by a probe of {owner}, to the same value (configure "
                    "defines it once either way); the arm's constant is left out"
                )
    return notes


def _merge_duplicate_defines(target: Target) -> list[str]:
    """`AC_DEFINE([HAVE_X])` reached on two paths of configure.ac: one define,
    required under either condition (configure writes it twice, identically).
    The same for a substitution (libtool looks OBJDUMP up twice)."""
    notes: list[str] = []
    seen: dict[tuple[str, tuple[str, ...], str], Call] = {}
    for item in list(target.checks):
        if not isinstance(item, Call) or item.fn not in (
            "checks.AC_DEFINE",
            "checks.AC_DEFINE_UNQUOTED",
            "checks.AC_SUBST",
        ):
            continue
        key = (item.fn, tuple(str(a) for a in item.args), str(item.kwargs.get("condition")))
        first = seen.get(key)
        if first is None:
            seen[key] = item
            continue
        a, b = strings(first.kwargs.get("requires")), strings(item.kwargs.get("requires"))
        if a and b:
            first.kwargs["requires"] = [f"({' && '.join(a)}) || ({' && '.join(b)})"]
        else:
            first.kwargs.pop("requires", None)
        target.checks.remove(item)
        notes.append(f"configure.ac: {item.args[0]} is defined on two paths; merged into one define")
    # The same probe twice, or two probes producing one define
    # (tcl's AC_CHECK_LIB([inet], [main]) and AC_CHECK_LIB([inet], [strncasecmp]) both
    # define HAVE_LIBINET): configure defines it once either way; the first stays.
    seen_defines: set[str] = set()
    seen_calls: set[str] = set()
    for item in list(target.checks):
        if (
            not isinstance(item, Call)
            or item.fn in ("checks.AC_DEFINE", "checks.AC_DEFINE_UNQUOTED", "checks.AC_SUBST", "checks.M4_VARIABLE")
            or item.fn in _LIST_CHECKS
        ):
            continue
        defines = _local_provided(item)[0]
        call_key = check_key(item) + repr(sorted((k, str(v)) for k, v in item.kwargs.items()))
        if call_key in seen_calls:
            target.checks.remove(item)
            notes.append(f"configure.ac: {check_key(item)} appears twice; one probe")
            continue
        seen_calls.add(call_key)
        taken = defines & seen_defines
        if taken:
            # gawk's AC_CHECK_LIB(socket, getaddrinfo) and AC_CHECK_LIB(socket, connect): both
            # define HAVE_LIBSOCKET, but a later check requires the second's cache variable
            # (`ac_cv_func_connect || ac_cv_lib_socket_connect`).  That check stays, its define
            # renamed out of the way (no template entry reads the new name).
            caches = {n for n in provided_names(item) if n.startswith(("ac_cv_", "gl_cv_"))}
            referenced = {
                c
                for c in caches
                if any(
                    other is not item and isinstance(other, Call) and any(c in str(v) for v in other.kwargs.values())
                    for items in [target.checks, *target.arm_lists]
                    for other in items
                )
            }
            if item.fn == "checks.AC_CHECK_LIB" and referenced and len(item.args) >= 2:
                renamed = f"{min(taken)}_{re.sub(r'[^A-Za-z0-9_]', '_', str(item.args[1])).upper()}"
                item.kwargs["define"] = renamed
                item.comments.append(
                    f"{', '.join(sorted(taken))} is already produced by an earlier probe; "
                    f"this check stays for {', '.join(sorted(referenced))}, its define renamed"
                )
                notes.append(
                    f"configure.ac: {check_key(item)} would produce {', '.join(sorted(taken))} a second "
                    f"time; kept for the checks requiring {', '.join(sorted(referenced))}, with its "
                    f"define renamed {renamed}"
                )
                seen_defines.add(renamed)
                continue
            target.checks.remove(item)
            notes.append(
                f"configure.ac: {check_key(item)} left out: {', '.join(sorted(taken))} is already produced "
                "by an earlier probe of this target (configure defines it once either way)"
            )
            continue
        seen_defines |= defines
    # Two unconditional substitutions of one name with different values
    # (libpcap's BISON_BYACC): the later assignment is what configure keeps.
    last_unconditional: dict[str, Call] = {}
    for item in list(target.checks):
        if (
            isinstance(item, Call)
            and item.fn == "checks.AC_SUBST"
            and len(item.args) == 2
            and not item.kwargs.get("requires")
            and "condition" not in item.kwargs
        ):
            earlier = last_unconditional.get(str(item.args[0]))
            if earlier is not None:
                target.checks.remove(earlier)
                notes.append(
                    f"configure.ac: {item.args[0]} is substituted twice ({earlier.args[1]!r}, then "
                    f"{item.args[1]!r}); the later value stands, as in configure"
                )
            last_unconditional[str(item.args[0])] = item
    # `X=true` under a condition, `X=false` under its negation (bison's
    # C_COMPILER_POSIXLY_CORRECT): one subst with if_true / if_false.
    by_name: dict[tuple[str, str], list[Call]] = {}
    for item in target.checks:
        if (
            isinstance(item, Call)
            and item.fn in ("checks.AC_SUBST", "checks.AC_DEFINE")
            and len(item.args) == 2
            and len(strings(item.kwargs.get("requires"))) == 1
            and "condition" not in item.kwargs
        ):
            by_name.setdefault((item.fn, str(item.args[0])), []).append(item)
    for (fn, name), items in by_name.items():
        if len(items) != 2:
            continue
        first, second = items
        c1, c2 = strings(first.kwargs["requires"])[0], strings(second.kwargs["requires"])[0]
        if c2 != f"!{c1}" and c1 != f"!{c2}":
            continue
        positive, negative = (first, second) if c2 == f"!{c1}" else (second, first)
        positive.kwargs.pop("requires")
        positive.kwargs.update(
            {
                "condition": positive.kwargs.get("condition") or (c1 if positive is first else c2),
                "if_true": positive.args[1],
                "if_false": negative.args[1],
            }
        )
        positive.args = [positive.args[0]]
        target.checks.remove(negative)
        notes.append(f"configure.ac: {name} is set on both branches of a condition; one conditional {fn.split('.')[1]}")
    return notes
