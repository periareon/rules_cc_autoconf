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
from dataclasses import dataclass, field
from pathlib import Path

from tools.convert import dataflow as df
from tools.convert import decisions as dec
from tools.convert import gnulibmk, lower
from tools.convert import libraries as registry_libraries
from tools.convert.emit import label_sort_key, render_statement
from tools.convert.expand import Expansion
from tools.convert.index import PortIndex
from tools.convert.ir import Assign, Call, Dep, Package, Raw, Rule, Target
from tools.convert.markers import Tree
from tools.convert.translate import (Options, ProjectState, Translator, basename, check_key, condition_names,
                                     default_tarname, describe, iso_standard, provided_names, slug)

# The snippet files gnulib's `make` splices into generated headers, keyed by
# the marker comment each replaces.
GL_INLINES = {
    "/* The definition of _GL_ARG_NONNULL is copied here.  */": "arg-nonnull.h",
    "/* The definition of _GL_WARN_ON_USE is copied here.  */": "warn-on-use.h",
    "/* The definition of _Noreturn is copied here.  */": "_Noreturn.h",
    "/* The definitions of _GL_FUNCDECL_RPL etc. are copied here.  */": "c++defs.h",
}

GNULIB_MK_CANDIDATES = ["lib/gnulib.mk", "gl/gnulib.mk", "gnulib/gnulib.mk", "lib/Makefile.am", "gl/Makefile.am"]

# Automake conditionals gnulib spells with a macro the converter does not
# evaluate, and what the hand-written overlays use for them.
KNOWN_AM_CONDITIONS = [
    # gl_LIBUNISTRING_VERSION_CMP: compile the bundled libunistring source
    # unless a system libunistring of a sufficient version was found.
    ("gl_LIBUNISTRING_VERSION_CMP", "HAVE_LIBUNISTRING!=yes"),
]
_INCLUDE_GNULIB_MK_RE = re.compile(r"^include\s+\S*(?:gnulib\.mk|Makefile\.gnulib)\s*$", re.M)
# gnulib-comp.m4 says where gnulib-tool put the library and what it named the fragment.
_GL_SOURCE_BASE_RE = re.compile(r"^\s*gl_source_base='([^']*)'", re.M)
_GL_MAKEFILE_NAME_RE = re.compile(r"--makefile-name=(\S+)")


@dataclass
class ProjectResult:
    package: Package
    module_bazel: str
    notes: list[str] = field(default_factory=list)
    name: str = ""
    version: str = ""
    homepage: str | None = None
    has_flags: bool = False
    subdirs: list[str] = field(default_factory=list)  # AC_CONFIG_SUBDIRS with a configure.ac of their own
    tarname: str = ""  # AC_INIT's tarname: `flex`, where the name is "the fast lexical analyser generator"
    extra_files: dict[str, str] = field(default_factory=dict)  # written next to BUILD.bazel (autoheader's config.h.in)
    state: "ProjectState | None" = None  # what the translation learned (templates, conditions): vendoring reads it
    source_tree_modules: list[str] = field(default_factory=list)
    """Repositories the BUILD files shipped in the source tree (or the --append fragment) load."""

    external_modules: dict[str, str] = field(default_factory=dict)
    """Registry libraries the project links (tools/convert/libraries.py): module -> version."""

    @property
    def module_name(self) -> str:
        return module_name(self.tarname or self.name)


def module_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_.-]", "_", name.lower())


def module_bazel_text(result: ProjectResult, rules_version: str, dev_override: str | None) -> str:
    return _module_bazel(result.tarname or result.name, result.version, rules_version, dev_override, result.has_flags,
                         source_modules=result.source_tree_modules, external_modules=result.external_modules)


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
    extra_loads: set[str] = frozenset(),
) -> ProjectResult:
    """Translate a whole project into a root package.  `command` is the
    `bazel run` line that reproduces the conversion, for the banners; `source_root` the
    whole source tree where `src` is a `--subdir` of it (the BUILD files shipped anywhere
    in the tree load into the generated module); `extra_loads` the repositories an `--append`
    fragment loads, declared the same way."""
    decisions = decisions if decisions is not None else dec.Decisions()
    m4_files = [basename(f) for f in expansion.m4_files]
    ported = {f for f in m4_files if index is not None and index.file_port(f) is not None}
    flatten = {f for f in m4_files if f == "gnulib-comp.m4"}
    selected = {f for f in m4_files if f not in ported and f not in flatten} | {expansion.configure_ac.name}

    sources = {}
    for relative in expansion.m4_files:
        path = expansion.work_dir / relative
        if path.is_file():
            sources[relative] = path.read_text(encoding="utf-8", errors="replace")
    sources[expansion.configure_ac.name] = expansion.configure_ac.read_text(encoding="utf-8", errors="replace")

    state = ProjectState(flatten_files=flatten, port_ambiguous=index.ambiguous if index is not None else set(),
                         port_substs=index.substs if index is not None else set(),
                         port_conditionals=index.conditionals if index is not None else {})
    options = Options(repo="@rules_cc_autoconf", in_repo=False, layout="package")
    translator = Translator(tree, index=index, selected_files=selected, m4_sources=sources,
                            options=options, project=state, decisions=decisions)
    translator.translate()
    main = translator.translate_root("autoconf", loc_file=expansion.configure_ac.name)
    notes_libraries = _registry_libraries_in_configure(sources, selected, state)
    # The root package, including the shared-probe helper targets the root
    # body created while it was translated.
    package = translator.packages.get("") or Package(path="", docstring="")
    empty = {t.name for t in package.targets if t.is_empty}
    package.targets = [t for t in package.targets if not t.is_empty]
    notes = list(translator.report.notes) + notes_libraries
    notes += _drop_duplicate_defines(main, package.targets, index, list(state.gnulib_packages.values()))
    notes += _header_variables(main, state, index,
                               list(state.gnulib_packages.values()) + list(state.gnulib_defaults.values()))
    segments, extensions = _confdefs(main)
    if extensions:
        package.before.append(Assign(
            "SYSTEM_EXTENSIONS", SYSTEM_EXTENSION_DEFINES,
            comments=["AC_USE_SYSTEM_EXTENSIONS: every probe configure runs afterwards sees these in",
                      "confdefs.h, so every compiling check lists them (the rules make that explicit)."]))
    package.targets.extend(segments)
    _configure_cc_deps(segments + [main], state)
    for target in package.targets + [main]:
        target.deps = [d for d in target.deps if d.label.lstrip(":") not in empty]
    for name in sorted(empty):
        notes.append(f"{name}: an empty macro (no checks, no requirements); no target emitted")

    init = state.init_args or ["", ""]
    name = init[0].strip() or src.name
    version = init[1].strip() if len(init) > 1 else ""
    package.docstring = generated_banner(f"{name} {version}".strip(), "Bazel build", command)

    # -- autoconf_package_info ------------------------------------------------
    # AC_INIT's defaults: the tarname is the lower-cased name without a `GNU `
    # prefix, and GNU packages get their www.gnu.org page as the URL.
    tarname = init[3].strip() if len(init) > 3 and init[3].strip() else default_tarname(name)
    state.tarname = tarname
    info_attrs: dict = {"package_name": name, "module_bazel": "MODULE.bazel", "strip_bcr_version": True}
    if len(init) > 2 and init[2].strip():
        info_attrs["package_bugreport"] = init[2].strip()
    if tarname != name:
        info_attrs["package_tarname"] = tarname
    if len(init) > 4 and init[4].strip():
        info_attrs["package_url"] = init[4].strip()
    elif name.startswith("GNU "):
        info_attrs["package_url"] = f"https://www.gnu.org/software/{tarname}/"
    if state.automake:
        info_attrs["aliases"] = {"PACKAGE": "PACKAGE_TARNAME", "VERSION": "PACKAGE_VERSION"}
    package.before.append(Rule(
        "autoconf_package_info", "package", info_attrs,
        comments=[f"configure.ac: AC_INIT({', '.join('[' + a.strip() + ']' for a in init)}); the version "
                  "comes from MODULE.bazel so a `.bcr.N` bump has one place to edit."],
        load_from="//autoconf:autoconf_package_info.bzl",
    ))

    options_at = len(package.before)  # the configure options go here, once their use is known

    # -- the main autoconf target -------------------------------------------------
    gnulib_deps = sorted(state.gnulib_packages.values())
    if gnulib_deps:
        package.before.append(Assign(
            "GNULIB_M4_DEPS", gnulib_deps,
            comments=["Gnulib m4 modules the configure script uses (m4/gnulib-comp.m4 gl_INIT and "
                      "the AC_REQUIREs reached from it), as the rules_cc_autoconf ports."],
        ))
        main.deps_suffix.append("GNULIB_M4_DEPS")
    gnulib_defaults = sorted(state.gnulib_defaults.values())
    if gnulib_defaults:
        package.before.append(Assign(
            "GNULIB_M4_DEFAULT_DEPS", gnulib_defaults,
            comments=["The gl_<HEADER>_H_DEFAULTS the configure script AC_REQUIREs: the headers' `:defaults`,",
                      "read beneath the results (a probe of the same name overrides a default)."],
        ))
        main.default_deps_suffix.append("GNULIB_M4_DEFAULT_DEPS")
    if state.indicator_substs:
        package.before.append(Assign(
            "GNULIB_MODULE_INDICATOR_SUBSTS", sorted(set(state.indicator_substs)),
            comments=["gl_<HEADER>_MODULE_INDICATOR calls in gl_INIT: the @GNULIB_*@ substitutions the",
                      ".in.h templates read; the rules_cc_autoconf <header>_h:defaults set them to 0."],
        ))
        main.checks.append(Raw("[\n        checks.AC_SUBST(name, 1)\n        for name in GNULIB_MODULE_INDICATOR_SUBSTS\n    ]"))
    indicator_defines = sorted(set(state.indicator_defines) - set(state.indicator_test_defines))
    # A later IN_<PKG>_GNULIB_TESTS definition overrides the library's 1.
    if indicator_defines:
        package.before.append(Assign(
            "GNULIB_MODULE_INDICATOR_DEFINES", indicator_defines,
            comments=["gl_MODULE_INDICATOR calls in gl_INIT: the config.h defines."],
        ))
        main.checks.append(Raw("[\n        checks.AC_DEFINE(name, 1)\n        for name in GNULIB_MODULE_INDICATOR_DEFINES\n    ]"))
    if state.indicator_test_defines:
        package.before.append(Assign(
            "GNULIB_MODULE_INDICATOR_TEST_DEFINES", dict(sorted(state.indicator_test_defines.items())),
            comments=["gl_MODULE_INDICATOR_FOR_TESTS: modules the package uses only in its tests; the",
                      "IN_<PACKAGE>_GNULIB_TESTS value is 0 in the library and 1 in the tests."],
        ))
        main.checks.append(Raw("[\n        checks.AC_DEFINE(name, value)\n        for name, value in "
                               "GNULIB_MODULE_INDICATOR_TEST_DEFINES.items()\n    ]"))
    main.add_dep(":package")
    main.comments.insert(0, "configure.ac: the project's own checks; the gnulib modules come through GNULIB_M4_DEPS.")
    package.targets.append(main)

    # -- config headers -------------------------------------------------------------
    extra_files: dict[str, str] = {}
    autoheader_template = getattr(expansion, "autoheader_template", None)
    if not state.config_headers and autoheader_template is not None and autoheader_template.is_file():
        template_text = autoheader_template.read_text(encoding="utf-8", errors="replace")
        if "#undef" in template_text and not (src / "config.h.in").exists():
            extra_files["config.h.in"] = template_text
            state.config_headers.append("config.h")
            notes.append("configure.ac has no AC_CONFIG_HEADERS (configure would pass the defines as -D flags): "
                         "autoheader's config.h.in is written next to BUILD.bazel and rendered as config.h, "
                         "which the sources see with HAVE_CONFIG_H (the BCR mpfr overlay does the same by hand)")
    config_targets = []
    for index_, entry in enumerate(state.config_headers):
        out, _, template = entry.partition(":")
        template = template or out + ".in"
        target_name = "config" if index_ == 0 else slug(out)
        config_targets.append(target_name)
        package.after.append(Rule(
            "autoconf_hdr", target_name,
            {"out": out, "template": template, "visibility": ["//visibility:public"], "deps": [":autoconf"]},
            comments=[f"configure.ac: AC_CONFIG_HEADERS([{entry}])."],
            load_from="//autoconf:autoconf_hdr.bzl",
        ))
    state.config_targets = config_targets
    if config_targets:
        # `AC_CONFIG_HEADERS([lib/config.h])`: sources still write `#include
        # <config.h>` (Makefile.am adds -Ilib), so that directory is an
        # include directory too.  `includes = ["."]` is fine: the generated
        # project is consumed as an external module (bazel_dep + override).
        dirs = sorted({os.path.dirname(e.partition(":")[0]) for e in state.config_headers} - {""})
        # make's src/config.h.in ends with `#include "../src/mkcustom.h"`: a file
        # every includer of config.h needs, declared here so the sandbox has it.
        template_includes: list[str] = []
        for entry in state.config_headers:
            out, _, template = entry.partition(":")
            template_path = src / (template or out + ".in")
            if template_path.is_file():
                for inc in _QUOTED_INCLUDE_RE.findall(template_path.read_text(encoding="utf-8", errors="replace")):
                    rel = os.path.normpath(os.path.join(os.path.dirname(template or out), inc)).replace(os.sep, "/")
                    # Only what the `*.h` glob below does not already list (gawk's custom.h is
                    # at the top level; make's src/mkcustom.h is not).
                    if (src / rel).is_file() and not rel.startswith("..") and "/" in rel and rel not in template_includes:
                        template_includes.append(rel)
        listed = ", ".join([quote_label(t) for t in config_targets] + [f'"{t}"' for t in template_includes])
        package.after.append(Rule(
            "cc_library", CONFIG_HEADERS,
            {"hdrs": Raw(f"[{listed}] + glob(\n        [\"*.h\"],\n"
                         "        allow_empty = True,\n    )"),
             "includes": ["."] + dirs, "visibility": ["//visibility:public"]},
            comments=["The config headers and the top-level headers, as `#include <config.h>` from any directory."],
            load_from="@rules_cc//cc:cc_library.bzl"))
        state.header_libraries = [f":{CONFIG_HEADERS}"]
    for entry in state.config_files:
        out, _, template = entry.partition(":")
        template = template or out + ".in"
        out, template = _inside_project(src, out), _inside_project(src, template)
        if out is None or template is None:
            notes.append(f"AC_CONFIG_FILES([{entry}]): leaves the project directory; not generated")
            continue
        if re.search(r"(^|/)(GNUmakefile|Makefile)(\.in)?$", out) or not (src / template).is_file():
            notes.append(f"AC_CONFIG_FILES([{entry}]): not generated (an autoconf_hdr with mode = \"subst\" "
                         "renders a template; Makefiles have no Bazel equivalent)")
            continue
        package.after.append(Rule(
            "autoconf_hdr", slug(out), {"out": out, "mode": "subst", "template": template, "deps": [":autoconf"]},
            comments=[f"configure.ac: AC_CONFIG_FILES([{entry}]), the template with configure's values filled in."],
            load_from="//autoconf:autoconf_hdr.bzl"))

    # -- gnulib.mk ------------------------------------------------------------------
    mk_path = _gnulib_mk_path(src, expansion)
    if mk_path is not None:
        _gnulib_library(package, mk_path, src, state, config_targets, notes, decisions)
        notes += _condition_providers(main, package, state, index)
        provided = set().union(*(provided_names(c) for items in [main.checks, *main.arm_lists]
                                 for c in items if isinstance(c, Call)))
        for subst, value in _literal_sed_substs(mk_path.read_text(encoding="utf-8", errors="replace")).items():
            if subst not in provided:
                main.checks.append(Call("checks.AC_SUBST", [subst, value], comments=[
                    f"{mk_path.name}: the wrapper headers' @{subst}@ is the literal `{value}` in the sed."]))
        # A template newer than a port's gnulib snapshot reads an indicator
        # no <header>_h default sets: 0, the module is not in this project.
        known = state.port_substs | set(state.indicator_substs) | provided
        unknown_names = {n for expr in state.src_condition_exprs.values() for n in condition_names(expr)
                         if _GNULIB_VARIABLE_RE.match(n)} - known - (index.provided if index is not None else set())
        for name in sorted(unknown_names):
            # bison's gnulib tests REPLACE_CALLOC; the ports' gnulib spells it
            # REPLACE_CALLOC_FOR_CALLOC_GNU: nothing provides the old name.
            main.checks.append(Call("checks.AC_SUBST", [name, 0], comments=[
                f"{name} is tested by gnulib.mk but set by no rules_cc_autoconf port (a different gnulib); "
                "0, as for an absent module."]))
            notes.append(f"{name}: gnulib.mk condition over a variable no port provides; set to 0")
        for indicator in sorted(_template_indicators(state.templates) - known):
            main.checks.append(Call("checks.AC_SUBST", [indicator, 0], comments=[
                f"@{indicator}@ is read by a template but set by no rules_cc_autoconf port (newer gnulib); "
                "the module is not in this project."]))
            notes.append(f"{indicator}: module indicator read by a template that no port provides; set to 0")
    else:
        notes.append("no lib/gnulib.mk found: no gnulib library; programs and libraries come from the Makefile.am "
                     "tree (or hand-written Makefile.in files)")

    state.literal_substs = _literal_substs(main)
    _programs_tree(package, src, mk_path, decisions, state, notes)
    _require_standards(main, state, notes)
    tools = sorted({s.kind for s in package.after if isinstance(s, Rule) and s.kind in ("bison", "flex")})
    option_rules = _option_rules(package, state)
    package.before[options_at:options_at] = option_rules

    uses_skylib = bool(option_rules) or any("@bazel_skylib" in (getattr(s, "load_from", None) or "")
                                           for s in package.after) \
        or any("@bazel_skylib" in line for line in package.loads)
    loaded = source_tree_loads(source_root or src) | set(extra_loads)
    source_modules = sorted(r for r in loaded if r in SOURCE_TREE_MODULES)
    for repo in sorted(loaded - set(SOURCE_TREE_MODULES) - _GENERATED_MODULES):
        notes.append(f"BUILD files in the source tree (or the --append fragment) load @{repo}; no module is "
                     "known for it: add its bazel_dep to MODULE.bazel")
    module_bazel = _module_bazel(state.tarname or name, version, rules_version, dev_override,
                                 uses_skylib or "bazel_skylib" in loaded, tools, command, source_modules=source_modules,
                                 external_modules=state.external_modules)
    homepage = info_attrs.get("package_url")
    subdirs = []
    for subdir in state.config_subdirs:
        if any((src / subdir / c).is_file() for c in ("configure.ac", "configure.in")):
            subdirs.append(subdir.strip("/"))
        else:
            notes.append(f"AC_CONFIG_SUBDIRS({subdir}): no configure.ac there; not followed")
    for subdir in _configured_subdirs(src, set(subdirs)):
        subdirs.append(subdir)
        notes.append(f"{subdir}/: has a configure.ac of its own and the Makefile.am's SUBDIRS is computed by "
                     "configure (texlive's $(MAKE_SUBDIRS)); converted as a package of this module")
    return ProjectResult(package, module_bazel, notes, name=name, version=version, homepage=homepage,
                         extra_files=extra_files, state=state, source_tree_modules=source_modules,
                         external_modules=dict(state.external_modules),
                         has_flags=bool(option_rules), subdirs=subdirs, tarname=state.tarname or "")


