"""Project mode: one BUILD file (plus MODULE.bazel) for a whole configure.ac.

The output follows the shape of the BCR overlays built on rules_cc_autoconf
(gzip, grep, cpio...): `autoconf_package_info` from AC_INIT, a `bool_flag` +
`config_setting` per configure option, one `autoconf` target for the
project's own checks with the gnulib modules it uses as deps, an
`autoconf_hdr` per AC_CONFIG_HEADERS entry, and, from lib/gnulib.mk, the
generated wrapper headers, the conditional ones, the conditional sources and
the library that compiles them.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk, licenses, lower, ownership
from tools.convert.automake import _programs_tree, _resolve_conditional
from tools.convert.common import (
    BOOL_OPTIONS,
    CONFIG_HEADERS,
    NO_WARNINGS,
    STANDARD_CONSTANTS,
    STANDARD_FEATURES_TODO,
    TOP_HEADERS,
    _compatible_with,
    _dict_literal,
    _inside_project,
    _list_literal,
    _platform_exceptions,
    _platform_labels,
    generated_docstring,
    headers_glob,
    module_name,
    no_warnings_statement,
    quote_label,
    standard_statement,
)
from tools.convert.emit import render_statement
from tools.convert.expand import Expansion
from tools.convert.gnulib_lib import (
    _GNULIB_VARIABLE_RE,
    _QUOTED_INCLUDE_RE,
    _gnulib_library,
    _gnulib_mk_paths,
    _header_variables,
    _literal_sed_substs,
    _template_indicators,
    _template_placeholders,
    _template_variables,
)
from tools.convert.index import PortIndex
from tools.convert.ir import Assign, AttrValue, Call, Each, Package, Raw, Rule, Statement, Target, strings
from tools.convert.makefiles import (
    _configured_subdirs,
    _literal_substs,
    _project_shape,
    _subst_values,
)
from tools.convert.markers import Tree
from tools.convert.modulebazel import (  # noqa: F401
    MODULES,
    _module_bazel,
    load_repos,
    source_tree_loads,
)
from tools.convert.results import (
    SYSTEM_EXTENSION_DEFINES,
    _condition_providers,
    _confdefs,
    _drop_duplicate_defines,
    _library_check_targets,
    _merge_list_checks,
    _merge_outer_substs,
    _produced_results,
    _registry_libraries_in_configure,
    _rename_repeated_probes,
    _sink_into_producing_arms,
    _underscore_twins,
    all_provided_names,
)
from tools.convert.sources import (
    _cross_package_includes,
    _require_standards,
)
from tools.convert.translate import (
    FlagInfo,
    Options,
    ProjectState,
    Translator,
    basename,
    condition_names,
    default_tarname,
    describe,
    provided_names,
    slug,
)

# Automake conditionals gnulib spells with a macro the converter does not
# evaluate, and what the hand-written overlays use for them.
KNOWN_SHELL_VARIABLES = {
    "USES_MSVCRT": {"@rules_cc//cc/compiler:msvc-cl": 1, "@rules_cc//cc/compiler:clang-cl": 1, lower.DEFAULT_ARM: 0},
}


@dataclass
class ProjectResult:
    package: Package
    """The root package: the BUILD file to write."""
    module_bazel: str
    """The MODULE.bazel text."""
    notes: list[str] = field(default_factory=list)
    """The report lines."""
    name: str = ""
    """AC_INIT's package name (`GNU grep`)."""
    version: str = ""
    """AC_INIT's version."""
    homepage: str | None = None
    """AC_INIT's URL, or the www.gnu.org page of a GNU package."""
    subdirs: list[str] = field(default_factory=list)
    """AC_CONFIG_SUBDIRS with a configure.ac of their own, the ones a `subdir:` decision rules out left out."""
    subdir_gates: dict[str, SubdirGate] = field(default_factory=dict)
    """Sub-configure -> how its package builds, for the ones configure enters conditionally (gawk's
    extension/ only where it finds dlfcn.h; see `_subdir_gates`)."""
    manual_package: str | None = None
    """Why every target of this package is tagged `manual`, when it is one (`_gate_package`)."""
    tarname: str = ""
    """AC_INIT's tarname: `flex`, where the name is "the fast lexical analyser generator"."""
    extra_files: dict[str, str] = field(default_factory=dict)
    """Written next to BUILD.bazel (autoheader's config.h.in)."""
    option_names: list[str] = field(default_factory=list)
    """The options this package declares flags for; a sub-configure declaring one of them keys
    on this package's setting instead of a flag of its own (`ProjectState.shared_options`)."""
    state: ProjectState | None = None
    """What the translation learned (templates, conditions): vendoring reads it."""
    source_tree_modules: list[str] = field(default_factory=list)
    """Repositories the BUILD files shipped in the source tree, or the hand-written BUILD.bazel, load."""
    external_modules: dict[str, str] = field(default_factory=dict)
    """Registry libraries the project links (tools/convert/libraries.py): module -> version."""
    module_attrs: dict[str, str] = field(default_factory=dict)
    """`module()` attributes kept from the MODULE.bazel that was at the destination."""
    uses_skylib: bool = True
    """Whether MODULE.bazel declares bazel_skylib (always: the test packages load it)."""
    tools: list[str] = field(default_factory=list)
    """The bison / flex modules whose rules run."""
    command: str | None = None
    """The reproduce command of the banners."""

    @property
    def module_name(self) -> str:
        return module_name(self.tarname or self.name)


_LABEL_REPO_RE = re.compile(r'"@([A-Za-z][A-Za-z0-9_.-]*)(?://|")')


_FILE_LIST_RE = re.compile(r"AC_DEFUN\(\[gl_FILE_LIST\],\s*\[(.*?)\]\)", re.DOTALL)


def _pinned_defines(decisions: dec.Decisions, package_path: str = ".") -> tuple[dict[str, str] | Raw | None, list[str]]:
    """`define:<NAME>` decisions with a value: the config header's `#undef NAME` line rewritten to
    `#define NAME <value>` (a select() of values: per arm, the other arms untouched), which the
    resolver then leaves alone, so a port's result for the name does not reach the header.  A bare
    name pins every config header of the project; `define:<package>:<NAME>` (`.` for the root) pins
    the header of that package alone, where the labels its select() names live (gawk's pc/config.h
    answers, selected on a setting its root BUILD.bazel defines).  Returns the `substitutions`
    attribute value (a dict, or a `select()` of dicts) and notes."""
    plain: dict[str, str] = {}
    arms: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    here = package_path or "."
    for key, raw in decisions.items():
        if not key.startswith("define:") or isinstance(raw, dec.Word):
            continue
        scope, scoped, name = key[len("define:") :].rpartition(":")
        if scoped and (scope or ".") != here:
            continue
        decision = decisions.optional(key, "define")
        if decision is None:
            continue
        line = f"#undef {name}"
        if decision.value is not None:
            plain[line] = f"#define {name} {decision.value}"
            notes.append(f"{name}: pinned to {decision.value} in the config header ({key})")
        else:
            values = decision.values or {}
            for label, value in values.items():
                arms.setdefault(label, {})[line] = f"#define {name} {value}"
            notes.append(
                f"{name}: pinned per platform in the config header ({key}: "
                f"{', '.join(f'{label} {value}' for label, value in values.items())})"
            )
    if not plain and not arms:
        return None, notes
    if not arms:
        return plain, notes
    arms.setdefault(lower.DEFAULT_ARM, {})
    rendered = ",\n".join(
        f'        "{label}": {_dict_literal({**plain, **lines}, "        ")}'
        for label, lines in sorted(arms.items(), key=lambda kv: kv[0] == lower.DEFAULT_ARM)
    )
    return Raw("select({\n" + rendered + ",\n    })"), notes


def _is_split_body(name: str, main: str) -> bool:
    """Whether `name` is a split of the main target's body (`autoconf_1`, `autoconf_2`, ...)."""
    prefix = f"{main}_"
    return name.startswith(prefix) and name[len(prefix) :].isdigit()


def _gnulib_file_list(sources: dict[str, str]) -> set[str]:
    """The m4 files gnulib-tool copied into the tree, by base name: gnulib-comp.m4's gl_FILE_LIST.
    Their macros are gnulib's, whatever the snapshot named the file (bison's m4/printf-posix-rpl.m4,
    since merged into printf-posix.m4, defines gl_FUNC_PRINTF_POSIX)."""
    out: set[str] = set()
    for relative, text in sources.items():
        if basename(relative) != "gnulib-comp.m4":
            continue
        for match in _FILE_LIST_RE.finditer(text):
            out |= {basename(word) for word in match.group(1).split() if word.endswith(".m4")}
    return out


