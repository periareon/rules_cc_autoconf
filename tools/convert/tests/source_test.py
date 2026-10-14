"""Unit tests for the source resolution: directories, archives and URLs."""

from __future__ import annotations

import io
import unittest.mock
import tarfile
import tempfile
import unittest
from pathlib import Path

from tools.convert import source


def _archive(directory: Path, name: str) -> Path:
    archive = directory / name
    with tarfile.open(archive, "w:gz") as tf:
        data = b"AC_INIT([demo], [1.0])\n"
        info = tarfile.TarInfo("demo-1.0/configure.ac")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return archive


class SourceTest(unittest.TestCase):

    def test_directory_archive_and_file_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "proj").mkdir()
            self.assertEqual(source.resolve("proj", root), root / "proj")
            archive = _archive(root, "demo-1.0.tar.gz")
            extracted = source.resolve(str(archive), root)
            self.assertEqual(extracted.name, "demo-1.0")  # the single top-level directory
            self.assertTrue((extracted / "configure.ac").is_file())
            cache = root / "cache"
            with unittest.mock.patch.dict("os.environ", {"BUILD_WORKSPACE_DIRECTORY": str(cache)}):
                url = archive.resolve().as_uri()
                fetched = source.resolve(url, root)
                self.assertTrue((fetched / "configure.ac").is_file())
                self.assertEqual(len(list(cache.rglob("demo-1.0.tar.gz"))), 1)  # cached once
                source.resolve(url, root)
                with self.assertRaises(RuntimeError):
                    source.resolve(url, root, integrity="sha256-AAAA")


if __name__ == "__main__":
    import unittest.mock  # noqa: F401

    unittest.main()