_LIST_CHECKS = {"macros.AC_CHECK_HEADERS", "macros.AC_CHECK_FUNCS", "macros.AC_CHECK_DECLS", "macros.AC_CHECK_TYPES",
                "macros.AC_CHECK_MEMBERS"}
# The define-less single check that keeps a list subject's cache variable.
_SINGULAR_OF = {"macros.AC_CHECK_HEADERS": "checks.AC_CHECK_HEADER", "macros.AC_CHECK_FUNCS": "checks.AC_CHECK_FUNC",
                "macros.AC_CHECK_DECLS": "checks.AC_CHECK_DECL", "macros.AC_CHECK_TYPES": "checks.AC_CHECK_TYPE",
                "macros.AC_CHECK_MEMBERS": "checks.AC_CHECK_MEMBER"}

# Checks that compile a program and so see configure's confdefs.h: in the
# rules a probe sees an earlier define only when it lists it in
# `compile_defines`, and only results of *dependencies* are available.
_COMPILING_CHECKS = {
    "checks.AC_TRY_COMPILE", "checks.AC_TRY_LINK", "checks.AC_CHECK_HEADER", "checks.AC_CHECK_FUNC",
    "checks.AC_CHECK_DECL", "checks.AC_CHECK_TYPE", "checks.AC_CHECK_MEMBER",
} | _LIST_CHECKS
# Where the feature-test macros of AC_USE_SYSTEM_EXTENSIONS change the
# answer: what a header declares.  Header existence and function linkage
# do not depend on them, and those checks stay identical to the ports'
# (the toolchain cache then shares the result instead of seeing a duplicate).
_DECLARATION_CHECKS = {
    "checks.AC_TRY_COMPILE", "checks.AC_TRY_LINK", "checks.AC_CHECK_DECL", "checks.AC_CHECK_TYPE",
    "checks.AC_CHECK_MEMBER", "macros.AC_CHECK_DECLS", "macros.AC_CHECK_TYPES", "macros.AC_CHECK_MEMBERS",
}
# What the ports of the two macros every configure.ac starts with define:
# listed on each probe the way confdefs.h carries them in configure.
SYSTEM_EXTENSIONS_PORTS = ("autoconf/macros/AC_USE_SYSTEM_EXTENSIONS", "gnulib/m4/extensions")
SYSTEM_EXTENSION_DEFINES = [
    "_ALL_SOURCE", "_COSMO_SOURCE", "_DARWIN_C_SOURCE", "_GNU_SOURCE", "_HPUX_ALT_XOPEN_SOCKET_API",
    "_NETBSD_SOURCE", "_OPENBSD_SOURCE", "_POSIX_PTHREAD_SEMANTICS", "_TANDEM_SOURCE", "__EXTENSIONS__",
    "__STDC_WANT_MATH_SPEC_FUNCS__",
]
INCLUDES_DEFAULT_PORT = "autoconf/macros/AC_CHECK_INCLUDES_DEFAULT"
INCLUDES_DEFAULT_DEFINES = {
    "STDC_HEADERS", "HAVE_SYS_TYPES_H", "HAVE_SYS_STAT_H", "HAVE_STRINGS_H", "HAVE_INTTYPES_H", "HAVE_STDINT_H",
    "HAVE_UNISTD_H", "HAVE_STDIO_H", "HAVE_STDLIB_H", "HAVE_STRING_H", "HAVE_WCHAR_H",
}
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def _option_rules(package: Package, state: ProjectState) -> list:
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
    rules: list = []
    taken = {s.name for s in [*package.targets, *package.after] if hasattr(s, "name")}
    for name in sorted(by_option):
        flag = state.flags[name]
        values = by_option[name]
        comment = f"configure.ac: {flag.macro}([{flag.option}]): {flag.help}".rstrip(": ")
        # libpcap's --with-pcap next to its `pcap` library: the flag steps aside,
        # the config_settings (what the selects key on) keep their names.
        flag_name = f"{name}_option" if name in taken else name
        if values <= {"yes"}:
            rules.append(Rule("bool_flag", flag_name, {"build_setting_default": flag.default, "visibility": ["//visibility:public"]},
                              comments=[comment], load_from="@bazel_skylib//rules:common_settings.bzl"))
            rules.append(Rule("config_setting", f"{name}_enabled", {"flag_values": {f":{flag_name}": "True"}}))
            continue
        default = state.flag_defaults.get(name) or ("yes" if flag.default else "no")
        allowed = sorted(values | {default, "yes", "no"})
        if all(re.fullmatch(r"-?[0-9]+", v) for v in values | {default}):
            rules.append(Rule("int_flag", flag_name, {"build_setting_default": int(default), "visibility": ["//visibility:public"]},
                              comments=[comment + " (a number)"], load_from="@bazel_skylib//rules:common_settings.bzl"))
        else:
            rules.append(Rule("string_flag", flag_name, {"build_setting_default": default, "values": allowed,
                                                    "visibility": ["//visibility:public"]},
                              comments=[comment + f" (--{'enable' if flag.macro == 'AC_ARG_ENABLE' else 'with'}-"
                                        f"{flag.option}=VALUE; `yes` is the bare option)"],
                              load_from="@bazel_skylib//rules:common_settings.bzl"))
        for value in sorted(values):
            setting = f"{name}_enabled" if value == "yes" else f"{name}_{slug(value)}"
            rules.append(Rule("config_setting", setting, {"flag_values": {f":{flag_name}": value}}))
    return rules


GENERATED_MARK = "@" + "generated"  # the marker review tools collapse files on; spelled apart to keep it out of this file


DEFAULT_COMMAND = "bazel run //tools/convert -- convert --src <sources> --out <this directory>"


def generated_banner(title: str, what: str, command: str | None = None) -> str:
    """The docstring of a generated file: what it is, the command that
    regenerates it, and that decisions.bzl next to it is the one file to edit."""
    return (f"{title}: {what} generated by rules_cc_autoconf convert.\n\n"
            f"{GENERATED_MARK} from configure.ac and the Makefile.am files, with the choices in\n"
            "decisions.bzl, the one hand-maintained file next to this one.  To regenerate,\n"
            "from a clone of rules_cc_autoconf:\n\n"
            f"    {command or DEFAULT_COMMAND}\n\n"
            "Edit decisions.bzl and rerun; do not edit this one.")


def quote_label(target: str) -> str:
    return f'":{target}"'


def _gnulib_mk_path(src: Path, expansion) -> Path | None:
    """`lib/gnulib.mk`, or where gnulib-comp.m4 says the gnulib library lives
    (libpaper: `gl_source_base='src/libgnu'`, `--makefile-name=Makefile.gnulib`)."""
    candidates = list(GNULIB_MK_CANDIDATES)
    for relative in expansion.m4_files:
        path = expansion.work_dir / relative
        if basename(relative) != "gnulib-comp.m4" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        base = _GL_SOURCE_BASE_RE.search(text)
        if base:
            named = _GL_MAKEFILE_NAME_RE.search(text)
            names = ([named.group(1)] if named else []) + ["gnulib.mk", "Makefile.gnulib", "Makefile.am"]
            candidates = [f"{base.group(1)}/{n}" for n in names] + candidates
    return next((src / c for c in candidates if (src / c).is_file()), None)


def _inside_project(src: Path, path: str) -> str | None:
    """`path` normalized relative to the project directory, or None when it climbs out
    (tcl's unix/configure.ac names `../unix/tcl.pc.in`: the same directory, by a detour)."""
    try:
        relative = (src / path).resolve().relative_to(src.resolve())
    except ValueError:
        return None
    return relative.as_posix()


def _config_deps(state: ProjectState) -> list[str]:
    return list(state.header_libraries)


# `-e 's|@''GUARD_PREFIX''@|GL|g'`: a wrapper-header substitution gnulib.mk
# spells as a literal rather than a configure variable.
_LITERAL_SED_RE = re.compile(r"s\|@''([A-Z][A-Z0-9_]*)''@\|([A-Za-z0-9_./-]*)\|g")