def label_repos(text: str) -> set[str]:
    """The repositories the labels of a BUILD text name (`"@openssl//:crypto"`, `"@mbedtls"`):
    the hand-written BUILD.bazel's dependencies, which the generated MODULE.bazel must declare."""
    return set(_LABEL_REPO_RE.findall(text))


def module_bazel_text(
    result: ProjectResult,
    rules_version: str,
    dev_override: str | None,
    version: str | None = None,
    module_name: str | None = None,
) -> str:
    """The MODULE.bazel text; `version` overrides the package's (a registry entry's `1.2.bcr.3`),
    `module_name` the declared name (the entry's directory, `libpcap` for the package `pcap`)."""
    return _module_bazel(
        module_name or result.tarname or result.name,
        version or result.version,
        rules_version,
        dev_override,
        result.uses_skylib,
        result.tools,
        result.command,
        source_modules=result.source_tree_modules,
        external_modules=result.external_modules,
        module_attrs=result.module_attrs,
    )


MODULE_PACKAGES = ["//:__subpackages__"]
"""A builder's spelling for "the module's own packages": normalized to the confinement."""


@dataclass(frozen=True)
class SubdirGate:
    """How a sub-configure's package builds: under `compatible_with` (its targets'
    `target_compatible_with`), or defined but tagged `manual` (`manual_reason` says why: the
    `subdir:` decision is MANUAL, or still undecided)."""

    compatible_with: Raw | None = None
    manual_reason: str | None = None


def _subdir_gates(
    src: Path, state: ProjectState, decisions: dec.Decisions, subdirs: list[str], notes: list[str]
) -> tuple[list[str], dict[str, SubdirGate]]:
    """The sub-configures to convert and, for the ones configure enters only sometimes, the
    `target_compatible_with` their packages' targets get.

    configure enters gawk's extension/ only when `$enable_extensions` is yes, which its dlfcn.h
    probe settles, and the Makefile.am lists the directory under `if ENABLE_EXTENSIONS`: a host
    without dlfcn.h (MSVC) builds no extensions.  A target's existence cannot follow a probe, so
    a `subdir:<dir>` decision says where the package builds: ALWAYS, NEVER (not converted),
    MANUAL (defined, `tags = ["manual"]`) or the configuration labels (`!<label>,...` for
    everywhere but those).  Without one, a condition that is purely about the host OS, or an
    automake conditional that resolves to a constant or to one config_setting, is taken as it
    stands; anything else asks for the decision, and the package's targets are `manual` until it
    is made, as a program configure builds only when a probe holds is (`_program_build_attrs`)."""
    am_conditions: dict[str, list[str]] = {}
    makefile = src / "Makefile.am"
    if makefile.is_file():
        mk = gnulibmk.parse(makefile.read_text(encoding="utf-8", errors="replace"))
        for word, am_cond in mk.conditional_variables.get("SUBDIRS", []):
            am_conditions.setdefault(word.strip("/"), []).append(am_cond)
    kept: list[str] = []
    gates: dict[str, SubdirGate] = {}
    for subdir in subdirs:
        key = f"subdir:{subdir}"
        cond = state.subdir_conditions.get(subdir, df.TRUE)
        conditionals = am_conditions.get(subdir, [])
        decision = decisions.optional(key, "subdir")
        if decision is not None:
            if decision.never:
                notes.append(f"{subdir}/: not converted ({key} is NEVER)")
                continue
            kept.append(subdir)
            if decision.manual:
                gates[subdir] = SubdirGate(manual_reason=f"{key} is MANUAL")
                notes.append(f"{subdir}/: its targets are defined but tagged manual ({key} is MANUAL)")
            elif decision.platforms:
                gates[subdir] = SubdirGate(compatible_with=_compatible_with(decision.platforms, only=True))
                notes.append(f"{subdir}/: its targets build under {', '.join(decision.platforms)} only ({key})")
            continue
        gate: Raw | None = None
        reasons: list[str] = []
        if cond is not df.TRUE:
            excluded = _platform_exceptions(cond)
            included = _platform_labels(cond) if excluded is None else None
            if excluded:
                gate = _compatible_with(excluded, only=False)
            elif included:
                gate = _compatible_with(included, only=True)
            elif excluded is None and included is None:
                reasons.append(f"AC_CONFIG_SUBDIRS({subdir}) runs under `{describe(cond)}`")
        never = False
        for conditional in conditionals:
            verdict, arm = _resolve_conditional(conditional, state, decisions)
            if verdict is False:
                notes.append(f"{subdir}/: not converted (SUBDIRS lists it under `if {conditional}`, never true here)")
                never = True
                break
            if verdict is True:
                continue
            if arm is not None:
                gate = gate or _compatible_with([arm], only=True)
            else:
                reasons.append(f"SUBDIRS lists it under `if {conditional}` ({verdict})")
        if never:
            continue
        kept.append(subdir)
        if reasons:
            decisions.resolve(
                dec.Request(
                    key,
                    "subdir",
                    "configure.ac",
                    f"configure enters {subdir}/ only sometimes, and a target's existence cannot depend on a "
                    "probe result: say where its package builds",
                    {"condition": reasons},
                )
            )
            why = f"{key} undecided: {'; '.join(reasons)}"
            gates[subdir] = SubdirGate(manual_reason=why)
            notes.append(f"{subdir}/: its targets are defined but tagged manual until {key} is decided ({why})")
        elif gate is not None:
            gates[subdir] = SubdirGate(compatible_with=gate)
            notes.append(f"{subdir}/: its targets build where configure enters it: {gate.text.replace(chr(10), ' ')}")
    return kept, gates


_GATED_RULE_KINDS = frozenset({"cc_binary", "cc_import", "cc_library", "cc_shared_library", "cc_test"})


def _gate_package(package: Package, gate: SubdirGate, notes: list[str]) -> None:
    """Apply a sub-configure's gate to every C/C++ rule of its package: `target_compatible_with`
    where a rule has none of its own (the package builds where the parent configure enters the
    directory), or `tags = ["manual"]` (defined, out of `//...`) for a MANUAL or undecided one."""
    count = 0
    for statement in package.after:
        if not isinstance(statement, Rule) or statement.kind not in _GATED_RULE_KINDS:
            continue
        if gate.manual_reason is not None:
            tags = strings(statement.attrs.get("tags"))
            if "manual" not in tags:
                statement.attrs["tags"] = [*tags, "manual"]
                count += 1
        elif gate.compatible_with is not None and "target_compatible_with" not in statement.attrs:
            statement.attrs["target_compatible_with"] = gate.compatible_with
            count += 1
    if gate.manual_reason is not None:
        notes.append(f"{count} target(s) tagged manual, out of `//...` ({gate.manual_reason})")
    else:
        notes.append(f"{count} target(s) build where the parent configure enters this directory (its subdir: gate)")


def confined_visibility(root_package: str, shared: bool) -> str:
    """The visibility of what the conversion keeps to itself: private, or every package of the
    converted tree when it has more than one (`shared`: a sub-configure, a hand-written `tests/`),
    which is `//:__subpackages__` for a module and `//<dir>:__subpackages__` for a tree rendered
    into a subdirectory of a larger workspace (`root_package`)."""
    return f"//{root_package}:__subpackages__" if shared else "//visibility:private"


def _confine_visibility(package: Package, confined: str) -> None:
    """Every generated target gets an explicit visibility, so a `default_visibility` in the
    hand-written BUILD.bazel never reaches them.  Public is what a builder marked so: what
    `make install` ships (the libraries, programs and headers, under their aliases too) and the
    option flags, the module's configure options.  The `autoconf` targets and every rule left
    unmarked, or marked `//:__subpackages__` (the config headers, `autoconf_linkopts`, the option
    settings, the gnulib and private header libraries, the generated-file rules,
    `autoconf_package_info`), are `confined`.  A Raw statement (a `generated:` decision's rule) is
    the author's as written."""
    for target in package.targets:
        target.visibility = confined
    for statement in [*package.before, *package.after]:
        if (
            isinstance(statement, (Rule, Each))
            and statement.attrs.get("visibility", MODULE_PACKAGES) == MODULE_PACKAGES
        ):
            statement.attrs["visibility"] = [confined]


