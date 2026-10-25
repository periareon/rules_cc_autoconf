"""Tests for the registry entry: `--bcr-entry` resolution and what `write_entry` keeps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.convert import bcr, ownership, source
from tools.convert.tests.helpers import OURS_DOCSTRING, write


def test_entry_from_path(tmp_path: Path) -> None:
    directory = tmp_path / "registry/modules/foo/1.2.bcr.1"
    directory.mkdir(parents=True)
    first = bcr.entry(directory)
    assert (first.name, first.version) == ("foo", "1.2.bcr.1")
    assert first.registry == tmp_path.resolve() / "registry"
    assert first.source is None
    assert first.decisions == directory.resolve() / "decisions.bzl"
    # A regeneration: source.json is there.
    (directory / "source.json").write_text(
        json.dumps({"url": "https://x/foo-1.2.tar.gz", "integrity": "sha256-abc", "strip_prefix": "foo-1.2"})
    )
    regenerated = bcr.entry(directory).source
    assert regenerated is not None
    assert regenerated["url"] == "https://x/foo-1.2.tar.gz"
    with pytest.raises(ValueError):
        bcr.entry(tmp_path / "registry/foo")
    with pytest.raises(ValueError):
        bcr.vendored_entry(directory)  # a registry entry is not a vendored one


def test_vendored_entry(tmp_path: Path) -> None:
    """`3rdparty/<name>`: named after the directory, no version, no registry; its source.json
    and the http_archive lines the consumer adds."""
    directory = tmp_path / "3rdparty/foo"
    directory.mkdir(parents=True)
    vendored = bcr.vendored_entry(directory)
    assert (vendored.name, vendored.version, vendored.registry, vendored.source) == ("foo", None, None, None)
    bcr.write_source_json(
        directory, url="https://x/foo-1.2.tar.gz", source_integrity="sha256-abc", strip_prefix="foo-1.2"
    )
    assert bcr.vendored_entry(directory).source == {
        "integrity": "sha256-abc",
        "strip_prefix": "foo-1.2",
        "url": "https://x/foo-1.2.tar.gz",
    }
    snippet = bcr.http_archive_snippet(
        name="foo",
        url="https://x/foo-1.2.tar.gz",
        source_integrity="sha256-abc",
        strip_prefix="foo-1.2",
        build_file="//3rdparty/foo:BUILD.foo.bazel",
        bazel_deps=['bazel_dep(name = "zlib", version = "1.3.2")'],
    )
    assert snippet.startswith('bazel_dep(name = "zlib", version = "1.3.2")\n\nhttp_archive = use_repo_rule(')
    assert '    build_file = "//3rdparty/foo:BUILD.foo.bazel",\n' in snippet
    assert '    urls = ["https://x/foo-1.2.tar.gz"],\n' in snippet


def test_write_entry(tmp_path: Path) -> None:
    """The overlay owns its files: generated ones are replaced, hand-written ones kept and listed
    in source.json with the generated, the converter's stale ones removed; presubmit.yml is the
    maintainer's once edited; a hand-written file where the conversion writes stops the run."""
    registry, staging = tmp_path / "registry", tmp_path / "staging"
    write(staging / "BUILD.bazel", OURS_DOCSTRING + "# b\n")
    write(staging / "lib/BUILD.bazel", OURS_DOCSTRING + "# l\n")

    def write_entry(staging: Path) -> tuple[list[Path], list[Path]]:
        return bcr.write_entry(
            registry,
            name="foo",
            version="1.2.bcr.1",
            staging=staging,
            module_text=OURS_DOCSTRING + "# m\n",
            source_url="https://x/foo-1.2.tar.gz",
            source_integrity="sha256-abc",
            strip_prefix=None,
            homepage="https://x",
        )

    written, removed = write_entry(staging)
    version_dir = registry / "modules/foo/1.2.bcr.1"
    overlay = version_dir / "overlay"
    assert version_dir / "presubmit.yml" in written
    assert removed == []
    source_json = json.loads((version_dir / "source.json").read_text())
    assert source_json["strip_prefix"] == "foo-1.2"
    assert sorted(source_json["overlay"]) == ["BUILD.bazel", "MODULE.bazel", "lib/BUILD.bazel"]
    assert (version_dir / "MODULE.bazel").read_text() == OURS_DOCSTRING + "# m\n"

    # Hand-written files join the overlay; a regeneration without lib/ keeps them, lists
    # them, drops the generated lib/BUILD.bazel and leaves the edited presubmit alone.
    write(overlay / "tests/BUILD.bazel", 'cc_test(name = "t")\n')
    write(overlay / "geninit.bzl", "def geninit():\n    pass\n")
    write(overlay / "MODULE.bazel.lock", "{}\n")
    (version_dir / "presubmit.yml").write_text("edited\n")
    staging2 = tmp_path / "staging2"
    write(staging2 / "BUILD.bazel", OURS_DOCSTRING + "# b2\n")
    written, removed = write_entry(staging2)
    assert (version_dir / "presubmit.yml").read_text() == "edited\n"
    assert (overlay / "BUILD.bazel").read_text() == OURS_DOCSTRING + "# b2\n"
    assert [p.relative_to(version_dir).as_posix() for p in removed] == ["overlay/lib/BUILD.bazel"]
    assert not (overlay / "lib").exists()
    source_json = json.loads((version_dir / "source.json").read_text())
    # In the order of the posix paths' bytes on every host (not Windows' case-insensitive Path order).
    assert list(source_json["overlay"]) == ["BUILD.bazel", "MODULE.bazel", "geninit.bzl", "tests/BUILD.bazel"]
    assert source_json["overlay"]["geninit.bzl"] == source.integrity(b"def geninit():\n    pass\n")
    metadata = json.loads((registry / "modules/foo/metadata.json").read_text())
    assert metadata["versions"] == ["1.2.bcr.1"]

    # A hand-written BUILD.bazel where the conversion writes one: refused, nothing written.
    (overlay / "BUILD.bazel").write_text("# mine\n")
    staging3 = tmp_path / "staging3"
    write(staging3 / "BUILD.bazel", OURS_DOCSTRING + "# b3\n")
    with pytest.raises(ownership.Conflict):
        write_entry(staging3)
    assert (overlay / "BUILD.bazel").read_text() == "# mine\n"