_QUOTED_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
_GNULIB_VARIABLE_RE = re.compile(r"^(?:REPLACE|HAVE|GNULIB|GL_GENERATE|NEED|WANT)_[A-Z0-9_]+$")
_TEMPLATE_INDICATOR_RE = re.compile(r"@(GNULIB_[A-Z0-9_]+)@")


def _template_indicators(templates: list[Path]) -> set[str]:
    """The @GNULIB_X@ module indicators the wrapper-header templates read."""
    out: set[str] = set()
    for template in templates:
        if template.is_file():
            out |= set(_TEMPLATE_INDICATOR_RE.findall(template.read_text(encoding="utf-8", errors="replace")))
    return out


def _literal_sed_substs(mk_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in _LITERAL_SED_RE.findall(mk_text):
        if not value.startswith("$"):
            out.setdefault(name, value)
    return out


def _header_variables(main: Target, state: ProjectState, index: PortIndex | None,
                      extra_labels: list[str]) -> list[str]:
    """`GL_GENERATE_<HDR>_H` for the gl_CONDITIONAL_HEADER calls of gl_INIT's
    own code (the module's inline code sets it from the header check), unless
    a port among the dependencies provides it already (sigsegv)."""
    notes: list[str] = []
    ported = _port_provided([main], index, extra_labels, variables=True)
    for var, cond in sorted(state.header_conditions.items()):
        if var in ported:
            continue
        lowered = lower.expression(_gnulib_names(state)(cond))
        if not lowered.ok or lowered.expr is None or var in condition_names(lowered.expr):
            notes.append(f"{var}: gl_CONDITIONAL_HEADER's condition cannot be expressed "
                         f"({lowered.unknown or describe(cond)}); the variable comes from a header port's "
                         ":defaults, if one in the dependencies provides it")
            continue
        main.checks.append(Call("checks.M4_VARIABLE", [var], {
            "condition": lowered.expr, "if_true": "true", "if_false": "false",
        }, comments=[f"gl_CONDITIONAL_HEADER: {var} from gl_INIT's inline module code."]))
    return notes


_LINK_TOKEN_RE = re.compile(r'(?<![\w./-])-l([A-Za-z0-9_+.-]+)\b')
_INCLUDE_RE = re.compile(r'#\s*include\s*[<"]([A-Za-z0-9_./-]+\.h)[>"]')
# Headers that name a registry library: a probe that includes one links it.
KNOWN_HEADERS = {"gmp.h": "gmp", "zlib.h": "z", "bzlib.h": "bz2", "lzma.h": "lzma", "curses.h": "ncurses",
                 "ncurses.h": "ncurses", "readline/readline.h": "readline", "pcap/pcap.h": "pcap", "pcap.h": "pcap"}


def _registry_libraries_in_configure(sources: dict[str, str], selected: set[str], state: ProjectState) -> list[str]:
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
                if header in KNOWN_HEADERS:
                    found.setdefault(KNOWN_HEADERS[header], f"#include <{header}>")
        for name, how in sorted(found.items()):
            if name in state.configure_libs:
                continue
            library = registry_libraries.known(name)
            state.external_modules[library.module] = library.version
            state.configure_libs[name] = library.label
            notes.append(f"{file}: {how}: the registry module {library.module} ({library.label}) is declared in "
                         "MODULE.bazel, probed through the main target's cc_deps and linked by the project's targets")
    return notes


def _configure_cc_deps(targets: list[Target], state: ProjectState) -> None:
    """The registry libraries configure links (`configure_libs`) as the `cc_deps` of the root
    targets: every probe compiles against their headers and links their archives, as a configure
    run with the libraries installed does."""
    labels = sorted(set(state.configure_libs.values()))
    if not labels:
        return
    for target in targets:
        target.attrs["cc_deps"] = labels


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
    provided = set(_port_provided(others + [main], index, labels, variables=True))
    provided |= set().union(*(provided_names(c) for t in others + [main] for items in [t.checks, *t.arm_lists]
                              for c in items if isinstance(c, Call)))
    for name in sorted({n for expr in state.src_condition_exprs.values() for n in condition_names(expr)} - provided):
        candidates = [p for p in index.providers_of(name) if p.package.startswith("gnulib/m4/")]
        if len(candidates) == 1 and candidates[0].package not in state.gnulib_packages:
            port = candidates[0]
            label = f"@rules_cc_autoconf{port.label()}"
            state.gnulib_packages[port.package] = label
            assign = next((s for s in package.before if isinstance(s, Assign) and s.name == "GNULIB_M4_DEPS"), None)
            if assign is not None:
                assign.value = sorted(set(assign.value) | {label})
            else:
                main.add_dep(label, f"{name} for gnulib.mk")
            notes.append(f"gnulib.mk reads {name}, which none of the project's gnulib modules provides; "
                         f"{port.label()} does (the project's gnulib snapshot computed it elsewhere): added to "
                         "GNULIB_M4_DEPS")
        elif len(candidates) > 1:
            notes.append(f"gnulib.mk reads {name}, which none of the project's gnulib modules provides; "
                         f"{', '.join(p.label() for p in candidates)} do: add the right one to the deps")
    return notes


def _port_provided(targets: list[Target], index: PortIndex | None, extra_labels: list[str] = (),
                   variables: bool = False):
    """The defines and the substitutions the port dependencies of `targets`
    (and `extra_labels`: the gnulib modules, appended through a list
    variable) provide, each with the dependency's label.  The two are kept
    apart: the rules do (string_h substs HAVE_DECL_MEMEQ for its template
    while configure.ac defines it)."""
    defines: dict[str, str] = {}
    substs: dict[str, str] = {}
    m4_variables: dict[str, str] = {}  # the M4 variables and, with `variables`, the defaults too
    if index is None:
        return m4_variables if variables else (defines, substs)
    labels = [dep.label for target in targets for dep in target.deps + target.default_deps] + list(extra_labels)
    seen: set[str] = set()
    queue = [(label.split("//", 1)[1].split(":")[0], label) for label in labels if "//" in label]
    while queue:  # the ports' own deps too: wint_t comes in through wctype_h
        package_name, label = queue.pop(0)
        port = index.by_package.get(package_name)
        if port is None or package_name in seen:
            continue
        seen.add(package_name)
        for name in sorted(port.defines):
            defines.setdefault(name, label)
        for name in sorted(port.substs):
            substs.setdefault(name, label)
        for name in sorted(port.variables):
            m4_variables.setdefault(name, label)
        # A port's defaults are read beneath the results: a condition may read
        # them, a local result of the name overrides them (no duplicate).
        for name in sorted(port.default_substs | port.default_variables | port.default_defines):
            m4_variables.setdefault(name, label)
        queue += [(dep, f"{label} (through {dep})" if dep != package_name else label) for dep in sorted(port.deps)]
    if variables:
        m4_variables.update(defines)  # GL_GENERATE_X_H is spelled either way in the ports
        return m4_variables
    return defines, substs


def _local_provided(item: Call) -> tuple[set[str], set[str]]:
    """The defines and the substs one check produces (cache variables aside)."""
    if item.fn == "checks.AC_SUBST":
        return set(), {str(item.args[0])} if item.args else set()
    return {n for n in provided_names(item) if not n.startswith(("ac_cv_", "gl_cv_"))}, set()


def _drop_duplicate_defines(main: Target, others: list[Target], index: PortIndex | None = None,
                            extra_labels: list[str] = ()) -> list[str]:
    """configure.ac's `AC_CHECK_FUNCS([lstat])` next to a project macro that
    defines HAVE_LSTAT itself: configure defines it twice, consistently; the
    rules reject two producers.  The plain list entry goes, the macro's
    probe (which knows more) stays.  Likewise a check whose define (or
    subst) a port dependency already provides: the port's probe stays
    (`AC_CHECK_FUNCS([fchown])` next to gl_FUNC_CHOWN, which checks fchown
    itself)."""
    ported_defines, ported_substs = _port_provided(others + [main], index, extra_labels)
    produced: dict[str, str] = {}
    for target in others + [main]:
        for items in [target.checks] + target.arm_lists:
            for item in items:
                if isinstance(item, Call) and item.fn not in _LIST_CHECKS:
                    for name in provided_names(item):
                        produced.setdefault(name, target.name)
    notes: list[str] = []
    notes += _merge_duplicate_defines(main)
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
                    notes.append(f"{target.loc.file if target.loc else 'configure.ac'}: {check_key(item)} left out: "
                                 f"{', '.join(sorted(set(taken.values())))} already provides "
                                 f"{', '.join(sorted(taken))} (configure produces it twice, consistently)")
    for items in [main.checks] + main.arm_lists:
        for item in list(items):
            if not isinstance(item, Call) or item.fn not in _LIST_CHECKS or not item.args:
                continue
            kept, dropped = [], []
            for subject in item.args[0]:
                names = provided_names(Call(item.fn, [[subject]]))
                owner = next((produced.get(n) or ported_defines.get(n) for n in names
                              if n in produced or n in ported_defines), None)
                (dropped if owner else kept).append((subject, owner))
            if not dropped:
                continue
            singular = _SINGULAR_OF[item.fn]
            carried = {k: v for k, v in item.kwargs.items() if k in ("includes", "compile_defines", "language", "copts")}
            at = items.index(item)
            for subject, owner in dropped:
                notes.append(f"configure.ac: {item.fn.split('.')[1]}([{subject}]) left out: {owner} already "
                             "defines the result (configure defines it twice, consistently); its cache variable "
                             "stays as a define-less check")
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
    segments: list[list] = [[]]
    produced: list[set[str]] = [set()]  # per segment
    for item in main.checks:
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
    if len(segments) == 1:
        return [], extensions
    earlier_targets: list[Target] = []
    for index, checks in enumerate(segments[:-1], start=1):
        target = Target(name=f"autoconf_{index}", checks=checks, visibility="//visibility:private")
        target.comments.append(f"configure.ac, part {index}: the checks before a probe that compiles against "
                               "one of their results (`compile_defines` reaches dependencies only).")
        target.deps = list(main.deps) + [Dep(f":autoconf_{i}") for i in range(1, index)]
        target.deps_suffix = list(main.deps_suffix)
        earlier_targets.append(target)
    main.checks = segments[-1]
    for index in range(1, len(segments)):
        main.add_dep(f":autoconf_{index}", f"configure.ac, part {index}")
    return earlier_targets, extensions


def _merge_duplicate_defines(target: Target) -> list[str]:
    """`AC_DEFINE([HAVE_X])` reached on two paths of configure.ac: one define,
    required under either condition (configure writes it twice, identically).
    The same for a substitution (libtool looks OBJDUMP up twice)."""
    notes: list[str] = []
    seen: dict[tuple, Call] = {}
    for item in list(target.checks):
        if not isinstance(item, Call) or item.fn not in ("checks.AC_DEFINE", "checks.AC_DEFINE_UNQUOTED",
                                                          "checks.AC_SUBST"):
            continue
        key = (item.fn, tuple(str(a) for a in item.args), str(item.kwargs.get("condition")))
        first = seen.get(key)
        if first is None:
            seen[key] = item
            continue
        a, b = first.kwargs.get("requires"), item.kwargs.get("requires")
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
        if not isinstance(item, Call) or item.fn in ("checks.AC_DEFINE", "checks.AC_DEFINE_UNQUOTED",
                                                      "checks.AC_SUBST", "checks.M4_VARIABLE") \
                or item.fn in _LIST_CHECKS:
            continue
        defines = _local_provided(item)[0]
        key = check_key(item) + repr(sorted((k, str(v)) for k, v in item.kwargs.items()))
        if key in seen_calls:
            target.checks.remove(item)
            notes.append(f"configure.ac: {check_key(item)} appears twice; one probe")
            continue
        seen_calls.add(key)
        taken = defines & seen_defines
        if taken:
            target.checks.remove(item)
            notes.append(f"configure.ac: {check_key(item)} left out: {', '.join(sorted(taken))} is already produced "
                         "by an earlier probe of this target (configure defines it once either way)")
            continue
        seen_defines |= defines
    # Two unconditional substitutions of one name with different values
    # (libpcap's BISON_BYACC): the later assignment is what configure keeps.
    last_unconditional: dict[str, Call] = {}
    for item in list(target.checks):
        if isinstance(item, Call) and item.fn == "checks.AC_SUBST" and len(item.args) == 2 \
                and not item.kwargs.get("requires") and "condition" not in item.kwargs:
            earlier = last_unconditional.get(str(item.args[0]))
            if earlier is not None:
                target.checks.remove(earlier)
                notes.append(f"configure.ac: {item.args[0]} is substituted twice ({earlier.args[1]!r}, then "
                             f"{item.args[1]!r}); the later value stands, as in configure")
            last_unconditional[str(item.args[0])] = item
    # `X=true` under a condition, `X=false` under its negation (bison's
    # C_COMPILER_POSIXLY_CORRECT): one subst with if_true / if_false.
    by_name: dict[tuple, list[Call]] = {}
    for item in target.checks:
        if isinstance(item, Call) and item.fn in ("checks.AC_SUBST", "checks.AC_DEFINE") and len(item.args) == 2 \
                and isinstance(item.kwargs.get("requires"), list) and len(item.kwargs["requires"]) == 1 \
                and "condition" not in item.kwargs:
            by_name.setdefault((item.fn, str(item.args[0])), []).append(item)
    for (fn, name), items in by_name.items():
        if len(items) != 2:
            continue
        first, second = items
        c1, c2 = first.kwargs["requires"][0], second.kwargs["requires"][0]
        if c2 != f"!{c1}" and c1 != f"!{c2}":
            continue
        positive, negative = (first, second) if c2 == f"!{c1}" else (second, first)
        positive.kwargs.pop("requires")
        positive.kwargs.update({"condition": positive.kwargs.get("condition") or (c1 if positive is first else c2),
                                "if_true": positive.args[1], "if_false": negative.args[1]})
        positive.args = [positive.args[0]]
        target.checks.remove(negative)
        notes.append(f"configure.ac: {name} is set on both branches of a condition; one conditional "
                     f"{fn.split('.')[1]}")
    return notes


# `$ERRNO_H`, `$STDINT_H`: gnulib's header variables, non-empty exactly when
# gl_CONDITIONAL_HEADER sets GL_GENERATE_<HDR>_H (what the ports provide).
_HEADER_VAR_RE = re.compile(r"^(?!HAVE_|REPLACE_|GNULIB_|GL_|NEXT_)[A-Z][A-Z0-9_]*_H$")


def _gnulib_names(state: ProjectState):
    """Spell a gnulib.mk condition the way the ports' results are named:
    a variable that is both a subst and a define of a port by its subst
    result (`ac_cv_subst_HAVE_X`: the rules reject the ambiguous bare name),
    and a header variable (`test -n "$ERRNO_H"`) by `GL_GENERATE_ERRNO_H`."""
    def atom(a):
        if _HEADER_VAR_RE.match(a.name):
            generated = df.Eq(f"GL_GENERATE_{a.name}", "true")
            if isinstance(a, df.Truthy):
                return generated
            if isinstance(a, df.Eq):
                return df.neg(generated) if a.value == "" else generated
            return a
        if a.name in state.port_ambiguous:
            name = f"ac_cv_subst_{a.name}"
            if isinstance(a, df.Truthy):
                return df.Truthy(name)
            if isinstance(a, df.Eq):
                return df.Eq(name, a.value)
            return df.Glob(name, a.pattern)
        return a
    return lambda cond: df.map_atoms(cond, atom)


def _am_block(text: str, name: str, conditional: str) -> dict:
    """`{"<file>:<lines>": [...]}`: the `if COND ... endif` block of an automake file."""
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
                    return {f"{name}:{start}-{end}": [l.rstrip() for l in lines[start - 1:end]]}
        break
    return {}


def _gnulib_library(package: Package, mk_path: Path, src: Path, state: ProjectState,  # noqa: PLR0913
                    config_targets: list[str], notes: list[str], decisions: dec.Decisions) -> None:
    """The gnulib library of `lib/gnulib.mk` (together with the Makefile.am that includes it): the
    generated and conditional wrapper headers (`autoconf_hdr`, `cc_gnulib_conditional_hdrs`), the
    private headers library, `autoconf_srcs` for the sources under gnulib's conditionals (host-OS
    ones as a platform `select()`), and the `cc_library` itself, named per `_library_target`.
    Records the library on `state` for the programs that link it.
    """
    lib_dir = mk_path.parent.relative_to(src).as_posix()
    mk_text = mk_path.read_text(encoding="utf-8", errors="replace")
    # gnulib.mk is a fragment: the library it fills (`noinst_LIBRARIES = libfoo.a`)
    # is declared by the Makefile.am that includes it.
    parse_text = mk_text
    sibling = mk_path.parent / "Makefile.am"
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
        notes.append(f"{mk_path.name}: paths carry `{lib_dir}/` (a non-recursive Makefile.am); the directory is "
                     "stripped and added back once")
    libraries = [l for l in mk.libraries.values() if l.sources or l.libadd]
    if not libraries:
        notes.append(f"{mk_path}: no library with sources")
        return
    library = libraries[0]
    if len(libraries) > 1:
        notes.append(f"{mk_path}: several libraries ({', '.join(l.archive for l in libraries)}); "
                     f"only {library.archive} is emitted")
    lib_name = library.archive.rsplit("/", 1)[-1]  # a non-recursive Makefile.am names it `lib/libsed.a`
    lib_name = lib_name[:-2] if lib_name.endswith(".a") else lib_name
    if lib_name.startswith("lib"):
        lib_name = lib_name[3:]
    upper = re.sub(r"[^A-Za-z0-9]", "_", lib_name).upper()

    inlines = {marker: f"{prefix}{file}" for marker, file in GL_INLINES.items() if (src / prefix / file).is_file()}
    if inlines:
        package.after.append(Assign(
            "GL_INLINES", inlines,
            comments=["Snippet files gnulib's `make` splices into each generated header."],
        ))
    inlines_expr = "GL_INLINES" if inlines else "{}"

    generated = {h.out: h for h in mk.headers if h.condition is None}
    conditional = [h for h in mk.headers if h.condition is not None]
    state.templates = [src / prefix / h.template for h in mk.headers]
    if generated:
        package.after.append(Assign(
            f"{upper}_GENERATED_HDRS",
            {f"{prefix}{out}": f"{prefix}{h.template}" for out, h in sorted(generated.items())},
            comments=[f"{mk_path.name} BUILT_SOURCES: the wrapper headers generated unconditionally."],
        ))
        package.after.append(Raw(
            "[\n    autoconf_hdr(\n        name = hdr.replace(\"/\", \"_\").replace(\".\", \"_\"),\n"
            "        out = hdr,\n"
            f"        inlines = {inlines_expr},\n        mode = \"subst\",\n        template = template,\n"
            "        deps = [\":autoconf\"],\n    )\n"
            f"    for hdr, template in {upper}_GENERATED_HDRS.items()\n]"
        ))
    conditions: dict[str, str] = {}
    outs: dict[str, str] = {}  # stem -> header path, where it is not `<stem>.h` (selinux/context.h)
    templates: dict[str, str] = {}  # stem -> template, where it is not `<stem>.in.h` (se-context.in.h)
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
            if header.condition != expected_var:
                conditions[stem] = f"{header.condition}==true"
            if header.extra_inputs:
                notes.append(f"{header.out}: also assembled from {', '.join(header.extra_inputs)} "
                             "(see the gzip overlay's lib_assert_h_static_assert_half for the pattern)")
        package.after.append(Assign(
            "GNULIB_COND_HDR_STEMS", stems,
            comments=["The wrapper headers generated only when the matching `GL_GENERATE_*_H` says the",
                      "system header is missing or unusable.  Each is processed unconditionally into",
                      "`_gnulib_cond/` and `cc_gnulib_conditional_hdrs` picks the ones whose condition holds,",
                      "placing them at their path under `_gnulib_cond/` (`selinux/context.h` included)."],
        ))
        if outs:
            package.after.append(Assign(
                "GNULIB_COND_HDR_OUTS", outs,
                comments=["Headers in a subdirectory: their path, where it is not `<stem>.h`."],
            ))
        if templates:
            package.after.append(Assign(
                "GNULIB_COND_HDR_TEMPLATES", templates,
                comments=["Templates not named `<stem>.in.h` (gnulib's se-context.in.h for selinux/context.h)."],
            ))
        out_expr = ("GNULIB_COND_HDR_OUTS.get(stem, stem + \".h\")" if outs else "stem + \".h\"")
        template_expr = (f"\"{prefix}\" + GNULIB_COND_HDR_TEMPLATES.get(stem, stem + \".in.h\")" if templates
                         else f"\"{prefix}{{}}.in.h\".format(stem)")
        dir_slug = (prefix.rstrip("/") or "lib").replace("/", "_")
        package.after.append(Raw(
            f"[\n    autoconf_hdr(\n        name = \"{dir_slug}_{{}}_h_processed\".format(stem.replace(\"-\", \"_\")),\n"
            f"        out = \"_gnulib_cond/\" + {out_expr},\n"
            f"        inlines = {inlines_expr},\n        mode = \"subst\",\n"
            f"        template = {template_expr},\n        deps = [\":autoconf\"],\n    )\n"
            "    for stem in GNULIB_COND_HDR_STEMS\n]"
        ))
        if conditions:
            package.after.append(Assign(
                "GNULIB_COND_HDR_CONDITIONS", conditions,
                comments=["Headers whose automake conditional is not the usual GL_GENERATE_<STEM>_H."],
            ))
        cond_expr = ("GNULIB_COND_HDR_CONDITIONS.get(stem, \"GL_GENERATE_{}_H\".format("
                     "stem.upper().replace(\"-\", \"_\")))" if conditions
                     else "\"GL_GENERATE_{}_H\".format(stem.upper().replace(\"-\", \"_\"))")
        package.after.append(Rule(
            "cc_gnulib_conditional_hdrs", f"{prefix}{lib_name}_gnulib_conditional_hdrs",
            {"hdrs": Raw(f"{{\n        \":{dir_slug}_{{}}_h_processed\".format(stem.replace(\"-\", \"_\")): " +
                         cond_expr + "\n        for stem in GNULIB_COND_HDR_STEMS\n    }"),
             # GL_GENERATE_ICONV_H and the like: the header ports' :defaults the
             # main target reads (GNULIB_M4_DEFAULT_DEPS) where the project
             # configures no such header itself.
             "deps": [":autoconf"],
             # `_gnulib_cond/selinux/context.h` is included as <selinux/context.h>.
             "strip_include_prefix": "_gnulib_cond"},
            load_from="//gnulib:conditional_hdrs.bzl",
        ))

    private = f"{prefix}{lib_name}_private_headers"
    hdrs = Raw(f"glob(\n        [\"{prefix}**/*.h\"],\n"
               f"        # The templates are inputs of the autoconf_hdr targets, not headers.\n"
               f"        exclude = [\"{prefix}**/*.in.h\"],\n    )" +
               (f" + {upper}_GENERATED_HDRS.keys()" if generated else ""))
    private_attrs: dict = {"hdrs": hdrs, "includes": [prefix.rstrip("/") or "."]}
    private_attrs["deps"] = sorted(
        _config_deps(state) + ([f":{prefix}{lib_name}_gnulib_conditional_hdrs"] if conditional else []),
        key=label_sort_key)
    package.after.append(Rule("cc_library", private, private_attrs, load_from="@rules_cc//cc:cc_library.bzl"))

    # -- sources ------------------------------------------------------------------
    src_conditions: dict[str, str] = {}
    platform_conditions: dict[str, list[str]] = {}  # cond name -> constraint labels where it holds
    for cond_name, conds in state.src_conditions.items():
        cond = df.disj(*conds)
        lowered = lower.expression(_gnulib_names(state)(cond))
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
        spelled = next((expr for macro, expr in KNOWN_AM_CONDITIONS if any(macro in describe(c) for c in conds)), None)
        if spelled is not None:
            src_conditions[cond_name] = spelled
            continue
        decision = decisions.resolve(dec.Request(
            f"src_condition:{cond_name}", "src_condition", mk_path.name,
            f"the condition for the sources under {cond_name} cannot be expressed "
            f"({lowered.unknown or 'always'})",
            {**_am_block(mk_text, mk_path.name, cond_name), "conditions": [describe(c) for c in conds]}))
        if decision is None:
            continue
        if decision.get("always"):
            src_conditions[cond_name] = "1"
        elif "condition" in decision:
            src_conditions[cond_name] = decision["condition"]
    conditional_srcs: dict[str, str] = {}
    platform_srcs: dict[str, list[str]] = {}  # constraint label -> sources
    plain_srcs: list[str] = []
    for source in library.sources:
        if not source.path.endswith((".c", ".cc", ".cpp")):
            continue
        path = f"{prefix}{source.path}"
        if source.condition:
            cond_name = source.condition
            if cond_name not in src_conditions and cond_name not in platform_conditions:
                known = state.am_conditionals.get(cond_name)
                labels = _platform_labels(known) if known is not None else None
                if cond_name.startswith("GL_GENERATE_"):
                    src_conditions[cond_name] = f"{cond_name}==true"
                elif labels is not None:
                    platform_conditions[cond_name] = labels
                else:
                    # gnulib's HAVE_* shell variables are substs with a define of
                    # the same name; the rules want the subst named outright.
                    lowered = lower.expression(_gnulib_names(state)(known)) if known is not None else None
                    spelled = next((expr for macro, expr in KNOWN_AM_CONDITIONS
                                    if known is not None and macro in describe(known)), None)
                    if lowered is not None and lowered.ok and lowered.expr:
                        src_conditions[cond_name] = lowered.expr
                    elif spelled is not None:
                        src_conditions[cond_name] = spelled
                    elif cond_name in state.port_conditionals:
                        # AM_CONDITIONAL in a ported m4: the port's <NAME>_TRUE subst says when.
                        src_conditions[cond_name] = state.port_conditionals[cond_name]
                    else:
                        decision = decisions.resolve(dec.Request(
                            f"src_condition:{cond_name}", "src_condition", mk_path.name,
                            f"{source.path} is compiled only `if {cond_name}`, an automake conditional "
                            "the converter did not follow",
                            {**_am_block(mk_text, mk_path.name, cond_name),
                             "am_conditional": describe(known) if known is not None else None}))
                        if decision is None or decision.get("never"):
                            notes.append(f"{source.path}: left out (`if {cond_name}` undecided or never)")
                            continue
                        src_conditions[cond_name] = "1" if decision.get("always") else decision["condition"]
            if cond_name in platform_conditions:
                for label in platform_conditions[cond_name]:
                    platform_srcs.setdefault(label, []).append(path)
                continue
            conditional_srcs[path] = cond_name
        elif source.extra:
            cond_name = "GL_COND_OBJ_" + re.sub(r"[^A-Za-z0-9]", "_", source.path[:-2]).upper()
            if cond_name in src_conditions:
                conditional_srcs[path] = cond_name
            else:
                notes.append(f"{source.path}: EXTRA source without an AC_LIBOBJ condition; left out")
        else:
            if path not in plain_srcs:
                plain_srcs.append(path)
    if conditional_srcs:
        package.after.append(Assign(
            "SRC_CONDITIONS", dict(sorted(src_conditions.items())),
            comments=["Conditions attached to each conditionally-compiled gnulib source: gl_INIT's",
                      "gl_CONDITIONAL tests and the `if`s around AC_LIBOBJ, over the rules' results."],
        ))
        package.after.append(Rule(
            "autoconf_srcs", f"{prefix}{lib_name}_gnulib_conditional_srcs",
            {"srcs": Raw("{\n        src: SRC_CONDITIONS[name]\n        for src, name in " +
                         _dict_literal(dict(sorted(conditional_srcs.items())), "        ") + ".items()\n    }"),
             # The <header>_h defaults (REPLACE_X=0, HAVE_X=1) come with the main
             # target's GNULIB_M4_DEFAULT_DEPS.
             "deps": [":autoconf"]},
            load_from="//autoconf:autoconf_srcs.bzl",
        ))
    package.after.append(Assign(f"{upper}_SRCS", sorted(plain_srcs),
                                comments=[f"{mk_path.name}: {library.archive}'s unconditional sources."]))
    srcs_expr = f"{upper}_SRCS"
    if conditional_srcs:
        srcs_expr += f" + [\":{prefix}{lib_name}_gnulib_conditional_srcs\"]"
    if platform_srcs:
        arms = {label: sorted(paths) for label, paths in sorted(platform_srcs.items())}
        arms[lower.DEFAULT_ARM] = []
        package.after.append(Assign(
            f"{upper}_PLATFORM_SRCS", Raw(f"select({_dict_of_lists(arms, '')})"),
            comments=[f"{mk_path.name}: sources compiled only on some platforms (`case $host_os` conditionals "
                      "such as OS_IS_NATIVE_WINDOWS and GL_COND_OBJ_WINDOWS_*)."]))
        srcs_expr += f" + {upper}_PLATFORM_SRCS"
    lib_attrs: dict = {
        "srcs": Raw(srcs_expr),
        "copts": ["-w"],
        "implementation_deps": [f":{private}"],
        "local_defines": ["HAVE_CONFIG_H"],
        "visibility": ["//visibility:public"],
    }
    name, alias = _library_target(lib_dir, library.archive, _program_names(src, set(state.config_subdirs)))
    package.after.append(Rule("cc_library", name, lib_attrs,
                              comments=[f"{library.archive} ({mk_path.name})."],
                              load_from="@rules_cc//cc:cc_library.bzl"))
    if alias:
        package.after.append(Rule(
            "alias", alias, {"actual": f":{name}", "visibility": ["//visibility:public"]},
            comments=[f"{library.archive} under its archive name."]))
    state.library_label = f":{name}"
    state.library_archive = library.archive
    state.private_headers_label = f":{private}"


# Directories whose Makefile.am builds nothing a consumer links or runs.
_SKIP_SUBDIRS = {"po", "doc", "man", "tests", "gnulib-tests", "testsuite", "test", "m4", "build-aux", "gnulib"}
_LIBRARY_RE = re.compile(r"(?:\$\(top_builddir\)/|\$\(top_srcdir\)/|\.\./|\./)*([A-Za-z0-9_./-]+?/)?(lib[A-Za-z0-9_.+-]*\.(?:a|la))$")
_SOURCE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".C")
# The library carrying the config headers and the top-level headers, with
# `includes = ["."]` (the generated project is an external module, where
# that is allowed), so `#include <config.h>` works from any directory.
CONFIG_HEADERS = "config_headers"
# Sources a tool generates C from: the BCR `bison` and `flex` modules' rules
# (they run the module's own binary with m4 configured), and the options
# variable automake reads for them.
_GENERATORS = {
    gnulibmk.YACC_SUFFIXES: ("bison", "@bison//:bison.bzl", "YFLAGS"),
    gnulibmk.LEX_SUFFIXES: ("flex", "@flex//:flex.bzl", "LFLAGS"),
}
_CXX_GENERATED = (".yy", ".y++", ".yxx", ".ypp", ".ll", ".l++", ".lxx", ".lpp")
# Pinned BCR versions of the modules that build the tools.
TOOL_MODULES = {"bison": ("bison", "3.8.2.bcr.11"), "flex": ("flex", "2.6.4.bcr.8")}

