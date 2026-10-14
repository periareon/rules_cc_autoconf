"""`bazel run //tools/convert -- <subcommand> ...`

Subcommands:
  convert  Convert an autoconf project (or selected m4 files of one) into
           BUILD files using rules_cc_autoconf.
  expand   Run the instrumented autom4te expansion only and dump the marker
           tree (a debugging aid for the converter itself).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from autoconf.tests.gnu.autotools import Autotools

from tools.convert import emit
from tools.convert import expand as expand_mod
from tools.convert import source as source_mod
from tools.convert import markers
from tools.convert.decisions import Decisions
from tools.convert.index import PortIndex
from tools.convert.translate import Options, Translator


def _decisions_path(args: argparse.Namespace, out: Path) -> Path:
    """`--decisions`, or `decisions.bzl` in the output directory."""
    return _resolve(args.decisions) if args.decisions else out / "decisions.bzl"


def _resolve(path: str) -> Path:
    """Paths on the command line are relative to where `bazel run` was invoked."""
    base = Path(os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd()))
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _source(args: argparse.Namespace) -> Path:
    """The source directory of `--src`: a directory, a release archive or its URL (fetched once)."""
    cached = getattr(args, "_source_path", None)
    if cached is None:
        base = Path(os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd()))
        cached = source_mod.resolve(args.src, base, getattr(args, "integrity", None))
        if getattr(args, "subdir", None):
            cached = cached / args.subdir
        args._source_path = cached  # noqa: SLF001
    return cached


def _source_root(args: argparse.Namespace) -> Path:
    """The whole source tree: `_source(args)` without the `--subdir`."""
    src = _source(args)
    subdir = getattr(args, "subdir", None)
    return Path(*src.parts[:len(src.parts) - len(Path(subdir).parts)]) if subdir else src


def reproduce_command(args: argparse.Namespace) -> str:
    """The `bazel run` line that reproduces this conversion from a clone of
    rules_cc_autoconf (what the generated files carry in their banner)."""
    words = ["bazel", "run", "//tools/convert", "--", "convert", "--src", args.src, "--out", args.out]
    for flag in ("subdir", "decisions", "append", "bcr_version", "source_url", "integrity", "strip_prefix", "homepage"):
        value = getattr(args, flag, None)
        if value:
            words += [f"--{flag.replace('_', '-')}", value]
    if getattr(args, "bcr", False):
        words.append("--bcr")
    return " ".join(shlex.quote(w) for w in words)


def _node_to_dict(item: markers.Node | markers.Text) -> dict:
    """A marker tree node as JSON-ready data, for `expand`'s tree.json."""
    if isinstance(item, markers.Text):
        return {"text": item.text, "line": item.line}
    data = {
        "kind": item.kind,
        "id": item.id,
        "name": item.name,
        "line": item.line,
        "end_line": item.end_line,
    }
    if item.loc:
        data["loc"] = item.loc
    if item.args:
        data["args"] = item.args
    if item.prog is not None:
        data["prog"] = item.prog
    data["items"] = [_node_to_dict(child) for child in item.items]
    return data


def _m4_files(dirs: list[str]) -> list[Path] | None:
    if not dirs:
        return None
    files: list[Path] = []
    for directory in dirs:
        files.extend(sorted(_resolve(directory).glob("*.m4")))
    return files


