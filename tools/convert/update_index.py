"""Regenerate `index.toml`, a snapshot of the hand ports' packages and targets, and the port mirror test.

The converter resolves macro names to existing targets by reading the BUILD
files of `//gnulib/m4/*`, `//autoconf/macros/*` and `//gnulib/macros/*`.
Those files are private to their packages, so tests (and `bazel run` outside
a checkout) use this snapshot instead; `bazel run //tools/convert` compares
it with the checkout and warns when it is stale.

Usage: `bazel run //tools/convert:update_index` from a clone.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from python.runfiles import Runfiles

from tools.convert.emit import render_package
from tools.convert.index import PortIndex, workspace_root
from tools.convert.ports import MIRROR_TEST_PACKAGE, PortSnapshot, mirror_test_package

COMMAND = "bazel run //tools/convert:update_index"


def _buildifier() -> str | None:
    """The buildifier the generated BUILD is formatted with: the prebuilt one in the runfiles
    (`@buildifier_prebuilt//:buildifier`, a data dependency), else one on PATH, else none."""
    runfiles = Runfiles.Create()
    if runfiles is not None:
        for rlocation in (
            "buildifier_prebuilt+/buildifier/buildifier.bash",
            "buildifier_prebuilt/buildifier/buildifier.bash",
        ):
            found = runfiles.Rlocation(rlocation)
            if found and os.path.exists(found):
                return found
    return shutil.which("buildifier")


def _formatted(text: str) -> str:
    """`text` as buildifier formats a BUILD file, so the committed file passes `buildifier
    -mode=check`; as rendered when no buildifier is at hand (the run says so)."""
    buildifier = _buildifier()
    if buildifier is None:
        print("warning: no buildifier found; the mirror BUILD is written unformatted")
        return text
    result = subprocess.run([buildifier, "-type=build"], input=text, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout:
        print(f"warning: buildifier failed ({result.stderr.strip()}); the mirror BUILD is written unformatted")
        return text
    return result.stdout


def main() -> int:
    """Regenerate tools/convert/index.toml from the ports of the workspace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args()
    workspace = workspace_root(args.workspace)
    index = PortIndex.from_workspace(workspace)
    target = workspace / "tools/convert/index.toml"
    target.write_text(index.to_snapshot(), encoding="utf-8", newline="\n")
    print(f"{target}: {len(index.ports)} packages, {sum(len(p.targets) for p in index.ports)} targets")

    # The package that re-declares each port rule from the lifted text of its BUILD file, as
    # `convert --inline-checks` does for a project, and compares it with the port.
    ports = PortSnapshot.from_workspace(workspace)
    build = workspace / MIRROR_TEST_PACKAGE / "BUILD.bazel"
    package = mirror_test_package(ports, COMMAND)
    text = render_package(package, repo="", in_repo=False)
    build.write_text(_formatted(text), encoding="utf-8", newline="\n")
    print(f"{build}: {len(package.targets)} autoconf mirrors, {len(package.after) - 1} other rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