def convert_project(
    tree: Tree,
    expansion: Expansion,
    src: Path,
    index: PortIndex | None,
    *,
    rules_version: str,
    dev_override: str | None = None,
    decisions: dec.Decisions | None = None,
    command: str | None = None,
    source_root: Path | None = None,
    extra_loads: AbstractSet[str] = frozenset(),
    hand_targets: Mapping[str, str] | None = None,
    hand_packages: AbstractSet[str] = frozenset(),
    hand_build: str = "",
    module_attrs: dict[str, str] | None = None,
    parent_headers: str | None = None,
    package_path: str = "",
    module_name: str | None = None,
    extra_modules: dict[str, str] | None = None,
    repo_file: bool = True,
    root_package: str = "",
    gate: SubdirGate | None = None,
    shared_options: AbstractSet[str] = frozenset(),
) -> ProjectResult:
    """Translate a whole project into a root package.  `command` is the
    `bazel run` line that reproduces the conversion, for the banners; `source_root` the
    whole source tree where `src` is a `--subdir` of it (the BUILD files shipped anywhere
    in the tree load into the generated module); `extra_loads` the repositories the hand-written
    BUILD.bazel loads, declared the same way, and `hand_targets` its rules, name -> kind (a
    `generated:` decision may hand a file to one; an option flag steps aside for one), `hand_build` its text
    (the option settings it keys a select() on are declared); `repo_file` whether the module root gets a
    REPO.bazel with the repository's defaults (not for an http_archive's single BUILD file); `hand_packages`
    the paths of the hand-written packages
    beside the root (an overlay's `tests/`), which no root rule may write into; `parent_headers` the enclosing project's config headers
    library when `src` is an AC_CONFIG_SUBDIRS sub-configure (gawk's extension/ compiles with
    `-I$(srcdir)/..`) and `package_path` that package's path in the module (the scope of its
    `library:` decisions); `module_name` the name MODULE.bazel declares when it is not AC_INIT's
    tarname (a registry entry's directory: `libpcap` for the package `pcap`); `extra_modules`
    module -> version for the registry modules the hand-written BUILD files' labels name (libssh2's
    `@openssl//:crypto`), declared beside the ones configure found."""
    decisions = decisions if decisions is not None else dec.Decisions()
    sources = {}
    for relative in expansion.m4_files:
        path = expansion.work_dir / relative
        if path.is_file():
            sources[relative] = path.read_text(encoding="utf-8", errors="replace")
    sources[expansion.configure_ac.name] = expansion.configure_ac.read_text(encoding="utf-8", errors="replace")

    m4_files = [basename(f) for f in expansion.m4_files]
    ported = {f for f in m4_files if index is not None and index.file_port(f) is not None}
    flatten = {f for f in m4_files if f == "gnulib-comp.m4"}
    # gnulib-tool's files stay selected (an unported macro of theirs is translated from its body);
    # a port target of a macro's name wins over the file (`Translator._gnulib_file`).
    gnulib_files = _gnulib_file_list(sources)
    selected = {f for f in m4_files if f not in ported and f not in flatten} | {expansion.configure_ac.name}

    state = ProjectState(
        flatten_files=flatten,
        gnulib_files=gnulib_files,
        port_ambiguous=index.ambiguous if index is not None else set(),
        port_substs=index.substs if index is not None else set(),
        port_plain_substs=index.plain_substs if index is not None else set(),
        port_result_substs=index.result_substs if index is not None else set(),
        port_probes=index.probes if index is not None else {},
        port_variables=index.variables if index is not None else set(),
        port_conditionals=index.conditionals if index is not None else {},
        package_path=package_path or ".",
        hand_targets=dict(hand_targets or {}),
        hand_packages=set(hand_packages),
        hand_build=hand_build,
        shared_options=set(shared_options),
    )
    options = Options(repo="@rules_cc_autoconf", in_repo=False, layout="package")
    translator = Translator(
        tree,
        index=index,
        selected_files=selected,
        m4_sources=sources,
        options=options,
        project=state,
        decisions=decisions,
    )
    translator.translate()
    main = translator.translate_root("autoconf", loc_file=expansion.configure_ac.name)
    notes_libraries = _registry_libraries_in_configure(sources, selected, state, decisions, package_path or ".")
    # The root package, including the shared-probe helper targets the root
    # body created while it was translated.
    package = translator.packages.get("") or Package(path="", docstring="")
    empty = {t.name for t in package.targets if t.is_empty}
    package.targets = [t for t in package.targets if not t.is_empty]
    notes = list(translator.report.notes) + notes_libraries

    notes += _drop_duplicate_defines(main, package.targets, index, list(state.gnulib_packages.values()))
    for target in [*package.targets, main]:
        notes += _rename_repeated_probes(target)
    notes += _sink_into_producing_arms(main)
    notes += _merge_outer_substs(main)
    notes += _header_variables(
        main, state, index, list(state.gnulib_packages.values()) + list(state.gnulib_defaults.values())
    )
    segments, extensions = _confdefs(main)
    if extensions:
        package.before.append(
            Assign(
                "SYSTEM_EXTENSIONS",
                SYSTEM_EXTENSION_DEFINES,
                comments=[
                    "AC_USE_SYSTEM_EXTENSIONS: every probe configure runs afterwards sees these in",
                    "confdefs.h, so every compiling check lists them (the rules make that explicit).",
                ],
            )
        )
    package.targets.extend(segments)
    init = state.init_args or ["", ""]
    project_name = init[0].strip() or src.name
    version = init[1].strip() if len(init) > 1 else ""
    # AC_INIT's default tarname is the lower-cased name without a `GNU ` prefix.
    tarname = init[3].strip() if len(init) > 3 and init[3].strip() else default_tarname(project_name)
    state.tarname = tarname
    for extra_name, extra_version in sorted((extra_modules or {}).items()):
        if extra_name not in state.external_modules:
            state.external_modules[extra_name] = extra_version
            notes.append(
                f"@{extra_name}: named by a hand-written BUILD file's labels; bazel_dep added at {extra_version}"
            )
    # A library pkg-config found carries the option it was found under (`pkg_conditions`); the
    # same library seen as a `-l` word in configure's text (grep's `-lpcre2-8` in m4/pcre.m4)
    # must not also join every target unconditionally.
    for lib, label in list(state.configure_libs.items()):
        if label in state.pkg_cc_deps:
            del state.configure_libs[lib]
            notes.append(
                f"-l{lib}: the same registry library pkg-config found; linked where that result is used, "
                "not by every target"
            )
    # The project is not a dependency of itself: readline's aclocal.m4 probes
    # -lreadline, which the library table resolves to the registry's readline.
    own = state.module_name()
    if state.external_modules.pop(own, None) is not None:
        notes.append(f"-l{own}: the project itself, not the registry module; dropped from MODULE.bazel and cc_deps")
    state.configure_libs = {
        lib: label for lib, label in state.configure_libs.items() if not label.startswith(f"@{own}//")
    }
    package.targets.extend(_library_check_targets(main, segments, state, notes))
    merged = _merge_list_checks(package.targets + [main])
    if merged:
        notes.append(f"{merged} plural check call(s) merged into the first call of their macro with the same keywords")
    for target in package.targets + [main]:
        target.deps = [d for d in target.deps if d.label.lstrip(":") not in empty]
    for name in sorted(empty):
        notes.append(f"{name}: an empty macro (no checks, no requirements); no target emitted")

    package.docstring = generated_docstring(f"{project_name} {version}".strip(), "targets", command)

    # -- autoconf_package_info ------------------------------------------------
    # AC_INIT's defaults: GNU packages get their www.gnu.org page as the URL.
    info_attrs: dict[str, AttrValue] = {
        "package_name": project_name,
        "module_bazel": "//:MODULE.bazel",
        "strip_bcr_version": True,
    }
    if len(init) > 2 and init[2].strip():
        info_attrs["package_bugreport"] = init[2].strip()
    if tarname != project_name:
        info_attrs["package_tarname"] = tarname
    if len(init) > 4 and init[4].strip():
        info_attrs["package_url"] = init[4].strip()
    elif project_name.startswith("GNU "):
        info_attrs["package_url"] = f"https://www.gnu.org/software/{tarname}/"
    if state.automake:
        info_attrs["aliases"] = {"PACKAGE": "PACKAGE_TARNAME", "VERSION": "PACKAGE_VERSION"}
    package.before.append(
        Rule(
            "autoconf_package_info",
            "package",
            info_attrs,
            comments=[
                (
                    f"configure.ac: AC_INIT({', '.join('[' + a.strip() + ']' for a in init)}); the version "
                    "comes from MODULE.bazel so a `.bcr.N` bump has one place to edit."
                )
            ],
            load_from="//autoconf:autoconf_package_info.bzl",
        )
    )
    if state.config_subdirs and not package.path:
        # The sub-configures' packages (gawk's extension/) read the version from
        # the same MODULE.bazel.
        package.before.append(Raw('exports_files(["MODULE.bazel"])'))

    options_at = len(package.before)  # the configure options go here, once their use is known

    # -- the main autoconf target -------------------------------------------------
    gnulib_deps = sorted(state.gnulib_packages.values())
    if gnulib_deps:
        package.before.append(
            Assign(
                "GNULIB_M4_DEPS",
                gnulib_deps,
                comments=[
                    (
                        "Gnulib m4 modules the configure script uses (m4/gnulib-comp.m4 gl_INIT and "
                        "the AC_REQUIREs reached from it), as the rules_cc_autoconf ports."
                    )
                ],
            )
        )
        main.deps_suffix.append("GNULIB_M4_DEPS")
        # The split bodies (`autoconf_1`, ...) are parts of the same configure run: their checks
        # read the ports' results too (patch's GNULIB_UNISTD_H_GETOPT under REPLACE_GETOPT).
        for target in package.targets:
            if _is_split_body(target.name, main.name) and "GNULIB_M4_DEPS" not in target.deps_suffix:
                target.deps_suffix.append("GNULIB_M4_DEPS")
    gnulib_defaults = sorted(state.gnulib_defaults.values())
    if gnulib_defaults:
        package.before.append(
            Assign(
                "GNULIB_M4_DEFAULT_DEPS",
                gnulib_defaults,
                comments=[
                    "The gl_<HEADER>_H_DEFAULTS the configure script AC_REQUIREs: the headers' `:defaults`,",
                    "read beneath the results (a probe of the same name overrides a default).",
                ],
            )
        )
        main.default_deps_suffix.append("GNULIB_M4_DEFAULT_DEPS")
    test_substs = {k: v for k, v in state.indicator_test_substs.items() if k not in state.indicator_substs}
    if test_substs:
        package.before.append(
            Assign(
                "GNULIB_MODULE_INDICATOR_TEST_SUBSTS",
                dict(sorted(test_substs.items())),
                comments=[
                    "gl_<HEADER>_MODULE_INDICATOR calls in the tests' gl_INIT: the library's wrapper headers",
                    "read IN_<PACKAGE>_GNULIB_TESTS for these (0 in the library, 1 in the tests).",
                ],
            )
        )
        main.checks.append(
            Raw(
                "[\n        checks.AC_SUBST(name, value)\n        for name, value in "
                "GNULIB_MODULE_INDICATOR_TEST_SUBSTS.items()\n    ]"
            )
        )
    if state.indicator_substs:
        package.before.append(
            Assign(
                "GNULIB_MODULE_INDICATOR_SUBSTS",
                sorted(set(state.indicator_substs)),
                comments=[
                    "gl_<HEADER>_MODULE_INDICATOR calls in gl_INIT: the @GNULIB_*@ substitutions the",
                    ".in.h templates read; the rules_cc_autoconf <header>_h:defaults set them to 0.",
                ],
            )
        )
        main.checks.append(
            Raw("[\n        checks.AC_SUBST(name, 1)\n        for name in GNULIB_MODULE_INDICATOR_SUBSTS\n    ]")
        )
    indicator_defines = sorted(set(state.indicator_defines) - set(state.indicator_test_defines))
    # A later IN_<PKG>_GNULIB_TESTS definition overrides the library's 1.
    if indicator_defines:
        package.before.append(
            Assign(
                "GNULIB_MODULE_INDICATOR_DEFINES",
                indicator_defines,
                comments=["gl_MODULE_INDICATOR calls in gl_INIT: the config.h defines."],
            )
        )
        main.checks.append(
            Raw("[\n        checks.AC_DEFINE(name, 1)\n        for name in GNULIB_MODULE_INDICATOR_DEFINES\n    ]")
        )
    if state.indicator_test_defines:
        package.before.append(
            Assign(
                "GNULIB_MODULE_INDICATOR_TEST_DEFINES",
                dict(sorted(state.indicator_test_defines.items())),
                comments=[
                    "gl_MODULE_INDICATOR_FOR_TESTS: modules the package uses only in its tests; the",
                    "IN_<PACKAGE>_GNULIB_TESTS value is 0 in the library and 1 in the tests.",
                ],
            )
        )
        main.checks.append(
            Raw(
                "[\n        checks.AC_DEFINE(name, value)\n        for name, value in "
                "GNULIB_MODULE_INDICATOR_TEST_DEFINES.items()\n    ]"
            )
        )
    main.add_dep(":package")
    main.comments.insert(0, "configure.ac: the project's own checks; the gnulib modules come through GNULIB_M4_DEPS.")
    package.targets.append(main)

    # -- config headers -------------------------------------------------------------
    extra_files, config_targets = _config_header_rules(
        package, state, src, expansion, notes, source_root, parent_headers, decisions
    )

    # -- gnulib.mk ------------------------------------------------------------------
    mk_paths = _gnulib_mk_paths(src, expansion)
    if mk_paths:
        for path in mk_paths:
            _gnulib_library(package, path, src, state, config_targets, notes, decisions)
        if len(mk_paths) > 1:
            notes.append(
                "gnulib libraries: "
                + ", ".join(f"{lib.directory} ({lib.label})" for lib in state.gnulib_libraries)
                + "; one set of results (gl_INIT) for all"
            )
        notes += _underscore_twins(package, state, index)
        notes += _condition_providers(main, package, state, index)
        _provide_unported_names(main, package, state, index, mk_paths, notes)
    else:
        notes.append(
            "no lib/gnulib.mk found: no gnulib library; programs and libraries come from the Makefile.am "
            "tree (or hand-written Makefile.in files)"
        )

    state.literal_substs = _literal_substs(main)
    state.subst_values = _subst_values(main)
    state.produced_results = _produced_results(main, package, state, index)
    _programs_tree(package, src, decisions, state, notes)
    _require_standards(main, state, notes)
    tools = sorted({s.kind for s in package.after if isinstance(s, Rule) and s.kind in ("bison", "flex")})
    option_rules = _option_rules(package, state)
    package.before[options_at:options_at] = option_rules
    if any(isinstance(st, Rule) and st.kind in ("cc_library", "cc_binary", "cc_test") for st in package.after):
        package.before.append(no_warnings_statement())
        notes += _module_flags(package, decisions, package_path or ".")
    _standard_flags(package, state)
    root = package_path in ("", ".")
    license_loads = _license_rules(package, src, decisions, package_path, notes, repo_defaults=repo_file and root)

    # bazel_skylib is always declared: the module's own test packages (the
    # overlays' tests/) load diff_test and write_file from it, and
    # rules_cc_autoconf depends on it anyway.
    uses_skylib = True
    loaded = source_tree_loads(source_root or src) | set(extra_loads) | license_loads
    # The overlay's own test module names the module (`m4_tool = "@m4//:m4"`): not a dependency.
    loaded -= {state.module_name(), module_name or ""}
    source_modules = sorted(r for r in loaded if r in MODULES and MODULES[r].role == "loaded")
    tools = sorted(set(tools) | {r for r in loaded if r in MODULES and MODULES[r].role == "tool"})
    for repo in sorted(loaded - set(MODULES) - set(state.external_modules)):
        notes.append(
            f"BUILD files in the source tree (or the hand-written BUILD.bazel) load @{repo}; no module is "
            "known for it: add its bazel_dep to MODULE.bazel"
        )
    module_bazel = _module_bazel(
        module_name or state.tarname,
        version,
        rules_version,
        dev_override,
        uses_skylib,
        tools,
        command,
        source_modules=source_modules,
        external_modules=state.external_modules,
        module_attrs=module_attrs,
    )
    package_url = info_attrs.get("package_url")
    homepage = package_url if isinstance(package_url, str) else None
    subdirs = []
    for subdir in state.config_subdirs:
        if any((src / subdir / c).is_file() for c in ("configure.ac", "configure.in")):
            subdirs.append(subdir.strip("/"))
        else:
            notes.append(f"AC_CONFIG_SUBDIRS({subdir}): no configure.ac there; not followed")
    for subdir in _configured_subdirs(src, set(subdirs)):
        subdirs.append(subdir)
        notes.append(
            f"{subdir}/: has a configure.ac of its own and the Makefile.am's SUBDIRS is computed by "
            "configure (texlive's $(MAKE_SUBDIRS)); converted as a package of this module"
        )
    subdirs, subdir_gates = _subdir_gates(src, state, decisions, subdirs, notes)
    if gate is not None:
        _gate_package(package, gate, notes)
    extra_files.update(state.extra_templates)
    if repo_file and root:
        repo_bazel = _repo_bazel(state, metadata=bool(license_loads))
        if repo_bazel is not None:
            extra_files["REPO.bazel"] = repo_bazel
    notes.insert(0, _project_shape(src, package))
    if package_path in ("", "."):
        _root_includes(package)
    shared = bool(hand_packages) or bool(state.config_subdirs) or package_path not in ("", ".")
    _confine_visibility(package, confined_visibility(root_package, shared))
    return ProjectResult(
        package,
        module_bazel,
        notes,
        name=project_name,
        version=version,
        homepage=homepage,
        extra_files=extra_files,
        state=state,
        source_tree_modules=source_modules,
        external_modules=dict(state.external_modules),
        subdirs=subdirs,
        subdir_gates=subdir_gates,
        option_names=state.option_names,
        manual_package=gate.manual_reason if gate is not None else None,
        tarname=state.tarname or "",
        module_attrs=dict(module_attrs or {}),
        uses_skylib=uses_skylib,
        tools=tools,
        command=command,
    )


