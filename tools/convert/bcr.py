"""Render a conversion as a Bazel Central Registry module entry.

Layout (what `bazel_dep` consumers and the BCR presubmit expect):

    modules/<name>/metadata.json
    modules/<name>/<version>/MODULE.bazel
    modules/<name>/<version>/source.json
    modules/<name>/<version>/presubmit.yml
    modules/<name>/<version>/overlay/BUILD.bazel
    modules/<name>/<version>/overlay/MODULE.bazel

The overlay only adds files on top of the release archive; `source.json`
records the archive and the integrity of every overlay file.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

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


def integrity(data: bytes) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")


def write_entry(
    registry: Path,
    *,
    name: str,
    version: str,
    build_text: str,
    module_text: str,
    source_url: str,
    source_integrity: str,
    strip_prefix: str | None,
    homepage: str,
) -> list[Path]:
    """Write the module entry; returns the files written.

    The archive URL, its integrity and the homepage are inputs, never
    placeholders: a registry entry with a value to fill in later would need
    a hand edit after every regeneration.
    """
    module_dir = registry / "modules" / name
    version_dir = module_dir / version
    overlay = version_dir / "overlay"
    overlay.mkdir(parents=True, exist_ok=True)
    written = []

    files = {"BUILD.bazel": build_text, "MODULE.bazel": module_text}
    overlay_integrity = {}
    for filename, text in files.items():
        path = overlay / filename
        path.write_text(text, encoding="utf-8", newline="\n")
        overlay_integrity[filename] = integrity(text.encode("utf-8"))
        written.append(path)

    (version_dir / "MODULE.bazel").write_text(module_text, encoding="utf-8", newline="\n")
    written.append(version_dir / "MODULE.bazel")

    source = {
        "integrity": source_integrity,
        "overlay": overlay_integrity,
        "strip_prefix": strip_prefix or f"{name}-{version.split('.bcr.')[0]}",
        "url": source_url,
    }
    (version_dir / "source.json").write_text(json.dumps(source, indent=4) + "\n", encoding="utf-8", newline="\n")
    written.append(version_dir / "source.json")

    (version_dir / "presubmit.yml").write_text(PRESUBMIT.format(name=name), encoding="utf-8", newline="\n")
    written.append(version_dir / "presubmit.yml")

    metadata_path = module_dir / "metadata.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    else:
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
    return written
