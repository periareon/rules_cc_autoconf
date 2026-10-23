"""Where the sources come from: a directory, a release archive, or its URL.

`--src https://ftp.gnu.org/gnu/gzip/gzip-1.15.tar.xz` fetches the archive
once into the checkout's git-ignored `.convert-cache/` and extracts it, so the command that produced a
conversion reproduces it from any clone of rules_cc_autoconf.
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import os
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip")


def is_url(spec: str) -> bool:
    return spec.startswith(("http://", "https://", "file://"))


def is_archive(path: Path) -> bool:
    return path.is_file() and path.name.endswith(ARCHIVE_SUFFIXES)


def integrity(data: bytes) -> str:
    """Bazel's subresource integrity string of `data`: `sha256-<base64 digest>`."""
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


CACHE_DIRECTORY = ".convert-cache"  # in the rules_cc_autoconf checkout, git-ignored


def cache_dir() -> Path:
    """`.convert-cache/sources` in the checkout `bazel run` was started from
    (`BUILD_WORKSPACE_DIRECTORY`), so fetched archives sit next to the tool
    and nothing lands outside the repository; the user's cache otherwise."""
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root:
        return Path(root) / CACHE_DIRECTORY / "sources"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "rules_cc_autoconf/sources"


# Some mirrors (texlive.info) answer anything but a command-line downloader's
# User-Agent with an HTML page; this is the shape they accept, with who we are.
USER_AGENT = "Wget/1.21 (rules_cc_autoconf-convert; +https://github.com/periareon/rules_cc_autoconf)"


def fetch(url: str, expected: str | None = None, mirrors: tuple[str, ...] = ()) -> Path:
    """The archive at `url`, downloaded once into the cache (checked against
    `expected`, a Bazel `sha256-<base64>` integrity string, when one is given).
    `mirrors` are tried in turn when `url` cannot be reached (a registry entry's
    `mirror_urls`); the cache entry is the primary URL's either way."""
    name = url.rsplit("/", 1)[-1].split("?")[0] or "archive"
    target = cache_dir() / hashlib.sha256(url.encode()).hexdigest()[:16] / name
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        failures: list[str] = []
        for candidate in (url, *mirrors):
            request = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request) as response, partial.open("wb") as out:
                    shutil.copyfileobj(response, out)
            except OSError as error:  # URLError is one; a refused connection, a 404, an unreachable network
                failures.append(f"{candidate}: {error}")
                partial.unlink(missing_ok=True)
                continue
            break
        else:
            raise RuntimeError("could not download the sources:\n  " + "\n  ".join(failures))
        partial.replace(target)
    if expected:
        actual = integrity(target.read_bytes())
        if expected != actual:
            target.unlink()
            raise RuntimeError(f"{url}: integrity mismatch (got {actual}, expected {expected})")
    return target


def extract(archive: Path) -> Path:
    """`archive` unpacked into a scratch directory, removed at exit; the one
    top-level directory release archives have, or the scratch directory."""
    scratch = Path(tempfile.mkdtemp(prefix="rccac-src-"))
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(scratch)
    else:
        with tarfile.open(archive) as tf:
            try:
                tf.extractall(scratch, filter="data")
            except TypeError:  # Python < 3.12
                tf.extractall(scratch)
    entries = [e for e in scratch.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return scratch


def resolve(spec: str, base: Path, integrity: str | None = None, mirrors: tuple[str, ...] = ()) -> Path:
    """The source directory for `spec`: a URL (fetched and extracted, `mirrors`
    tried when it is unreachable), an archive (extracted), or a directory
    (relative to `base`)."""
    if is_url(spec):
        return extract(fetch(spec, integrity, mirrors))
    path = Path(spec) if Path(spec).is_absolute() else (base / spec).resolve()
    if is_archive(path):
        return extract(path)
    return path
