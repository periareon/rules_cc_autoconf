"""The cc_library and cc_binary targets of the automake tree: LDADD / LIBADD as deps, the
include-directory and gnulib dependencies, link order, program aliases."""

from __future__ import annotations

import os
import re
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk, lower
from tools.convert import libraries as registry_libraries
from tools.convert.common import (
    _LIBRARY_RE,
    _SOURCE_SUFFIXES,
    CONFIG_HEADERS,
    _compatible_with,
    _config_deps,
    _dict_literal,
    _list_literal,
    _platform_exceptions,
    headers_glob,
    source_label,
)
from tools.convert.emit import label_sort_key
from tools.convert.ir import AttrValue, Package, Raw, Rule, strings
from tools.convert.makefiles import (
    _am_block,
    _automake_tree,
    _configured_makefile_dirs,
    _expand_make_vars,
    _library_target,
    _parse_tree_makefile,
    make_var_name,
)
from tools.convert.sources import (
    COMPILED_SUFFIXES,
    _arm_selects,
    _built_sources,
    _conditional_sources,
    _copts_value,
    _defines_value,
    _directory_generated_headers,
    _generated_files,
    _generated_sources,
    _preprocessor_flags,
    _probe_conditioned_srcs,
    _record_generated_headers,
    _record_makefile_standards,
    _root_textual,
    _srcs_value,
    _textual_includes,
    _with_headers,
    am_arms,
    am_condition,
    am_decision,
    includes_value,
    single_arm,
)
from tools.convert.translate import ProjectState, describe, slug


def _public(installed: bool) -> dict[str, AttrValue]:
    """The `visibility` of what `make install` ships; nothing for the rest, which the project's
    visibility pass confines (`project._confine_visibility`)."""
    return {"visibility": ["//visibility:public"]} if installed else {}


def _link_vars(items: list[str], state: ProjectState) -> list[str]:
    """The `$(X_LIB)` variables of an LDADD / LIBADD line (a pkg-config `$(X_LIBS)` the registry
    answers is a dep instead, see `_link_items`).  A variable configure only ever sets to `-l`
    words of libraries the registry provides (`LIBLZMA` = `-llzma`, the module `xz` probed
    through the main target) names libraries that are every target's deps already
    (`configure_libs`): its linkopts would ask the linker for a system copy, so it is left out.
    The substitutions configure appends to LIBS for a system library (`configure_link_vars`,
    `-ldl` from AC_SEARCH_LIBS([dlopen], [dl])) come first, on every line: LIBS is on every link."""
    out: list[str] = list(state.configure_link_vars)
    for item in items:
        name = make_var_name(item)
        if name is not None and name not in out and name not in state.pkg_libs and not _registry_covered(name, state):
            out.append(name)
    return out


def _registry_covered(name: str, state: ProjectState) -> bool:
    """Whether every value configure substitutes for `name` is `-l` words of registry libraries
    (or empty), with at least one such word."""
    words = [word for value in state.subst_values.get(name, []) for word in value.split()]
    return bool(words) and all(word.startswith("-l") and word[2:] in state.configure_libs for word in words)


def _ensure_linkopts(package: Package, link_vars: list[str]) -> None:
    """The one `autoconf_linkopts` target every program and library links against."""
    rule = next((s for s in package.after if isinstance(s, Rule) and s.kind == "autoconf_linkopts"), None)
    if rule is None:
        package.after.append(
            Rule(
                "autoconf_linkopts",
                "linkopts",
                {"vars": list(link_vars), "deps": [":autoconf"]},
                comments=["Makefile.am: the $(X_LIB) variables the programs and libraries link against."],
                load_from="//autoconf:autoconf_linkopts.bzl",
            )
        )
    else:
        rule.attrs["vars"] = sorted(set(strings(rule.attrs["vars"])) | set(link_vars))


# `mpz/abs$U.lo`, `mpq/add.lo`, `rand/randmt.o`: an object file of another directory's sources
# (automake's `$U` is the ansi2knr suffix variable).
_OBJECT_RE = re.compile(r"^(?P<dir>[^/$@]+)/(?P<name>[^/$]+)(?:\$U|\$\(U\))?\.l?o$")


def _directory_library(directory: str, libraries: dict[str, str]) -> str | None:
    """The one library of `directory` among the archives `_programs_tree` emitted (keyed
    `dir/libfoo.la` and `libfoo.la`), or None when there is none or several."""
    labels = {label for archive, label in libraries.items() if archive.startswith(directory + "/")}
    return next(iter(labels)) if len(labels) == 1 else None


def _link_items(
    items: list[str],
    libraries: dict[str, str],
    notes: list[str],
    makefile_name: str,
    owner: str,
    decisions: dec.Decisions,
    state: ProjectState | None = None,
    dep_arms: dict[str, list[str]] | None = None,
    under_condition: bool = False,
) -> tuple[list[str], list[str]]:
    """An LDADD / LIBADD line as Bazel deps (libraries of the tree, registry modules for the
    `-l` names tools/convert/libraries.py knows) and linkopts (other `-l`, `-L`); anything else
    is a `link` decision.  With `state`, the configure-found registry libraries (LIBS) join every
    target's deps, and a pkg-config `$(X_LIBS)` is the registry library it stands for.  With
    `under_condition`, the line is one automake conditional's words and the caller puts the
    result in that conditional's select() arm: LIBS are not seeded again (they are in the
    unconditional deps already) and a library found under an option is not noted as linked
    unconditionally."""
    deps: list[str] = [] if state is None or under_condition else list(state.configure_libs.values())
    linkopts: list[str] = []
    directory = makefile_name[: -len("Makefile.am")].rstrip("/") or "."
    for item in items:
        library = _LIBRARY_RE.match(item)
        obj = _OBJECT_RE.match(item)
        directory_library = _directory_library(obj.group("dir"), libraries) if obj else None
        var = make_var_name(item)
        registry = registry_libraries.known(item) if item.startswith("-l") else None
        if registry is not None and state is not None and registry.module == state.module_name():
            registry = None  # the project itself (readline's examples link -lreadline): a tree library or a linkopt
        if library is not None and library.group(2) in libraries:
            if libraries[library.group(2)] not in deps:
                deps.append(libraries[library.group(2)])
        elif registry is not None:
            if registry.label not in deps:
                deps.append(registry.label)
            if state is not None:
                state.external_modules[registry.module] = registry.version
            notes.append(
                f"{makefile_name}: {owner} links {item}: the registry module {registry.module} ({registry.label})"
            )
        elif item.startswith(("-l", "-L")):
            linkopts.append(item)
        elif obj and directory_library:
            # gmp's `libgmp_la_LIBADD = mpz/abs$U.lo mpq/add.lo ...`: the objects of a subdirectory's
            # own library, listed one by one; the library they belong to is the dependency.
            if directory_library not in deps:
                deps.append(directory_library)
                notes.append(
                    f"{makefile_name}: {owner} links the objects of {obj.group('dir')}/ "
                    f"({item}, ...): a dependency on {directory_library}"
                )
        elif state is not None and var in state.pkg_libs:
            # `$(FUSE_LIBS)`: a package found through pkg-config; the registry library it stands
            # for.  Found under a configure option (grep's `$(PCRE_LIBS)` behind
            # --enable-perl-regexp), it is a dependency in that option's select() arm.
            labels = state.pkg_libs[var]
            arm = _option_arm(state.pkg_conditions.get(var), state)
            if arm is not None and dep_arms is not None:
                dep_arms.setdefault(arm, []).extend(label for label in labels if label not in dep_arms.get(arm, []))
            else:
                cond = state.pkg_conditions.get(var)
                if under_condition:
                    pass  # the caller's conditional is the arm
                elif arm is not None:
                    notes.append(
                        f"{makefile_name}: {owner} links {item} under a configure option, which a library's "
                        "deps do not model; linked unconditionally"
                    )
                elif cond is not None and cond is not df.TRUE:
                    notes.append(
                        f"{makefile_name}: {owner} links {item}, found under `{describe(cond)}`, which no "
                        "select() over one option expresses; linked unconditionally"
                    )
                deps += [label for label in labels if label not in deps]
        elif var is None:
            decision = decisions.resolve(
                dec.Request(
                    f"link:{directory}/{owner}:{item}",
                    "link",
                    makefile_name,
                    f"{owner} links {item}"
                    + (
                        ", a library the converter did not find"
                        if library
                        else ", which is neither a library of the tree nor a -l option"
                    ),
                    {},
                )
            )
            if decision is None or decision.ignore:
                notes.append(f"{makefile_name}: {owner} links {item}; left out (link decision missing or IGNORE)")
            elif decision.dep is not None:
                if decision.dep not in deps:
                    deps.append(decision.dep)
            elif decision.linkopt is not None:
                linkopts.append(decision.linkopt)
    return deps, linkopts


