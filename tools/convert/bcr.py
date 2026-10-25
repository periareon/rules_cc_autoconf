"""Render a conversion as a Bazel Central Registry module entry.

Layout (what `bazel_dep` consumers and the BCR presubmit expect):

    modules/<name>/metadata.json
    modules/<name>/<version>/MODULE.bazel
    modules/<name>/<version>/source.json
    modules/<name>/<version>/presubmit.yml
    modules/<name>/<version>/overlay/BUILD.bazel
    modules/<name>/<version>/overlay/MODULE.bazel

The overlay only adds files on top of the release archive; `source.json`
records the archive and the integrity of every overlay file.  Hand-written
files (a tests package, a `.bzl` BUILD.bazel loads) live in the overlay
beside the generated ones: the converter owns only the files that carry its
mark (`ownership.py`), and the overlay map lists what is on disk, as the
registry's own integrity tool does.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict, cast

from tools.convert import ownership
from tools.convert.libraries import upstream_version
from tools.convert.source import integrity

PRESUBMIT = """\
matrix:
  platform:
    - debian11
    - macos
    - macos_arm64
    - ubuntu2004
    - windows
  bazel:
    - 7.x
    - 8.x
tasks:
  verify_targets:
    name: Verify build targets
    platform: ${{ platform }}
    bazel: ${{ bazel }}
    build_targets:
      - '@{name}//:all'
"""


GNU_FTP = "https://ftp.gnu.org/gnu/"
GNU_MIRRORS = ("https://ftpmirror.gnu.org/gnu/", "https://mirrors.kernel.org/gnu/")


class SourceJson(TypedDict):
    """A module version's `source.json`: the release archive and what the registry lays over it.
    The archive keys are always there; a registry entry adds the overlay map and, when it has
    some, the patches and the mirror URLs (a vendored entry has the archive keys only)."""

    url: str
    integrity: str
    strip_prefix: str
    mirror_urls: NotRequired[list[str]]
    overlay: NotRequired[dict[str, str]]
    patches: NotRequired[dict[str, str]]
    patch_strip: NotRequired[int]


class Metadata(TypedDict):
    """`modules/<name>/metadata.json`: the keys the converter reads or writes (the registry's
    schema has more; an existing file keeps them, `json.dumps` writes back what was loaded)."""

    homepage: str
    maintainers: list[dict[str, str]]
    repository: list[str]
    versions: list[str]
    yanked_versions: dict[str, str]


@dataclass(frozen=True)
class Entry:
    """The directory a module's kept files live in: a registry entry, `modules/<name>/<version>`
    (`--bcr-entry`), or a vendored one, `3rdparty/<name>` (`--http-archive-entry`).  Both hold
    `decisions.bzl` and, once written, `source.json`; the module's hand-written BUILD files live
    in the overlay beside the generated `autoconf.bzl` files."""

    directory: Path
    """The entry directory, resolved."""

    name: str
    """The module name: the `<name>` of the path."""

    version: str | None
    """The registry version; None for a vendored entry."""
    registry: Path | None
    """The registry checkout, the directory holding `modules/`; None for a vendored entry."""
    source: SourceJson | None
    """`source.json` when the entry has one (a regeneration): its `url`, `integrity`, `strip_prefix`."""

    @property
    def decisions(self) -> Path:
        return self.directory / "decisions.bzl"

    @property
    def overlay(self) -> Path:
        """The module's files: `overlay/` of a registry entry, the entry itself for a vendored one."""
        return self.directory / "overlay" if self.version is not None else self.directory


def _source_json(directory: Path) -> SourceJson | None:
    """The entry's `source.json`; None when the entry has none yet (a first run)."""
    path = directory / "source.json"
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} is not a JSON object")
    # json.loads is untyped: this is where the document's shape is asserted, not checked key by
    # key (the registry's own tooling validates its entries).
    return cast(SourceJson, loaded)


def _metadata(registry: Path, name: str) -> Metadata | None:
    """`modules/<name>/metadata.json` of the registry; None when the registry has no such module."""
    path = registry / "modules" / name / "metadata.json"
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{path} is not a JSON object")
    # As in _source_json: json.loads is untyped, and the registry validates the document.
    return cast(Metadata, loaded)


