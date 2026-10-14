"""Regenerate `index.toml`: a snapshot of the hand ports' packages and targets.

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
import sys
from pathlib import Path

from tools.convert.index import PortIndex


def main() -> int:
    """Regenerate tools/convert/index.toml from the ports of the workspace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=None)
    args = parser.parse_args()
    workspace = (
        args.workspace
        or (Path(os.environ["BUILD_WORKSPACE_DIRECTORY"]) if "BUILD_WORKSPACE_DIRECTORY" in os.environ else None)
        or Path(__file__).resolve().parents[2]
    )
    index = PortIndex.from_workspace(workspace)
    target = workspace / "tools" / "convert" / "index.toml"
    target.write_text(index.to_snapshot(), encoding="utf-8", newline="\n")
    print(f"{target}: {len(index.ports)} packages, {sum(len(p.targets) for p in index.ports)} targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