def _programs_tree(
    package: Package, src: Path, decisions: dec.Decisions, state: ProjectState, notes: list[str]
) -> None:
    """A cc_library per library and a cc_binary per program of every
    Makefile.am in the tree (the gnulib libraries themselves come from their gnulib.mk)."""
    gnulib_dirs = {lib.directory for lib in state.gnulib_libraries}
    tree = [
        (directory, makefile, text, _parse_tree_makefile(directory, makefile, text, src, state, notes))
        for directory, makefile, text in _automake_tree(
            src, set(state.config_subdirs), _configured_makefile_dirs(state)
        )
    ]
    libraries: dict[str, str] = {}  # archive basename -> label
    for lib in state.gnulib_libraries:
        libraries.setdefault(lib.archive, lib.label)
        libraries.setdefault(os.path.basename(lib.archive), lib.label)
        if lib.directory:
            libraries.setdefault(f"{lib.directory}/{lib.archive}", lib.label)
    emitted = []
    gates: dict[str, str] = {}  # library target name -> the config_setting it exists under
    taken = {
        f"{directory}/{p.name}" if directory else p.name
        for directory, _makefile, _text, mk in tree
        for p in mk.programs.values()
    }
    text_of = {directory: text for directory, _makefile, text, _mk in tree}
    for directory, makefile, _text, mk in tree:
        makefile_name = f"{directory}/{makefile.name}" if directory else makefile.name
        for canonical, library in mk.libraries.items():
            archive_dir = os.path.dirname(os.path.join(directory, library.archive)) if library.archive else directory
            if directory in gnulib_dirs or archive_dir in gnulib_dirs or not (library.sources or library.libadd):
                continue
            # teckit's `lib_LTLIBRARIES = libTECkit_x86.la` under `if OLD_LIB_NAMES`, else the plain
            # names: a library listed only inside an automake conditional exists as the
            # conditional does (ALWAYS, NEVER, or a config_setting it is compatible with).
            conds = sorted(
                {
                    c
                    for var, entries in mk.conditional_variables.items()
                    if var.endswith(("_LIBRARIES", "_LTLIBRARIES"))
                    for word, c in entries
                    if word == library.archive
                }
            )
            listed_plain = any(
                library.archive in words
                for var, words in mk.variables.items()
                if var.endswith(("_LIBRARIES", "_LTLIBRARIES"))
            )
            gate = None
            if conds and not listed_plain:
                if len(conds) > 1:
                    notes.append(
                        f"{makefile_name}: {library.archive} is listed under {len(conds)} conditionals "
                        f"({', '.join(conds)}); built unconditionally"
                    )
                else:
                    verdict, gate = _resolve_conditional(conds[0], state, decisions)
                    if verdict is False:
                        notes.append(
                            f"{makefile_name}: {library.archive} left out (`if {conds[0]}` is never true here)"
                        )
                        continue
                    if verdict is not True and gate is None:
                        decision = decisions.resolve(
                            dec.Request(
                                f"am_conditional:{conds[0]}",
                                "am_conditional",
                                f"{makefile_name} ({library.archive})",
                                f"{library.archive} is built only `if {conds[0]}`",
                                _am_block(_text, makefile_name, conds[0]),
                            )
                        )
                        if decision is None or decision.never:
                            notes.append(
                                f"{makefile_name}: {library.archive} left out (`if {conds[0]}` undecided or never)"
                            )
                            continue
                        gate = decision.arm
            name, alias = _library_target(directory, library.archive, taken)
            if gate:
                gates[name] = gate
            taken.add(name)
            libraries.setdefault(library.archive, f":{name}")
            # LDADD names it by file (`sed/libver.a` as `libver.a` in _LIBRARY_RE's
            # terms): the archive name alone finds it too.
            libraries.setdefault(os.path.basename(library.archive), f":{name}")
            if directory:  # the objects of its directory, listed in another Makefile's LIBADD (_OBJECT_RE)
                libraries.setdefault(f"{directory}/{library.archive}", f":{name}")
            emitted.append((directory, mk, canonical, library, name, alias))
    # Public headers: what automake installs.  A Makefile.am that installs
    # headers but builds no library (libssh2's top level, for include/) hands
    # them to the tree's one installed library, or to a shared `exported_headers`.
    installed_by_dir = {
        directory: _installed_headers(mk, directory, src)
        for directory, _m, _t, mk in tree
        if directory not in gnulib_dirs
    }
    hdrs_of: dict[str, list[str]] = {
        name: list(installed_by_dir.get(directory, [])) for directory, _mk, _c, _lib, name, _a in emitted
    }
    orphans = [
        h
        for directory, headers in installed_by_dir.items()
        if not any(d == directory for d, *_ in emitted)
        for h in headers
    ]
    installed_libs = [name for _d, _mk, _c, lib, name, _a in emitted if lib.installed]
    exported_headers_lib = None
    if orphans and len(installed_libs) == 1:
        hdrs_of[installed_libs[0]] += [h for h in orphans if h not in hdrs_of[installed_libs[0]]]
    elif orphans:
        exported_headers_lib = "exported_headers"
        package.after.append(
            Rule(
                "cc_library",
                exported_headers_lib,
                {
                    "hdrs": sorted(orphans),
                    "includes": sorted({os.path.dirname(h) or "." for h in orphans}),
                    "visibility": ["//visibility:public"],
                },
                comments=["The headers automake installs from directories that build no library."],
                load_from="@rules_cc//cc:cc_library.bzl",
            )
        )
    # What each dependency exports, for the deps / implementation_deps split.
    exports: dict[str, set[str]] = {}
    if src is not None:
        top_headers = {f.name for f in src.glob("*.h")}
        for label in _config_deps(state):
            exports[label] = {Path(h).name for h in state.config_headers + state.subst_headers} | top_headers
        for lib in state.gnulib_libraries:
            if lib.directory:
                exports[lib.label] = {
                    f.relative_to(src / lib.directory).as_posix().replace(".in.h", ".h")
                    for f in (src / lib.directory).rglob("*.h")
                }
            exports[lib.private_headers_label] = set()
        exports[":linkopts"] = set()
        for _d, _mk, _c, _lib, name, _a in emitted:
            exports[f":{name}"] = set(hdrs_of[name])
        if exported_headers_lib:
            exports[f":{exported_headers_lib}"] = set(orphans)
    # The headers of a library that automake does not install: a
    # `<name>_private_headers` library the library compiles with, which the
    # project's own programs and tests may depend on too (automake's -I. lets
    # them include anything; pkgconf's tests include libpkgconf/path.h).
    private_of: dict[str, tuple[list[str], list[str]]] = {}  # name -> (directories, public headers under them)
    for directory, _mk, _c, library, name, _a in emitted:
        paths = [s.path for s in library.sources]
        dirs = sorted(
            {directory}
            if directory
            else {f"{directory}/{p}" if directory else p for p in {q.rsplit("/", 1)[0] for q in paths if "/" in q}}
        )
        found = sorted(
            h.relative_to(src).as_posix() for d in dirs if (src / d).is_dir() for h in (src / d).rglob("*.h")
        )
        public = set(hdrs_of[name])
        private = [h for h in found if h not in public and not h.endswith(".in.h")]
        if private:
            private_of[name] = (dirs, sorted(h for h in public if any(h.startswith(d + "/") for d in dirs)))
            state.private_header_libs[f":{name}"] = f":{name}_private_headers"
            exports[f":{name}_private_headers"] = set(private)
    # The order of the archives on every LDADD / LIBADD line is their link order: an archive
    # depends on the ones listed after it (grub's libgrubmods.a needs libgrubkern.a, which needs
    # libgrubgcry.a), so the static link resolves their symbols whatever order the deps take.
    links_after = _link_order_edges(tree, libraries)
    rule_names: set[str] = set()  # the names of the rules in package.after, kept up to date below
    scanned = 0
    # Libraries first: programs anywhere may link them.
    for directory, mk, canonical, library, name, alias in emitted:
        prefix = f"{directory}/" if directory else ""
        makefile_name = f"{prefix}Makefile.am"
        included = [s for s in library.sources if s.condition is None and not s.extra]
        for s in library.sources:
            if s.extra and s.condition is None:
                notes.append(
                    f"{makefile_name}: {s.path}: EXTRA_{library.archive}_SOURCES, compiled only through a "
                    "substituted object list; left out"
                )
        extra, arms, probe, probe_arms = _conditional_sources(
            library.sources, state, decisions, makefile_name, library.archive, text_of[directory], prefix, notes
        )
        included += extra
        srcs = sorted(source_label(prefix, s.path) for s in included if s.path.endswith(_SOURCE_SUFFIXES))
        probe_label, probe_arm_labels = _probe_conditioned_srcs(package, name, probe, probe_arms)
        if probe_label:
            srcs.append(probe_label)
        for arm_label, arm_target in probe_arm_labels.items():
            arms.setdefault(arm_label, []).append(arm_target)
        generated, gen_includes = _generated_sources(
            package, mk, canonical, included, prefix, src=src, state=state, notes=notes
        )
        written, excluded = _generated_files(
            package, mk, prefix, included + _built_sources(mk, included), src, notes, decisions, library.archive, state
        )
        _record_generated_headers(written, state)
        srcs = [p for p in srcs if p not in excluded]
        generated += [g for g in written if g not in srcs]
        items = _expand_make_vars(library.libadd, mk)
        link_vars = _link_vars(items, state)
        if link_vars:
            _ensure_linkopts(package, link_vars)
        deps, linkopts = _link_items(items, libraries, notes, makefile_name, library.archive, decisions, state)
        # `pppoatm_la_LIBADD = -latm` under `if WITH_LIBATM`: the words join as the conditional
        # resolves (an am_conditional decision, or a flag-free condition that lowers to a
        # constant); an arm a select() would need is reported, not modeled for a library.
        for word, cond in mk.conditional_variables.get(f"{canonical}_LIBADD", []):
            verdict, _arm = _resolve_conditional(cond, state, decisions)
            if verdict is True:
                arm_deps, arm_linkopts = _link_items(
                    [word], libraries, notes, makefile_name, library.archive, decisions, state
                )
                deps += [d for d in arm_deps if d not in deps]
                linkopts += [o for o in arm_linkopts if o not in linkopts]
            elif verdict is False:
                notes.append(
                    f"{makefile_name}: {library.archive} links {word} under `if {cond}`, which is never true here"
                )
            else:
                notes.append(f"{makefile_name}: {library.archive} links {word} under `if {cond}`; left out ({verdict})")
        # The gnulib libraries this archive's own LIBADD names, before the link-order edges below
        # add the ones that merely follow it on a program's LDADD (see `_gnulib_deps`).
        linked_gnulib = {lib.label for lib in state.gnulib_libraries if lib.label in deps}
        if probe_label:
            deps.append(probe_label)  # the conditional sources reach the compiler through deps
        deps += [t for t in probe_arm_labels.values() if t not in deps]  # the arm sources' headers, every platform
        deps += [d for d in links_after.get(f":{name}", []) if d not in deps]
        deps += [d for d in _config_deps(state) if d not in deps]
        if link_vars:
            deps.append(":linkopts")
        if exported_headers_lib and f":{exported_headers_lib}" not in deps:
            deps.append(f":{exported_headers_lib}")
        public_headers = hdrs_of[name]
        attrs: dict[str, AttrValue] = {}
        textual = _textual_includes(src, directory, [s.path for s in included])
        # Public interface: the installed headers.  The directory's other
        # headers are private: they compile with the sources (`srcs`) and
        # never reach a consumer, which also keeps their names from colliding
        # with anything the consumer includes.
        in_arms = [p for paths in arms.values() for p in paths]
        srcs_all = sorted(
            set(srcs + generated) | _directory_generated_headers(srcs + in_arms, state), key=label_sort_key
        )
        attrs["srcs"] = _srcs_value(srcs_all, arms, package)
        if textual and directory:
            attrs["textual_hdrs"] = textual
        elif textual:  # the top-level headers come with CONFIG_HEADERS
            _root_textual(package, textual, notes)
        if name in private_of:
            private_dirs, public_under = private_of[name]
            private_lib = f"{name}_private_headers"
            package.after.append(
                Rule(
                    "cc_library",
                    private_lib,
                    {
                        "hdrs": headers_glob([f"{d}/**/*.h" for d in private_dirs], exclude=public_under),
                        # The module's own test packages (tests/, as the overlays lay them out)
                        # include private headers as upstream's -I$(top_srcdir)/lib lets them.
                        "includes": private_dirs,
                        "visibility": ["//:__subpackages__"],
                    },
                    comments=[
                        (
                            f"{makefile_name}: the headers of {library.archive} that are not installed.  Targets of "
                            "this project that include them depend on it, as automake's -I lets them."
                        )
                    ],
                    load_from="@rules_cc//cc:cc_library.bzl",
                )
            )
            deps.append(f":{private_lib}")
        deps += [p for lib, p in state.private_header_libs.items() if lib in deps and p not in deps]
        if public_headers:
            attrs["hdrs"] = sorted(public_headers)
        include_dirs = sorted(
            {os.path.dirname(h) or "." for h in public_headers} | ({directory} if directory else set())
        )
        if include_dirs:
            attrs["includes"] = include_dirs
        defines, flag_copts, standards, flag_includes, copt_arms, define_arms, include_arms = _preprocessor_flags(
            mk, canonical, directory, notes, state, decisions
        )
        _record_makefile_standards(standards, state)
        deps = _gnulib_deps(state, deps, directory, flag_includes, fallback=True, linked=linked_gnulib)
        include_deps, flag_includes = _include_dir_deps(package, flag_includes, src, exports, notes, directory)
        deps += [d for d in include_deps if d not in deps]
        if flag_includes or gen_includes or include_arms:
            attrs["includes"] = includes_value(
                sorted(set(strings(attrs.get("includes"))) | set(flag_includes) | set(gen_includes)), include_arms
            )
        public_deps, implementation_deps = _split_deps(
            sorted(deps, key=label_sort_key), exports, _includes_of(src, public_headers)
        )
        if library.include_prefix and public_headers:
            # The installed headers under their install subdirectory, as consumers include
            # them (`<readline/readline.h>`); the library's own sources keep the bare names.
            prefixed = f"{name}_prefixed_headers"
            package.after.append(
                Rule(
                    "cc_library",
                    prefixed,
                    {
                        "hdrs": sorted(public_headers),
                        "include_prefix": library.include_prefix,
                        "visibility": ["//visibility:public"],
                    },
                    comments=[
                        (
                            f"{makefile_name}: the headers {library.archive} installs under "
                            f"$(includedir)/{library.include_prefix}, as `#include <{library.include_prefix}/...>`."
                        )
                    ],
                    load_from="@rules_cc//cc:cc_library.bzl",
                )
            )
            public_deps.append(f":{prefixed}")
        attrs.update(
            {
                "copts": _copts_value(flag_copts, copt_arms),
                "local_defines": _defines_value(["HAVE_CONFIG_H"] + defines, define_arms),
                **_public(library.installed),
            }
        )
        if public_deps:
            attrs["deps"] = public_deps
        if implementation_deps:
            attrs["implementation_deps"] = implementation_deps
        if linkopts:
            attrs["linkopts"] = linkopts
        if name in gates:
            attrs["target_compatible_with"] = _compatible_with([gates[name]], only=True)
        shared = library.archive.endswith(".la") and library.installed
        if shared:
            # The cc_shared_library below writes lib<name>.so; the cc_library
            # must not write its own copy of that file (rules_cc builds one
            # for a cc_library unless it is linkstatic).
            attrs["linkstatic"] = True
        package.after.append(
            Rule(
                "cc_library",
                name,
                attrs,
                comments=[f"{makefile_name}: {library.archive}."],
                load_from="@rules_cc//cc:cc_library.bzl",
            )
        )
        if shared:
            # A libtool library that gets installed is a shared library (a
            # `-module` one a plugin); cc_shared_library wraps the cc_library.
            ldflags = _expand_make_vars(
                mk.variables.get(f"{canonical}_LDFLAGS", []) + mk.variables.get("AM_LDFLAGS", []), mk
            )
            module = "-module" in ldflags
            stem = library.archive[: -len(".la")]
            shared_name = _dict_literal(
                {  # a bare name lands in the package directory: keep the subdirectory
                    "@platforms//os:windows": f"{prefix}{stem[3:] if stem.startswith('lib') and not module else stem}.dll",
                    "@platforms//os:macos": f"{prefix}{stem}.so" if module else f"{prefix}{stem}.dylib",
                    lower.DEFAULT_ARM: f"{prefix}{stem}.so",
                },
                "    ",
            )
            package.after.append(
                Rule(
                    "cc_shared_library",
                    f"{name}_shared",
                    {
                        "shared_lib_name": Raw(f"select({shared_name})"),
                        "deps": [f":{name}"],
                        "visibility": ["//visibility:public"],
                    },
                    comments=[
                        (
                            f"{makefile_name}: {library.archive} is a libtool {'module' if module else 'library'}: "
                            "the shared object automake installs."
                        )
                    ],
                    load_from="@rules_cc//cc:cc_shared_library.bzl",
                )
            )
        if alias:
            package.after.append(
                Rule(
                    "alias",
                    alias,
                    {"actual": f":{name}", **_public(library.installed)},
                    comments=[
                        (
                            f"{library.archive} under its archive name: a program takes `{name.replace('lib/', '', 1)}`, "
                            f"and rules_cc names the output of `{name}` {library.archive.rsplit('.', 1)[0]}.a."
                        )
                    ],
                )
            )
        # `@mpfr//:mpfr` for `src/mpfr`: the registry's modules name the library
        # after the module at the root; an alias keeps that address when free.
        bare = name.rsplit("/", 1)[-1]
        rule_names |= {item.name for item in package.after[scanned:] if isinstance(item, Rule)}
        scanned = len(package.after)
        if "/" in name and bare not in rule_names and bare not in taken:
            taken.add(bare)
            package.after.append(
                Rule(
                    "alias",
                    bare,
                    {"actual": f":{name}", **_public(library.installed)},
                    comments=[f"{bare} at the root, as the registry addresses a module's library."],
                )
            )
    for directory, _makefile, text, mk in tree:
        if mk.programs:
            _programs(
                package,
                mk,
                decisions,
                state,
                notes,
                text,
                prefix=f"{directory}/" if directory else "",
                libraries=libraries,
                src=src,
            )
    _convenience_aliases(package)


