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


CACHE_DIRECTORY = ".convert-cache"  # in the rules_cc_autoconf checkout, git-ignored


def cache_dir() -> Path:
    """`.convert-cache/sources` in the checkout `bazel run` was started from
    (`BUILD_WORKSPACE_DIRECTORY`), so fetched archives sit next to the tool
    and nothing lands outside the repository; the user's cache otherwise."""
    root = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if root:
        return Path(root) / CACHE_DIRECTORY / "sources"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "rules_cc_autoconf" / "sources"


# Some mirrors (texlive.info) answer anything but a command-line downloader's
# User-Agent with an HTML page; this is the shape they accept, with who we are.
USER_AGENT = "Wget/1.21 (rules_cc_autoconf-convert; +https://github.com/periareon/rules_cc_autoconf)"


def fetch(url: str, integrity: str | None = None) -> Path:
    """The archive at `url`, downloaded once into the cache (checked against
    `integrity`, a Bazel `sha256-<base64>` string, when one is given)."""
    name = url.rsplit("/", 1)[-1].split("?")[0] or "archive"
    target = cache_dir() / hashlib.sha256(url.encode()).hexdigest()[:16] / name
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - the user's URL
        with urllib.request.urlopen(request) as response, partial.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(response, out)
        partial.replace(target)
    if integrity:
        digest = base64.b64encode(hashlib.sha256(target.read_bytes()).digest()).decode()
        if integrity != f"sha256-{digest}":
            target.unlink()
            raise RuntimeError(f"{url}: integrity mismatch (got sha256-{digest}, expected {integrity})")
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
                tf.extractall(scratch)  # noqa: S202 - the user's archive
    entries = [e for e in scratch.iterdir() if not e.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return scratch


def resolve(spec: str, base: Path, integrity: str | None = None) -> Path:
    """The source directory for `spec`: a URL (fetched and extracted), an
    archive (extracted), or a directory (relative to `base`)."""
    if is_url(spec):
        return extract(fetch(spec, integrity))
    path = Path(spec) if Path(spec).is_absolute() else (base / spec).resolve()
    if is_archive(path):
        return extract(path)
    return path