def entry(directory: Path) -> Entry:
    """The registry entry at `directory`; its name and version come from the path."""
    directory = directory.resolve()
    if directory.parent.parent.name != "modules":
        raise ValueError(f"--bcr-entry must name a registry's modules/<name>/<version> directory, not {directory}")
    return Entry(
        directory=directory,
        name=directory.parent.name,
        version=directory.name,
        registry=directory.parent.parent.parent,
        source=_source_json(directory),
    )


def vendored_entry(directory: Path) -> Entry:
    """The vendored entry at `directory` (`3rdparty/<name>`); the module is named after it."""
    directory = directory.resolve()
    if directory.parent.name == "modules" or directory.parent.parent.name == "modules":
        raise ValueError(f"{directory} is a registry entry: use --bcr-entry")
    return Entry(directory=directory, name=directory.name, version=None, registry=None, source=_source_json(directory))


def _published(registry: Path, name: str) -> list[str]:
    """The non-yanked versions `modules/<name>/metadata.json` lists, in publication order."""
    metadata = _metadata(registry, name)
    if metadata is None:
        return []
    yanked = set(metadata.get("yanked_versions", {}))
    return [v for v in metadata.get("versions", []) if v not in yanked]


def newest_versions(
    registry: Path,
    names: Iterable[str],
    *,
    current: Mapping[str, str] | None = None,
    allow_upstream: Collection[str] = (),
) -> dict[str, str]:
    """The newest non-yanked version of each of `names` the registry has.

    With `current` (module -> the version a table or an entry holds now), a module stays on its
    upstream version: the newest `.bcr.N` of `1.3.2` is taken, `1.4.0` is not, unless the module
    is in `allow_upstream`.  Moving a dependency to another upstream release is a decision
    someone makes per module; a registry re-cut of the same release is not.  A module without
    a current version takes the newest of all."""
    out: dict[str, str] = {}
    for name in names:
        versions = _published(registry, name)
        held = (current or {}).get(name)
        if held is not None and name not in allow_upstream:
            versions = [v for v in versions if upstream_version(v) == upstream_version(held)]
        if versions:
            out[name] = versions[-1]
    return out


def upstream_bumps(registry: Path, current: Mapping[str, str]) -> dict[str, str]:
    """Module -> the newest version the registry has of another upstream release than the one
    `current` holds: what `newest_versions` left alone, for the message that says so."""
    out: dict[str, str] = {}
    for name, held in current.items():
        versions = _published(registry, name)
        if versions and upstream_version(versions[-1]) != upstream_version(held):
            out[name] = versions[-1]
    return out


def write_source_json(directory: Path, *, url: str, source_integrity: str, strip_prefix: str) -> Path:
    """A vendored entry's `source.json`: the archive only (no overlay)."""
    path = directory / "source.json"
    source: SourceJson = {"integrity": source_integrity, "strip_prefix": strip_prefix, "url": url}
    path.write_text(json.dumps(source, indent=4) + "\n", encoding="utf-8", newline="\n")
    return path


def http_archive_snippet(
    *, name: str, url: str, source_integrity: str, strip_prefix: str, build_file: str, bazel_deps: list[str]
) -> str:
    """What the consumer's MODULE.bazel needs: the module's dependencies and the archive with
    the generated BUILD file injected."""
    deps = "\n".join(bazel_deps)
    return (f"{deps}\n\n" if deps else "") + (
        'http_archive = use_repo_rule("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")\n\n'
        "http_archive(\n"
        f'    name = "{name}",\n'
        f'    build_file = "{build_file}",\n'
        f'    integrity = "{source_integrity}",\n'
        f'    strip_prefix = "{strip_prefix}",\n'
        f'    urls = ["{url}"],\n'
        ")\n"
    )


def recorded_homepage(registry: Path, name: str) -> str | None:
    """The homepage an existing `modules/<name>/metadata.json` records, for a regeneration."""
    metadata = _metadata(registry, name)
    if metadata is None:
        return None
    return metadata.get("homepage") or None