def _gnulib_deps(
    state: ProjectState,
    deps: list[str],
    directory: str,
    flag_includes: list[str],
    *,
    fallback: bool,
    linked: set[str] | None = None,
) -> list[str]:
    """`deps` with each gnulib library and its private headers added, or removed, by whether the
    Makefile puts gnulib's headers on the target's include path: `-I` for the gnulib directory,
    the target's own directory being under it, or the archive on its own LIBADD / LDADD line.
    With `fallback`, a Makefile that spells no `-I` at all is taken to reach them, as the flags
    may be configure's (with one gnulib library; several would double every symbol).  Otherwise
    the target is built without gnulib, as upstream builds it, and a link-order edge to the
    archive (a program's LDADD lists libgnu.a after this archive) is dropped too: a dependency
    carries headers, and grub's libgrubgcry.a does not compile with gnulib's stdlib.h ahead of
    its libc wrappers'.  The link order follows from the archives that do use gnulib.  `linked`
    names the gnulib libraries the target's own LIBADD / LDADD listed, when the caller has since
    added link-order edges to `deps` (an archive listed after libgnu.a is not thereby linking it)."""
    out = list(deps)
    for lib in state.gnulib_libraries:
        linked_here = lib.label in (linked if linked is not None else deps)
        on_path = (
            lib.directory in flag_includes or directory == lib.directory or directory.startswith(lib.directory + "/")
        )
        assumed = fallback and not flag_includes and len(state.gnulib_libraries) == 1
        if not (linked_here or on_path or assumed):
            out = [d for d in out if d not in (lib.label, lib.private_headers_label)]
            continue
        if lib.label not in out:
            out.append(lib.label)
        # The gnulib library's private headers too (coreutils' src/cksum_avx2.c includes system.h,
        # which includes lib/attribute.h).
        if lib.private_headers_label not in out:
            out.append(lib.private_headers_label)
    return out


