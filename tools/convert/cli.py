"""`bazel run //tools/convert -- <subcommand> ...`

Subcommands:
  convert  Convert an autoconf project (or selected m4 files of one) into
           BUILD files using rules_cc_autoconf.
  check    Run GNU configure with the Bazel C++ toolchain and compare its
           config headers with the ones Bazel built from the conversion.
  expand   Run the instrumented autom4te expansion only and dump the marker
           tree (a debugging aid for the converter itself).
"""

from __future__ import annotations

import argparse
import cProfile
import dataclasses
import json
import os
import pstats
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict

from python.runfiles import Runfiles

from autoconf.tests.gnu.autotools import IS_WINDOWS, Autotools
from tools.convert import bcr, bzl, emit, markers, ownership, vendor
from tools.convert import check as check_mod
from tools.convert import expand as expand_mod
from tools.convert import libraries as libraries_mod
from tools.convert import modulebazel as modulebazel_mod
from tools.convert import project as project_mod
from tools.convert import source as source_mod
from tools.convert.common import DEFAULT_COMMAND, TOP_HEADERS
from tools.convert.decisions import MIGRATION_GUIDE, Decisions
from tools.convert.index import PortIndex, ports_checkout
from tools.convert.ir import Package
from tools.convert.translate import Options, Translator


def _decisions_path(args: argparse.Namespace, out: Path) -> Path:
    """`--decisions`, or `decisions.bzl` in the output directory."""
    return _resolve(args.decisions) if args.decisions else out / "decisions.bzl"


def _invocation_dir() -> Path:
    """The directory `bazel run` was invoked from (`BUILD_WORKING_DIRECTORY`), else the current one."""
    return Path(os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd()))


def _resolve(path: str) -> Path:
    """Paths on the command line are relative to where `bazel run` was invoked."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (_invocation_dir() / candidate).resolve()


def _source(args: argparse.Namespace) -> Path:
    """The source directory of `--src`: a directory, a release archive or its URL (fetched once)."""
    cached = getattr(args, "_source_path", None)
    if cached is None:
        cached = source_mod.resolve(
            args.src,
            _invocation_dir(),
            getattr(args, "integrity", None),
            tuple(getattr(args, "mirror_urls", None) or ()),
        )
        if getattr(args, "subdir", None):
            cached = cached / args.subdir
        args._source_path = cached
    return cached


def _source_root(args: argparse.Namespace) -> Path:
    """The whole source tree: `_source(args)` without the `--subdir`."""
    src = _source(args)
    subdir = getattr(args, "subdir", None)
    return Path(*src.parts[: len(src.parts) - len(Path(subdir).parts)]) if subdir else src


# Flags of the convert subcommand that do not shape the output: left out of the reproduce command.
_UNREPRODUCED = {"keep_work", "buildifier", "help"}
# What an entry directory supplies (its source.json, decisions.bzl, the hand-written BUILD files).
_ENTRY_SUPPLIED = {
    "src",
    "out",
    "decisions",
    "source_url",
    "integrity",
    "strip_prefix",
    "homepage",
    "bcr",
    "bcr_version",
}
_PATH_FLAGS = {"src", "out", "decisions", "m4_dir", "only", "bcr_entry", "http_archive_entry", "index"}


def reproduce_command(args: argparse.Namespace) -> str:
    """The reproduce command as taken at the start of the run, before the flags are adjusted
    for the work (`--wrap` gains every ported macro, an entry fills in its paths)."""
    cached = getattr(args, "reproduce", None)
    return cached if cached else _reproduce_command(args)


def _reproduce_command(args: argparse.Namespace) -> str:
    """The `bazel run` line that reproduces this conversion from a clone of rules_cc_autoconf,
    for the banner of the generated files: every `convert` flag that differs from its default,
    in the order the help lists them, so the line can be pasted back as it was run.  Paths
    under the directory `bazel run` was invoked from are written relative to it, paths under
    the home directory with `~` (a generated file never carries a user name); with an entry,
    what the entry supplies is left to it."""
    entry = any(getattr(args, flag, None) for flag in ("bcr_entry", "http_archive_entry"))
    base = _invocation_dir().resolve()

    def spell(value: str) -> str:
        """A path argument as the banner spells it: an absolute path relative to the invocation
        directory, or under `~` (the shell expands it; the user name stays out of the file);
        a URL or a relative path as given."""
        text = str(value)
        path = Path(text)
        if source_mod.is_url(text) or not path.is_absolute():
            return text
        resolved = path.resolve()
        if resolved.is_relative_to(base):
            return resolved.relative_to(base).as_posix() or "."
        if resolved.is_relative_to(Path.home()):
            return "~/" + resolved.relative_to(Path.home()).as_posix()
        return text

    words = ["bazel", "run", "//tools/convert", "--", "convert"]
    convert_parser = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices[
        "convert"
    ]
    for action in convert_parser._actions:
        dest = action.dest
        if dest in _UNREPRODUCED or (entry and dest in _ENTRY_SUPPLIED) or not action.option_strings:
            continue
        value = getattr(args, dest, None)
        flag = action.option_strings[-1]
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            if value is not None and value == action.const:
                words.append(flag)
        elif isinstance(action, argparse._AppendAction):
            for item in value or []:
                words += [flag, spell(item) if dest in _PATH_FLAGS else str(item)]
        elif value not in (None, action.default):
            words += [flag, spell(value) if dest in _PATH_FLAGS else str(value)]
    return " ".join(_shell_word(w) for w in words)


_BARE_TILDE_RE = re.compile(r"~/[\w./+-]*")


def _shell_word(word: str) -> str:
    """`shlex.quote`, except a plain `~/...` path stays bare so the shell expands it."""
    return word if _BARE_TILDE_RE.fullmatch(word) else shlex.quote(word)


class TextDump(TypedDict):
    """A `markers.Text` in tree.json."""

    text: str
    line: int


class _NodeDumpOptional(TypedDict, total=False):
    """The `markers.Node` fields tree.json carries only when set."""

    loc: str
    args: list[str]
    prog: str


class NodeDump(_NodeDumpOptional):
    """A `markers.Node` in tree.json, its children under `items`."""

    kind: str
    id: int
    name: str
    line: int
    end_line: int
    items: list[NodeDump | TextDump]


class TreeDump(TypedDict):
    """`expand`'s tree.json: the expansion's inputs and warnings, and the marker tree."""

    configure_ac: str
    m4_files: list[str]
    warnings: list[str]
    items: list[NodeDump | TextDump]


def _node_to_dict(item: markers.Node | markers.Text) -> NodeDump | TextDump:
    """A marker tree node as JSON-ready data, for `expand`'s tree.json."""
    if isinstance(item, markers.Text):
        return {"text": item.text, "line": item.line}
    optional: _NodeDumpOptional = {}
    if item.loc:
        optional["loc"] = item.loc
    if item.args:
        optional["args"] = item.args
    if item.prog is not None:
        optional["prog"] = item.prog
    return {
        "kind": item.kind,
        "id": item.id,
        "name": item.name,
        "line": item.line,
        "end_line": item.end_line,
        **optional,
        "items": [_node_to_dict(child) for child in item.items],
    }