# Repositories the BUILD files already in a source tree may load (tcl ships
# compat/zlib/BUILD.bazel with rules_license), and the module that provides
# each: the generated MODULE.bazel declares them so those packages load.
SOURCE_TREE_MODULES = {
    "rules_license": ("rules_license", "1.0.0"),  # tcl's compat/zlib/BUILD.bazel
    "rules_shell": ("rules_shell", "0.8.0"),  # sh_test in an --append fragment
}
_GENERATED_MODULES = {"rules_cc", "platforms", "bazel_skylib", "rules_cc_autoconf"}
_LOAD_REPO_RE = re.compile(r'load\(\s*"@@?([A-Za-z0-9_.-]+)//')


def load_repos(text: str) -> set[str]:
    """The external repositories a BUILD text loads."""
    return set(_LOAD_REPO_RE.findall(text))


def source_tree_loads(src: Path) -> set[str]:
    """The external repositories the BUILD and .bzl files already in `src` load."""
    repos: set[str] = set()
    for path in src.rglob("*"):
        if path.name in ("BUILD", "BUILD.bazel") or path.suffix == ".bzl":
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            repos.update(load_repos(text))
    return repos


_COMPUTED_SUBDIRS_RE = re.compile(r"^(?:DIST_)?SUBDIRS\s*\+?=.*\$\(", re.M)