def _include_dir_deps(
    package: Package, dirs: list[str], src: Path, exports: dict[str, set[str]], notes: list[str], makefile_dir: str = ""
) -> tuple[list[str], list[str]]:
    """A Makefile's `-I<dir>` directories as dependencies: for a source directory that holds
    headers, a `cc_library` of those headers with the directory as its include path (one per
    directory, shared; an existing library serving the directory is reused), so the headers are
    declared for the sandbox and the directory is private to the targets that asked for it
    (`implementation_deps` of a library) rather than exported through `includes` to everything
    that links it.  Returns (dependency labels, the directories left for `includes`: the root,
    generated directories, directories without headers)."""
    deps: list[str] = []
    remaining: list[str] = []
    for directory in dirs:
        if directory == "." and makefile_dir:
            # A subdirectory's Makefile.am with -I$(top_srcdir) (ppp's plugins include
            # <pppd/pppd.h>): the tree's headers by their repository paths, which
            # `includes = ["."]` cannot say at the root; one shared library serves them.
            name = "top_srcdir_headers"
            if not any(isinstance(r, Rule) and r.name == name for r in package.after):
                package.after.append(
                    Rule(
                        "cc_library",
                        name,
                        {
                            "hdrs": headers_glob(["**/*.h"]),
                            "strip_include_prefix": "/",
                            "visibility": ["//:__subpackages__"],
                        },
                        comments=[
                            (
                                "The tree's headers by repository path, for the Makefiles whose -I$(top_srcdir) "
                                "lets their sources include <dir/header.h>."
                            )
                        ],
                        load_from="@rules_cc//cc:cc_library.bzl",
                    )
                )
            if f":{name}" not in deps:
                deps.append(f":{name}")
            continue
        if directory == "." or not (src / directory).is_dir() or not any((src / directory).rglob("*.h")):
            remaining.append(directory)
            continue
        existing = next(
            (
                r
                for r in package.after
                if isinstance(r, Rule)
                and r.kind == "cc_library"
                and "hdrs" in r.attrs
                and r.attrs.get("includes") == [directory]
                and "include_prefix" not in r.attrs
            ),
            None,
        )
        if existing is None:
            name = f"{slug(directory)}_includes"
            existing = Rule(
                "cc_library",
                name,
                {
                    "hdrs": headers_glob([f"{directory}/**/*.h"]),
                    "includes": [directory],
                    "visibility": ["//:__subpackages__"],
                },
                comments=[
                    (
                        f"The headers under {directory}/, which the Makefiles' -I$(top_srcdir)/{directory} "
                        "put on the include path of the targets that depend on this."
                    )
                ],
                load_from="@rules_cc//cc:cc_library.bzl",
            )
            package.after.append(existing)
            exports[f":{name}"] = {h.name for h in (src / directory).rglob("*.h")}
        if f":{existing.name}" not in deps:
            deps.append(f":{existing.name}")
    return deps, remaining