def _run_expansion(args: argparse.Namespace, src: Path | None = None):
    src = src if src is not None else _source(args)
    tooldir = expand_mod.scratch_root("rccac-autotools")
    work = expand_mod.scratch_root("rccac-work")
    autotools = Autotools(tooldir)
    expansion = expand_mod.expand(
        autotools, src, work, m4_files=_m4_files(args.m4_dir), extra_wraps=args.wrap,
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
        root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        if root and (Path(root) / "gnulib" / "m4").is_dir():
            live = PortIndex.from_workspace(Path(root))
            try:
                differences = PortIndex.from_snapshot().differences(live)
            except (FileNotFoundError, OSError):
                differences = []
            if differences:
                print(f"warning: tools/convert/index.toml is stale ({len(differences)} differences); "
                      "run `bazel run //tools/convert:update_index`")
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
    try:
        from python.runfiles import Runfiles  # pylint: disable=import-outside-toplevel
        from autoconf.tests.gnu.autotools import IS_WINDOWS  # pylint: disable=import-outside-toplevel

        runfiles = Runfiles.Create()
        if runfiles:
            found = runfiles.Rlocation("_main/version.bzl", "_main" if IS_WINDOWS else None)
            if found:
                candidates.append(Path(found))
    except ImportError:
        pass
    for candidate in candidates:
        if candidate.is_file():
            match = re.search(r'VERSION\s*=\s*"([^"]+)"', candidate.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
    return "0.0.0"


def cmd_convert(args: argparse.Namespace) -> int:
    """`convert`: expand the project's configure.ac with the instrumented autoconf, then translate
    either the whole project into one package (project mode, the default) or the selected m4
    files into per-file packages (--only); write the BUILD files and the report.
    """
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

        m4_sources = {}
        for relative in expansion.m4_files:
            path = work / relative
            if path.is_file():
                m4_sources[relative] = path.read_text(encoding="utf-8", errors="replace")
        m4_sources[expansion.configure_ac.name] = expansion.configure_ac.read_text(
            encoding="utf-8", errors="replace")

        selected = set(args.only) if args.only else set(expansion.m4_files) | {expansion.configure_ac.name}
        in_repo = args.in_repo if args.in_repo is not None else (layout == "per-file")
        repo = "" if in_repo else "@rules_cc_autoconf"
        options = Options(repo=repo, in_repo=in_repo, layout=layout,
                          package_prefix=args.package_prefix, gnulib_commit=args.gnulib_commit)
        decisions = Decisions.load(_decisions_path(args, out))
        translator = Translator(tree, index=index, selected_files=selected, m4_sources=m4_sources,
                                options=options, decisions=decisions)
        packages = translator.translate()

        buildifier = find_buildifier(args.buildifier)
        written = []
        for package in packages:
            directory = out / package.path if package.path else out
            directory.mkdir(parents=True, exist_ok=True)
            build = directory / "BUILD.bazel"
            if not args.annotate:
                emit.strip_annotations(package)
            build.write_text(emit.render_package(package, repo=repo, in_repo=in_repo),
                             encoding="utf-8", newline="\n")
            if buildifier:
                subprocess.run([buildifier, str(build)], check=False)
            written.append(build)

        report = translator.report
        lines = []
        if report.inlined:
            lines += ["", "## Macros expanded inline", ""] + [f"- {n}" for n in sorted(report.inlined)]
        if report.notes:
            lines += ["", "## Notes", ""] + [f"- {n}" for n in report.notes]
        return _write_report(out, expansion, tree, lines, written, decisions,
                             f"converting {_source(args).name}", reproduce_command(args))
    finally:
        _cleanup(args, work, tooldir)


def _write_report(out: Path, expansion, tree, report_lines: list[str], written: list[Path],  # noqa: PLR0913
                  decisions, title: str, command: str | None = None) -> int:
    """Write CONVERSION_REPORT.md (and the decisions skeleton); the exit code: 3 while decisions
    are missing.  The outputs are written either way, for a look at the project, but a conversion
    is complete only when decisions.bzl, the one reviewed file, answers every question."""
    lines = ["# Conversion report", "",
             "Generated, like BUILD.bazel and MODULE.bazel next to it; decisions.bzl is the one",
             "hand-maintained file. To regenerate, from a clone of rules_cc_autoconf:", "",
             f"    {command or 'bazel run //tools/convert -- convert --src <sources> --out <this directory>'}", ""]
    lines.append(f"- configure.ac: {expansion.configure_ac}")
    lines.append(f"- m4 files: {len(expansion.m4_files)}")
    lines.append(f"- files written: {len(written)}")
    if decisions.source:
        lines.append(f"- decisions: {decisions.source}")
    pending = decisions.pending
    decisions_path = Path(decisions.source) if decisions.source else out / "decisions.bzl"
    if pending:
        added = decisions.write_needed(decisions_path, title=title)
        lines += ["", f"## Decisions required ({len(pending)})", "",
                  "The constructs below have no mechanical translation and were left out. Decide each",
                  f"key in `{decisions_path}` and run the same command again"
                  + (f" ({len(added)} new entries were added with `UNDECIDED` as the value)." if added else "."), ""]
        lines += decisions.report_lines()
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


def _ports_workspace(args: argparse.Namespace) -> Path:
    """The rules_cc_autoconf checkout whose ports are vendored: the one `bazel run` started
    from, else `--index <path>`."""
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root and (Path(root) / "gnulib" / "m4").is_dir():
        return Path(root)
    if args.index not in ("auto", "snapshot", "none") and (_resolve(args.index) / "gnulib" / "m4").is_dir():
        return _resolve(args.index)
    raise SystemExit("--vendor-ports needs the rules_cc_autoconf checkout the ports are copied from: run the "
                     "converter from it with `bazel run`, or pass --index <path-to-checkout>")


def _vendor(args: argparse.Namespace, result, index, out: Path) -> list[Path]:
    """`--vendor-ports`: rewrite the package to use copies of its ports, written under the module root."""
    if not getattr(args, "vendor_ports", False):
        return []
    if index is None:
        raise SystemExit("--vendor-ports needs the port index (not --index none)")
    from tools.convert import vendor  # pylint: disable=import-outside-toplevel

    vendored = vendor.vendor_ports(result.package, result.state, index, _ports_workspace(args), rules_version())
    result.notes.extend(vendored.notes)
    result.module_bazel = vendor.ensure_module_deps(result.module_bazel, vendored.files)
    written: list[Path] = []
    for name, content in vendored.files.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def _convert_project(args: argparse.Namespace, out: Path, expansion, tree, index) -> int:
    """Project mode: the root package from `project.convert_project`, written as BUILD.bazel
    (formatted with buildifier when available) and MODULE.bazel, plus the report and, when
    decisions are required, the decisions skeleton.
    """
    from tools.convert import project as project_mod  # pylint: disable=import-outside-toplevel

    dev_override = None
    if args.dev:
        root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
        dev_override = Path(root).resolve().as_posix() if root else None
    decisions = Decisions.load(_decisions_path(args, out))
    fragment_text, fragment_name = "", ""
    if args.append:
        fragment = Path(args.append)
        if not fragment.is_absolute():
            fragment = Path(os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())) / fragment
        fragment_text, fragment_name = fragment.read_text(encoding="utf-8"), fragment.name
    result = project_mod.convert_project(tree, expansion, _source(args), index, source_root=_source_root(args),
                                         rules_version=rules_version(), dev_override=dev_override,
                                         decisions=decisions, command=reproduce_command(args),
                                         extra_loads=set(project_mod.load_repos(fragment_text)))
    out.mkdir(parents=True, exist_ok=True)
    written_vendored = _vendor(args, result, index, out)
    build = out / "BUILD.bazel"
    if not args.annotate:
        emit.strip_annotations(result.package)
    if fragment_text:
        emit.append_fragment(result.package, fragment_text, fragment_name)
    build.write_text(emit.render_package(result.package, repo="@rules_cc_autoconf", in_repo=False),
                     encoding="utf-8", newline="\n")
    buildifier = find_buildifier(args.buildifier)
    if buildifier:
        subprocess.run([buildifier, str(build)], check=False)
    module = out / "MODULE.bazel"
    module.write_text(result.module_bazel, encoding="utf-8", newline="\n")
    written = [build, module] + written_vendored
    for name, content in result.extra_files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(content, encoding="utf-8", newline="\n")
        written.append(out / name)
    if args.bcr:
        from tools.convert import bcr  # pylint: disable=import-outside-toplevel

        name = result.module_name
        version = args.bcr_version or result.version or "0.0.0"
        # The registry entry never points at a local checkout.
        module_text = result.module_bazel
        if dev_override:
            module_text = project_mod.module_bazel_text(result, rules_version(), None)
        homepage = args.homepage or result.homepage
        missing = [flag for flag, value in (("--source-url", args.source_url), ("--integrity", args.integrity),
                                            ("--homepage", homepage)) if not value]
        if missing:
            raise SystemExit(f"--bcr needs {', '.join(missing)} (AC_INIT's URL argument supplies the homepage)")
        written += bcr.write_entry(
            out, name=name, version=version, build_text=build.read_text(encoding="utf-8"),
            module_text=module_text, source_url=args.source_url, source_integrity=args.integrity,
            strip_prefix=args.strip_prefix, homepage=homepage,
        )
    report_lines = []
    if result.notes:
        report_lines += ["", "## Notes", ""] + [f"- {n}" for n in result.notes]
    written += _convert_subprojects(args, out, _source(args), result.subdirs, index, decisions, report_lines,
                                    dev_override, buildifier)
    return _write_report(out, expansion, tree, report_lines, written, decisions,
                         f"converting {result.name} {result.version or ''}".strip(), reproduce_command(args))


def _convert_subprojects(args: argparse.Namespace, out: Path, src: Path, subdirs: list[str], index,  # noqa: PLR0913
                         decisions, report_lines: list[str], dev_override: str | None, buildifier,
                         depth: int = 1, prefix: str = "") -> list[Path]:
    """`AC_CONFIG_SUBDIRS`: each sub-configure becomes a package of the same
    module, at its directory (texlive's `libs/*`, `texk/*`), converted from its
    own configure.ac and Makefile.am tree; its own AC_CONFIG_SUBDIRS are
    followed in turn.  One MODULE.bazel, one decisions file, one report."""
    from tools.convert import project as project_mod  # pylint: disable=import-outside-toplevel

    written: list[Path] = []
    if depth > 4:
        return written
    for subdir in subdirs:
        package_path = f"{prefix}{subdir}"
        sub_src = src / subdir
        try:
            expansion, work, tooldir = _run_expansion(args, sub_src)
        except expand_mod.ExpandError as error:
            report_lines += ["", f"## {package_path}", "", f"- not converted: {error}"]
            continue
        try:
            tree = markers.parse(expansion.read())
            result = project_mod.convert_project(tree, expansion, sub_src, index,
                                                 rules_version=rules_version(), dev_override=dev_override,
                                                 decisions=decisions, command=reproduce_command(args))
            result.package.path = package_path
            directory = out / package_path
            directory.mkdir(parents=True, exist_ok=True)
            written += _vendor(args, result, index, out)
            build = directory / "BUILD.bazel"
            if not args.annotate:
                emit.strip_annotations(result.package)
            build.write_text(emit.render_package(result.package, repo="@rules_cc_autoconf", in_repo=False),
                             encoding="utf-8", newline="\n")
            if buildifier:
                subprocess.run([buildifier, str(build)], check=False)
            written.append(build)
            for name, content in result.extra_files.items():
                (directory / name).parent.mkdir(parents=True, exist_ok=True)
                (directory / name).write_text(content, encoding="utf-8", newline="\n")
                written.append(directory / name)
            report_lines += ["", f"## {package_path} ({result.name} {result.version or ''})".rstrip(" ()") , ""]
            report_lines += [f"- {n}" for n in result.notes] or ["- no notes"]
            written += _convert_subprojects(args, out, sub_src, result.subdirs, index, decisions, report_lines,
                                            dev_override, buildifier, depth + 1, f"{package_path}/")
        finally:
            _cleanup(args, work, tooldir)
    return written


def cmd_check(args: argparse.Namespace) -> int:
    """`check`: run GNU configure on the sources with the hermetic autotools and diff its config.h
    (and the other configured headers) against the ones Bazel built (`--bazel-config-h`).
    """
    from tools.convert import check as check_mod  # pylint: disable=import-outside-toplevel

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
        name, _, path = entry.rpartition("=") if "=" in entry else ("", "", entry)
        bazel_headers[name or Path(path).name] = _resolve(path)
    try:
        result = check_mod.run_check(
            autotools, src, out / "gnu", cc_env=cc_env, resolve=resolve,
            configure_args=args.configure_arg or None, bazel_headers=bazel_headers,
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
    for header, delta in result.differences.items():
        (out / f"{Path(header).name}.diff").write_text(delta + "\n", encoding="utf-8", newline="\n")
        print(f"\n{header} differs from the Bazel header:\n{delta}")
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
        (out / "tree.json").write_text(
            json.dumps(
                {
                    "configure_ac": str(expansion.configure_ac),
                    "m4_files": expansion.m4_files,
                    "warnings": expansion.warnings + tree.warnings,
                    "items": [_node_to_dict(item) for item in tree.items],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        defuns = tree.defuns()
        print(f"expanded {expansion.configure_ac.name}: {len(expansion.m4_files)} m4 files, "
              f"{len(defuns)} macro bodies, {len(list(tree.walk()))} nodes")
        for node in defuns:
            print(f"  {node.name}  ({node.loc})  calls={len(node.calls())}")
        for warning in expansion.warnings + tree.warnings:
            print(f"warning: {warning}")
        return 0
    finally:
        _cleanup(args, work, tooldir)


def _common_input_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--src", required=True,
                        help="the sources: a directory with configure.ac, a release archive, or its URL")
    parser.add_argument("--subdir", default=None, metavar="DIR",
                        help="the directory of configure.ac inside --src when it is not the root (tcl: unix)")
    parser.add_argument("--m4-dir", action="append", default=[], metavar="DIR",
                        help="stage the *.m4 files of DIR instead of the project's own macro dirs")
    parser.add_argument("--wrap", action="append", default=[], metavar="MACRO",
                        help="also record calls of this macro")
    parser.add_argument("--keep-work", action="store_true", help="keep the scratch directory")


def main(argv: list[str] | None = None) -> int:
    """The command line: the `convert`, `check` and `expand` subcommands. The exit code is 0, 1 when
    the expansion fails, 2 on a usage or environment error, 3 when decisions are required.
    """
    parser = argparse.ArgumentParser(prog="convert", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="convert a project to BUILD files")
    _common_input_args(convert_parser)
    convert_parser.add_argument("--out", required=True, help="output directory")
    convert_parser.add_argument("--only", action="append", default=[], metavar="FILE",
                                help="translate only the AC_DEFUNs of this m4 file (basename); repeatable")
    convert_parser.add_argument("--layout", choices=["per-file", "package"], default=None,
                                help="one package per m4 file, or a single package (default: per-file "
                                     "with --only, package otherwise)")
    convert_parser.add_argument("--package-prefix", default="m4/",
                                help="per-file layout: packages land at <prefix><stem> (default m4/)")
    convert_parser.add_argument("--in-repo", dest="in_repo", action="store_true", default=None,
                                help="emit for this repository (autoconf_cache, // labels)")
    convert_parser.add_argument("--external", dest="in_repo", action="store_false",
                                help="emit for an external project (@rules_cc_autoconf labels)")
    convert_parser.add_argument("--vendor-ports", action="store_true",
                                help="copy the gnulib ports the project uses into third_party/rules_cc_autoconf/ of the "
                                     "project (no dependency on rules_cc_autoconf's ports; run from its checkout, or "
                                     "pass --index <checkout>)")
    convert_parser.add_argument("--index", default="auto",
                                help="where the hand ports are: a workspace path, `snapshot`, `auto`, `none`")
    convert_parser.add_argument("--gnulib-commit", default=None,
                                help="gnulib commit for docstring URLs of unported m4 files")
    convert_parser.add_argument("--annotate", action="store_true",
                                help="comment every generated check, dep and rule with the m4 it came from "
                                     "(off by default: the BUILD file stays short)")
    convert_parser.add_argument("--buildifier", default="auto",
                                help="buildifier to format the output with (`auto`, `none`, or a path)")
    convert_parser.add_argument("--decisions", default=None, metavar="FILE",
                                help="Starlark decisions file for constructs without a mechanical translation "
                                     "(default: decisions.bzl in --out; created and extended by the converter)")
    convert_parser.add_argument("--append", default=None, metavar="BUILD",
                                help="a hand-written BUILD file appended to the generated BUILD.bazel, its load() "
                                     "statements hoisted to the top, before buildifier runs: the project's tests "
                                     "and anything else the conversion cannot produce")
    convert_parser.add_argument("--dev", action="store_true",
                                help="project mode: MODULE.bazel gets a local_path_override to this checkout")
    convert_parser.add_argument("--bcr", action="store_true",
                                help="project mode: also write a Bazel Central Registry entry under <out>/modules/")
    convert_parser.add_argument("--bcr-version", default=None, help="registry version (default: the package version)")
    convert_parser.add_argument("--source-url", default=None, help="release archive URL for source.json (required with --bcr)")
    convert_parser.add_argument("--integrity", default=None, help="release archive integrity, sha256-... (required with --bcr)")
    convert_parser.add_argument("--strip-prefix", default=None, help="release archive strip_prefix")
    convert_parser.add_argument("--homepage", default=None,
                                help="project homepage for metadata.json (default: AC_INIT's URL argument)")
    convert_parser.set_defaults(func=cmd_convert)

    check_parser = subparsers.add_parser(
        "check", help="run GNU configure with the Bazel C++ toolchain and compare config headers")
    check_parser.add_argument("--src", required=True,
                              help="the sources: a directory with configure.ac, a release archive, or its URL")
    check_parser.add_argument("--out", required=True, help="where the GNU results and diffs go")
    check_parser.add_argument("--bazel-config-h", action="append", default=[], metavar="[NAME=]FILE",
                              help="a config header Bazel built from the converted BUILD; NAME is the "
                                   "AC_CONFIG_HEADERS output it corresponds to (default: the file name)")
    check_parser.add_argument("--configure-arg", action="append", default=[], metavar="ARG",
                              help="arguments for ./configure (default: the conformance tests' cache presets)")
    check_parser.add_argument("--regenerate", action="store_true",
                              help="run aclocal and autoconf even if the tree ships a configure script")
    check_parser.add_argument("--cc-env", default=None, help=argparse.SUPPRESS)
    check_parser.set_defaults(func=cmd_check)

    expand_parser = subparsers.add_parser("expand", help="instrumented autom4te expansion")
    _common_input_args(expand_parser)
    expand_parser.add_argument("--out", required=True, help="where to write expanded.sh and tree.json")
    expand_parser.set_defaults(func=cmd_expand)

    parser.add_argument("--profile", default=None, metavar="FILE",
                        help="write a cProfile of the run to FILE (pstats format) and a summary to FILE.txt")
    args = parser.parse_args(argv)
    if args.profile:
        import cProfile  # pylint: disable=import-outside-toplevel
        import pstats  # pylint: disable=import-outside-toplevel

        profile_path = _resolve(args.profile)
        profiler = cProfile.Profile()
        code = profiler.runcall(args.func, args)
        profiler.dump_stats(profile_path)
        with profile_path.with_suffix(profile_path.suffix + ".txt").open("w", encoding="utf-8") as out:
            stats = pstats.Stats(profiler, stream=out).sort_stats("cumulative")
            stats.print_stats(40)
            stats.sort_stats("tottime").print_stats(25)
        return code
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