def _provide_unported_names(
    main: Target, package: Package, state: ProjectState, index: PortIndex | None, mk_paths: list[Path], notes: list[str]
) -> None:
    """The names gnulib.mk and the wrapper-header templates read that no port and no check of the
    project provides, given to the main target with the values the hand-written overlays use: the
    literal sed substitutions of gnulib.mk, 0 for an indicator or a variable of a newer gnulib,
    every conditional-dependencies switch enabled, a bool_flag for a ported macro's configure
    option, 0 for any other template placeholder."""
    index_provided = index.provided if index is not None else set()
    provided = all_provided_names([main])
    for path in mk_paths:
        for subst, value in _literal_sed_substs(path.read_text(encoding="utf-8", errors="replace")).items():
            if subst not in provided:
                provided.add(subst)
                main.checks.append(
                    Call(
                        "checks.AC_SUBST",
                        [subst, value],
                        comments=[f"{path.name}: the wrapper headers' @{subst}@ is the literal `{value}` in the sed."],
                    )
                )
    # A template newer than a port's gnulib snapshot reads an indicator
    # no <header>_h default sets: 0, the module is not in this project.
    known = state.port_substs | set(state.indicator_substs) | set(state.indicator_test_substs) | provided
    unknown_names = (
        {
            n
            for expr in state.src_condition_exprs.values()
            for n in condition_names(expr)
            if _GNULIB_VARIABLE_RE.match(n)
        }
        - known
        - index_provided
    )
    for name in sorted(unknown_names):
        # bison's gnulib tests REPLACE_CALLOC; the ports' gnulib spells it
        # REPLACE_CALLOC_FOR_CALLOC_GNU: nothing provides the old name.
        main.checks.append(
            Call(
                "checks.AC_SUBST",
                [name, 0],
                comments=[
                    (
                        f"{name} is tested by gnulib.mk but set by no rules_cc_autoconf port (a different gnulib); "
                        "0, as for an absent module."
                    )
                ],
            )
        )
        notes.append(f"{name}: gnulib.mk condition over a variable no port provides; set to 0")
    # Shell variables gnulib-comp.m4 computes outside any macro the ports
    # carry, and what the overlays provide for them: USES_MSVCRT is set by
    # an AC_EGREP_CPP for _MSC_VER inside `case $host_os in mingw*|windows*`
    # (the console-safe stdio module); the compiler says it directly.
    for name, arms in KNOWN_SHELL_VARIABLES.items():
        if (
            name in known
            or name in provided
            or not any(name in condition_names(expr) for expr in state.src_condition_exprs.values())
        ):
            continue
        for label, arm_value in arms.items():
            main.arm(label, family=name).append(
                Call(
                    "checks.AC_SUBST",
                    [name, arm_value],
                    comments=(
                        [
                            (
                                f"{name}: gnulib-comp.m4 sets it by an AC_EGREP_CPP for _MSC_VER on mingw*/windows*; "
                                "the compiler says so directly."
                            )
                        ]
                        if label == lower.DEFAULT_ARM
                        else []
                    ),
                )
            )
        notes.append(
            f"{name}: gnulib.mk condition over a shell variable gnulib-comp.m4 computes; provided per compiler"
        )
    # gnulib-tool --conditional-dependencies: gl_GNULIB_ENABLED_<module> says
    # whether another module pulled this one in, through shell functions
    # the converter does not run.  A static build enables them all.
    enabled = {
        n
        for expr in state.src_condition_exprs.values()
        for n in condition_names(expr)
        if n.startswith("gl_GNULIB_ENABLED_")
    } | {n for cond in state.cond_headers.values() for n in [cond] if n.startswith("gl_GNULIB_ENABLED_")}
    enabled |= {
        n
        for expr in state.header_conditions.values()
        for n in condition_names(lower.expression(expr).expr or "")
        if n.startswith("gl_GNULIB_ENABLED_")
    }
    for name in sorted(state.generate_defaults - known - provided - index_provided - set(state.header_conditions)):
        main.checks.append(
            Call(
                "checks.M4_VARIABLE",
                [name, "false"],
                comments=[
                    (
                        f"{name}: a wrapper header's switch no port provides (an older gnulib's conditional); "
                        "the system header is used."
                    )
                ],
            )
        )
        notes.append(f"{name}: conditional wrapper header switch no port provides; set to false")
    for name in sorted((enabled | state.enabled_switches) - known - provided):
        main.checks.append(
            Call(
                "checks.M4_VARIABLE",
                [name, "true"],
                comments=[f"{name}: a conditional-dependencies switch of gl_INIT; every module is enabled here."],
            )
        )
        notes.append(f"{name}: conditional-dependencies switch; set to true (the module is enabled)")
    # Configure options declared by a ported m4 macro (gl_LIBSELINUX's
    # --with-selinux) that gl_INIT's gl_CONDITIONALs test: a bool_flag, off
    # by default as the registry overlays have it, exposed as the shell
    # variable the condition reads.
    option_names = (
        {
            n
            for expr in state.src_condition_exprs.values()
            for n in condition_names(expr)
            if re.fullmatch(r"(?:with|enable)_[a-z0-9_]+", n)
        }
        - known
        - provided
    )
    for name in sorted(option_names):
        kind, _, option = name.partition("_")
        if name not in state.flags:
            state.flags[name] = FlagInfo(
                "AC_ARG_WITH" if kind == "with" else "AC_ARG_ENABLE",
                option.replace("_", "-"),
                "declared by a ported m4 macro; off unless set",
            )
        label = state.label(name, "yes")
        main.arm(label, family=name).append(
            Call(
                "checks.AC_SUBST",
                [name, 1],
                comments=[
                    f"--{kind}-{option.replace('_', '-')}: gnulib.mk compiles sources under it; the option of a ported m4 macro."
                ],
            )
        )
        main.arm(lower.DEFAULT_ARM, family=name).append(Call("checks.AC_SUBST", [name, 0]))
        notes.append(
            f"{name}: a configure option gnulib.mk tests, declared by a ported macro; a bool_flag "
            f"`{name}` (default False) provides it"
        )
    for indicator in sorted(_template_indicators(state.templates) - known):
        main.checks.append(
            Call(
                "checks.AC_SUBST",
                [indicator, 0],
                comments=[
                    (
                        f"@{indicator}@ is read by a template but set by no rules_cc_autoconf port (newer gnulib); "
                        "the module is not in this project."
                    )
                ],
            )
        )
        notes.append(f"{indicator}: module indicator read by a template that no port provides; set to 0")
    # A template newer than the ports' gnulib also reads HAVE_* / REPLACE_*
    # variables its <header>_h defaults would set before any probe
    # (HAVE_LANGINFO_T_FMT_AMPM): gnulib's defaults are 1 and 0, the
    # answer for a libc that has the function and needs no replacement.
    all_known = known | index_provided
    for variable in sorted(_template_variables(state.templates) - all_known):
        default_value = 1 if variable.startswith("HAVE_") else 0
        main.checks.append(
            Call(
                "checks.AC_SUBST",
                [variable, default_value],
                comments=[
                    (
                        f"@{variable}@ is read by a template but set by no rules_cc_autoconf port (newer gnulib); "
                        f"gnulib's own default, {default_value}."
                    )
                ],
            )
        )
        notes.append(f"{variable}: read by a template, set by no port (newer gnulib); gnulib's default {default_value}")
    # Any other placeholder of a template that nothing provides: errno.in.h reads
    # @EMULTIHOP_HIDDEN@ / @EMULTIHOP_VALUE@ (gl_REPLACE_ERRNO_VALUE's results, which the
    # errno_h port does not carry) where the wrapper is generated at all (MSVC).  0, as the
    # hand-written overlays put there: an unsubstituted @NAME@ does not compile.
    emitted = set().union(*(provided_names(c) for c in main.checks if isinstance(c, Call)))
    for name in sorted(_template_placeholders(state.templates) - all_known - emitted):
        main.checks.append(
            Call(
                "checks.AC_SUBST",
                [name, 0],
                comments=[f"@{name}@ is read by a template but set by no rules_cc_autoconf port; 0."],
            )
        )
        notes.append(f"{name}: read by a template, set by no port; set to 0")


