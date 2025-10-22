"""The `diff_test` runner."""

import difflib
import os
import platform
import unittest
from pathlib import Path

from python.runfiles import Runfiles


class DiffTests(unittest.TestCase):

    def setUp(self) -> None:
        """Setup class instances"""

        # Get file paths from environment variables
        file_1 = os.environ["TEST_FILE_1"]
        file_2 = os.environ["TEST_FILE_2"]

        r = Runfiles.Create()
        if not r:
            raise EnvironmentError("Failed to locate runfiles")

        # Use runfiles to locate files
        source_repo = None
        if platform.system() == "Windows":
            source_repo = "_main"

        file_1_path = r.Rlocation(file_1, source_repo)
        if not file_1_path:
            raise FileNotFoundError(f"Failed to locate runfile: {file_1}")
        file_2_path = r.Rlocation(file_2, source_repo)
        if not file_2_path:
            raise FileNotFoundError(f"Failed to locate runfile: {file_2}")

        self.file_1_rloc = file_1
        self.file_1 = Path(file_1_path)
        self.file_2_rloc = file_2
        self.file_2 = Path(file_2_path)

        return super().setUp()

    def test_diff(self):
        """Compare two files."""

        diff = difflib.unified_diff(
            self.file_1.read_text(encoding="utf-8").splitlines(),
            self.file_2.read_text(encoding="utf-8").splitlines(),
            fromfile=f"a/{self.file_1_rloc}",
            tofile=f"b/{self.file_2_rloc}",
            lineterm="",
        )

        diff_output = "\n".join(diff)
        divider = "=" * 70

        if diff_output:
            self.fail(f"Files differ\n{divider}\n```diff\n{diff_output}\n```")


if __name__ == "__main__":
    unittest.main()