def _link_order_edges(
    tree: list[tuple[str, Path, str, gnulibmk.Makefile]], libraries: dict[str, str]
) -> dict[str, list[str]]:
    """For each archive of the tree, the archives every LDADD / LIBADD line lists after it: the
    dependencies that keep a static link in the order the Makefiles use.  An edge that would
    close a cycle (two lines in opposite orders) is not added."""
    edges: dict[str, list[str]] = {}

    def reaches(start: str, goal: str, seen: set[str]) -> bool:
        if start == goal:
            return True
        seen.add(start)
        return any(reaches(n, goal, seen) for n in edges.get(start, []) if n not in seen)

    for _directory, _makefile, _text, mk in tree:
        lines = [p.ldadd or mk.variables.get("LDADD", []) for p in mk.programs.values()]
        lines += [lib.libadd for lib in mk.libraries.values()]
        for items in lines:
            labels: list[str] = []
            for item in _expand_make_vars(list(items), mk):
                match = _LIBRARY_RE.match(item)
                if match and match.group(2) in libraries and libraries[match.group(2)] not in labels:
                    labels.append(libraries[match.group(2)])
            for i, before in enumerate(labels):
                for after in labels[i + 1 :]:
                    if after not in edges.get(before, []) and not reaches(after, before, set()):
                        edges.setdefault(before, []).append(after)
    return edges


_INCLUDE_DIRECTIVE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)


def _header_path(path: str, directory: str) -> str | None:
    """A `*_HEADERS` entry as a path from the project root (`$(top_srcdir)/include/x.h`,
    `../include/x.h`, `x.h`), or None when make would have to expand it."""
    for variable, base in (
        ("$(top_srcdir)/", ""),
        ("$(top_builddir)/", ""),
        ("$(srcdir)/", directory),
        ("$(builddir)/", directory),
    ):
        if path.startswith(variable):
            path = os.path.join(base, path[len(variable) :])
            break
    else:
        path = os.path.join(directory, path)
    if "$(" in path:
        return None
    path = os.path.normpath(path).replace(os.sep, "/")
    return None if path.startswith("..") else path


def _installed_headers(mk: gnulibmk.Makefile, directory: str, src: Path) -> list[str]:
    """The headers a Makefile.am installs, as paths from the project root, that exist."""
    out = []
    for header in mk.installed_headers:
        if header.condition is not None or not header.path.endswith((".h", ".hh", ".hpp", ".hxx")):
            continue
        path = _header_path(header.path, directory)
        if path and (src / path).is_file() and path not in out:
            out.append(path)
    return out


def _includes_of(src: Path, headers: list[str]) -> set[str]:
    """What a set of public headers `#include`: each name as written and its basename."""
    names: set[str] = set()
    for header in headers:
        file = src / header
        if not file.is_file():
            continue
        for included in _INCLUDE_DIRECTIVE_RE.findall(file.read_text(encoding="utf-8", errors="replace")):
            names.add(included)
            names.add(included.rsplit("/", 1)[-1])
    return names


def _option_arm(cond: df.Cond | None, state: ProjectState) -> str | None:
    """The one select() arm a condition over a configure option holds in (its default arm
    false), or None for an unconditional one, or one no select() expresses."""
    if cond is None or cond is df.TRUE:
        return None
    arms = lower.flag_arms(cond, state)
    if arms and len(arms) == 2 and arms[lower.DEFAULT_ARM] is df.FALSE:
        label = next(l for l in arms if l != lower.DEFAULT_ARM)
        if arms[label] is df.TRUE:
            return label
    return None


def _single_arm_label(label: str | None) -> str | None:
    """`label` unless it is a negated conditional's complement (`!<label>`), which is no single
    arm here: a library gated on it is a decision."""
    return None if label is not None and label.startswith("!") else label