def _config_header_rules(
    package: Package,
    state: ProjectState,
    src: Path,
    expansion: Expansion,
    notes: list[str],
    source_root: Path | None,
    parent_headers: str | None,
    decisions: dec.Decisions,
) -> tuple[dict[str, str], list[str]]:
    """The config headers: AC_CONFIG_HEADERS as `autoconf_hdr` rules (autoheader's template when
    configure.ac has none), AC_CONFIG_FILES headers rendered beside them, and the `config_headers`
    library (with the sub-configures' `top_headers`); returns the extra files to write and the
    header target names."""
    extra_files: dict[str, str] = {}
    autoheader_template = expansion.autoheader_template
    if not state.config_headers and autoheader_template is not None and autoheader_template.is_file():
        template_text = autoheader_template.read_text(encoding="utf-8", errors="replace")
        if "#undef" in template_text and not (src / "config.h.in").exists():
            extra_files["config.h.in"] = ownership.marked(
                template_text, ".in", "autoheader's config.h.in for a configure.ac without AC_CONFIG_HEADERS"
            )
            state.config_headers.append("config.h")
            notes.append(
                "configure.ac has no AC_CONFIG_HEADERS (configure would pass the defines as -D flags): "
                "autoheader's config.h.in is written next to BUILD.bazel and rendered as config.h, "
                "which the sources see with HAVE_CONFIG_H (the BCR mpfr overlay does the same by hand)"
            )
    config_targets = []
    pinned, pinned_notes = _pinned_defines(decisions, state.package_path)
    notes += pinned_notes
    template_texts: dict[str, str] = {}  # AC_CONFIG_HEADERS entry -> its template's text, where the template exists
    for index_, entry in enumerate(state.config_headers):
        out, _, template = entry.partition(":")
        template = template or out + ".in"
        target_name = "config" if index_ == 0 else slug(out)
        config_targets.append(target_name)
        attrs: dict[str, AttrValue] = {
            "out": out,
            "template": template,
            "deps": [":autoconf"],
        }
        comments = [f"configure.ac: AC_CONFIG_HEADERS([{entry}])."]
        template_path = src / template
        if template_path.is_file():
            template_texts[entry] = template_path.read_text(encoding="utf-8", errors="replace")
        if pinned is not None and "#undef" in template_texts.get(entry, ""):
            attrs["substitutions"] = pinned
            comments.append(
                "`define:` decisions: lines pinned over the ports' results (a bundled gnulib older than "
                "the ports', whose sources want its own m4's answer)."
            )
        package.after.append(
            Rule(
                "autoconf_hdr",
                target_name,
                attrs,
                comments=comments,
                load_from="//autoconf:autoconf_hdr.bzl",
            )
        )
    state.config_targets = config_targets
    # `AC_CONFIG_FILES([config.h])`: a header configure renders from a hand-written template
    # (grub's config.h.in includes config-util.h and adds the installation directories).  Sources
    # include it as they do the AC_CONFIG_HEADERS output, so it joins config_headers.
    subst_rules: list[Rule] = []
    subst_headers: list[str] = []
    for entry in state.config_files:
        out, _, template = entry.partition(":")
        template = template or out + ".in"
        inside_out, inside_template = _inside_project(src, out), _inside_project(src, template)
        if inside_out is None or inside_template is None:
            notes.append(f"AC_CONFIG_FILES([{entry}]): leaves the project directory; not generated")
            continue
        out, template = inside_out, inside_template
        if re.search(r"(^|/)(GNUmakefile|Makefile)(\.in)?$", out) or not (src / template).is_file():
            notes.append(
                f'AC_CONFIG_FILES([{entry}]): not generated (an autoconf_hdr with mode = "subst" '
                "renders a template; Makefiles have no Bazel equivalent)"
            )
            continue
        inside = next((p for p in state.hand_packages if out.startswith(p + "/")), None)
        if inside is not None:
            # cpio's tests/atlocal: Bazel rejects a root rule whose output lies in a subpackage.
            notes.append(
                f"AC_CONFIG_FILES([{entry}]): not generated; {inside}/ is a hand-written package, "
                f"so the root package cannot write {out} (render it from {inside}/BUILD.bazel if it is needed)"
            )
            continue
        # A file without a directory or an extension (gawk's `gawkbug`) would give the rule its
        # own output's name, which Bazel rejects.
        rule_name = slug(out) if slug(out) != out else slug(out) + "_file"
        subst_rules.append(
            Rule(
                "autoconf_hdr",
                rule_name,
                {"out": out, "mode": "subst", "template": template, "deps": [":autoconf"]},
                comments=[f"configure.ac: AC_CONFIG_FILES([{entry}]), the template with configure's values filled in."],
                load_from="//autoconf:autoconf_hdr.bzl",
            )
        )
        if out.endswith(".h"):
            subst_headers.append(out)
    state.subst_headers = subst_headers
    header_targets = config_targets + [slug(h) for h in subst_headers]
    if header_targets:
        # `AC_CONFIG_HEADERS([lib/config.h])`: sources still write `#include
        # <config.h>` (Makefile.am adds -Ilib), so that directory is an
        # include directory too.  In the root package `includes = ["."]` becomes the two
        # prefix attributes at the end (`_root_includes`).
        dirs = sorted(
            (
                {os.path.dirname(e.partition(":")[0]) for e in state.config_headers}
                | {os.path.dirname(h) for h in subst_headers}
            )
            - {""}
        )
        # make's src/config.h.in ends with `#include "../src/mkcustom.h"`: a file
        # every includer of config.h needs, declared here so the sandbox has it.
        template_includes: list[str] = []
        for entry, text in template_texts.items():
            out, _, template = entry.partition(":")
            for inc in _QUOTED_INCLUDE_RE.findall(text):
                rel = os.path.normpath(os.path.join(os.path.dirname(template or out), inc)).replace(os.sep, "/")
                # Only what the `*.h` glob below does not already list (gawk's custom.h is
                # at the top level; make's src/mkcustom.h is not).
                if (src / rel).is_file() and not rel.startswith("..") and "/" in rel and rel not in template_includes:
                    template_includes.append(rel)
        # Not what the globs below list already (make's src/mkcustom.h, under the config header's directory).
        plain = [f'"{t}"' for t in template_includes if os.path.dirname(t) not in dirs]
        # The config header's directory is an include directory of every dependent (make's
        # `includes = ["src"]`): its headers are declared with it, so a system header reaching
        # one through that path (MinGW's <unistd.h> includes <getopt.h>, found as src/getopt.h)
        # finds a declared file in the sandbox.
        glob_hdrs = headers_glob(["*.h"] + [f"{d}/*.h" for d in dirs]).text
        # Files a sub-configure's sources read across the package boundary
        # (gawk's extension/time.c includes "../missing_d/strptime.c"): they
        # ride along with the top-level headers, which every package depends on.
        crossing = _cross_package_includes(src, source_root or src)
        if state.config_subdirs and not package.path:
            # A sub-configure generates its own config.h.  Bazel passes
            # `-iquote` for the repository root and its generated-files root
            # on every compile, so the root config.h would shadow it whenever
            # the root config headers are an input: the sub-configures depend
            # on the top-level headers alone.
            listed = ", ".join(plain)
            attrs = {"hdrs": Raw(f"[{listed}] + {glob_hdrs}" if listed else glob_hdrs), "includes": ["."]}
            if crossing:
                attrs["textual_hdrs"] = crossing
            attrs["visibility"] = ["//:__subpackages__"]
            package.after.append(
                Rule(
                    "cc_library",
                    TOP_HEADERS,
                    attrs,
                    comments=[
                        (
                            "The top-level headers without config.h: the sub-configures' packages generate "
                            "their own, which a root config.h on the include path would shadow."
                        )
                    ],
                    load_from="@rules_cc//cc:cc_library.bzl",
                )
            )
            attrs = {"hdrs": [f":{t}" for t in header_targets], "includes": ["."] + dirs, "deps": [f":{TOP_HEADERS}"]}
        else:
            listed = ", ".join([quote_label(t) for t in header_targets] + plain)
            attrs = {"hdrs": Raw(f"[{listed}] + {glob_hdrs}"), "includes": ["."] + dirs}
            if crossing:
                attrs["textual_hdrs"] = crossing
        package.after.append(
            Rule(
                "cc_library",
                CONFIG_HEADERS,
                attrs,
                comments=["The config headers and the top-level headers, as `#include <config.h>` from any directory."],
                load_from="@rules_cc//cc:cc_library.bzl",
            )
        )
        state.header_libraries = [f":{CONFIG_HEADERS}"]
    if parent_headers and parent_headers not in state.header_libraries:
        state.header_libraries.append(parent_headers)
    package.after.extend(subst_rules)
    return extra_files, config_targets