def write_entry(
    registry: Path,
    *,
    name: str,
    version: str,
    staging: Path,
    module_text: str,
    source_url: str,
    source_integrity: str,
    strip_prefix: str | None,
    homepage: str,
    finish_overlay: Callable[[Path], list[Path]] | None = None,
) -> tuple[list[Path], list[Path]]:
    """Write the module entry; returns (the files written, the stale generated files removed).

    `staging` holds the conversion's files at their paths within the module (`BUILD.bazel`,
    `lib/BUILD.bazel`, ...); `module_text` is the MODULE.bazel for the overlay and the version
    directory (never one with a local override).  The files go into `overlay/` under the
    ownership rules of `ownership.sync`: hand-written files there are kept, and listed in
    `source.json` with the generated ones.  The archive URL, its integrity and the homepage are
    inputs, never placeholders: a registry entry with a value to fill in later would need a hand
    edit after every regeneration.  `finish_overlay(overlay)`, when given, runs after the
    generated files are placed and before the overlay map is taken: it writes the hand-written
    BUILD files the generated packages lack, returning them.
    """
    module_dir = registry / "modules" / name
    version_dir = module_dir / version
    overlay = version_dir / "overlay"
    overlay.mkdir(parents=True, exist_ok=True)
    (staging / "MODULE.bazel").write_text(module_text, encoding="utf-8", newline="\n")
    written, removed = ownership.sync(staging, overlay, regenerated=("MODULE.bazel",))
    if finish_overlay is not None:
        written += finish_overlay(overlay)

    (version_dir / "MODULE.bazel").write_text(module_text, encoding="utf-8", newline="\n")
    written.append(version_dir / "MODULE.bazel")

    # Keyed and ordered by the posix path, not the Path: Windows paths compare case-insensitively
    # and would order `autoconf.bzl` before `BUILD.bazel`, unlike a run on Linux or macOS.
    overlay_integrity = {
        rel: integrity(path.read_bytes())
        for rel, path in sorted(
            (path.relative_to(overlay).as_posix(), path)
            for path in overlay.rglob("*")
            if path.is_file() and path.name != "MODULE.bazel.lock"
        )
    }
    # The registry's own keys of an existing source.json (mirror URLs, the patch strip level) and
    # the entry's patches/ directory are kept: they are the registry's, not the conversion's.
    source_path = version_dir / "source.json"
    existing = _source_json(version_dir)
    mirror_urls = existing.get("mirror_urls") if existing is not None else None
    if not mirror_urls and source_url.startswith(GNU_FTP):
        # The registry's GNU entries list the project mirrors too: ftp.gnu.org alone times out
        # often enough to fail a CI fetch.
        mirror_urls = [prefix + source_url[len(GNU_FTP) :] for prefix in GNU_MIRRORS]
    archive_prefix = strip_prefix or f"{name}-{version.split('.bcr.')[0]}"
    source: SourceJson = (
        {"url": source_url, "mirror_urls": mirror_urls, "integrity": source_integrity, "strip_prefix": archive_prefix}
        if mirror_urls
        else {"url": source_url, "integrity": source_integrity, "strip_prefix": archive_prefix}
    )
    source["overlay"] = overlay_integrity
    patches_dir = version_dir / "patches"
    patches = (
        {path.name: integrity(path.read_bytes()) for path in sorted(patches_dir.iterdir()) if path.is_file()}
        if patches_dir.is_dir()
        else {}
    )
    if patches:
        # The registry's default is 0; an entry whose patches carry no a/ b/ prefix has none.
        if existing is not None and "patch_strip" in existing:
            source["patch_strip"] = existing["patch_strip"]
        source["patches"] = patches
    source_path.write_text(json.dumps(source, indent=4) + "\n", encoding="utf-8", newline="\n")
    written.append(version_dir / "source.json")

    # A presubmit a maintainer has edited (more targets, fewer platforms) is theirs to keep.
    if not (version_dir / "presubmit.yml").is_file():
        (version_dir / "presubmit.yml").write_text(PRESUBMIT.format(name=name), encoding="utf-8", newline="\n")
        written.append(version_dir / "presubmit.yml")

    metadata_path = module_dir / "metadata.json"
    metadata = _metadata(registry, name)
    if metadata is None:
        metadata = {
            "homepage": homepage,
            "maintainers": [],
            "repository": [],
            "versions": [],
            "yanked_versions": {},
        }
    if version not in metadata.setdefault("versions", []):
        metadata["versions"].append(version)
    metadata_path.write_text(json.dumps(metadata, indent=4) + "\n", encoding="utf-8", newline="\n")
    written.append(metadata_path)
    return written, removed
