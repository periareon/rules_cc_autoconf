"""`bazel run` entry point for the bundled autotools (e.g. `:autoconf -- --version`)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from autoconf.tests.gnu.autotools import Autotools


def main() -> int:
    tool = os.environ["AUTOTOOLS_TOOL"]
    with tempfile.TemporaryDirectory(prefix="autotools-") as tmp:
        autotools = Autotools(Path(tmp))
        argv = autotools.command(tool)
        if tool == "aclocal":
            argv += autotools.aclocal_args()
        argv += sys.argv[1:]
        cwd = os.environ.get("BUILD_WORKING_DIRECTORY", os.getcwd())
        return subprocess.call(argv, cwd=cwd, env=autotools.environment())


if __name__ == "__main__":
    sys.exit(main())