def _configured_subdirs(src: Path, known: set[str]) -> list[str]:
    """Sub-configures a project names only at configure time.

    texlive's `libs/Makefile.am` says `SUBDIRS = . $(MAKE_SUBDIRS)` and configure fills the
    variable from a list in its own m4; `AC_CONFIG_SUBDIRS` never sees literal names.  When a
    Makefile.am's SUBDIRS is computed like that, the immediate child directories that carry
    a configure.ac of their own are those sub-configures.
    """
    makefile = src / "Makefile.am"
    if not makefile.is_file() or not _COMPUTED_SUBDIRS_RE.search(makefile.read_text(encoding="utf-8", errors="replace")):
        return []
    found = []
    for child in sorted(src.iterdir()):
        if child.is_dir() and child.name not in known and child.name not in _SKIP_SUBDIRS and \
                any((child / c).is_file() for c in ("configure.ac", "configure.in")):
            found.append(child.name)
    return found


_OBJECT_SUFFIXES = (".o", ".lo", ".obj")
_LINK_RE = re.compile(r"\$[({](?:CC|CXX|LINK|CCLD|CXXLD|LIBTOOL)[)}].*\s-o\s*\$[@(]")
_SUBST_REF_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)@")


def _parse_tree_makefile(directory: str, makefile: Path, text: str, src: Path | None,
                         substs: dict[str, str] | None = None, notes: list[str] | None = None) -> gnulibmk.Makefile:
    """Parse one Makefile of the tree; a hand-written Makefile.in (an autoconf project
    without automake) gets its programs and libraries from its rules."""
    mk = gnulibmk.parse(text)
    if makefile.name == "Makefile.in" and src is not None:
        _discover_makefile_in_targets(mk, directory, src, substs or {}, notes if notes is not None else [])
    return mk


