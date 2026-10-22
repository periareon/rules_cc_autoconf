"""Runner for `convert_golden_test` and `convert_project_golden_test`."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from python.runfiles import Runfiles

from autoconf.tests.diff_tester import diff
from autoconf.tests.gnu.autotools import IS_WINDOWS, Autotools, scratch_dir
from tools.convert import bzl, emit, markers
from tools.convert import expand as expand_mod
from tools.convert import project as project_mod
from tools.convert.decisions import Decisions
from tools.convert.index import PortIndex
from tools.convert.translate import Options, Translator

UPDATE = "--update" in sys.argv[1:]


# The rules_cc_autoconf version the goldens name: fixed, so a release does not change them.
GOLDEN_VERSION = "0.0.0"


def _runfiles() -> Runfiles:
    runfiles = Runfiles.Create()
    if runfiles is None:
        raise RuntimeError("no runfiles: run under `bazel test` or `bazel run`")
    return runfiles


def load_decisions(runfiles: Runfiles) -> Decisions:
    rloc = os.environ.get("TEST_DECISIONS")
    return Decisions.load(_rlocation(runfiles, rloc)) if rloc else Decisions()


def _rlocation(runfiles: Runfiles, rloc: str) -> Path:
    found = runfiles.Rlocation(rloc, "_main" if IS_WINDOWS else None)
    if not found or not Path(found).exists():
        raise FileNotFoundError(f"runfile not found: {rloc}")
    return Path(found)


def convert_module(
    configure_ac: Path, m4_files: list[Path], only: list[str], decisions: Decisions
) -> tuple[str, str, str]:
    """Per-file mode: one gnulib module against the ports.

    Returns (BUILD text, summary, the decisions file the converter would write
    for this module when run without one).
    """
    autotools = Autotools(scratch_dir("golden-autotools"))
    src = scratch_dir("golden-src")
    shutil.copyfile(configure_ac, src / "configure.ac")
    work = scratch_dir("golden-work")
    expansion = expand_mod.expand(autotools, src, work, m4_files=m4_files)
    tree = markers.parse(expansion.read())
    index = PortIndex.from_snapshot()
    sources = {}
    for relative in expansion.m4_files:
        path = work / relative
        if path.is_file():
            sources[relative] = path.read_text(encoding="utf-8", errors="replace")
    options = Options(repo="", in_repo=True, layout="per-file", package_prefix="m4/")
    translator = Translator(
        tree, index=index, selected_files=set(only), m4_sources=sources, options=options, decisions=decisions
    )
    packages = translator.translate()
    asked = Decisions()
    Translator(
        markers.parse(expansion.read()),
        index=index,
        selected_files=set(only),
        m4_sources=sources,
        options=options,
        decisions=asked,
    ).translate()
    skeleton = asked.skeleton(f"converting {Path(only[0]).stem}" if only else "converting", GOLDEN_VERSION)
    if len(packages) != 1:
        raise AssertionError(f"expected one package, got {[p.path for p in packages]}")
    if not os.environ.get("TEST_ANNOTATE"):
        emit.strip_annotations(packages[0])
    text = emit.render_package(packages[0], repo="", in_repo=True)
    summary = (
        f"unresolved={sorted(translator.report.unresolved)} inlined={sorted(translator.report.inlined)} "
        f"pending={sorted(decisions.pending)}"
    )
    return text, summary, skeleton


def convert_project(listing: Path, runfiles: Runfiles, decisions: Decisions) -> tuple[str, str, str, str]:
    """Project mode on a source tree recreated from runfiles.

    Returns (autoconf.bzl, MODULE, summary, the decisions file the converter
    would write for the project when run without one).
    """
    src = scratch_dir("golden-project-src")
    for line in listing.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rloc, relative = line.split("\t", 1)
        target = src / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_rlocation(runfiles, rloc), target)
    autotools = Autotools(scratch_dir("golden-autotools"))
    work = scratch_dir("golden-project-work")
    index = PortIndex.from_snapshot()
    wraps = [p.basename for p in index.ports if p.package.startswith("autoconf/macros/")]
    expansion = expand_mod.expand(autotools, src, work, extra_wraps=wraps, wrap_library=True)
    tree = markers.parse(expansion.read())
    result = project_mod.convert_project(tree, expansion, src, index, rules_version="0.0.0", decisions=decisions)
    asked = Decisions()
    project_mod.convert_project(
        markers.parse(expansion.read()), expansion, src, index, rules_version="0.0.0", decisions=asked
    )
    skeleton = asked.skeleton(f"converting {result.name} {result.version or ''}".strip(), GOLDEN_VERSION)
    if not os.environ.get("TEST_ANNOTATE"):
        emit.strip_annotations(result.package)
    build = bzl.to_bzl(emit.render_package(result.package, repo=bzl.RULES_REPO, in_repo=False))
    summary = f"notes={len(result.notes)} warnings={len(expansion.warnings)} pending={sorted(decisions.pending)}"
    return build, result.module_bazel, summary, skeleton


def lint(text: str, filename: str) -> str:
    """buildifier's findings for `text` as a file called `filename` ("" when clean).

    Runs `buildifier -lint=warn -warnings=all -mode=check`: the converter's
    output must be a fixed point of the formatter and free of every lint.
    """
    runfiles = _runfiles()
    buildifier = _rlocation(runfiles, os.environ["TEST_BUILDIFIER"])
    directory = scratch_dir("golden-lint")
    path = directory / filename
    path.write_text(text, encoding="utf-8", newline="\n")
    # buildifier_prebuilt's wrapper changes to BUILD_WORKING_DIRECTORY under
    # `bazel run`: give it an absolute path and no such directory.
    env = {k: v for k, v in os.environ.items() if k not in ("BUILD_WORKING_DIRECTORY", "BUILD_WORKSPACE_DIRECTORY")}
    result = subprocess.run(
        [str(buildifier), "-lint=warn", "-warnings=all", "-mode=check", str(path)],
        cwd=directory,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return ""
    return result.stdout.strip() or f"buildifier exited {result.returncode}"


class GoldenTest(unittest.TestCase):
    def check_decided(self, decisions: Decisions) -> None:
        """Every request must be answered by the decisions file (no silent omissions)."""
        if decisions.pending:
            outputs = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
            if outputs:
                (Path(outputs) / "decisions.needed.bzl").write_text(
                    decisions.skeleton(version=GOLDEN_VERSION), encoding="utf-8", newline="\n"
                )
            if UPDATE:
                print("decisions needed:\n" + decisions.skeleton(version=GOLDEN_VERSION))
            self.fail("decisions required (add them to the decisions file):\n" + "\n".join(decisions.report_lines()))
        self.assertEqual(decisions.unused, [], "unused decisions")

    def check_lint(self, text: str, filename: str) -> None:
        findings = lint(text, filename)
        self.assertEqual(findings, "", f"buildifier is not clean on the generated {filename}:\n{findings}")

    def compare(self, actual: str, expected_rloc: str, expected_path: str, name: str) -> None:
        runfiles = _runfiles()
        if UPDATE:
            root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
            if not root:
                self.fail("--update needs `bazel run`")
            target = Path(root) / expected_path
            target.write_text(actual, encoding="utf-8", newline="\n")
            print(f"updated {target}")
            return
        expected = _rlocation(runfiles, expected_rloc).read_text(encoding="utf-8").replace("\r\n", "\n")
        outputs = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
        if outputs:
            (Path(outputs) / name).write_text(actual, encoding="utf-8", newline="\n")
        delta = diff(expected.splitlines(), actual.splitlines(), file1_name=expected_path, file2_name=name)
        if delta:
            self.fail(f"converter output changed; run with `-- --update` to accept:\n```diff\n{delta}\n```")

    def test_matches_expected(self) -> None:
        runfiles = _runfiles()
        decisions = load_decisions(runfiles)
        if os.environ.get("TEST_PROJECT"):
            listing = _rlocation(runfiles, os.environ["TEST_SOURCES"])
            build, module, summary, skeleton = convert_project(listing, runfiles, decisions)
            print(summary)
            self.check_decided(decisions)
            self.check_lint(build, bzl.BZL_NAME)
            self.check_lint(module, "MODULE.bazel")
            self.compare(build, os.environ["TEST_EXPECTED"], os.environ["TEST_EXPECTED_PATH"], "actual.autoconf.bzl")
            self.compare(
                module,
                os.environ["TEST_EXPECTED_MODULE"],
                os.environ["TEST_EXPECTED_MODULE_PATH"],
                "actual.MODULE.bazel",
            )
            self.check_skeleton(skeleton)
            return
        configure_ac = _rlocation(runfiles, os.environ["TEST_CONFIGURE_AC"])
        m4_list = _rlocation(runfiles, os.environ["TEST_M4_LIST"])
        m4_files = [
            _rlocation(runfiles, line.strip())
            for line in m4_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        only = os.environ["TEST_ONLY"].split()
        actual, summary, skeleton = convert_module(configure_ac, m4_files, only, decisions)
        print(summary)
        self.check_decided(decisions)
        self.check_lint(actual, "BUILD.bazel")
        self.compare(actual, os.environ["TEST_EXPECTED"], os.environ["TEST_EXPECTED_PATH"], "actual.BUILD")
        self.check_skeleton(skeleton)

    def check_skeleton(self, skeleton: str) -> None:
        """The decisions file the converter writes for this input equals `expected_decisions`."""
        expected = os.environ.get("TEST_EXPECTED_DECISIONS")
        if not expected:
            return
        self.check_lint(skeleton, "decisions.bzl")
        self.compare(skeleton, expected, os.environ["TEST_EXPECTED_DECISIONS_PATH"], "actual.decisions.bzl")


if __name__ == "__main__":
    sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--update"]
    unittest.main()
