"""Unit tests for the source resolution: directories, archives and URLs."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from tools.convert import source


def _archive(directory: Path, name: str) -> Path:
    archive = directory / name
    with tarfile.open(archive, "w:gz") as tf:
        data = b"AC_INIT([demo], [1.0])\n"
        info = tarfile.TarInfo("demo-1.0/configure.ac")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return archive


def test_directory_archive_and_file_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "proj").mkdir()
    assert source.resolve("proj", tmp_path) == tmp_path / "proj"
    archive = _archive(tmp_path, "demo-1.0.tar.gz")
    extracted = source.resolve(str(archive), tmp_path)
    assert extracted.name == "demo-1.0"  # the single top-level directory
    assert (extracted / "configure.ac").is_file()
    cache = tmp_path / "cache"
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(cache))
    url = archive.resolve().as_uri()
    fetched = source.resolve(url, tmp_path)
    assert (fetched / "configure.ac").is_file()
    assert len(list(cache.rglob("demo-1.0.tar.gz"))) == 1  # cached once
    source.resolve(url, tmp_path)
    with pytest.raises(RuntimeError):
        source.resolve(url, tmp_path, integrity="sha256-AAAA")


def test_mirrors_when_the_url_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry entry's mirror_urls are tried when the primary URL fails; the download is
    cached under the primary URL, and every failure is reported when no mirror answers."""
    archive = _archive(tmp_path, "demo-1.0.tar.gz")
    missing = (tmp_path / "nowhere" / "demo-1.0.tar.gz").as_uri()
    cache = tmp_path / "cache"
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(cache))
    fetched = source.resolve(missing, tmp_path, mirrors=(archive.resolve().as_uri(),))
    assert (fetched / "configure.ac").is_file()
    assert len(list(cache.rglob("demo-1.0.tar.gz"))) == 1
    with pytest.raises(RuntimeError) as failed:
        source.resolve((tmp_path / "elsewhere" / "x.tar.gz").as_uri(), tmp_path, mirrors=(missing,))
    assert "elsewhere" in str(failed.value)
    assert "nowhere" in str(failed.value)