def _discover_makefile_in_targets(mk: gnulibmk.Makefile, directory: str, src: Path,  # noqa: C901
                                  substs: dict[str, str], notes: list[str]) -> None:
    """Programs and libraries of a hand-written Makefile.in (readline, libpcap,
    tcpdump: autoconf without automake), from its rules.

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
    where = f"{directory}/Makefile.in" if directory else "Makefile.in"

    def expand(words: list[str]) -> list[str]:
        out = []
        for word in gnulibmk.expand_variables(words, mk.variables):
            if "@" in word:
                missing = [name for name in _SUBST_REF_RE.findall(word) if name not in substs]
                if missing:
                    notes.append(f"{where}: @{missing[0]}@ is computed by configure; its contribution is left out")
                    continue
                word = _SUBST_REF_RE.sub(lambda m: substs[m.group(1)], word)
                if not word.strip():
                    continue
                out.extend(word.split())
                continue
            if word.startswith("$(") or word.startswith("${"):
                continue  # an unknown make variable
            out.append(word)
        return out

    def sources_of(objects: list[str], owner: str) -> list[gnulibmk.Source]:
        found = []
        for obj in objects:
            stem = re.sub(r"^(?:\$\(srcdir\)/|\./)", "", obj)
            stem = stem.rsplit(".", 1)[0]
            for suffix in _SOURCE_SUFFIXES:
                if (here / f"{stem}{suffix}").is_file():
                    found.append(gnulibmk.Source(f"{stem}{suffix}", None, None))
                    break
            else:
                notes.append(f"{where}: {owner}: no source for {obj} next to the Makefile; left out")
        return found

    for target, deps in mk.rules.items():
        names = expand([target]) if "$" in target else [target]
        if len(names) != 1:
            continue
        name = names[0]
        recipe = mk.recipes.get(target, [])
        prerequisites = expand(deps)
        objects = [d for d in prerequisites if d.endswith(_OBJECT_SUFFIXES)]
        base = name.rsplit("/", 1)[-1]
        if _LIBRARY_RE.match(name) and objects:
            canonical = re.sub(r"[^A-Za-z0-9_@]", "_", base)
            if canonical not in mk.libraries:
                mk.libraries[canonical] = gnulibmk.Library(canonical, base, sources_of(objects, base))
        elif any(_LINK_RE.search(line) for line in recipe) and objects and "." not in base:
            canonical = re.sub(r"[^A-Za-z0-9_@]", "_", base)
            if canonical not in mk.programs:
                archives = [d for d in prerequisites if _LIBRARY_RE.match(d)]
                mk.programs[canonical] = gnulibmk.Program(base, sources_of(objects, base), ldadd=archives)


def _automake_tree(src: Path, skip: set[str] = frozenset()) -> list[tuple[str, Path, str]]:
    """`(directory, Makefile.am or Makefile.in, text)` for the top level and
    every SUBDIRS directory under it, build-only directories left out.  A
    directory without automake (a hand-written Makefile.in) is read for its
    rules instead."""
    out: list[tuple[str, Path, str]] = []
    queue = [""]
    seen: set[str] = set()
    while queue:
        directory = queue.pop(0)
        base = src / directory if directory else src
        makefile = base / "Makefile.am"
        if not makefile.is_file():
            makefile = base / "Makefile.in"
        if directory in seen or not makefile.is_file():
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


def _program_names(src: Path, skip: set[str] = frozenset()) -> set[str]:
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
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
    prefix = f"{directory}/" if directory not in ("", ".") else ""
    if subdir:
        prefix += subdir.strip("/") + "/"
    if not stem.startswith("lib") or stem == "lib":
        return f"{prefix}{stem}", None
    name = f"{prefix}{stem[3:]}"
    if name not in taken:
        return name, None
    return f"{prefix}lib/{stem[3:]}", f"{prefix}{stem}"


def _generated_sources(package: Package, mk: gnulibmk.Makefile, canonical: str, sources: list,  # noqa: PLR0913
                       prefix: str, *, src: Path | None = None, state: ProjectState | None = None,
                       notes: list[str] | None = None) -> tuple[list[str], list[str]]:
    """`bison()` / `flex()` rules (the BCR modules' own) for the yacc and lex
    sources of one target: the generated files to compile and the `-I` that
    finds the header.

    Release tarballs ship the generated `foo.c` next to `foo.y`, so the rule
    is named `bison/foo` and writes `bison/foo.c` and `bison/foo.h` where no
    source file is in the way; `-I$(BINDIR)/bison` makes `#include "foo.h"`
    find the generated header."""
    srcs: list[str] = []
    copts: list[str] = []
    for source in sources:
        tool = next((t for suffixes, t in _GENERATORS.items() if source.path.endswith(suffixes)), None)
        if tool is None:
            continue
        kind, load_from, flags_var = tool
        stem = source.path.rsplit(".", 1)[0]
        name = f"{prefix}{kind}/{stem}"
        cxx = source.path.endswith(_CXX_GENERATED)
        shipped = f"{stem}.{'cc' if cxx else 'c'}"
        if state is not None and src is not None and TOOL_MODULES[kind][0] == state.tarname \
                and (src / prefix / shipped).is_file():
            # flex's scan.l, bison's parse-gram.y: the tool cannot generate
            # itself; the release ships the output, as the bootstrap does.
            if notes is not None:
                notes.append(f"{prefix}Makefile.am: {source.path}: this module is {kind} itself; "
                             f"the shipped {shipped} is compiled instead of running the tool")
            srcs.append(f"{prefix}{shipped}")
            continue
        out_src = f"{name}.{'cc' if cxx else 'c'}"
        out_hdr = None if kind == "flex" and cxx else f"{name}.{'hh' if cxx else 'h'}"
        if not any(isinstance(s, Rule) and s.name == name for s in package.after):
            options = [
                o for o in mk.variables.get(f"AM_{flags_var}", []) + mk.variables.get(f"{canonical}_{flags_var}", [])
                + mk.generator_flags.get(source.path, [])
                # The header is always generated, under the rule's name; yacc
                # compatibility only renames outputs.
                if o not in ("-d", "--defines", "--header", "-y", "--yacc")
            ]
            source_label = f"{prefix}{source.path}"
            args = ["-o", f"$(execpath {out_src})"]
            if out_hdr and kind == "bison":
                args.append(f"--defines=$(execpath {out_hdr})")
            elif out_hdr:
                args.append(f"--header-file=$(execpath {out_hdr})")
            if kind == "flex" and cxx:
                args.append("--c++")
            args += options + [f"$(execpath {source_label})"]
            package.after.append(Rule(
                kind, name,
                {"srcs": [source_label], "outs": [out_src] + ([out_hdr] if out_hdr else []), "args": args},
                comments=[f"{prefix}Makefile.am: {source.path}, run through {kind} at build time."],
                load_from=load_from))
        srcs.append(out_src)
        if out_hdr:
            srcs.append(out_hdr)
        include = f"-I$(BINDIR)/{prefix}{kind}"
        for option in (include, f"{include}/{stem.rsplit('/', 1)[0]}" if "/" in stem else include):
            if option not in copts:
                copts.append(option)
    return srcs, copts


_TEXTUAL_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^">]+\.c)[">]', re.M)


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
            included = os.path.normpath(os.path.join(os.path.dirname(path), name)).replace(os.sep, "/")
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


def _root_textual(package: Package, files: list[str], notes: list[str]) -> None:
    """Top-level sources read by other compiles ride along with the config headers."""
    rule = next((s for s in package.after if isinstance(s, Rule) and s.name == CONFIG_HEADERS), None)
    if rule is None:
        notes.append(f"{', '.join(files)}: included by other sources but no {CONFIG_HEADERS} library to carry them")
        return
    rule.attrs["textual_hdrs"] = sorted(set(rule.attrs.get("textual_hdrs", [])) | set(files))


_FLAG_VARIABLES = ("DEFS", "AM_CPPFLAGS", "{}_CPPFLAGS", "AM_CFLAGS", "{}_CFLAGS")
# Automake's installation directories at configure's defaults (`--prefix=/usr/local`).
INSTALL_DIRS = {
    "prefix": "/usr/local", "exec_prefix": "$(prefix)", "bindir": "$(exec_prefix)/bin",
    "sbindir": "$(exec_prefix)/sbin", "libexecdir": "$(exec_prefix)/libexec", "libdir": "$(exec_prefix)/lib",
    "datarootdir": "$(prefix)/share", "datadir": "$(datarootdir)", "sysconfdir": "$(prefix)/etc",
    "sharedstatedir": "$(prefix)/com", "localstatedir": "$(prefix)/var", "runstatedir": "$(localstatedir)/run",
    "includedir": "$(prefix)/include", "oldincludedir": "/usr/include", "localedir": "$(datarootdir)/locale",
    "infodir": "$(datarootdir)/info", "mandir": "$(datarootdir)/man", "docdir": "$(datarootdir)/doc/$(PACKAGE)",
    "pkgdatadir": "$(datadir)/$(PACKAGE)", "pkglibdir": "$(libdir)/$(PACKAGE)",
    "pkglibexecdir": "$(libexecdir)/$(PACKAGE)", "pkgincludedir": "$(includedir)/$(PACKAGE)",
    "PATH_SEPARATOR": ":",
}
_MAKE_VAR_RE = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)[)}]")


def _expand_make_text(text: str, mk: gnulibmk.Makefile, state: ProjectState, depth: int = 0) -> str:
    """`$(VAR)` references in a define's value: the Makefile.am's own
    variables, configure's unconditional literal substitutions
    (`AC_SUBST([pkgextensiondir], ['${pkglibdir}'])`), then automake's
    directories at their defaults."""
    def one(match: re.Match) -> str:
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
        if isinstance(item, Call) and item.fn == "checks.AC_SUBST" and len(item.args) == 2 \
                and isinstance(item.args[0], str) and isinstance(item.args[1], str) \
                and not any(k in item.kwargs for k in ("requires", "condition")):
            out[item.args[0]] = item.args[1].strip("'")
    return out


def _shell_unquote(text: str) -> str:
    """The C token a Makefile's shell quoting spells: `'"x"'` and `"\\"x\\""` are `"x"`."""
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"')
    return text


_CXX_SOURCES = (".cc", ".cpp", ".cxx", ".C")


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
            notes.append(f"{standard}: a Makefile.am passes -std={standard}; the main target checks for it instead "
                         "of pinning it (a Bazel build's standard is the root module's)")
    for lang in ("C", "CXX"):
        standard = wanted[lang]
        if not standard or iso_standard(standard) in state.required_standards:
            continue
        state.required_standards.add(iso_standard(standard))
        call = Call("checks.AC_LANG_STANDARD", [standard], {"error": True})
        call.comments.append(f"The script compiles everything with -std={standard}.  A Bazel build's standard is the")
        call.comments.append("root module's (--copt / --cxxopt), so it is checked here rather than pinned on the targets.")
        main.checks.append(call)


def _record_makefile_standards(options: list[str], state: ProjectState) -> None:
    """`-std=` options of a Makefile's flags, for `_require_standards`: per language, the last one wins."""
    for option in options:
        value = option[len("-std="):]
        state.makefile_standards["CXX" if "++" in value else "C"] = value


def _preprocessor_flags(mk: gnulibmk.Makefile, canonical: str, directory: str,  # noqa: PLR0913
                        notes: list[str], state: ProjectState,
                        decisions: dec.Decisions) -> tuple[list[str], list[str], list[str], list[str]]:
    """`-D` and `-I` options of DEFS / AM_CPPFLAGS / <target>_CPPFLAGS (and the
    CFLAGS variables) as local_defines, copts and includes. `$(VAR)` in a define's
    value is expanded from the Makefile.am and automake's default
    directories (`-DDEFPATH=$(DEFPATH)`); what configure alone knows
    (`-DSHLIBEXT=$(SHLIBEXT)`) is reported and left out."""
    defines: list[str] = []
    copts: list[str] = []
    includes: list[str] = []  # `-I` options, package-relative: a `-I` copt would be execroot-relative
    standards: list[str] = []  # `-std=` options: a requirement check, not a pin (see _require_standards)
    for variable in (v.format(canonical) for v in _FLAG_VARIABLES):
        for item in mk.variables.get(variable, []):
            option = item
            if option.startswith("-D"):
                name, eq, value = option[2:].partition("=")
                value = _shell_unquote(_expand_make_text(value, mk, state))
                if "$" in value or "@" in value or "$" in name:
                    # `-DSHLIBEXT=$(SHLIBEXT)`: a value only configure computes.
                    decision = decisions.resolve(dec.Request(
                        f"cppflag:{directory or '.'}:{name}", "cppflag", f"{directory or '.'}/Makefile.am",
                        f"{item} ({variable}) has a value only configure computes",
                        {"expanded": f"-D{name}={value}"}))
                    if decision is None or decision.get("ignore"):
                        notes.append(f"{directory or '.'}/Makefile.am: {variable} {item} left out "
                                     "(cppflag decision missing or IGNORE)")
                        continue
                    value = decision["value"]
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    # Bazel tokenizes local_defines like a shell: a C string
                    # literal keeps its quotes only when they are escaped.
                    value = '"\\"' + value[1:-1] + '\\""'
                define = f"{name}={value}" if eq else name
                if name and name != "HAVE_CONFIG_H" and define not in defines:
                    defines.append(define)
            elif option.startswith("-std="):
                if option not in standards:
                    standards.append(option)
            elif option.startswith("-I"):
                path = _expand_make_text(option[2:].strip("\"'"), mk, state)
                base = directory
                for variable_, replacement in (("$(top_srcdir)", ""), ("$(srcdir)", directory),
                                              ("$(top_builddir)", "$(BINDIR)"), ("$(builddir)", f"$(BINDIR)/{directory}")):
                    if path.startswith(variable_):
                        path, base = path[len(variable_):].lstrip("/"), replacement
                        break
                if "$(" in path or "@" in path:
                    notes.append(f"{directory or '.'}/Makefile.am: {variable} {item} needs make's expansion; left out")
                    continue
                joined = os.path.normpath(os.path.join(base, path) if path else base or ".").replace(os.sep, "/")
                if joined.startswith(".."):
                    notes.append(f"{directory or '.'}/Makefile.am: {variable} {item} leaves the tree; left out")
                    continue
                if joined.startswith("$(BINDIR)"):
                    joined = joined[len("$(BINDIR)"):].lstrip("/") or "."
                if joined not in includes:
                    includes.append(joined)
    return defines, copts, standards, includes


_WRITE_RE = re.compile(r"^(?:\$\(AM_V_\w+\))?\s*(printf|echo)\s+'([^']*)'\s*(>>?)\s*(?:\"?\$@\"?[-a-z]*|\S+)\s*$")
_RECIPE_NOISE_RE = re.compile(r"^(?:\$\(AM_V_\w+\))?\s*(?:rm|chmod|mv|touch)\b")


_CONFIGMAKE_ECHO_RE = re.compile(r"""echo\s+'([^']*)'""")
_CONFIGMAKE_PRINTF_RE = re.compile(r"""printf\s+'#define\s+([A-Z_][A-Z0-9_]*)\s+%s\\n'\s+\$\(([a-z_]+)_c_make\)""")


def _configmake_content(recipe: list[str], mk: gnulibmk.Makefile, state: ProjectState) -> list[str] | None:
    """gnulib's `configmake.h`: one `#define BINDIR "..."` per installation
    directory, each `$(x_c_make)` being the directory as a C string.  The
    directories are automake's defaults (`--prefix=/usr/local`), as the
    converter assumes everywhere."""
    lines: list[str] = []
    for line in recipe:
        for echo in _CONFIGMAKE_ECHO_RE.finditer(line):
            lines.append(echo.group(1))
        for define in _CONFIGMAKE_PRINTF_RE.finditer(line):
            name, variable = define.groups()
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


def _generated_files(package: Package, mk: gnulibmk.Makefile, prefix: str, sources: list, src: Path,  # noqa: PLR0913
                     notes: list[str], decisions: dec.Decisions, owner: str,
                     state: ProjectState | None = None) -> tuple[list[str], list[str]]:
    """Sources that are not in the tree: a `printf` / `echo` recipe becomes a
    `write_file` (through an `autoconf_hdr(mode = "subst")` when the text
    carries configure's values); any other recipe, or none, is a `generated`
    decision (the rule that writes the file, or IGNORE).  Returns the paths
    of the generated files and the paths left out."""
    out: list[str] = []
    excluded: list[str] = []
    for source in sources:
        path = f"{prefix}{source.path}"
        if (src / path).is_file():
            continue
        name = slug(path)
        if any(isinstance(s, Rule) and s.name in (name, f"{name}_in") for s in package.after) \
                or any(isinstance(s, Raw) and f'"{path}"' in s.text for s in package.after):
            out.append(path)
            continue
        content = None
        if source.path in mk.recipes:
            content = (_configmake_content(mk.recipes[source.path], mk, state)
                       if source.path.endswith("configmake.h") and state is not None
                       else _written_content(mk.recipes[source.path]))
        if content is None:
            decision = decisions.resolve(dec.Request(
                f"generated:{path}", "generated", f"{prefix}Makefile.am ({owner})",
                f"{source.path} is not in the tree and "
                + ("its recipe is not one the converter translates" if source.path in mk.recipes
                   else "no rule of the Makefile.am writes it"),
                {"recipe": mk.recipes.get(source.path)}))
            if decision is None or decision.get("ignore"):
                notes.append(f"{prefix}Makefile.am: {source.path} left out (generated decision missing or IGNORE)")
                excluded.append(path)
                continue
            package.after.append(Raw(decision["rule"]))
            package.loads += [line for line in decision["loads"] if line not in package.loads]
            out.append(path)
            continue
        if any("@" in line for line in content):
            package.after.append(Rule(
                "write_file", f"{name}_in", {"out": f"{path}.in", "content": content, "newline": "unix"},
                comments=[f"{prefix}Makefile.am: {source.path}, written by its recipe; configure's values "
                          "come from the autoconf results (@PACKAGE_VERSION@ is the quoted C literal)."],
                load_from="@bazel_skylib//rules:write_file.bzl"))
            package.after.append(Rule(
                "autoconf_hdr", name, {"out": path, "mode": "subst", "template": f"{path}.in", "deps": [":autoconf"]},
                load_from="//autoconf:autoconf_hdr.bzl"))
        else:
            package.after.append(Rule(
                "write_file", name, {"out": path, "content": content, "newline": "unix"},
                comments=[f"{prefix}Makefile.am: {source.path}, written by its recipe."],
                load_from="@bazel_skylib//rules:write_file.bzl"))
        out.append(path)
    return out, excluded


def _conditional_sources(sources: list, state: ProjectState, decisions: dec.Decisions, makefile_name: str,  # noqa: PLR0913
                         owner: str, am_text: str, prefix: str, notes: list[str]) -> tuple[list, dict[str, list[str]]]:
    """Sources under an automake conditional: the ones a configure option
    switches on go in a `select()` arm, the rest are `am_conditional`
    decisions (ALWAYS, NEVER or a config_setting label)."""
    compiled = _SOURCE_SUFFIXES + gnulibmk.YACC_SUFFIXES + gnulibmk.LEX_SUFFIXES
    included: list = []
    arms: dict[str, list[str]] = {}
    for source in sources:
        if not source.condition or not source.path.endswith(compiled):
            continue
        path = f"{prefix}{source.path}"
        cond = state.am_conditionals.get(source.condition)
        split = lower.flag_arms(cond, state) if cond is not None else None
        if split and len(split) == 2 and split[lower.DEFAULT_ARM] is df.FALSE:
            label = next(l for l in split if l != lower.DEFAULT_ARM)
            if split[label] is df.TRUE:
                arms.setdefault(label, []).append(path)
                continue
        decision = decisions.resolve(dec.Request(
            f"am_conditional:{source.condition}", "am_conditional", f"{makefile_name} ({owner})",
            f"{source.path} is compiled only `if {source.condition}`"
            + (f" ({describe(cond)})" if cond is not None else ""),
            {**_am_block(am_text, makefile_name, source.condition or ""), "sources": [source.path]}))
        if decision is None or decision.get("never"):
            notes.append(f"{owner}: {source.path} left out (`if {source.condition}` undecided or never)")
        elif decision.get("always"):
            included.append(source)
        elif "select" in decision:
            arms.setdefault(decision["select"], []).append(path)
    return included, arms


def _srcs_value(srcs: list[str], arms: dict[str, list[str]]):
    """A srcs list, plus one `select()` per configure option for conditional sources."""
    if not arms:
        return srcs
    # One select() per option: two options can be on at once, which Bazel
    # rejects as an ambiguous match within one select().
    selects = " + ".join(
        f"select({_dict_of_lists({label: sorted(paths), lower.DEFAULT_ARM: []}, '    ')})"
        for label, paths in sorted(arms.items()))
    return Raw(f"{_list_literal(srcs, '    ')} + {selects}")


def _with_headers(srcs_value, dirs: list[str]):
    """The headers next to a program's sources in subdirectories of a
    non-recursive Makefile.am (make's `src/`, pkgconf's `cli/`): no headers
    library serves them, so they are private sources of the program."""
    text = _list_literal(srcs_value, "    ") if isinstance(srcs_value, list) else srcs_value.text
    patterns = _list_literal([f"{d}/**/*.h" for d in dirs], "        ")
    return Raw(f"{text} + glob(\n        {patterns},\n        allow_empty = True,\n    )")


def _built_sources(mk: gnulibmk.Makefile, included: list) -> list:
    """Unconditional BUILT_SOURCES (headers the sources include) not already among a target's sources."""
    listed = {s.path for s in included}
    return [gnulibmk.Source(name, None, None) for name, cond in mk.built_sources
            if cond is None and not name.startswith("$") and name not in listed]


def _link_vars(items: list[str]) -> list[str]:
    """The `$(X_LIB)` variables of an LDADD / LIBADD line."""
    out: list[str] = []
    for item in items:
        match = re.fullmatch(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", item)
        if match and match.group(1) not in out:
            out.append(match.group(1))
    return out


def _ensure_linkopts(package: Package, link_vars: list[str]) -> None:
    """The one `autoconf_linkopts` target every program and library links against."""
    rule = next((s for s in package.after if isinstance(s, Rule) and s.kind == "autoconf_linkopts"), None)
    if rule is None:
        package.after.append(Rule(
            "autoconf_linkopts", "linkopts", {"vars": list(link_vars), "deps": [":autoconf"]},
            comments=["Makefile.am: the $(X_LIB) variables the programs and libraries link against."],
            load_from="//autoconf:autoconf_linkopts.bzl",
        ))
    else:
        rule.attrs["vars"] = sorted(set(rule.attrs["vars"]) | set(link_vars))


def _link_items(items: list[str], libraries: dict[str, str], notes: list[str], makefile_name: str,  # noqa: PLR0913
                owner: str, decisions: dec.Decisions, state: ProjectState | None = None) -> tuple[list[str], list[str]]:
    """An LDADD / LIBADD line as Bazel deps (libraries of the tree, registry modules for the
    `-l` names tools/convert/libraries.py knows) and linkopts (other `-l`, `-L`); anything else
    is a `link` decision.  With `state`, the configure-found registry libraries (LIBS) join every
    target's deps."""
    deps: list[str] = [] if state is None else list(state.configure_libs.values())
    linkopts: list[str] = []
    directory = makefile_name[:-len("Makefile.am")].rstrip("/") or "."
    for item in items:
        library = _LIBRARY_RE.match(item)
        registry = registry_libraries.known(item) if item.startswith("-l") else None
        if library is not None and library.group(2) in libraries:
            if libraries[library.group(2)] not in deps:
                deps.append(libraries[library.group(2)])
        elif registry is not None:
            if registry.label not in deps:
                deps.append(registry.label)
            if state is not None:
                state.external_modules[registry.module] = registry.version
            notes.append(f"{makefile_name}: {owner} links {item}: the registry module {registry.module} "
                         f"({registry.label})")
        elif item.startswith("-l") or item.startswith("-L"):
            linkopts.append(item)
        elif not re.fullmatch(r"\$\([A-Za-z_][A-Za-z0-9_]*\)", item):
            decision = decisions.resolve(dec.Request(
                f"link:{directory}/{owner}:{item}", "link", makefile_name,
                f"{owner} links {item}" + (", a library the converter did not find" if library else
                                           ", which is neither a library of the tree nor a -l option"),
                {}))
            if decision is None or decision.get("ignore"):
                notes.append(f"{makefile_name}: {owner} links {item}; left out (link decision missing or IGNORE)")
            elif "dep" in decision:
                if decision["dep"] not in deps:
                    deps.append(decision["dep"])
            else:
                linkopts.append(decision["linkopt"])
    return deps, linkopts


def _programs_tree(package: Package, src: Path, gnulib_mk: Path | None, decisions: dec.Decisions,
                   state: ProjectState, notes: list[str]) -> None:
    """A cc_library per library and a cc_binary per program of every
    Makefile.am in the tree (the gnulib library itself comes from gnulib.mk)."""
    gnulib_dir = gnulib_mk.parent.relative_to(src).as_posix() if gnulib_mk is not None else None
    tree = [(directory, makefile, text, _parse_tree_makefile(directory, makefile, text, src, state.literal_substs, notes))
            for directory, makefile, text in _automake_tree(src, set(state.config_subdirs))]
    libraries: dict[str, str] = {}  # archive basename -> label
    if state.library_label and gnulib_mk is not None and state.library_archive:
        libraries[state.library_archive] = state.library_label
    emitted = []
    taken = {f"{directory}/{p.name}" if directory else p.name
             for directory, _makefile, _text, mk in tree for p in mk.programs.values()}
    for directory, _makefile, _text, mk in tree:
        for canonical, library in mk.libraries.items():
            archive_dir = os.path.dirname(os.path.join(directory, library.archive)) if library.archive else directory
            if directory == gnulib_dir or archive_dir == gnulib_dir or not (library.sources or library.libadd):
                continue
            name, alias = _library_target(directory, library.archive, taken)
            taken.add(name)
            libraries.setdefault(library.archive, f":{name}")
            emitted.append((directory, mk, canonical, library, name, alias))
    # Public headers: what automake installs.  A Makefile.am that installs
    # headers but builds no library (libssh2's top level, for include/) hands
    # them to the tree's one installed library, or to a shared `exported_headers`.
    installed_by_dir = {directory: _installed_headers(mk, directory, src) for directory, _m, _t, mk in tree
                        if directory != gnulib_dir}
    hdrs_of: dict[str, list[str]] = {name: list(installed_by_dir.get(directory, []))
                                     for directory, _mk, _c, _lib, name, _a in emitted}
    orphans = [h for directory, headers in installed_by_dir.items()
               if not any(d == directory for d, *_ in emitted) for h in headers]
    installed_libs = [name for _d, _mk, _c, lib, name, _a in emitted if lib.installed]
    exported_headers_lib = None
    if orphans and len(installed_libs) == 1:
        hdrs_of[installed_libs[0]] += [h for h in orphans if h not in hdrs_of[installed_libs[0]]]
    elif orphans:
        exported_headers_lib = "exported_headers"
        package.after.append(Rule(
            "cc_library", exported_headers_lib,
            {"hdrs": sorted(orphans), "includes": sorted({os.path.dirname(h) or "." for h in orphans}),
             "visibility": ["//visibility:public"]},
            comments=["The headers automake installs from directories that build no library."],
            load_from="@rules_cc//cc:cc_library.bzl"))
    # What each dependency exports, for the deps / implementation_deps split.
    exports: dict[str, set[str]] = {}
    if src is not None:
        top_headers = {f.name for f in src.glob("*.h")}
        for label in _config_deps(state):
            exports[label] = {Path(h).name for h in state.config_headers} | top_headers
        if state.library_label and gnulib_dir:
            exports[state.library_label] = {
                f.relative_to(src / gnulib_dir).as_posix().replace(".in.h", ".h")
                for f in (src / gnulib_dir).rglob("*.h")}
        if state.private_headers_label:
            exports[state.private_headers_label] = set()
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
        dirs = sorted({directory} if directory else {f"{directory}/{p}" if directory else p for p in
                                                      {q.rsplit("/", 1)[0] for q in paths if "/" in q}})
        found = sorted(h.relative_to(src).as_posix() for d in dirs if (src / d).is_dir() for h in (src / d).rglob("*.h"))
        public = set(hdrs_of[name])
        private = [h for h in found if h not in public and not h.endswith(".in.h")]
        if private:
            private_of[name] = (dirs, sorted(h for h in public if any(h.startswith(d + "/") for d in dirs)))
            state.private_header_libs[f":{name}"] = f":{name}_private_headers"
            exports[f":{name}_private_headers"] = set(private)
    # Libraries first: programs anywhere may link them.
    for directory, mk, canonical, library, name, alias in emitted:
        prefix = f"{directory}/" if directory else ""
        makefile_name = f"{prefix}Makefile.am"
        included = [s for s in library.sources if s.condition is None and not s.extra]
        for s in library.sources:
            if s.extra and s.condition is None:
                notes.append(f"{makefile_name}: {s.path}: EXTRA_{library.archive}_SOURCES, compiled only through a "
                             "substituted object list; left out")
        text = next(t for d, _m, t, _mk in tree if d == directory)
        extra, arms = _conditional_sources(library.sources, state, decisions, makefile_name, library.archive,
                                           text, prefix, notes)
        included += extra
        srcs = sorted(f"{prefix}{s.path}" for s in included if s.path.endswith(_SOURCE_SUFFIXES))
        generated, gen_copts = _generated_sources(package, mk, canonical, included, prefix,
                                                  src=src, state=state, notes=notes)
        written, excluded = _generated_files(package, mk, prefix, included + _built_sources(mk, included), src,
                                             notes, decisions, library.archive, state)
        srcs = [p for p in srcs if p not in excluded]
        generated += [g for g in written if g not in srcs]
        items = _expand_make_vars(library.libadd, mk)
        link_vars = _link_vars(items)
        if link_vars:
            _ensure_linkopts(package, link_vars)
        deps, linkopts = _link_items(items, libraries, notes, makefile_name, library.archive, decisions, state)
        deps += [d for d in _config_deps(state) if d not in deps]
        if state.library_label and state.library_label not in deps:
            deps.append(state.library_label)
        if link_vars:
            deps.append(":linkopts")
        if exported_headers_lib and f":{exported_headers_lib}" not in deps:
            deps.append(f":{exported_headers_lib}")
        public_headers = hdrs_of[name]
        attrs: dict = {}
        textual = _textual_includes(src, directory, [s.path for s in included])
        # Public interface: the installed headers.  The directory's other
        # headers are private: they compile with the sources (`srcs`) and
        # never reach a consumer, which also keeps their names from colliding
        # with anything the consumer includes.
        srcs_all = sorted(srcs + generated, key=label_sort_key)
        attrs["srcs"] = _srcs_value(srcs_all, arms)
        if textual and directory:
            attrs["textual_hdrs"] = textual
        elif textual:  # the top-level headers come with CONFIG_HEADERS
            _root_textual(package, textual, notes)
        if name in private_of:
            private_dirs, public_under = private_of[name]
            private_lib = f"{name}_private_headers"
            exclude = "".join(f'            "{h}",\n' for h in public_under)
            package.after.append(Rule(
                "cc_library", private_lib,
                {"hdrs": Raw(f"glob(\n        {_list_literal([f'{d}/**/*.h' for d in private_dirs], '        ')},\n" +
                             (f"        exclude = [\n{exclude}        ],\n" if exclude else "") +
                             "        allow_empty = True,\n    )"),
                 "includes": private_dirs, "visibility": ["//visibility:private"]},
                comments=[f"{makefile_name}: the headers of {library.archive} that are not installed.  Targets of "
                          "this project that include them depend on it, as automake's -I lets them."],
                load_from="@rules_cc//cc:cc_library.bzl"))
            deps.append(f":{private_lib}")
        deps += [p for lib, p in state.private_header_libs.items() if lib in deps and p not in deps]
        if public_headers:
            attrs["hdrs"] = sorted(public_headers)
        include_dirs = sorted({os.path.dirname(h) or "." for h in public_headers} | ({directory} if directory else set()))
        if include_dirs:
            attrs["includes"] = include_dirs
        defines, flag_copts, standards, flag_includes = _preprocessor_flags(mk, canonical, directory, notes, state,
                                                                          decisions)
        _record_makefile_standards(standards, state)
        if flag_includes:
            attrs["includes"] = sorted(set(attrs.get("includes", [])) | set(flag_includes))
        public_deps, implementation_deps = _split_deps(
            sorted(deps, key=label_sort_key), exports, _includes_of(src, public_headers))
        attrs.update({
            "copts": ["-w"] + gen_copts + flag_copts,
            "local_defines": ["HAVE_CONFIG_H"] + defines,
            "visibility": ["//visibility:public"],
        })
        if public_deps:
            attrs["deps"] = public_deps
        if implementation_deps:
            attrs["implementation_deps"] = implementation_deps
        if linkopts:
            attrs["linkopts"] = linkopts
        package.after.append(Rule("cc_library", name, attrs,
                                  comments=[f"{makefile_name}: {library.archive}."],
                                  load_from="@rules_cc//cc:cc_library.bzl"))
        if library.archive.endswith(".la") and library.installed:
            # A libtool library that gets installed is a shared library (a
            # `-module` one a plugin); cc_shared_library wraps the cc_library.
            ldflags = _expand_make_vars(mk.variables.get(f"{canonical}_LDFLAGS", []) + mk.variables.get("AM_LDFLAGS", []), mk)
            module = "-module" in ldflags
            stem = library.archive[:-len(".la")]
            shared_name = _dict_literal({  # a bare name lands in the package directory: keep the subdirectory
                "@platforms//os:windows": f"{prefix}{stem[3:] if stem.startswith('lib') and not module else stem}.dll",
                "@platforms//os:macos": f"{prefix}{stem}.so" if module else f"{prefix}{stem}.dylib",
                lower.DEFAULT_ARM: f"{prefix}{stem}.so",
            }, "    ")
            package.after.append(Rule(
                "cc_shared_library", f"{name}_shared",
                {"shared_lib_name": Raw(f"select({shared_name})"), "deps": [f":{name}"],
                 "visibility": ["//visibility:public"]},
                comments=[f"{makefile_name}: {library.archive} is a libtool {'module' if module else 'library'}: "
                          "the shared object automake installs."],
                load_from="@rules_cc//cc:cc_shared_library.bzl"))
        if alias:
            package.after.append(Rule(
                "alias", alias, {"actual": f":{name}", "visibility": ["//visibility:public"]},
                comments=[f"{library.archive} under its archive name: a program takes `{name.replace('lib/', '', 1)}`, "
                          f"and rules_cc names the output of `{name}` {library.archive.rsplit('.', 1)[0]}.a."]))
    for directory, _makefile, text, mk in tree:
        if mk.programs:
            _programs(package, mk, decisions, state, notes, text, prefix=f"{directory}/" if directory else "",
                      libraries=libraries, src=src)
    _convenience_aliases(package)


_INCLUDE_DIRECTIVE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)


def _header_path(path: str, directory: str) -> str | None:
    """A `*_HEADERS` entry as a path from the project root (`$(top_srcdir)/include/x.h`,
    `../include/x.h`, `x.h`), or None when make would have to expand it."""
    for variable, base in (("$(top_srcdir)/", ""), ("$(top_builddir)/", ""),
                           ("$(srcdir)/", directory), ("$(builddir)/", directory)):
        if path.startswith(variable):
            path = os.path.join(base, path[len(variable):])
            break
    else:
        path = os.path.join(directory, path)
    if "$(" in path:
        return None
    path = os.path.normpath(path).replace(os.sep, "/")
    return None if path.startswith("..") else path


def _installed_headers(mk: gnulibmk.Makefile, directory: str, src: Path | None) -> list[str]:
    """The headers a Makefile.am installs, as paths from the project root, that exist."""
    out = []
    for header in mk.installed_headers:
        if header.condition is not None or not header.path.endswith((".h", ".hh", ".hpp", ".hxx")):
            continue
        path = _header_path(header.path, directory)
        if path and (src is None or (src / path).is_file()) and path not in out:
            out.append(path)
    return out


def _includes_of(src: Path | None, headers: list[str]) -> set[str]:
    """What a set of public headers `#include`: each name as written and its basename."""
    names: set[str] = set()
    if src is None:
        return names
    for header in headers:
        file = src / header
        if not file.is_file():
            continue
        for included in _INCLUDE_DIRECTIVE_RE.findall(file.read_text(encoding="utf-8", errors="replace")):
            names.add(included)
            names.add(included.rsplit("/", 1)[-1])
    return names


def _split_deps(deps: list[str], exports: dict[str, set[str]], public_includes: set[str]) -> tuple[list[str], list[str]]:
    """`deps` and `implementation_deps` of a cc_library: a dependency whose headers one of
    the library's public headers includes stays a dep (consumers need it to compile); one
    the sources alone use becomes an implementation dep, so its headers do not reach
    consumers.  A dependency whose headers are unknown (one a decision named) stays a dep."""
    public, private = [], []
    for dep in deps:
        exported = exports.get(dep)
        if exported is None:
            public.append(dep)
        elif exported & public_includes or {e.rsplit("/", 1)[-1] for e in exported} & public_includes:
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
    internal = ("headers", "_private_headers", "_gnulib_conditional_srcs", "_gnulib_conditional_hdrs", "_shared")
    for base, names in candidates.items():
        if base in taken or len(names) != 1 or base.endswith(internal):
            continue
        taken.add(base)
        package.after.append(Rule("alias", base, {"actual": f":{names[0]}", "visibility": ["//visibility:public"]},
                                  comments=[f"Convenience alias for {names[0]}."]))


def _expand_make_vars(items: list[str], mk: gnulibmk.Makefile, depth: int = 0) -> list[str]:
    """`$(LDADD)` and other variables of the same Makefile.am, spelled out."""
    out: list[str] = []
    for item in items:
        match = re.fullmatch(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", item)
        if match and match.group(1) in mk.variables and depth < 4:
            out.extend(_expand_make_vars(mk.variables[match.group(1)], mk, depth + 1))
        else:
            out.append(item)
    return out


def _programs(package: Package, mk: gnulibmk.Makefile, decisions: dec.Decisions, state: ProjectState,  # noqa: C901, PLR0912, PLR0913, PLR0915
              notes: list[str], am_text: str = "", prefix: str = "", libraries: dict[str, str] | None = None,
              src: Path | None = None) -> None:
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
        link_vars += [v for v in _link_vars(items) if v not in link_vars]
    if link_vars:
        _ensure_linkopts(package, link_vars)
    headers_lib = None
    if prefix:
        headers_lib = f"{prefix}headers"
        headers_attrs: dict = {
            "hdrs": Raw(f'glob(\n        ["{prefix}**/*.h"],\n        allow_empty = True,\n    )'),
            "includes": [prefix.rstrip("/")],
        }
        if _config_deps(state):
            headers_attrs["deps"] = _config_deps(state)
        package.after.append(Rule(
            "cc_library", headers_lib, headers_attrs,
            comments=[f"{makefile_name}: the headers next to the programs."],
            load_from="@rules_cc//cc:cc_library.bzl"))
    compiled = _SOURCE_SUFFIXES + gnulibmk.YACC_SUFFIXES + gnulibmk.LEX_SUFFIXES
    for canonical, program in mk.programs.items():
        included = [s for s in program.sources if s.condition is None and not s.extra and s.path.endswith(compiled)]
        for s in program.sources:
            if s.extra and s.condition is None:
                notes.append(f"{makefile_name}: {s.path}: EXTRA_{program.name}_SOURCES, compiled only through a "
                             "substituted object list; left out")
        comments = [f"{makefile_name}: {program.name}."]
        extra, arms = _conditional_sources(program.sources, state, decisions, makefile_name, program.name,
                                           am_text, prefix, notes)
        included += extra
        srcs = sorted(f"{prefix}{s.path}" for s in included if s.path.endswith(_SOURCE_SUFFIXES))
        generated, gen_copts = _generated_sources(package, mk, canonical, included, prefix,
                                                  src=src, state=state, notes=notes)
        srcs += generated
        if src is not None:
            written, excluded = _generated_files(package, mk, prefix, included + _built_sources(mk, included), src,
                                                 notes, decisions, program.name, state)
            srcs = [p for p in srcs if p not in excluded] + [g for g in written if g not in srcs]
        textual = _textual_includes(src, prefix.rstrip("/"), [s.path for s in included]) if src is not None else []
        if textual and headers_lib:
            rule = next(s for s in package.after if isinstance(s, Rule) and s.name == headers_lib)
            rule.attrs["textual_hdrs"] = sorted(set(rule.attrs.get("textual_hdrs", [])) | set(textual))
        elif textual:
            _root_textual(package, textual, notes)
        srcs_value = _srcs_value(sorted(srcs, key=label_sort_key), arms)
        if not headers_lib:
            # automake's -I. lets tests/api/test-api.c include "tests/win-shim.h":
            # the whole top-level directory of each source.
            source_dirs = sorted({p.split("/", 1)[0] for p in srcs if "/" in p})
            if source_dirs:
                srcs_value = _with_headers(srcs_value, source_dirs)
        deps, linkopts = _link_items(link_items.get(canonical, []), libraries, notes, makefile_name, program.name,
                                     decisions, state)
        if headers_lib and f":{headers_lib}" not in deps:
            deps.insert(0, f":{headers_lib}")
        elif not headers_lib:
            deps += [d for d in _config_deps(state) if d not in deps]
        if state.library_label and state.library_label not in deps and not libraries:
            deps.append(state.library_label)
        if state.library_label and state.private_headers_label and state.private_headers_label not in deps:
            deps.append(state.private_headers_label)
        deps += [p for lib, p in state.private_header_libs.items() if lib in deps and p not in deps]
        if link_vars:
            deps.append(":linkopts")
        defines, flag_copts, standards, flag_includes = _preprocessor_flags(mk, canonical, prefix.rstrip("/"), notes,
                                                                          state, decisions)
        attrs: dict = {"srcs": srcs_value, "copts": ["-w"] + gen_copts + flag_copts,
                       "local_defines": ["HAVE_CONFIG_H"] + defines,
                       "visibility": ["//visibility:public"], "deps": sorted(deps, key=label_sort_key)}
        if flag_includes:
            attrs["includes"] = flag_includes
        _record_makefile_standards(standards, state)
        if linkopts:
            attrs["linkopts"] = linkopts
        package.after.append(Rule(
            "cc_binary", f"{prefix}{program.name}", attrs,
            comments=comments, load_from="@rules_cc//cc:cc_binary.bzl",
        ))
    for name in sorted(mk.conditionals):
        if name not in state.am_conditionals:
            notes.append(f"{makefile_name}: `if {name}` has no AM_CONDITIONAL the converter followed")


def _platform_labels(cond: df.Cond | None) -> list[str] | None:
    """The constraint labels under which a purely host-OS condition holds, or
    None when the condition is not (only) about the host OS."""
    if cond is None or not df.host_os_atoms(cond):
        return None
    arms = lower.os_arms(cond)
    if any(residual not in (df.TRUE, df.FALSE) for residual in arms.values()):
        return None
    # A pattern that tells the systems of one constraint apart (`*-gnu*` is
    # linux-gnu but not linux-musl) is not a platform condition.
    for _label, hosts in lower.OS_CONSTRAINTS:
        verdicts = {df.substitute_host_os(cond, host) for host in hosts}
        if len(verdicts) > 1:
            return None
    if arms.get(lower.DEFAULT_ARM) is df.TRUE:
        return None  # holds everywhere but a few platforms: not a plain select list
    return [label for label, residual in arms.items() if residual is df.TRUE and label != lower.DEFAULT_ARM]


def _list_literal(items: list[str], indent: str) -> str:
    if not items:
        return "[]"
    if len(items) == 1:
        return f'["{items[0]}"]'
    inner = indent + "    "
    return "[\n" + "".join(f'{inner}"{item}",\n' for item in items) + indent + "]"


def _dict_of_lists(items: dict[str, list[str]], indent: str) -> str:
    inner = indent + "    "
    lines = ["{"]
    for key, values in items.items():
        lines.append(f'{inner}"{key}": {_list_literal(values, inner)},')
    lines.append(indent + "}")
    return "\n".join(lines)


def _dict_literal(items: dict[str, str], indent: str) -> str:
    inner = indent + "    "
    lines = ["{"]
    for key, value in items.items():
        lines.append(f'{inner}"{key}": "{value}",')
    lines.append(indent + "}")
    return "\n".join(lines)


def _module_bazel(name: str, version: str, rules_version: str, dev_override: str | None, flags: bool,  # noqa: PLR0913
                  tools: list[str] = (), command: str | None = None, source_modules: list[str] = (),
                  external_modules: dict[str, str] | None = None) -> str:
    """The MODULE.bazel of a converted project: the banner, module(), the rules_cc_autoconf dep
    (with a local_path_override under --dev), rules_cc and platforms, bazel_skylib when flags are
    emitted, the bison / flex modules when parsers or scanners are generated, and the modules the
    source tree's own BUILD files load (`source_modules`, keys of SOURCE_TREE_MODULES).
    """
    lines = [
        f'"""{generated_banner(f"{name} {version}".strip(), "Bazel module", command)}',
        '"""',
        "",
        "module(",
        f'    name = "{module_name(name)}",',
        f'    version = "{version or "0.0.0"}",',
        ")",
        "",
        f'bazel_dep(name = "rules_cc_autoconf", version = "{rules_version}")',
    ]
    if dev_override:
        lines += [
            "local_path_override(",
            '    module_name = "rules_cc_autoconf",',
            f'    path = "{dev_override}",',
            ")",
        ]
    lines += [
        'bazel_dep(name = "rules_cc", version = "0.2.9")',
        'bazel_dep(name = "platforms", version = "1.0.0")',
    ]
    if flags:
        lines.append('bazel_dep(name = "bazel_skylib", version = "1.8.2")')
    if tools:
        lines.append("")
        lines.append("# The BCR modules whose bison() / flex() rules generate the parsers and scanners.")
        lines += [f'bazel_dep(name = "{TOOL_MODULES[tool][0]}", version = "{TOOL_MODULES[tool][1]}")' for tool in tools]
    if external_modules:
        lines.append("")
        lines.append("# Registry libraries the configure script finds (AC_CHECK_LIB, AC_SEARCH_LIBS) or the")
        lines.append("# Makefiles link; their targets are deps of what links them.")
        lines += [f'bazel_dep(name = "{module}", version = "{version}")' for module, version in sorted(external_modules.items())]
    if source_modules:
        lines.append("")
        lines.append("# Modules the BUILD files shipped in the source tree, or the --append fragment, load.")
        lines += [f'bazel_dep(name = "{SOURCE_TREE_MODULES[r][0]}", version = "{SOURCE_TREE_MODULES[r][1]}")'
                  for r in source_modules]
    return "\n".join(lines) + "\n"
