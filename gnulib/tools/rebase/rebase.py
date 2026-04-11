#!/usr/bin/env python3
"""Compare pinned gnulib m4 files against upstream HEAD and generate a rebase report."""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

GITHUB_REPO_URL = "https://github.com/coreutils/gnulib.git"
BLOB_URL_RE = re.compile(
    r"https://github\.com/coreutils/gnulib/blob/([a-f0-9]{40})/(.+)"
)


# -- Local git operations on a bare clone -------------------------------------


def clone_bare(tmpdir: str) -> str:
    """Blobless bare clone — fetches commits and trees but downloads blobs on demand."""
    repo_dir = str(Path(tmpdir) / "gnulib.git")
    print("Cloning gnulib (bare, blobless)...", file=sys.stderr)
    subprocess.run(
        [
            "git",
            "clone",
            "--bare",
            "--filter=blob:none",
            GITHUB_REPO_URL,
            repo_dir,
        ],
        check=True,
        capture_output=True,
    )
    print("Clone complete.", file=sys.stderr)
    return repo_dir


def _git(repo_dir: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "--git-dir", repo_dir, *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def get_master_head(repo_dir: str) -> str:
    return _git(repo_dir, "rev-parse", "refs/heads/master").strip()


def get_changed_m4_files(repo_dir: str, old: str, new: str) -> set[str]:
    """Return set of paths (e.g. 'm4/fopen.m4') that differ between two commits."""
    output = _git(repo_dir, "diff", "--name-only", f"{old}..{new}", "--", "m4/")
    if not output.strip():
        return set()
    return set(output.strip().splitlines())


def get_file_at(repo_dir: str, ref: str, path: str) -> str | None:
    """Return file contents at the given ref, or None if the path doesn't exist."""
    try:
        return _git(repo_dir, "show", f"{ref}:{path}")
    except subprocess.CalledProcessError:
        return None


# -- BUILD.bazel parsing ------------------------------------------------------


def find_build_files(workspace: Path) -> list[Path]:
    return sorted((workspace / "gnulib" / "m4").glob("*/BUILD.bazel"))


def parse_url(build_file: Path) -> tuple[str, str] | None:
    """Extract (commit_hash, m4_path) from the docstring URL, or None."""
    text = build_file.read_text()
    m = BLOB_URL_RE.search(text)
    if m:
        return m.group(1), m.group(2)
    return None


def bazel_label(build_file: Path, workspace: Path) -> str:
    """Return the Bazel package label, e.g. //gnulib/m4/fopen."""
    rel = build_file.parent.relative_to(workspace)
    return f"//{rel}"


# -- Diff computation ---------------------------------------------------------


COPYRIGHT_RE = re.compile(
    r"^[-+]dnl Copyright (?:\(C\) )?[\d,\- ]+ Free Software Foundation[,.]"
)


def _is_copyright_only(diff_lines: list[str]) -> bool:
    """True if every added/removed line in the diff is a copyright notice."""
    for line in diff_lines:
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-") or line.startswith("+"):
            if not COPYRIGHT_RE.match(line):
                return False
    return True


def compute_diff(old_content: str, new_content: str, m4_path: str) -> str | None:
    """Return a unified diff string, or None if files are identical / copyright-only."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{m4_path}",
            tofile=f"b/{m4_path}",
        )
    )
    if not diff:
        return None
    if _is_copyright_only(diff):
        return None
    return "".join(diff)


# -- Report generation --------------------------------------------------------


def build_report(
    old_hash: str,
    new_hash: str,
    diffs: dict[str, str],
    unchanged: list[str],
    skipped: dict[str, str],
) -> str:
    lines: list[str] = []
    lines.append("# Rebase Report\n")
    lines.append(f"\nPinned: `{old_hash}` -> Master HEAD: `{new_hash}`\n")

    if diffs:
        lines.append(f"\n{len(diffs)} file(s) changed upstream.\n")
        for label in sorted(diffs):
            lines.append(f"\n## `{label}`\n")
            lines.append("\n```diff\n")
            lines.append(diffs[label])
            if not diffs[label].endswith("\n"):
                lines.append("\n")
            lines.append("```\n")
    else:
        lines.append("\nNo upstream m4 files changed.\n")

    if unchanged:
        lines.append(f"\n## Unchanged ({len(unchanged)} file(s))\n\n")
        for label in sorted(unchanged):
            lines.append(f"- `{label}`\n")

    if skipped:
        lines.append("\n## Skipped\n")
        for label in sorted(skipped):
            lines.append(f"\n- `{label}`: {skipped[label]}\n")

    return "".join(lines)


# -- Main ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare pinned gnulib m4 files against upstream HEAD and generate a diff report."
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Path for the markdown report (default: <workspace>/rebase_report.md).",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (default: auto-detected from script location).",
    )
    args = parser.parse_args()

    workspace: Path = args.workspace or Path(__file__).resolve().parents[3]
    report_path: Path = args.report_path or (workspace / "rebase_report.md")

    print(f"Workspace: {workspace}")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = clone_bare(tmpdir)

        new_hash = get_master_head(repo_dir)
        print(f"Master HEAD: {new_hash}", file=sys.stderr)

        build_files = find_build_files(workspace)
        print(f"Found {len(build_files)} BUILD.bazel files.", file=sys.stderr)

        by_hash: dict[str, list[tuple[Path, str]]] = {}
        no_url: list[Path] = []
        for bf in build_files:
            parsed = parse_url(bf)
            if parsed is None:
                no_url.append(bf)
                continue
            commit, m4_path = parsed
            by_hash.setdefault(commit, []).append((bf, m4_path))

        if no_url:
            print(
                f"WARNING: {len(no_url)} file(s) have no URL comment, skipped:",
                file=sys.stderr,
            )
            for bf in no_url:
                print(f"  {bf.relative_to(workspace)}", file=sys.stderr)

        diffs: dict[str, str] = {}
        skipped: dict[str, str] = {}
        unchanged: list[str] = []
        old_hashes: set[str] = set()

        for old_hash, entries in by_hash.items():
            old_hashes.add(old_hash)

            if old_hash == new_hash:
                print(
                    f"Hash {old_hash[:12]} already at master HEAD, nothing to do.",
                    file=sys.stderr,
                )
                for bf, _ in entries:
                    unchanged.append(bazel_label(bf, workspace))
                continue

            print(
                f"Diffing {old_hash[:12]}..{new_hash[:12]} in m4/...", file=sys.stderr
            )
            changed_paths = get_changed_m4_files(repo_dir, old_hash, new_hash)
            print(
                f"  {len(changed_paths)} m4 file(s) changed upstream.", file=sys.stderr
            )

            for bf, m4_path in entries:
                label = bazel_label(bf, workspace)

                if m4_path not in changed_paths:
                    unchanged.append(label)
                    continue

                old_content = get_file_at(repo_dir, old_hash, m4_path)
                new_content = get_file_at(repo_dir, new_hash, m4_path)

                if old_content is None and new_content is None:
                    skipped[label] = "file does not exist at either commit"
                    print(f"  SKIP {label}: missing at both commits", file=sys.stderr)
                elif old_content is None:
                    skipped[label] = f"new file at {new_hash[:12]}"
                    print(f"  SKIP {label}: new file", file=sys.stderr)
                elif new_content is None:
                    skipped[label] = f"deleted at {new_hash[:12]}"
                    print(f"  SKIP {label}: deleted upstream", file=sys.stderr)
                else:
                    diff_text = compute_diff(old_content, new_content, m4_path)
                    if diff_text is None:
                        unchanged.append(label)
                    else:
                        diffs[label] = diff_text
                        print(f"  CHANGED {label}", file=sys.stderr)

        primary_old = (
            max(old_hashes, key=lambda h: len(by_hash[h])) if old_hashes else "unknown"
        )

        for bf in no_url:
            skipped[bazel_label(bf, workspace)] = "no URL comment in docstring"

        report = build_report(primary_old, new_hash, diffs, unchanged, skipped)

        report_path.write_text(report)
        print(f"\nReport written to {report_path}", file=sys.stderr)

        print(
            f"\nSummary: {len(unchanged)} unchanged, {len(diffs)} changed, "
            f"{len(skipped)} skipped.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