def test_newest_versions_stay_on_the_upstream_release(tmp_path: Path) -> None:
    """A dependency takes the newest `.bcr.N` of the upstream release it already depends on; a
    newer upstream release is reported, and taken only for a module named in `allow_upstream`
    or one without a current version.  Yanked versions never count."""
    registry = tmp_path / "registry"
    (registry / "modules/zlib").mkdir(parents=True)
    (registry / "modules/zlib/metadata.json").write_text(
        json.dumps(
            {
                "versions": ["1.3.1.bcr.1", "1.3.1.bcr.2", "1.3.1.bcr.3", "1.4.0", "1.4.0.bcr.1"],
                "yanked_versions": {"1.3.1.bcr.3": "broken"},
            }
        )
    )
    held = {"zlib": "1.3.1.bcr.1"}
    assert bcr.newest_versions(registry, ["zlib"], current=held) == {"zlib": "1.3.1.bcr.2"}
    assert bcr.newest_versions(registry, ["zlib"], current=held, allow_upstream={"zlib"}) == {"zlib": "1.4.0.bcr.1"}
    assert bcr.newest_versions(registry, ["zlib"]) == {"zlib": "1.4.0.bcr.1"}
    assert bcr.newest_versions(registry, ["zlib", "nothere"], current={"zlib": "1.4.0"}) == {"zlib": "1.4.0.bcr.1"}
    assert bcr.upstream_bumps(registry, held) == {"zlib": "1.4.0.bcr.1"}
    assert bcr.upstream_bumps(registry, {"zlib": "1.4.0"}) == {}