def _resolve_conditional(cond: str, state: ProjectState, decisions: dec.Decisions) -> tuple[bool | str, str | None]:
    """Whether an automake conditional holds here, and the one select() arm it holds in otherwise.

    The verdict is True / False when an `am_conditional:` decision says so (ALWAYS / NEVER) or
    the condition lowers to a constant without a configure option in it; otherwise the reason it
    cannot be settled (a string), with the arm label: a decided select, or the arm a condition
    over one configure option or the host OS lowers to (None when there is no single arm; see
    `_single_arm_label`).  The arm is None with a True / False verdict."""
    decision = am_decision(cond, decisions)
    if decision is not None:
        if decision.always:
            return True, None
        if decision.never:
            return False, None
        return f"am_conditional:{cond} is a select() arm", _single_arm_label(decision.arm)
    lowered = am_condition(cond, state)
    if lowered is None:
        return f"am_conditional:{cond} undecided", None
    arms = am_arms(lowered, state)
    if arms and all(c is df.TRUE for c in arms.values()):
        return True, None
    if arms and all(c is df.FALSE for c in arms.values()):
        return False, None
    return f"{cond} depends on a configure option or a probe", _single_arm_label(single_arm(arms))


def _linkopts_value(linkopts: list[str], arms: dict[str, list[str]]) -> list[str] | Raw:
    """A target's linkopts: the list, plus one `select()` per conditional whose options apply in
    one arm."""
    if not arms:
        return linkopts
    return Raw(_list_literal(linkopts, "    ") + " + " + " + ".join(_arm_selects(arms, list)))


def _split_deps(
    deps: list[str], exports: dict[str, set[str]], public_includes: set[str]
) -> tuple[list[str], list[str]]:
    """`deps` and `implementation_deps` of a cc_library: a dependency whose headers one of
    the library's public headers includes stays a dep (consumers need it to compile); one
    the sources alone use becomes an implementation dep, so its headers do not reach
    consumers.  A dependency whose headers are unknown (one a decision named) stays a dep."""
    public, private = [], []
    for dep in deps:
        exported = exports.get(dep)
        if exported is None and dep.startswith("@"):
            # A registry library (`@xz//:lzma`): its headers are guessed from the label.  It
            # stays an implementation detail unless a public header includes one, so that its
            # own include directories (xz publishes a config.h) come after the project's.
            module, _, target = dep[1:].partition("//")
            target = target.rsplit(":", 1)[-1]
            exported = {f"{target}.h", f"{module}.h", f"{module}/{module}.h", f"{target}/{target}.h"}
        if exported is not None and dep == f":{CONFIG_HEADERS}":
            # The config headers library also globs the root's other headers (TOP_HEADERS), which
            # a public header may include; only the configuration headers themselves make it
            # public, or a consumer's `"config.h"` finds this project's first (libpcap's reached
            # ppp's plugins through -iquote ahead of their own).
            exported = {e for e in exported if "config" in e.rsplit("/", 1)[-1] or e.endswith("conf.h")}
        if exported is None or exported & public_includes or {e.rsplit("/", 1)[-1] for e in exported} & public_includes:
            public.append(dep)
        else:
            private.append(dep)
    return public, private


def _convenience_aliases(package: Package) -> None:
    """`grep` for `src/grep`, `gpiod` for `lib/gpiod`: a top-level alias for
    every program and library of a subdirectory whose base name is free."""
    taken = {s.name for s in package.after if isinstance(s, Rule)} | {t.name for t in package.targets}
    candidates: dict[str, list[str]] = {}
    for kind in ("cc_binary", "cc_library"):  # programs first: `grep` means the program
        for rule in package.after:
            if isinstance(rule, Rule) and rule.kind == kind and "/" in rule.name:
                candidates.setdefault(rule.name.rsplit("/", 1)[1], []).append(rule.name)
    internal = (
        "headers",
        "_private_headers",
        "_gnulib_conditional_srcs",
        "_gnulib_conditional_hdrs",
        "_gnulib_generated_hdrs",
        "_shared",
    )
    for base, names in candidates.items():
        if base in taken or len(names) != 1 or base.endswith(internal):
            continue
        taken.add(base)
        # The alias is as visible as what it names: public for an installed library or program.
        aliased = next(rule for rule in package.after if isinstance(rule, Rule) and rule.name == names[0])
        attrs: dict[str, AttrValue] = {"actual": f":{names[0]}"}
        if "visibility" in aliased.attrs:
            attrs["visibility"] = aliased.attrs["visibility"]
        package.after.append(
            Rule(
                "alias",
                base,
                attrs,
                comments=[f"Convenience alias for {names[0]}."],
            )
        )