def _root_includes(package: Package) -> None:
    """The root package's `includes = ["."]` goes, and every root library with headers gets
    `strip_include_prefix = "/"` with `include_prefix = "."`.  Together they reach a root header
    the way `-I$(top_srcdir)` served it, on every Bazel a registry consumer runs:

    - `includes = ["."]` would be the obvious spelling, but Bazel 7 and 8 refuse it at the main
      repository's root, and everywhere it turns the repository root into a `-isystem` entry,
      which gcc then drops from the quote chain it already holds through `-iquote`: a dependency's
      `config.h` earlier in the bracket chain (gmp's, for mpfr's `#include "config.h"`) is found
      first.
    - `strip_include_prefix = "/"` gives Bazel 9 a virtual include directory with every header
      under its repository path; Bazel 7 and 8 build that directory only for a header whose path
      changes or, with an `include_prefix`, for a generated one, which `include_prefix = "."`
      (a no-op on the path) provides: the generated `config.h` is reachable as `<config.h>`, and
      the source headers of the root through `-iquote`, as before.

    A library with a prefix of its own keeps it; a binary just loses the entry.  The virtual
    directory of `config_headers` must come before a dependency that exposes a `config.h` of its
    own (xz's), so the config headers library is first in `deps`."""
    for rule in package.after:
        if not isinstance(rule, Rule) or rule.kind not in ("cc_library", "cc_binary", "cc_test"):
            continue
        includes = rule.attrs.get("includes")
        if isinstance(includes, list) and "." in includes:
            remaining = [d for d in includes if d != "."]
            if remaining:
                rule.attrs["includes"] = remaining
            else:
                del rule.attrs["includes"]
        if (
            rule.kind == "cc_library"
            and "hdrs" in rule.attrs
            and not any(k in rule.attrs for k in ("strip_include_prefix", "include_prefix"))
        ):
            rule.attrs["include_prefix"] = "."
            rule.attrs["strip_include_prefix"] = "/"