def _m4_files(dirs: list[str]) -> list[Path] | None:
    if not dirs:
        return None
    files: list[Path] = []
    for directory in dirs:
        files.extend(sorted(_resolve(directory).glob("*.m4")))
    return files


def _run_expansion(args: argparse.Namespace, src: Path | None = None) -> tuple[expand_mod.Expansion, Path, Path]:
    """The instrumented expansion of `src` (`--src` by default), with the work and tool directories
    `_cleanup` removes."""
    src = src if src is not None else _source(args)
    tooldir = expand_mod.scratch_root("rccac-autotools")
    work = expand_mod.scratch_root("rccac-work")
    autotools = Autotools(tooldir)
    expansion = expand_mod.expand(
        autotools,
        src,
        work,
        m4_files=_m4_files(args.m4_dir),
        extra_wraps=args.wrap,
        wrap_library=getattr(args, "wrap_library", False),
    )
    return expansion, work, tooldir


def _cleanup(args: argparse.Namespace, work: Path, tooldir: Path) -> None:
    if args.keep_work:
        print(f"work directory kept: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(tooldir, ignore_errors=True)


def load_index(spec: str) -> PortIndex | None:
    """`--index`: a workspace path, `snapshot`, `auto` or `none`.

    `auto` scans the checkout `bazel run` was started from and warns when the
    committed `index.toml` snapshot disagrees with it.
    """
    if spec == "none":
        return None
    if spec == "snapshot":
        return PortIndex.from_snapshot()
    if spec == "auto":
        root = ports_checkout()
        if root is not None:
            live = PortIndex.from_workspace(root)
            try:
                differences = PortIndex.from_snapshot().differences(live)
            except FileNotFoundError, OSError:
                differences = []
            if differences:
                print(
                    f"warning: tools/convert/index.toml is stale ({len(differences)} differences); "
                    "run `bazel run //tools/convert:update_index`"
                )
            return live
        return PortIndex.from_snapshot()
    return PortIndex.from_workspace(_resolve(spec))


def find_buildifier(spec: str) -> str | None:
    if spec == "none":
        return None
    if spec == "auto":
        return shutil.which("buildifier")
    return spec


def rules_version() -> str:
    """The rules_cc_autoconf version of this checkout (version.bzl)."""
    candidates = []
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root:
        candidates.append(Path(root) / "version.bzl")
    runfiles = Runfiles.Create()
    if runfiles:
        found = runfiles.Rlocation("_main/version.bzl", "_main" if IS_WINDOWS else None)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            match = re.search(r'VERSION\s*=\s*"([^"]+)"', candidate.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
    return "0.0.0"


def _apply_entry(args: argparse.Namespace) -> None:
    """`--bcr-entry modules/<name>/<version>` or `--http-archive-entry 3rdparty/<name>`: the entry
    directory supplies what the other flags would.

    The path gives the module name (and, for a registry entry, the version); `source.json`,
    when the entry has one, gives the archive URL, integrity and strip_prefix (a first run takes
    `--src` and `--integrity` instead and writes it); `decisions.bzl` in the entry is the
    decisions file, and the hand-written `BUILD.bazel` files of the module (in `overlay/` for a
    registry entry) are read for what they declare.  `--out` is a scratch directory for the
    report and defaults to a temporary one: nothing but the entry's own files lands in the
    checkout.
    """

    args.entry = None
    if args.bcr_entry and args.http_archive_entry:
        raise SystemExit("--bcr-entry and --http-archive-entry exclude each other")
    if not args.bcr_entry and not args.http_archive_entry:
        if not args.src or not args.out:
            raise SystemExit("convert needs --src and --out (or --bcr-entry / --http-archive-entry)")
        return
    try:
        entry = (
            bcr.entry(_resolve(args.bcr_entry))
            if args.bcr_entry
            else bcr.vendored_entry(_resolve(args.http_archive_entry))
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    args.entry = entry
    if args.bcr_entry:
        args.bcr = True
        args.bcr_version = entry.version
    if entry.source is not None:
        args.src = args.src or entry.source.get("url")
        args.mirror_urls = entry.source.get("mirror_urls") or []  # tried when the URL is unreachable
        args.source_url = args.source_url or entry.source.get("url")
        args.integrity = args.integrity or entry.source.get("integrity")
        args.strip_prefix = args.strip_prefix or entry.source.get("strip_prefix")
    if not args.src:
        raise SystemExit(f"{entry.directory} has no source.json yet: pass --src <archive URL> and --integrity")
    if not args.source_url and source_mod.is_url(args.src):
        args.source_url = args.src
    if not args.source_url or not args.integrity:
        raise SystemExit("a first run on an entry needs --src <archive URL> (or --source-url) and --integrity")
    args.decisions = args.decisions or str(entry.decisions)
    if entry.registry is not None:
        # An entry's edges to the registry's other tool modules (bison, flex, m4) point at the
        # newest version the checkout holds, so that a set cut together depends on itself.

        # Newest of the same upstream release: a registry re-cut (`.bcr.N`) is taken, another
        # upstream version only for the modules `--allow-upstream` names.
        allow_upstream = set(args.allow_upstream or [])
        tools = {
            n: m.version
            for n, m in modulebazel_mod.MODULES.items()
            if m.role in ("tool", "loaded") and ".bcr." in m.version
        }
        for name, version in bcr.newest_versions(
            entry.registry, tools, current=tools, allow_upstream=allow_upstream
        ).items():
            modulebazel_mod.MODULES[name] = dataclasses.replace(modulebazel_mod.MODULES[name], version=version)
        # The registry libraries likewise (gmp for mpc): the checkout's newest over the snapshot's.
        held = {lib.module: lib.version for lib in libraries_mod.table().values()}
        libraries_mod.pin_versions(
            bcr.newest_versions(entry.registry, held, current=held, allow_upstream=allow_upstream)
        )
        depends_on = {**tools, **held}
        for name, version in sorted(bcr.upstream_bumps(entry.registry, depends_on).items()):
            if name not in allow_upstream:
                print(
                    f"note: the registry has {name} {version}, another upstream release than the "
                    f"{depends_on[name]} this entry depends on; pass --allow-upstream {name} to take it",
                    file=sys.stderr,
                )
    # A new entry has no overlay yet; the conversion writes into it either way.
    entry.overlay.mkdir(parents=True, exist_ok=True)
    if not args.out:
        # A stable scratch directory per entry, so the report is where the last run left it.
        scratch = Path(tempfile.gettempdir()) / f"convert-{entry.name}-{entry.version or 'vendored'}"
        scratch.mkdir(parents=True, exist_ok=True)
        args.out = str(scratch)


def _hand_root(args: argparse.Namespace, out: Path) -> Path | None:
    """Where the hand-written BUILD.bazel files of the generated packages live: a registry
    entry's overlay, else the output directory.  None when the run writes no such file: an
    http_archive entry, whose one BUILD file is all generated, and `--bcr` without an entry,
    whose overlay is known once the module is."""
    if args.http_archive_entry or (args.bcr and args.entry is None):
        return None
    return args.entry.overlay if args.entry is not None else out


@dataclasses.dataclass
class HandBuild:
    """A generated package's hand-written BUILD text, read before the conversion."""

    text: str = ""
    """The text; empty when the package has none yet."""
    source: str = ""
    """The file names read, for the report (`BUILD.bazel`; the earlier layout's additive file)."""


# The layout before autoconf.bzl kept the hand-written part in this file beside a generated
# BUILD.bazel.  Read and moved by `_migrate_additions`; remove both together.
_ADDITIONS_NAME = "BUILD.additions.bazel"


def _hand_build(directory: Path) -> HandBuild:
    """The hand-written text of a generated package's directory: its `BUILD.bazel` unless that
    is the converter's (the earlier layout's generated file, read as absent), with the earlier
    layout's additive file after it."""
    found = [
        (name, path)
        for name in (bzl.BUILD_NAME, _ADDITIONS_NAME)
        for path in [directory / name]
        if path.is_file() and not (name == bzl.BUILD_NAME and ownership.is_owned(path))
    ]
    return HandBuild(
        "\n\n".join(path.read_text(encoding="utf-8") for _, path in found), ", ".join(name for name, _ in found)
    )


def _hand_builds(root: Path | None) -> dict[str, HandBuild]:
    """The hand-written text of every package the conversion generates into, by package path
    ("" for the root), read before the conversion: the root, and each directory under `root`
    an earlier run generated into (it holds an `autoconf.bzl`, or the earlier layout's generated
    `BUILD.bazel` or additive file); a sub-configure's is known only after the root converts."""
    if root is None:
        return {}
    directories = (
        {root}
        | {p.parent for p in root.rglob(bzl.BZL_NAME)}
        | {p.parent for p in root.rglob(_ADDITIONS_NAME)}
        | {p.parent for p in root.rglob(bzl.BUILD_NAME) if ownership.is_owned(p)}
    )
    out = {("" if d == root else d.relative_to(root).as_posix()): _hand_build(d) for d in sorted(directories)}
    return {path: hand for path, hand in out.items() if hand.source}


def _hand_packages(root: Path | None) -> set[str]:
    """The packages below `root` with a BUILD file (an overlay's `tests/`, a sub-configure's),
    by path: nothing generated for the root may be an output under them."""
    out: set[str] = set()
    if root is None or not root.is_dir():
        return out
    for build in list(root.rglob("BUILD.bazel")) + list(root.rglob("BUILD")):
        package = build.parent.relative_to(root).as_posix()
        if package != ".":
            out.add(package)
    return out


def _omit_replaced(package: Package, hand: HandBuild, report_lines: list[str]) -> list[str]:
    """Drop the generated targets the package's hand-written BUILD.bazel declares itself, and
    report them."""
    replaced = emit.omit_replaced(package, set(emit.rule_names(hand.text)), hand.text) if hand.text else []
    if replaced:
        where = f"{package.path}/{hand.source}" if package.path else hand.source
        report_lines += [
            "",
            f"## Targets replaced by {where}",
            "",
            (
                "The generated rule of each is left out of autoconf.bzl; the one in BUILD.bazel stands "
                "under the same name."
            ),
            "",
        ] + [f"- `{n}`" for n in replaced]
    return replaced


def cmd_convert(args: argparse.Namespace) -> int:
    """`convert`: expand the project's configure.ac with the instrumented autoconf, then translate
    either the whole project into one package (project mode, the default) or the selected m4
    files into per-file packages (--only); write the BUILD files and the report.
    """
    _apply_entry(args)
    args.reproduce = _reproduce_command(args)  # as given, before the flags below are adjusted
    out = _resolve(args.out)
    index = load_index(args.index)
    # Library macros with a port are recorded as calls so they become deps;
    # in project mode every public autoconf macro is, so that the internals
    # of AC_PROG_CC and friends do not pass for the project's own checks.
    wraps = list(args.wrap)
    if index is not None:
        wraps += [p.basename for p in index.ports if p.package.startswith("autoconf/macros/")]
    args.wrap = wraps
    layout = args.layout or ("per-file" if args.only else "package")
    args.wrap_library = layout == "package" and not args.only
    try:
        expansion, work, tooldir = _run_expansion(args)
    except expand_mod.ExpandError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        tree = markers.parse(expansion.read())
        if args.wrap_library:
            return _convert_project(args, out, expansion, tree, index)
        return _convert_files(args, out, expansion, tree, index, work, layout)
    finally:
        _cleanup(args, work, tooldir)


def _convert_files(
    args: argparse.Namespace,
    out: Path,
    expansion: expand_mod.Expansion,
    tree: markers.Tree,
    index: PortIndex | None,
    work: Path,
    layout: str,
) -> int:
    """Per-file mode: the AC_DEFUNs of the selected m4 files (`--only`, else every m4 file and
    configure.ac) translated into packages, written with the report."""
    m4_sources: dict[str, str] = {}
    for relative in expansion.m4_files:
        path = work / relative
        if path.is_file():
            m4_sources[relative] = path.read_text(encoding="utf-8", errors="replace")
    m4_sources[expansion.configure_ac.name] = expansion.configure_ac.read_text(encoding="utf-8", errors="replace")

    selected = set(args.only) if args.only else set(expansion.m4_files) | {expansion.configure_ac.name}
    in_repo = args.in_repo if args.in_repo is not None else (layout == "per-file")
    repo = "" if in_repo else "@rules_cc_autoconf"
    options = Options(
        repo=repo, in_repo=in_repo, layout=layout, package_prefix=args.package_prefix, gnulib_commit=args.gnulib_commit
    )
    decisions_path = _decisions_path(args, out)
    decisions = Decisions.load(decisions_path)
    translator = Translator(
        tree, index=index, selected_files=selected, m4_sources=m4_sources, options=options, decisions=decisions
    )
    packages = translator.translate()

    buildifier = find_buildifier(args.buildifier)
    written: list[Path] = []
    for package in packages:
        directory = out / package.path if package.path else out
        directory.mkdir(parents=True, exist_ok=True)
        build = directory / "BUILD.bazel"
        if not args.annotate:
            emit.strip_annotations(package)
        _write_build(build, package, repo=repo, in_repo=in_repo, buildifier=buildifier)
        written.append(build)

    report = translator.report
    lines = []
    if report.inlined:
        lines += ["", "## Macros expanded inline", ""] + [f"- {n}" for n in sorted(report.inlined)]
    if report.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
    return _write_report(
        out,
        expansion,
        tree,
        lines,
        written,
        decisions,
        decisions_path,
        f"converting {_source(args).name}",
        reproduce_command(args),
    )


def _format(buildifier: str | None, text: str, kind: str) -> str:
    """`text` formatted by buildifier as a file of `kind` (`build`, `bzl`); as given without one."""
    if not buildifier:
        return text
    result = subprocess.run([buildifier, f"-type={kind}"], input=text, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 and result.stdout else text


def _write_build(build: Path, package: Package, *, repo: str, in_repo: bool, buildifier: str | None) -> None:
    """Render `package` into `build` as a BUILD file (per-file mode, an http_archive entry)."""
    build.write_text(
        _format(buildifier, emit.render_package(package, repo=repo, in_repo=in_repo), "build"),
        encoding="utf-8",
        newline="\n",
    )


def _write_targets(directory: Path, package: Package, *, buildifier: str | None, replaced: list[str]) -> Path:
    """Project mode: the package's generated targets as `autoconf.bzl`, formatted twice: as a
    BUILD file first, where buildifier expands every rule call and sorts the lists it sorts,
    then as the .bzl file the statements move into."""
    text = _format(buildifier, emit.render_package(package, repo=bzl.RULES_REPO, in_repo=False), "build")
    text = _format(buildifier, bzl.to_bzl(text, replaced=replaced), "bzl")
    path = directory / bzl.BZL_NAME
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _place_hand_builds(root: Path, packages: list[str], report_lines: list[str], buildifier: str | None) -> list[Path]:
    """After the generated files are placed: a generated package without a hand-written
    `BUILD.bazel` gets the stub that loads `autoconf.bzl`, one with the earlier layout's
    additive file gets it moved in, and one whose BUILD.bazel does not call the macro is
    reported.  Returns the files written."""
    written: list[Path] = []
    moved: list[str] = []
    missing: list[str] = []
    for package_path in packages:
        directory = root / package_path if package_path else root
        build = directory / bzl.BUILD_NAME
        if build.is_file() and ownership.is_owned(build):
            build.unlink()  # the earlier layout's generated BUILD.bazel
        if (directory / _ADDITIONS_NAME).is_file():
            moved.append(_migrate_additions(directory, package_path, buildifier))
            written.append(build)
        elif not build.is_file():
            build.write_text(_format(buildifier, bzl.build_file(), "build"), encoding="utf-8", newline="\n")
            written.append(build)
        elif not re.search(rf"^{bzl.MACRO}\(", build.read_text(encoding="utf-8"), re.MULTILINE):
            missing.append(f"{package_path}/{bzl.BUILD_NAME}" if package_path else bzl.BUILD_NAME)
    if moved:
        report_lines += [
            "",
            "## Hand-written files moved",
            "",
            (
                f"The hand-written BUILD file of a generated package is its `{bzl.BUILD_NAME}`, which loads "
                f"the generated `{bzl.BZL_NAME}` beside it; the additive file of the earlier layout was "
                "moved into it, its loads merged."
            ),
            "",
        ] + [f"- {line}" for line in moved]
    if missing:
        report_lines += [
            "",
            f"## BUILD files that do not call {bzl.MACRO}()",
            "",
            (
                f"These hand-written files stand where the conversion writes an `{bzl.BZL_NAME}`; add "
                f'`load(":{bzl.BZL_NAME}", "{bzl.MACRO}")` and a call of it, or the generated '
                "targets are not declared."
            ),
            "",
        ] + [f"- `{f}`" for f in missing]
    return written


def _migrate_additions(directory: Path, package_path: str, buildifier: str | None) -> str:
    """The layout before `autoconf.bzl`, read once more: the package's additive file becomes
    (or joins) its `BUILD.bazel`, loads merged, the top-level constants it reads loaded from
    `autoconf.bzl` and the loads it leaned on the generated file for copied in; the additive
    file is removed.  Returns the report line, which names the constants it reads that live
    inside the macro and have to be declared by hand."""
    build = directory / bzl.BUILD_NAME
    additions = directory / _ADDITIONS_NAME
    text = "\n\n".join(path.read_text(encoding="utf-8") for path in (build, additions) if path.is_file())
    bzl_text = (directory / bzl.BZL_NAME).read_text(encoding="utf-8")
    top, inner = bzl.assigned_names(bzl_text)
    used = bzl.names_used(text)
    build.write_text(
        _format(
            buildifier,
            bzl.build_file(text, symbols=sorted(used & top), loads=bzl.loads_needed(text, bzl_text)),
            "build",
        ),
        encoding="utf-8",
        newline="\n",
    )
    additions.unlink()
    shown = f"{package_path}/" if package_path else ""
    line = f"`{shown}{_ADDITIONS_NAME}` into `{shown}{bzl.BUILD_NAME}`"
    if used & inner:
        line += (
            f"; it reads {', '.join(sorted(used & inner))}, which {bzl.BZL_NAME} defines inside "
            f"{bzl.MACRO}(): declare them in {bzl.BUILD_NAME}"
        )
    return line


def _write_extra_files(directory: Path, files: dict[str, str]) -> list[Path]:
    """Write `files` (path relative to `directory` -> content); the paths written."""
    written: list[Path] = []
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def _destination_module(args: argparse.Namespace, out: Path) -> Path:
    """The MODULE.bazel the conversion will replace: the overlay's for a registry entry (the
    version directory's when the overlay has none yet), the output directory's otherwise."""
    entry: bcr.Entry | None = getattr(args, "entry", None)
    if entry is not None and entry.version is not None:
        overlay = entry.overlay / "MODULE.bazel"
        return overlay if overlay.is_file() else entry.directory / "MODULE.bazel"
    return out / "MODULE.bazel"


def _http_archive_entry(
    args: argparse.Namespace, staging: Path, result: project_mod.ProjectResult, build: Path
) -> tuple[list[str], list[Path]]:
    """`--http-archive-entry`: the root BUILD file goes to the entry as `BUILD.<name>.bazel`, the
    archive to `source.json`, and the report gets the MODULE.bazel lines the consumer adds.  An
    `http_archive` injects one BUILD file, so a conversion that wrote more (a sub-configure
    package, a config.h.in, vendored ports) stops here:
    that project is rendered into its tree instead, see docs/src/convert.md.  Returns the report
    lines and the files placed in the entry."""

    entry = args.entry
    extra = sorted(
        p.relative_to(staging).as_posix()
        for p in staging.rglob("*")
        if p.is_file() and p != build and p.name != "MODULE.bazel"
    )
    if extra:
        raise SystemExit(
            f"{entry.name}: the conversion writes more than one BUILD file ({', '.join(extra)}), "
            "which an http_archive cannot inject; render it into the project tree instead "
            '(docs/src/convert.md, "Rendering into the project")'
        )
    build_file = ownership.place(entry.directory / f"BUILD.{entry.name}.bazel", build.read_text(encoding="utf-8"))
    strip_prefix = args.strip_prefix or f"{result.tarname or result.name}-{result.version}"
    bcr.write_source_json(
        entry.directory, url=args.source_url, source_integrity=args.integrity, strip_prefix=strip_prefix
    )
    placed = [build_file, entry.directory / "source.json"]
    deps = [
        line
        for line in result.module_bazel.splitlines()
        if line.startswith("bazel_dep(") and "rules_cc_autoconf" not in line
    ]
    base = _invocation_dir().resolve()
    label = (
        f"//{entry.directory.relative_to(base).as_posix()}:{build_file.name}"
        if entry.directory.is_relative_to(base)
        else f"//<path to {entry.directory.name}>:{build_file.name}"
    )
    snippet = bcr.http_archive_snippet(
        name=entry.name,
        url=args.source_url,
        source_integrity=args.integrity,
        strip_prefix=strip_prefix,
        build_file=label,
        bazel_deps=deps,
    )
    print(f"MODULE.bazel of the consumer (besides bazel_dep on rules_cc_autoconf):\n\n{snippet}")
    return (
        [
            "",
            "## MODULE.bazel of the consumer",
            "",
            'Besides `bazel_dep(name = "rules_cc_autoconf", ...)`:',
            "",
            "```python",
            snippet.rstrip("\n"),
            "```",
        ],
        placed,
    )


def _manual_programs_section(result: project_mod.ProjectResult, package_path: str = "") -> list[str]:
    """The programs emitted with `tags = ["manual"]`, and a whole package tagged so because its
    sub-configure's `subdir:` decision is MANUAL or undecided, so a reviewer sees what `//...` leaves out."""
    manual = result.state.manual_programs if result.state is not None else []
    if not manual and result.manual_package is None:
        return []
    where = f" in {package_path}" if package_path else ""
    lines = [
        "",
        f"## Targets tagged manual{where}",
        "",
        "configure builds these only when a probe holds. A target's existence cannot depend on a",
        "probe result, so they are defined but left out of `//...`; tests that use them should carry",
        "`target_compatible_with` for the platforms they build on. To build one in `//...`, decide",
        "the key given with the platforms (a label list), or `decision.ALWAYS` / `decision.NEVER`.",
        "",
    ]
    if result.manual_package is not None:
        lines.append(f"- every C/C++ target of this package: {result.manual_package}")
    return lines + [f"- `{name}` ({why}): `program:{name}`" for name, why in sorted(manual)]


def _write_report(
    out: Path,
    expansion: expand_mod.Expansion,
    tree: markers.Tree,
    report_lines: list[str],
    written: list[Path],
    decisions: Decisions,
    decisions_path: Path,
    title: str,
    command: str | None = None,
) -> int:
    """Write CONVERSION_REPORT.md (and the decisions skeleton at `decisions_path`); the exit code:
    3 while decisions are missing.  The outputs are written either way, for a look at the project,
    but a conversion is complete only when decisions.bzl, the one reviewed file, answers every
    question."""
    lines = [
        "# Conversion report",
        "",
        "Generated, like BUILD.bazel and MODULE.bazel next to it; decisions.bzl is the one",
        "hand-maintained file. To regenerate, from a clone of rules_cc_autoconf:",
        "",
        f"    {command or DEFAULT_COMMAND}",
        "",
    ]
    lines.append(f"- configure.ac: {expansion.configure_ac}")
    lines.append(f"- m4 files: {len(expansion.m4_files)}")
    lines.append(f"- files written: {len(written)}")
    if decisions.source:
        lines.append(f"- decisions: {decisions.source}")
    pending = decisions.pending
    if pending:
        added = decisions.write_needed(decisions_path, title=title, version=rules_version())
        lines += [
            "",
            f"## Decisions required ({len(pending)})",
            "",
            "The constructs below have no mechanical translation and were left out. Decide each",
            f"key in `{decisions_path}` (the migration guide, {MIGRATION_GUIDE}, is the reference",
            "for how each construct maps) and run the same command again"
            + (f" ({len(added)} new entries were added with `UNDECIDED` as the value)." if added else "."),
            "",
        ]
        lines += decisions.report_lines()
    if decisions.inherited:
        lines += [
            "",
            f"## Decisions inherited ({len(decisions.inherited)})",
            "",
            f"Answered by the decisions bundled with the ports ({decisions.shared.source if decisions.shared else ''}),",
            "the questions every gnulib project raises; an entry for the key in the project's own file overrides.",
            "",
        ] + decisions.inherited_lines()
    if decisions.defaulted:
        lines += [
            "",
            f"## Decisions taken by default ({len(decisions.defaulted)})",
            "",
            "Keys the table leaves undecided that `DEFAULTS` answered by kind; an entry for a key",
            "overrides its kind's default.",
            "",
        ] + decisions.defaulted_lines()
    if decisions.unused:
        lines += ["", "## Unused decisions", ""] + [f"- `{k}`" for k in decisions.unused]
    lines += report_lines
    if expansion.warnings or tree.warnings:
        lines += ["", "## Tool warnings", ""] + [f"- {w}" for w in expansion.warnings + tree.warnings]
    out.mkdir(parents=True, exist_ok=True)
    (out / "CONVERSION_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for path in written:
        print(f"wrote {path}")
    print(f"report: {out / 'CONVERSION_REPORT.md'}")
    if pending:
        print(f"{len(pending)} decision(s) required; fill in {decisions_path} and rerun", file=sys.stderr)
        return 3
    return 0


BUNDLED_DECISIONS = "gnulib/m4/decisions.bzl"
"""The decisions bundled with the ports, relative to the rules_cc_autoconf checkout."""


def _bundled_decisions(args: argparse.Namespace) -> Decisions | None:
    """The bundled decisions (`--bundled-decisions`): a file, `none`, or by default the checkout's
    `gnulib/m4/decisions.bzl`, through the runfiles when the converter runs from elsewhere."""
    spec = getattr(args, "bundled_decisions", "auto")
    if spec == "none":
        return None
    if spec != "auto":
        return Decisions.load(_resolve(spec))
    root = ports_checkout()
    if root is not None and (root / BUNDLED_DECISIONS).is_file():
        return Decisions.load(root / BUNDLED_DECISIONS)
    runfiles = Runfiles.Create()
    found = runfiles.Rlocation(f"_main/{BUNDLED_DECISIONS}", "_main" if IS_WINDOWS else None) if runfiles else None
    return Decisions.load(Path(found)) if found and Path(found).is_file() else None


def _ports_workspace(args: argparse.Namespace) -> Path:
    """The rules_cc_autoconf checkout whose ports are vendored: the one `bazel run` started
    from, else `--index <path>`."""
    root = ports_checkout()
    if root is not None:
        return root
    if args.index not in ("auto", "snapshot", "none") and (_resolve(args.index) / "gnulib/m4").is_dir():
        return _resolve(args.index)
    raise SystemExit(
        "--vendor-ports needs the rules_cc_autoconf checkout the ports are copied from: run the "
        "converter from it with `bazel run`, or pass --index <path-to-checkout>"
    )


def _root_package(args: argparse.Namespace, out: Path) -> str:
    """The package the converted tree is rooted at, for the visibility of what it keeps to itself:
    "" for a registry or http_archive entry and for an output directory that is a module root;
    the directory's path within the workspace when `--out` renders into a subdirectory of a
    larger Bazel workspace (a vendored tree), so the confinement is `//<dir>:__subpackages__`."""
    if args.entry is not None:
        return ""
    for ancestor in out.resolve().parents:
        if any((ancestor / marker).is_file() for marker in ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")):
            return out.resolve().relative_to(ancestor).as_posix()
    return ""


def _vendor(
    args: argparse.Namespace, result: project_mod.ProjectResult, index: PortIndex | None, out: Path
) -> list[Path]:
    """`--vendor-ports`: rewrite the package to use copies of its ports, written under the module root."""
    if not getattr(args, "vendor_ports", False):
        return []
    if index is None:
        raise SystemExit("--vendor-ports needs the port index (not --index none)")

    vendored = vendor.vendor_ports(
        result.package,
        result.state,
        index,
        _ports_workspace(args),
        rules_version(),
        confined=project_mod.confined_visibility(_root_package(args, out), shared=True),
    )
    result.notes.extend(vendored.notes)
    result.module_bazel = vendor.ensure_module_deps(result.module_bazel, vendored.files)
    return _write_extra_files(out, vendored.files)


def _convert_project(
    args: argparse.Namespace, out: Path, expansion: expand_mod.Expansion, tree: markers.Tree, index: PortIndex | None
) -> int:
    """Project mode: the root package from `project.convert_project`, written as `autoconf.bzl`
    (formatted with buildifier when available) beside the hand-written BUILD.bazel that loads
    it, and MODULE.bazel, plus the report and, when decisions are required, the decisions
    skeleton.
    """

    dev_override = None
    if args.dev:
        root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        dev_override = Path(root).resolve().as_posix() if root else None
    decisions_path = _decisions_path(args, out)
    decisions = Decisions.load(decisions_path)
    decisions.shared = _bundled_decisions(args)
    hand_root = _hand_root(args, out)
    hand = _hand_builds(hand_root)
    # The hand-written BUILD files' loads name modules of the generated MODULE.bazel; the root's
    # rule names are what a root `generated:` decision may hand a file to.
    extra_loads = set().union(*(modulebazel_mod.load_repos(h.text) for h in hand.values())) if hand else set()
    if args.entry is not None:
        # The other hand-written files of the overlay (a tests/ package, a .bzl BUILD.bazel
        # loads) load modules too: gawk's test package needs rules_shell.
        extra_loads |= modulebazel_mod.source_tree_loads(args.entry.overlay)
    # The registry modules the hand-written files' labels name (`"@openssl//:crypto"`):
    # bazel_deps at the checkout's newest version; a repository the registry does not hold is
    # reported.
    extra_modules: dict[str, str] = {}
    if args.entry is not None and args.entry.registry is not None and hand:
        referenced = set().union(*(project_mod.label_repos(h.text) for h in hand.values()))
        referenced -= set(modulebazel_mod.MODULES) | {args.entry.name}  # the known modules have their own path
        extra_modules = bcr.newest_versions(args.entry.registry, sorted(referenced))
        for missing in sorted(referenced - set(extra_modules)):
            print(
                f"warning: a hand-written BUILD file names @{missing}, which the registry checkout has no module for",
                file=sys.stderr,
            )
    root_hand = hand.get("", HandBuild())
    # A MODULE.bazel already at the destination: its module() entry is kept (bazel_compatibility,
    # compatibility_level), the rest is regenerated and anything else it held is reported.
    module_attrs, module_others = modulebazel_mod.existing_module(_destination_module(args, out))
    result = project_mod.convert_project(
        tree,
        expansion,
        _source(args),
        index,
        source_root=_source_root(args),
        rules_version=rules_version(),
        dev_override=dev_override,
        decisions=decisions,
        command=reproduce_command(args),
        extra_loads=extra_loads,
        hand_targets=emit.rule_kinds(root_hand.text),
        hand_packages=_hand_packages(hand_root),
        hand_build=root_hand.text,
        module_attrs=module_attrs,
        module_name=args.entry.name if args.entry is not None else None,
        extra_modules=extra_modules,
        root_package=_root_package(args, out),
        repo_file=not args.http_archive_entry,
    )
    # Rendered into a staging directory, then placed: the destination (the scratch output, a
    # registry entry's overlay, the project tree) holds hand-written files too, and the converter
    # owns only the files that carry its mark (ownership.py).
    staging = Path(tempfile.mkdtemp(prefix="convert-staging-"))
    try:
        _vendor(args, result, index, staging)
        if not args.annotate:
            emit.strip_annotations(result.package)
        report_lines = _manual_programs_section(result) + _module_report(module_attrs, module_others)
        buildifier = find_buildifier(args.buildifier)
        build = staging / "BUILD.bazel"
        if args.http_archive_entry:
            # http_archive injects one file: the BUILD file is all generated.
            _write_build(build, result.package, repo="@rules_cc_autoconf", in_repo=False, buildifier=buildifier)
        else:
            replaced = _omit_replaced(result.package, root_hand, report_lines)
            _write_targets(staging, result.package, buildifier=buildifier, replaced=replaced)
        (staging / "MODULE.bazel").write_text(result.module_bazel, encoding="utf-8", newline="\n")
        _write_extra_files(staging, result.extra_files)
        if result.notes:
            report_lines += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]
        _convert_subprojects(
            args,
            staging,
            _source(args),
            result.subdirs,
            index,
            decisions,
            report_lines,
            dev_override,
            buildifier,
            hand,
            gates=result.subdir_gates,
        )
        # The packages that got an autoconf.bzl: each gets its hand-written BUILD.bazel once placed.
        generated = sorted(
            "" if path.parent == staging else path.parent.relative_to(staging).as_posix()
            for path in staging.rglob(bzl.BZL_NAME)
        )
        placed: list[Path] = []
        removed: list[Path] = []
        try:
            if args.entry is None:
                placed, removed = ownership.sync(staging, out, regenerated=("MODULE.bazel",))
                if hand_root is not None:
                    placed += _place_hand_builds(out, generated, report_lines, buildifier)
            if args.http_archive_entry:
                lines, placed_entry = _http_archive_entry(args, staging, result, build)
                report_lines += lines
                placed += placed_entry
            if args.bcr:
                entry_written, entry_removed = _write_bcr(
                    args,
                    out,
                    staging,
                    result,
                    dev_override,
                    finish_overlay=lambda overlay: _place_hand_builds(overlay, generated, report_lines, buildifier),
                )
                placed += entry_written
                removed += entry_removed
        except ownership.Conflict as error:
            raise SystemExit(str(error)) from None
        if removed:
            report_lines += [
                "",
                "## Generated files removed",
                "",
                "Files an earlier run generated and this one does not. Hand-written files are never removed.",
                "",
            ] + [f"- `{p}`" for p in removed]
        return _write_report(
            out,
            expansion,
            tree,
            report_lines,
            placed,
            decisions,
            decisions_path,
            f"converting {result.name} {result.version or ''}".strip(),
            reproduce_command(args),
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _module_report(module_attrs: dict[str, str], module_others: list[str]) -> list[str]:
    """The report sections on the MODULE.bazel found at the destination: the `module()` attributes
    kept and the statements not carried over (`modulebazel.existing_module`)."""
    lines: list[str] = []
    if module_attrs:
        lines += [
            "",
            "## MODULE.bazel: kept from the existing file",
            "",
            "The `module()` attributes the file at the destination had besides name and version:",
            "",
        ] + [f"- `{attr} = {text}`" for attr, text in module_attrs.items()]
    if module_others:
        lines += [
            "",
            "## MODULE.bazel: statements not carried over",
            "",
            (
                "The existing file held these besides `module()` and the `bazel_dep` lines the conversion "
                "regenerates; they are not in the new file:"
            ),
            "",
        ] + [f"- `{line}`" for line in module_others]
    return lines


def _write_bcr(
    args: argparse.Namespace,
    out: Path,
    staging: Path,
    result: project_mod.ProjectResult,
    dev_override: str | None,
    finish_overlay: Callable[[Path], list[Path]],
) -> tuple[list[Path], list[Path]]:
    """`--bcr`: the registry entry, in the entry's registry or under `<out>/modules/`; returns the
    files written and the stale generated files removed (`bcr.write_entry`).  `finish_overlay`
    places the hand-written BUILD files once the generated ones are in the overlay."""
    name = args.entry.name if args.entry is not None else result.module_name
    version = args.bcr_version or result.version or "0.0.0"
    # The registry entry never points at a local checkout.
    module_text = result.module_bazel
    if dev_override or version != result.version:
        module_text = project_mod.module_bazel_text(result, rules_version(), None, version=version, module_name=name)
    registry = args.entry.registry if args.entry is not None else out
    # The homepage: --homepage, AC_INIT's URL argument, or the entry's existing metadata.json.
    homepage = args.homepage or result.homepage or bcr.recorded_homepage(registry, name)
    missing = [
        flag
        for flag, value in (
            ("--source-url", args.source_url),
            ("--integrity", args.integrity),
            ("--homepage", homepage),
        )
        if not value
    ]
    if missing or homepage is None:  # a missing homepage is in `missing` already; spelled out for the type
        raise SystemExit(
            f"a registry entry needs {', '.join(missing)} "
            "(AC_INIT's URL argument supplies the homepage when it has one)"
        )
    return bcr.write_entry(
        registry,
        name=name,
        version=version,
        staging=staging,
        module_text=module_text,
        source_url=args.source_url,
        source_integrity=args.integrity,
        strip_prefix=args.strip_prefix,
        homepage=homepage,
        finish_overlay=finish_overlay,
    )


def _convert_subprojects(
    args: argparse.Namespace,
    out: Path,
    src: Path,
    subdirs: list[str],
    index: PortIndex | None,
    decisions: Decisions,
    report_lines: list[str],
    dev_override: str | None,
    buildifier: str | None,
    hand: dict[str, HandBuild],
    depth: int = 1,
    prefix: str = "",
    gates: dict[str, project_mod.SubdirGate] | None = None,
    inherited_gate: project_mod.SubdirGate | None = None,
) -> list[Path]:
    """`AC_CONFIG_SUBDIRS`: each sub-configure becomes a package of the same
    module, at its directory (texlive's `libs/*`, `texk/*`), converted from its
    own configure.ac and Makefile.am tree; its own AC_CONFIG_SUBDIRS are
    followed in turn.  One MODULE.bazel, one decisions file, one report.
    `gates` (`ProjectResult.subdir_gates`) is the `target_compatible_with` of
    the packages their configure enters conditionally; a sub-sub-configure
    inherits its parent's unless it has one of its own."""

    written: list[Path] = []
    if depth > 4:
        return written
    for subdir in subdirs:
        gate = (gates or {}).get(subdir, inherited_gate)
        package_path = f"{prefix}{subdir}"
        sub_src = src / subdir
        try:
            expansion, work, tooldir = _run_expansion(args, sub_src)
        except expand_mod.ExpandError as error:
            report_lines += ["", f"## {package_path}", "", f"- not converted: {error}"]
            continue
        try:
            tree = markers.parse(expansion.read())
            hand_package = hand.get(package_path, HandBuild())
            result = project_mod.convert_project(
                tree,
                expansion,
                sub_src,
                index,
                rules_version=rules_version(),
                dev_override=dev_override,
                decisions=decisions,
                command=reproduce_command(args),
                parent_headers=f"//:{TOP_HEADERS}",
                package_path=package_path,
                hand_targets=emit.rule_kinds(hand_package.text),
                hand_build=hand_package.text,
                root_package=_root_package(args, out),
                gate=gate,
            )
            result.package.path = package_path
            directory = out / package_path
            directory.mkdir(parents=True, exist_ok=True)
            written += _vendor(args, result, index, out)
            if not args.annotate:
                emit.strip_annotations(result.package)
            replaced = _omit_replaced(result.package, hand_package, report_lines)
            written.append(_write_targets(directory, result.package, buildifier=buildifier, replaced=replaced))
            written += _write_extra_files(directory, result.extra_files)
            report_lines += ["", f"## {package_path} ({result.name} {result.version or ''})".rstrip(" ()"), ""]
            report_lines += [f"- {n}" for n in result.notes] or ["- no notes"]
            report_lines += _manual_programs_section(result, package_path)
            written += _convert_subprojects(
                args,
                out,
                sub_src,
                result.subdirs,
                index,
                decisions,
                report_lines,
                dev_override,
                buildifier,
                hand,
                depth + 1,
                f"{package_path}/",
                gates=result.subdir_gates,
                inherited_gate=gate,
            )
        finally:
            _cleanup(args, work, tooldir)
    return written


def cmd_check(args: argparse.Namespace) -> int:
    """`check`: run GNU configure on the sources with the hermetic autotools and diff its config.h
    (and the other configured headers) against the ones Bazel built (`--bazel-config-h`).
    """

    src = _source(args)
    out = _resolve(args.out)
    try:
        cc_env, resolve = check_mod.load_cc_env(args.cc_env)
    except (RuntimeError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    tooldir = expand_mod.scratch_root("rccac-autotools")
    autotools = Autotools(tooldir)
    bazel_headers = {}
    for entry in args.bazel_config_h:
        name, _, path = entry.rpartition("=")
        bazel_headers[name or Path(path).name] = _resolve(path)
    try:
        result = check_mod.run_check(
            autotools,
            src,
            out / "gnu",
            cc_env=cc_env,
            resolve=resolve,
            configure_args=args.configure_arg or None,
            bazel_headers=bazel_headers,
            regenerate=args.regenerate,
        )
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tooldir, ignore_errors=True)
    print(f"GNU configure results: {result.work_dir}")
    for header in result.headers:
        print(f"  {header.relative_to(result.work_dir).as_posix()}")
    print(f"  output_variables.txt ({len(result.substs)} variables)")
    for differing, delta in result.differences.items():
        (out / f"{Path(differing).name}.diff").write_text(delta + "\n", encoding="utf-8", newline="\n")
        print(f"\n{differing} differs from the Bazel header:\n{delta}")
    if bazel_headers and not result.differences:
        print("config headers match.")
    return 1 if result.differences else 0


def cmd_expand(args: argparse.Namespace) -> int:
    """`expand`: write the instrumented expansion (expanded.sh) and its marker tree (tree.json), for
    debugging the front end.
    """
    out = _resolve(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        expansion, work, tooldir = _run_expansion(args)
    except expand_mod.ExpandError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        tree = markers.parse(expansion.read())
        shutil.copyfile(expansion.expanded, out / "expanded.sh")
        dump: TreeDump = {
            "configure_ac": str(expansion.configure_ac),
            "m4_files": expansion.m4_files,
            "warnings": expansion.warnings + tree.warnings,
            "items": [_node_to_dict(item) for item in tree.items],
        }
        (out / "tree.json").write_text(
            json.dumps(dump, indent=2),
            encoding="utf-8",
        )
        defuns = tree.defuns()
        print(
            f"expanded {expansion.configure_ac.name}: {len(expansion.m4_files)} m4 files, "
            f"{len(defuns)} macro bodies, {len(list(tree.walk()))} nodes"
        )
        for node in defuns:
            print(f"  {node.name}  ({node.loc})  calls={len(node.calls())}")
        for warning in expansion.warnings + tree.warnings:
            print(f"warning: {warning}")
        return 0
    finally:
        _cleanup(args, work, tooldir)


def _common_input_args(parser: argparse.ArgumentParser, src_required: bool = True) -> None:
    parser.add_argument(
        "--src", required=src_required, help="the sources: a directory with configure.ac, a release archive, or its URL"
    )
    parser.add_argument(
        "--subdir",
        default=None,
        metavar="DIR",
        help="the directory of configure.ac inside --src when it is not the root (tcl: unix)",
    )
    parser.add_argument(
        "--m4-dir",
        action="append",
        default=[],
        metavar="DIR",
        help="stage the *.m4 files of DIR instead of the project's own macro dirs",
    )
    parser.add_argument("--wrap", action="append", default=[], metavar="MACRO", help="also record calls of this macro")
    parser.add_argument("--keep-work", action="store_true", help="keep the scratch directory")


def build_parser() -> argparse.ArgumentParser:
    """The command line: the `convert`, `check` and `expand` subcommands."""
    parser = argparse.ArgumentParser(
        prog="convert", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="convert a project to BUILD files")
    _common_input_args(convert_parser, src_required=False)
    convert_parser.add_argument(
        "--out",
        default=None,
        help="output directory (with --bcr-entry or "
        "--http-archive-entry: a scratch directory for the "
        "report, by default a temporary one)",
    )
    convert_parser.add_argument(
        "--bcr-entry",
        default=None,
        metavar="DIR",
        help="a registry checkout's modules/<name>/<version>: write the entry there, taking "
        "the archive from its source.json (or --src and --integrity the first time) and "
        "the decisions from its decisions.bzl; the hand-written BUILD.bazel files in its "
        "overlay are kept and read",
    )
    convert_parser.add_argument(
        "--http-archive-entry",
        default=None,
        metavar="DIR",
        help="a consumer's 3rdparty/<name>: write the generated BUILD file there as "
        "BUILD.<name>.bazel for http_archive(build_file = ...), with decisions.bzl and "
        "source.json read from it as for --bcr-entry; only for a conversion that is one "
        "BUILD file",
    )
    convert_parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILE",
        help="translate only the AC_DEFUNs of this m4 file (basename); repeatable",
    )
    convert_parser.add_argument(
        "--layout",
        choices=["per-file", "package"],
        default=None,
        help="one package per m4 file, or a single package (default: per-file with --only, package otherwise)",
    )
    convert_parser.add_argument(
        "--package-prefix", default="m4/", help="per-file layout: packages land at <prefix><stem> (default m4/)"
    )
    convert_parser.add_argument(
        "--in-repo",
        dest="in_repo",
        action="store_true",
        default=None,
        help="emit for this repository (autoconf_cache, // labels)",
    )
    convert_parser.add_argument(
        "--external",
        dest="in_repo",
        action="store_false",
        help="emit for an external project (@rules_cc_autoconf labels)",
    )
    convert_parser.add_argument(
        "--vendor-ports",
        action="store_true",
        help="copy the gnulib ports the project uses into third_party/rules_cc_autoconf/ of the "
        "project (no dependency on rules_cc_autoconf's ports; run from its checkout, or "
        "pass --index <checkout>)",
    )
    convert_parser.add_argument(
        "--allow-upstream",
        action="append",
        metavar="MODULE",
        help="let this registry dependency move to a newer upstream release (by default an entry's "
        "dependencies take the newest `.bcr.N` of the release they already depend on)",
    )
    convert_parser.add_argument(
        "--index", default="auto", help="where the hand ports are: a workspace path, `snapshot`, `auto`, `none`"
    )
    convert_parser.add_argument(
        "--gnulib-commit", default=None, help="gnulib commit for docstring URLs of unported m4 files"
    )
    convert_parser.add_argument(
        "--annotate",
        action="store_true",
        help="comment every generated check, dep and rule with the m4 it came from "
        "(off by default: the BUILD file stays short)",
    )
    convert_parser.add_argument(
        "--buildifier", default="auto", help="buildifier to format the output with (`auto`, `none`, or a path)"
    )
    convert_parser.add_argument(
        "--bundled-decisions",
        default="auto",
        metavar="FILE",
        help="the decisions bundled with the ports, read beneath the project's own (default: the checkout's "
        "gnulib/m4/decisions.bzl; `none` to read none)",
    )
    convert_parser.add_argument(
        "--decisions",
        default=None,
        metavar="FILE",
        help="Starlark decisions file for constructs without a mechanical translation "
        "(default: decisions.bzl in --out; created and extended by the converter)",
    )
    convert_parser.add_argument(
        "--dev", action="store_true", help="project mode: MODULE.bazel gets a local_path_override to this checkout"
    )
    convert_parser.add_argument(
        "--bcr",
        action="store_true",
        help="project mode: also write a Bazel Central Registry entry under <out>/modules/",
    )
    convert_parser.add_argument("--bcr-version", default=None, help="registry version (default: the package version)")
    convert_parser.add_argument(
        "--source-url", default=None, help="release archive URL for source.json (required with --bcr)"
    )
    convert_parser.add_argument(
        "--integrity", default=None, help="release archive integrity, sha256-... (required with --bcr)"
    )
    convert_parser.add_argument("--strip-prefix", default=None, help="release archive strip_prefix")
    convert_parser.add_argument(
        "--homepage", default=None, help="project homepage for metadata.json (default: AC_INIT's URL argument)"
    )
    convert_parser.set_defaults(func=cmd_convert)

    check_parser = subparsers.add_parser(
        "check", help="run GNU configure with the Bazel C++ toolchain and compare config headers"
    )
    check_parser.add_argument(
        "--src", required=True, help="the sources: a directory with configure.ac, a release archive, or its URL"
    )
    check_parser.add_argument("--out", required=True, help="where the GNU results and diffs go")
    check_parser.add_argument(
        "--bazel-config-h",
        action="append",
        default=[],
        metavar="[NAME=]FILE",
        help="a config header Bazel built from the converted BUILD; NAME is the "
        "AC_CONFIG_HEADERS output it corresponds to (default: the file name)",
    )
    check_parser.add_argument(
        "--configure-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="arguments for ./configure (default: the conformance tests' cache presets)",
    )
    check_parser.add_argument(
        "--regenerate", action="store_true", help="run aclocal and autoconf even if the tree ships a configure script"
    )
    check_parser.add_argument("--cc-env", default=None, help=argparse.SUPPRESS)
    check_parser.set_defaults(func=cmd_check)

    expand_parser = subparsers.add_parser("expand", help="instrumented autom4te expansion")
    _common_input_args(expand_parser)
    expand_parser.add_argument("--out", required=True, help="where to write expanded.sh and tree.json")
    expand_parser.set_defaults(func=cmd_expand)

    parser.add_argument(
        "--profile",
        default=None,
        metavar="FILE",
        help="write a cProfile of the run to FILE (pstats format) and a summary to FILE.txt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command line. The exit code is 0, 1 when the expansion fails, 2 on a usage or
    environment error, 3 when decisions are required.
    """
    args = build_parser().parse_args(argv)
    command: Callable[[argparse.Namespace], int] = args.func
    if args.profile:
        profile_path = _resolve(args.profile)
        profiler = cProfile.Profile()
        code = profiler.runcall(command, args)
        profiler.dump_stats(profile_path)
        with profile_path.with_suffix(profile_path.suffix + ".txt").open("w", encoding="utf-8") as out:
            stats = pstats.Stats(profiler, stream=out).sort_stats("cumulative")
            stats.print_stats(40)
            stats.sort_stats("tottime").print_stats(25)
        return code
    return command(args)


if __name__ == "__main__":
    sys.exit(main())