def _programs(
    package: Package,
    mk: gnulibmk.Makefile,
    decisions: dec.Decisions,
    state: ProjectState,
    notes: list[str],
    am_text: str,
    src: Path,
    prefix: str = "",
    libraries: dict[str, str] | None = None,
) -> None:
    """The programs of one Makefile.am as `cc_binary` targets: sources (conditional ones through
    `am_conditional` decisions, yacc/lex ones through bison()/flex(), generated ones through
    their recipes or `generated:` decisions, textually included .c files as textual_hdrs of the
    directory's headers library), LDADD as deps, linkopts and the shared `autoconf_linkopts`,
    DEFS and CPPFLAGS as local_defines and copts.
    """
    libraries = libraries or {}
    makefile_name = f"{prefix}Makefile.am"
    link_vars: list[str] = []
    link_items: dict[str, list[str]] = {}
    for canonical, program in mk.programs.items():
        items = _expand_make_vars(program.ldadd or mk.variables.get("LDADD", []), mk)
        link_items[canonical] = items
        link_vars += [v for v in _link_vars(items, state) if v not in link_vars]
    if link_vars:
        _ensure_linkopts(package, link_vars)
    headers_lib = None
    if prefix:
        headers_lib = f"{prefix}headers"
        headers_attrs: dict[str, AttrValue] = {
            "hdrs": headers_glob([f"{prefix}**/*.h"]),
            "includes": [prefix.rstrip("/")],
        }
        if _config_deps(state):
            headers_attrs["deps"] = _config_deps(state)
        package.after.append(
            Rule(
                "cc_library",
                headers_lib,
                headers_attrs,
                comments=[f"{makefile_name}: the headers next to the programs."],
                load_from="@rules_cc//cc:cc_library.bzl",
            )
        )
    rule_names: set[str] = set()  # the names of the rules in package.after, kept up to date below
    scanned = 0
    for canonical, program in mk.programs.items():
        build_attrs = _substituted_program_attrs(program, mk, state, decisions, notes, makefile_name, prefix)
        if build_attrs is None:
            continue
        if program.default_sources and not (src / prefix / program.sources[0].path).is_file():
            notes.append(
                f"{makefile_name}: {program.name} has no _SOURCES and automake's default "
                f"{program.sources[0].path} does not exist; no target emitted"
            )
            continue
        included = [
            s for s in program.sources if s.condition is None and not s.extra and s.path.endswith(COMPILED_SUFFIXES)
        ]
        for s in program.sources:
            if s.extra and s.condition is None:
                notes.append(
                    f"{makefile_name}: {s.path}: EXTRA_{program.name}_SOURCES, compiled only through a "
                    "substituted object list; left out"
                )
        comments = [f"{makefile_name}: {program.name}."]
        # `if COND_GRUB_MOUNT` / `bin_PROGRAMS += grub-mount`: a program listed under a conditional
        # alone exists only when the conditional holds, whatever its sources' own conditions.
        gate = None
        program_conds = sorted(
            {
                c
                for var, entries in mk.conditional_variables.items()
                if var.endswith("_PROGRAMS")
                for word, c in entries
                if word == program.name
            }
        )
        listed_plain = any(program.name in words for var, words in mk.variables.items() if var.endswith("_PROGRAMS"))
        if program_conds and not listed_plain and "target_compatible_with" not in build_attrs:
            if len(program_conds) > 1:
                notes.append(
                    f"{makefile_name}: {program.name} is listed under {len(program_conds)} conditionals "
                    f"({', '.join(program_conds)}); built unconditionally"
                )
            else:
                cond = program_conds[0]
                decision = decisions.resolve(
                    dec.Request(
                        f"am_conditional:{cond}",
                        "am_conditional",
                        f"{makefile_name} ({program.name})",
                        f"{program.name} is built only `if {cond}`",
                        _am_block(am_text, makefile_name, cond),
                    )
                )
                if decision is None or decision.never:
                    notes.append(f"{makefile_name}: {program.name} left out (`if {cond}` undecided or never)")
                    continue
                if decision.arm is not None:
                    gate = decision.arm
        extra, arms, probe, probe_arms = _conditional_sources(
            program.sources, state, decisions, makefile_name, program.name, am_text, prefix, notes
        )
        included += extra
        srcs = sorted(source_label(prefix, s.path) for s in included if s.path.endswith(_SOURCE_SUFFIXES))
        probe_label, probe_arm_labels = _probe_conditioned_srcs(package, f"{prefix}{program.name}", probe, probe_arms)
        if probe_label:
            srcs.append(probe_label)
        for arm_label, arm_target in probe_arm_labels.items():
            arms.setdefault(arm_label, []).append(arm_target)
        generated, gen_includes = _generated_sources(
            package, mk, canonical, included, prefix, src=src, state=state, notes=notes
        )
        srcs += generated
        written, excluded = _generated_files(
            package, mk, prefix, included + _built_sources(mk, included), src, notes, decisions, program.name, state
        )
        _record_generated_headers(written, state)
        srcs = [p for p in srcs if p not in excluded] + [g for g in written if g not in srcs]
        srcs += sorted(
            _directory_generated_headers(srcs + [p for paths in arms.values() for p in paths], state) - set(srcs)
        )
        if (
            len(arms) == 1
            and "target_compatible_with" not in build_attrs
            and (
                gate in (None, next(iter(arms)))
                and not any(s.endswith(_SOURCE_SUFFIXES) for s in srcs)
                or gate == next(iter(arms))
            )
        ):
            # grub-mount: every source sits under `if COND_GRUB_MOUNT`, decided as a config
            # setting.  A select() with an empty default arm would be a binary with no sources
            # when the option is off; the program exists only when it is on.
            ((gate, gated),) = arms.items()
            srcs = sorted(set(srcs) | set(gated), key=label_sort_key)
            arms = {}
        if gate is not None and "target_compatible_with" not in build_attrs:
            build_attrs["target_compatible_with"] = _compatible_with([gate], only=True)
            comments.append(f"Built when {gate} is set: the program is under that conditional.")
        if not srcs:
            # gawk's grcat: its only source is generated by a recipe left out by decision.
            notes.append(f"{makefile_name}: {program.name} has no source left to compile; no target emitted")
            continue
        textual = _textual_includes(src, prefix.rstrip("/"), [s.path for s in included])
        if textual and headers_lib:
            rule = next(s for s in package.after if isinstance(s, Rule) and s.name == headers_lib)
            rule.attrs["textual_hdrs"] = sorted(set(strings(rule.attrs.get("textual_hdrs"))) | set(textual))
        elif textual:
            _root_textual(package, textual, notes)
        srcs_value = _srcs_value(sorted(srcs, key=label_sort_key), arms, package)
        dep_arms: dict[str, list[str]] = {}
        deps, linkopts = _link_items(
            link_items.get(canonical, []), libraries, notes, makefile_name, program.name, decisions, state, dep_arms
        )
        # automake's -I. lets tests/api/test-api.c include "tests/win-shim.h": the whole
        # top-level directory of each source comes along (below, once the flags are known).
        source_dirs = sorted({p.split("/", 1)[0] for p in srcs if "/" in p}) if not headers_lib else []
        # `src_cksum_LDADD += $(cksum_pclmul_ldadd)` under `if USE_PCLMUL_CRC32`: the words
        # join the deps as the am_conditional decision says (ALWAYS, or in a select() arm).
        # ppp's `pppd_LIBS += $(CRYPT_LIBS)` under `if LINUX` with `pppd_LDADD = $(pppd_LIBS)`:
        # the conditional words of a helper variable LDADD expands count like LDADD's own.
        entries = list(mk.conditional_variables.get(f"{canonical}_LDADD", []))
        for raw in program.ldadd or mk.variables.get("LDADD", []):
            helper = make_var_name(raw)
            if helper is not None and helper in mk.conditional_variables and not helper.endswith("_LDADD"):
                entries += mk.conditional_variables[helper]
        linkopt_arms: dict[str, list[str]] = {}
        for word, cond in entries:
            verdict, label = _resolve_conditional(cond, state, decisions)
            if verdict is False or (verdict is not True and label is None):
                if verdict is not False:
                    notes.append(
                        f"{makefile_name}: {program.name} links {word} under `if {cond}`; left out ({verdict})"
                    )
                continue
            scoped = dict(mk.variables)
            for var, var_entries in mk.conditional_variables.items():
                scoped.setdefault(var, [w for w, c in var_entries if c == cond])
            words = gnulibmk.expand_variables([word], scoped)
            arm_vars = [v for v in _link_vars(words, state) if v not in link_vars]
            if arm_vars:
                # `$(CRYPT_LIBS)`: a subst the autoconf target decides; the shared linkopts
                # target carries it, and its own condition (the probe) gates the option.
                link_vars += arm_vars
                _ensure_linkopts(package, arm_vars)
            # With `state`, a pkg-config `$(X_LIBS)` among the words is the registry library it
            # stands for; `under_condition` keeps LIBS out of the arm, where they would duplicate
            # the unconditional deps (Bazel rejects a label listed twice in `deps`).
            arm_deps, arm_linkopts = _link_items(
                words, libraries, notes, makefile_name, program.name, decisions, state, under_condition=True
            )
            if verdict is True:
                deps += [d for d in arm_deps if d not in deps]
                linkopts += [o for o in arm_linkopts if o not in linkopts]
            elif label is not None:  # the one other case left by the `continue` above
                dep_arms.setdefault(label, []).extend(
                    d for d in arm_deps if d not in deps and d not in dep_arms.get(label, [])
                )
                linkopt_arms.setdefault(label, []).extend(
                    o for o in arm_linkopts if o not in linkopt_arms.get(label, [])
                )
        if headers_lib and f":{headers_lib}" not in deps:
            deps.insert(0, f":{headers_lib}")
        elif not headers_lib:
            deps += [d for d in _config_deps(state) if d not in deps]
        deps += [p for lib, p in state.private_header_libs.items() if lib in deps and p not in deps]
        # A library linked only under an option (`if WITH_X` / `prog_LDADD += libbar.a`) sits in
        # that option's select() arm; its `_private_headers` companion goes into the same arm,
        # not the unconditional deps, so its headers are visible exactly when it is linked.
        for label, arm in dep_arms.items():
            arm += [p for lib, p in state.private_header_libs.items() if lib in arm and p not in deps and p not in arm]
        if link_vars:
            deps.append(":linkopts")
        defines, flag_copts, standards, flag_includes, copt_arms, define_arms, include_arms = _preprocessor_flags(
            mk, canonical, prefix.rstrip("/"), notes, state, decisions
        )
        deps = _gnulib_deps(state, deps, prefix.rstrip("/"), flag_includes, fallback=not libraries)
        # The same for a gnulib archive linked only under an option: its private headers
        # library joins the arm (after _gnulib_deps, which handles the unconditional case).
        for arm in dep_arms.values():
            for lib in state.gnulib_libraries:
                if lib.label in arm and lib.private_headers_label not in deps and lib.private_headers_label not in arm:
                    arm.append(lib.private_headers_label)
        if source_dirs and state.package_path == "." and "." in flag_includes:
            # Root package with -I$(top_srcdir): the headers are included by their repository
            # paths (`<tests/win-shim.h>`), which `includes = ["."]` cannot say there (rules_cc
            # rejects the repository root): a headers library with `strip_include_prefix = "/"`
            # serves them, shared by the programs of the directory.
            headers_name = f"{'_'.join(source_dirs)}_headers"
            if not any(isinstance(r, Rule) and r.name == headers_name for r in package.after):
                package.after.append(
                    Rule(
                        "cc_library",
                        headers_name,
                        {"hdrs": headers_glob([f"{d}/**/*.h" for d in source_dirs]), "strip_include_prefix": "/"},
                        comments=[
                            (
                                f"{makefile_name}: the headers under {', '.join(source_dirs)}/, which "
                                "-I$(top_srcdir) let the programs include by path."
                            )
                        ],
                        load_from="@rules_cc//cc:cc_library.bzl",
                    )
                )
            deps.append(f":{headers_name}")
            flag_includes = [d for d in flag_includes if d != "."]
        elif source_dirs:
            srcs_value = _with_headers(package, srcs_value, source_dirs)
        include_deps, flag_includes = _include_dir_deps(package, flag_includes, src, {}, notes, prefix.rstrip("/"))
        deps += [d for d in include_deps if d not in deps]
        sorted_deps = sorted(deps, key=label_sort_key)
        deps_value: list[str] | Raw = sorted_deps
        if dep_arms:
            deps_value = Raw(_list_literal(sorted_deps, "    ") + " + " + " + ".join(_arm_selects(dep_arms, sorted)))
        attrs: dict[str, AttrValue] = {
            "srcs": srcs_value,
            "copts": _copts_value(flag_copts, copt_arms),
            "local_defines": _defines_value(["HAVE_CONFIG_H"] + defines, define_arms),
            "deps": deps_value,
        }
        attrs.update(_public(program.installed))
        if flag_includes or gen_includes or include_arms:
            attrs["includes"] = includes_value(sorted(set(flag_includes) | set(gen_includes)), include_arms)
        _record_makefile_standards(standards, state)
        if linkopts or linkopt_arms:
            attrs["linkopts"] = _linkopts_value(linkopts, linkopt_arms)
        build_comment = build_attrs.pop("_comment", None)
        if isinstance(build_comment, str):
            comments.append(build_comment)
        attrs.update(build_attrs)
        package.after.append(
            Rule(
                "cc_binary",
                f"{prefix}{program.name}",
                attrs,
                comments=comments,
                load_from="@rules_cc//cc:cc_binary.bzl",
            )
        )
        # `@patch//:patch` for `src/patch`: the registry's modules name the
        # program after the module at the root; an alias keeps that address
        # when nothing else has taken the name.
        rule_names |= {item.name for item in package.after[scanned:] if isinstance(item, Rule)}
        scanned = len(package.after)
        bare = program.name.rsplit("/", 1)[-1]  # a non-recursive Makefile.am names it `sed/sed`
        if f"{prefix}{program.name}" != bare and bare not in rule_names:
            package.after.append(
                Rule(
                    "alias",
                    bare,
                    {"actual": f":{prefix}{program.name}", **_public(program.installed)},
                    comments=[f"{bare} at the root, as the registry addresses a module's program."],
                )
            )
    for name in sorted(mk.conditionals):
        if name not in state.am_conditionals:
            notes.append(f"{makefile_name}: `if {name}` has no AM_CONDITIONAL the converter followed")