_LICENSE_FILE_RE = re.compile(r"(COPYING|LICEN[CS]E|COPYRIGHT)(\..*)?$", re.IGNORECASE)


_CC_RULES = ("cc_library", "cc_binary", "cc_test")
_MODULE_FLAG_CONSTANTS = {"copts": "COPTS", "conlyopts": "CONLYOPTS", "cxxopts": "CXXOPTS"}


def _module_flags(package: Package, decisions: dec.Decisions, package_path: str) -> list[str]:
    """Compiler options every target of the package compiles with, from the never-requested
    `copts:<package>`, `conlyopts:<package>` and `cxxopts:<package>` decisions (ppp's
    `-Wno-error=incompatible-pointer-types`: a diagnostic clang 16 makes an error in code written
    for older compilers, which no consumer can set on a module it depends on).  `COPTS` is
    `NO_WARNINGS` plus the flags, and takes NO_WARNINGS' place in every target's `copts`;
    `CONLYOPTS` / `CXXOPTS` become the attributes of the same name."""
    notes: list[str] = []
    flags: dict[str, list[str]] = {}
    for kind in _MODULE_FLAG_CONSTANTS:
        decision = decisions.optional(f"{kind}:{package_path}", kind)
        flags[kind] = (decision.flags if decision is not None else None) or []
    if not any(flags.values()):
        return notes
    constants = []
    for kind, words in flags.items():
        if not words:
            continue
        name = _MODULE_FLAG_CONSTANTS[kind]
        value = Raw(f"{NO_WARNINGS} + {words!r}".replace("'", '"')) if kind == "copts" else list(words)
        constants.append(
            Assign(name, value, comments=[f"decisions.bzl: {kind}:{package_path}, for every target of the package."])
        )
        notes.append(f"{kind}:{package_path}: every target compiles with {' '.join(words)}")
    at = next(i for i, st in enumerate(package.before) if isinstance(st, Assign) and st.name == NO_WARNINGS) + 1
    package.before[at:at] = constants
    for rule in package.after:
        if not isinstance(rule, Rule) or rule.kind not in _CC_RULES:
            continue
        if flags["copts"]:
            copts = rule.attrs.get("copts")
            if isinstance(copts, Raw) and copts.text.startswith(NO_WARNINGS):
                rule.attrs["copts"] = Raw("COPTS" + copts.text[len(NO_WARNINGS) :])
            elif copts is None:
                rule.attrs["copts"] = Raw("COPTS")
        for kind in ("conlyopts", "cxxopts"):
            if flags[kind] and kind not in rule.attrs:
                rule.attrs[kind] = Raw(_MODULE_FLAG_CONSTANTS[kind])
    return notes


_STANDARD_ATTRS = {"C": "conlyopts", "CXX": "cxxopts"}


def _standard_flags(package: Package, state: ProjectState) -> None:
    """The standards configure switches the compiler into (`pinned_standards`), as `C_STD_OPTS` /
    `CXX_STD_OPTS` constants on every cc target and autoconf target of the package: the
    `conlyopts` / `cxxopts` the macro's or the flags' `-std=` amount to, per compiler family
    (`common.standard_statement`)."""
    for lang, standard in sorted(state.pinned_standards.items()):
        constant = STANDARD_CONSTANTS[lang]
        attr = _STANDARD_ATTRS[lang]
        at = next(
            (i + 1 for i, st in enumerate(package.before) if isinstance(st, Assign) and st.name == NO_WARNINGS),
            len(package.before),
        )
        package.before.insert(at, standard_statement(lang, standard, state.standard_origins.get(lang, "configure")))
        for rule in package.after:
            if isinstance(rule, Rule) and rule.kind in _CC_RULES:
                rule.attrs[attr] = _plus_constant(rule.attrs.get(attr), constant)
        for target in package.targets:
            if target.kind == "autoconf":
                target.attrs[attr] = _plus_constant(target.attrs.get(attr), constant)


def _plus_constant(value: AttrValue | None, constant: str) -> AttrValue:
    """`value + CONSTANT`: the constant alone for an absent or empty attribute."""
    if value is None or value == []:
        return Raw(constant)
    text = value.text if isinstance(value, Raw) else repr(value).replace("'", '"')
    return Raw(f"{text} + {constant}")


def _repo_bazel(state: ProjectState, *, metadata: bool) -> str | None:
    """REPO.bazel, the repository's defaults for every package of the module: the license metadata
    (`default_package_metadata`, autoconf.bzl's `package_metadata`), and the language standard
    configure switches the compiler into, written as the toolchain feature it will be and
    commented out until rules_cc names the standards (`C_STD_OPTS` pins it meanwhile).  None when
    there is nothing to set."""
    standards = [state.pinned_standards[lang] for lang in ("C", "CXX") if lang in state.pinned_standards]
    if not metadata and not standards:
        return None
    lines = ["# The repository's defaults: REPO.bazel applies to every package of the module.", "repo("]
    if metadata:
        lines += [
            "    # The license metadata every target carries (autoconf.bzl's package_metadata).",
            '    default_package_metadata = ["//:package_metadata"],',
        ]
    if standards:
        features = ", ".join(f'"{standard}"' for standard in standards)
        lines += [
            f"    # {STANDARD_FEATURES_TODO}",
            f"    # features = [{features}],",
        ]
    lines.append(")")
    return ownership.marked("\n".join(lines) + "\n", ".bazel", "REPO.bazel, the module's defaults")


def _license_rules(
    package: Package, src: Path, decisions: dec.Decisions, package_path: str, notes: list[str], *, repo_defaults: bool
) -> set[str]:
    """The root package exports the license files the release ships (COPYING, LICENSE), as the
    registry's hand-written entries do, and adds the registry's package metadata over them: a
    `license()` per file with its SPDX identifier, the `package_metadata` that carries them with
    the module's purl, and `package(default_package_metadata = ...)` so every target has them (REPO.bazel's
    `repo(default_package_metadata = ...)` instead, for every package of the module, with `repo_defaults`).
    The identifier comes from a `license:<file>` decision, else from the text and the source
    headers (`licenses.detect`); a file neither settles is requested, with its opening lines.
    `IGNORE` leaves a file exported without metadata.  Returns the repositories the rules load,
    for MODULE.bazel."""
    if package_path not in ("", ".") or not src.is_dir():
        return set()
    files = sorted(p.name for p in src.iterdir() if p.is_file() and _LICENSE_FILE_RE.fullmatch(p.name))
    if not files:
        return set()
    decided: dict[str, str] = {}
    for file in files:
        decision = decisions.optional(f"license:{file}", "license")
        if decision is not None:
            if decision.spdx:
                decided[file] = decision.spdx
            continue
        spdx, evidence = licenses.detect(src, file)
        if spdx:
            decided[file] = spdx
            notes.append(f"license:{file}: {spdx} ({evidence}); a `license:` decision overrides")
            continue
        head = (src / file).read_text(encoding="utf-8", errors="replace").splitlines()[:12]
        decisions.resolve(
            dec.Request(
                f"license:{file}",
                "license",
                file,
                f"{evidence}; the registry's package metadata needs its SPDX identifier",
                {f"{file}:1-{len(head)}": head},
            )
        )
    statements: list[Statement] = []
    if decided and not repo_defaults:
        statements.append(Raw('package(default_package_metadata = [":package_metadata"])'))
    listed = _list_literal(files, "    ") if len(files) > 1 else f'["{files[0]}"]'
    statements.append(Raw(f"exports_files({listed})"))
    names = []
    for file, spdx in decided.items():
        # Named by the file, never the bare `license`: the registry's older hand-written overlays
        # declare a rules_license `license` of their own, which must not stand in for this one.
        name = f"license_{slug(file)}"
        names.append(name)
        statements.append(
            Rule(
                "license",
                name,
                {
                    "kind": f"@package_metadata//licenses/spdx:{spdx}",
                    "text": file,
                },
                comments=[f"decisions.bzl: license:{file} is {spdx}."],
                load_from="@package_metadata//licenses/rules:license.bzl",
            )
        )
    if decided:
        statements.append(
            Rule(
                "package_metadata",
                "package_metadata",
                {
                    "attributes": [f":{n}" for n in names],
                    "purl": Raw("purl.bazel(\n        module_name(),\n        module_version(),\n    )"),
                },
                load_from="@package_metadata//rules:package_metadata.bzl",
            )
        )
        package.loads.append('load("@package_metadata//purl:purl.bzl", "purl")')
    package.before[0:0] = statements  # the first statements beneath the loads, where licensing goes
    return {"package_metadata"} if decided else set()


FLAG_KINDS = frozenset({"bool_flag", "string_flag", "int_flag"})


def _option_rules(package: Package, state: ProjectState) -> list[Statement]:
    """The configure options as build settings: only the ones some `select()`
    in the output keys on, each with the config_settings it uses.  An option
    only ever compared with yes / no is a `bool_flag`; one the script compares
    with other values is a `string_flag` (an `int_flag` when they are all
    numbers) with one config_setting per value."""
    body = "\n".join(render_statement(s) for s in [*package.before, *package.targets, *package.after])
    by_option: dict[str, set[str]] = {}
    for label, (name, value) in state.option_settings.items():
        if f'"{label}"' in body and name in state.flags:
            by_option.setdefault(name, set()).add(value)
    # A decision or the hand-written BUILD.bazel may key a select() on an option's
    # `:<name>_enabled` setting that no translated condition reached (gawk's `extensions`, whose
    # tests mix in the host OS; libssh2's `libz`, whose only test every invocation answers alike):
    # the option is a flag then too.
    for name in state.flags:
        if f'":{name}_enabled"' in body or f'":{name}_enabled"' in state.hand_build:
            by_option.setdefault(name, set()).add("yes")
    # An option a sub-configure shares with the root is the root's flag (`shared_options`): the
    # sub-package keys on `//:<name>_enabled` and declares nothing of its own.
    for name in state.shared_options:
        by_option.pop(name, None)
    state.option_names = sorted(by_option)
    rules: list[Statement] = []
    taken = {s.name for s in [*package.targets, *package.after] if hasattr(s, "name")}
    # The yes/no options, as one table with a `bool_flag` and a `config_setting` comprehension
    # over it (thirteen lines an option otherwise).  An option whose flag had to step aside, or
    # whose flag or setting BUILD.bazel replaces (the replacement goes by a rule's literal
    # name, which a comprehension has none of), is written out on its own.
    table: dict[str, bool] = {}
    table_comments: list[str] = []
    for name in sorted(by_option):
        flag = state.flags[name]
        if by_option[name] <= {"yes"} and name not in taken and not {name, f"{name}_enabled"} & set(state.hand_targets):
            table[name] = flag.generated_default
            table_comments.append(f"{name}: configure.ac: {flag.macro}([{flag.option}]): {flag.help}".rstrip(": "))
    if len(table) < 2:
        table = {}
    if table:
        rules.append(Assign(BOOL_OPTIONS, table, comments=table_comments))
        rules.append(
            Each(
                "bool_flag",
                {"name": Raw("name"), "build_setting_default": Raw("default"), "visibility": ["//visibility:public"]},
                f"name, default in {BOOL_OPTIONS}.items()",
                load_from="@bazel_skylib//rules:common_settings.bzl",
            )
        )
        rules.append(
            Each(
                "config_setting",
                {
                    "name": Raw('name + "_enabled"'),
                    "flag_values": Raw('{":" + name: "True"}'),
                },
                f"name in {BOOL_OPTIONS}",
            )
        )
    for name in sorted(by_option):
        if name in table:
            continue
        flag = state.flags[name]
        values = by_option[name]
        comment = f"configure.ac: {flag.macro}([{flag.option}]): {flag.help}".rstrip(": ")
        # An option named like a target: a generated library of that name (--with-foo next to
        # `libfoo.a`), or a hand-written rule of that name that is not a flag (an alias kept
        # for an archive's old label).  The flag steps aside to `<name>_option`; the
        # config_settings (what the selects key on) keep their names.  Under the target's name
        # the flag would be left out as replaced, and the config_setting would read a library
        # as a flag.  A flag BUILD.bazel declares itself replaces the generated one, as any
        # hand-written rule does.
        hand_kind = state.hand_targets.get(name)
        flag_name = (
            f"{name}_option" if name in taken or (hand_kind is not None and hand_kind not in FLAG_KINDS) else name
        )
        if values <= {"yes"}:
            rules.append(
                Rule(
                    "bool_flag",
                    flag_name,
                    {"build_setting_default": flag.generated_default, "visibility": ["//visibility:public"]},
                    comments=[comment],
                    load_from="@bazel_skylib//rules:common_settings.bzl",
                )
            )
            rules.append(
                Rule(
                    "config_setting",
                    f"{name}_enabled",
                    {"flag_values": {f":{flag_name}": "True"}},
                )
            )
            continue
        default = state.flag_defaults.get(name) or ("yes" if flag.generated_default else "no")
        allowed = sorted(values | {default, "yes", "no"})
        if all(re.fullmatch(r"-?[0-9]+", v) for v in values | {default}):
            rules.append(
                Rule(
                    "int_flag",
                    flag_name,
                    {"build_setting_default": int(default), "visibility": ["//visibility:public"]},
                    comments=[comment + " (a number)"],
                    load_from="@bazel_skylib//rules:common_settings.bzl",
                )
            )
        else:
            rules.append(
                Rule(
                    "string_flag",
                    flag_name,
                    {"build_setting_default": default, "values": allowed, "visibility": ["//visibility:public"]},
                    comments=[
                        comment + f" (--{'enable' if flag.macro == 'AC_ARG_ENABLE' else 'with'}-"
                        f"{flag.option}=VALUE; `yes` is the bare option)"
                    ],
                    load_from="@bazel_skylib//rules:common_settings.bzl",
                )
            )
        for value in sorted(values):
            setting = f"{name}_enabled" if value == "yes" else f"{name}_{slug(value)}"
            rules.append(
                Rule(
                    "config_setting",
                    setting,
                    {"flag_values": {f":{flag_name}": value}},
                )
            )
    return rules