def _substituted_program_attrs(
    program: gnulibmk.Program,
    mk: gnulibmk.Makefile,
    state: ProjectState,
    decisions: dec.Decisions,
    notes: list[str],
    makefile_name: str,
    prefix: str,
) -> dict[str, AttrValue] | None:
    """The attributes that say when an `EXTRA_PROGRAMS` candidate builds, or None when it does not.

    configure's value of the substituted `*_PROGRAMS` variable (`state.program_lists`) names the
    program under a condition: unconditionally (a plain target), under a host-OS condition
    (`target_compatible_with`), or under a probe result, which a target's existence cannot depend
    on: `tags = ["manual"]` so `//...` leaves it alone, the condition in a comment and in the
    report.  A `program:` decision overrides: ALWAYS, NEVER or the platforms it builds on.
    """
    if not program.substituted:
        return {}
    target = f"{prefix}{program.name}"
    bare = program.name.rsplit("/", 1)[-1]
    listed = [
        entry
        for var, entries in state.program_lists.items()
        if var in mk.substituted_targets
        for entry in entries
        if entry.name == bare
    ]
    key = f"program:{target}"  # the program's path from the project root, the key the report names
    decision = decisions.optional(key, "program")
    for older in (f"program:{program.name}", f"program:{bare}"):  # decisions files written to earlier spellings
        if decision is None and older != key:
            decision = decisions.optional(older, "program")
    if decision is not None:
        if decision.never:
            notes.append(f"{makefile_name}: {program.name}: not built ({key} is NEVER)")
            return None
        if decision.always:
            return {}
        return {"target_compatible_with": _compatible_with(decision.platforms or [], only=True)}
    if not listed:
        notes.append(
            f"{makefile_name}: {program.name} is an EXTRA_PROGRAMS candidate configure never "
            f"adds to the programs it builds; no target (decide {key} to build it)"
        )
        return None
    cond = df.disj(*(e.cond for e in listed))
    if cond is df.TRUE:
        return {}
    excluded = _platform_exceptions(cond)
    if excluded is not None:
        return {"target_compatible_with": _compatible_with(excluded, only=False)}
    why = lower.expression(cond).expr or describe(cond)
    state.manual_programs.append((target, why))
    return {
        "tags": ["manual"],
        "_comment": f"configure builds {program.name} only when {why} ({listed[0].source}); a target cannot "
        f"depend on a probe result, so `manual` keeps it out of `//...` (decide {key} "
        "with the platforms it builds on)",
    }
