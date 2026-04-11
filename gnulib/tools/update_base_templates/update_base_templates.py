#!/usr/bin/env python3
"""Merge new #define / #undef entries from bazel-testlogs into base templates.

After running `bazel test //gnulib/tests/compat/...`, the *_test_gnu_autoconf
targets produce expected_config.h.in and expected_subst.h.in via GNU autoheader.
This script merges any NEW symbols from those outputs into the checked-in
gnulib/tests/compat/<module>/ templates, preserving existing entries.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXPECTED_PREFIX = "expected_"
TEST_OUTPUTS_DIR = "test.outputs"
AUTOCONF_SUFFIX = "_test_gnu_autoconf"
COMPAT_REL = Path("gnulib") / "tests" / "compat"

# Patterns for extracting symbol names from template lines.
# config.h.in:  #undef FOO  or  /* #undef FOO */
# subst.h.in:   #define SUBST_FOO "@FOO@"
UNDEF_RE = re.compile(r"^#undef\s+(\w+)")
COMMENT_UNDEF_RE = re.compile(r"^/\*\s*#undef\s+(\w+)\s*\*/")
DEFINE_RE = re.compile(r"^#define\s+(\w+)\s")


def extract_symbol(line: str) -> str | None:
    """Return the symbol name from a #define or #undef line, or None."""
    m = UNDEF_RE.match(line)
    if m:
        return m.group(1)
    m = COMMENT_UNDEF_RE.match(line)
    if m:
        return m.group(1)
    m = DEFINE_RE.match(line)
    if m:
        return m.group(1)
    return None


def parse_symbols(content: str) -> dict[str, str]:
    """Return {symbol: full_line} for every #define / #undef line in content."""
    symbols: dict[str, str] = {}
    for line in content.splitlines():
        sym = extract_symbol(line)
        if sym is not None:
            symbols[sym] = line
    return symbols


def merge_defines(
    existing_content: str, expected_content: str
) -> tuple[str, list[str]]:
    """Merge new symbols from expected into existing, preserving existing order.

    Returns (merged_content, list_of_added_symbols).
    """
    existing_symbols = parse_symbols(existing_content)
    expected_symbols = parse_symbols(expected_content)

    new_symbols = {
        sym: line
        for sym, line in expected_symbols.items()
        if sym not in existing_symbols
    }

    if not new_symbols:
        return existing_content, []

    lines = (
        existing_content.rstrip("\n").split("\n") if existing_content.strip() else []
    )

    for sym in sorted(new_symbols):
        lines.append(new_symbols[sym])

    added = sorted(new_symbols.keys())
    return "\n".join(lines) + "\n", added


def find_expected_files(
    testlogs_compat: Path,
    module_filter: str | None = None,
) -> list[tuple[str, Path, str]]:
    """Return list of (module, source_path, target_filename) tuples."""
    results: list[tuple[str, Path, str]] = []

    if not testlogs_compat.is_dir():
        return results

    for module_dir in sorted(testlogs_compat.iterdir()):
        if not module_dir.is_dir():
            continue

        module = module_dir.name
        if module_filter and module != module_filter:
            continue

        autoconf_dir = module_dir / f"{module}{AUTOCONF_SUFFIX}" / TEST_OUTPUTS_DIR
        if not autoconf_dir.is_dir():
            continue

        for expected_file in sorted(autoconf_dir.iterdir()):
            if not expected_file.name.startswith(EXPECTED_PREFIX):
                continue
            target_name = expected_file.name[len(EXPECTED_PREFIX) :]
            results.append((module, expected_file, target_name))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge new #define/#undef entries from expected_* test outputs "
        "into gnulib/tests/compat/ base templates."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying files.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (default: auto-detected from script location).",
    )
    parser.add_argument(
        "--module",
        type=str,
        default=None,
        help="Update only a specific module (e.g. 'stdlib_h').",
    )
    args = parser.parse_args()

    workspace: Path = args.workspace or Path(__file__).resolve().parents[3]
    testlogs = workspace / "bazel-testlogs"

    if not testlogs.exists():
        print(
            "ERROR: bazel-testlogs not found. Run tests first:\n"
            "  bazel test //gnulib/tests/compat/...",
            file=sys.stderr,
        )
        sys.exit(1)

    testlogs_compat = testlogs / "gnulib" / "tests" / "compat"
    if not testlogs_compat.is_dir():
        print(
            "ERROR: No gnulib compat test outputs found in bazel-testlogs.",
            file=sys.stderr,
        )
        sys.exit(1)

    entries = find_expected_files(testlogs_compat, args.module)
    if not entries:
        if args.module:
            print(
                f"No expected_* files found for module '{args.module}'.",
                file=sys.stderr,
            )
        else:
            print("No expected_* files found in bazel-testlogs.", file=sys.stderr)
        sys.exit(1)

    merged_count = 0
    skipped_missing = 0
    skipped_identical = 0
    total_added = 0

    for module, source, target_name in entries:
        target_dir = workspace / COMPAT_REL / module
        target = target_dir / target_name

        if not target_dir.is_dir():
            skipped_missing += 1
            continue

        expected_content = source.read_text()

        if not target.exists():
            if args.dry_run:
                syms = parse_symbols(expected_content)
                print(
                    f"  [dry-run] Would create: {COMPAT_REL / module / target_name} "
                    f"({len(syms)} symbols)"
                )
            else:
                target.write_text(expected_content)
                print(f"  CREATE: {COMPAT_REL / module / target_name}")
            merged_count += 1
            continue

        existing_content = target.read_text()
        merged, added = merge_defines(existing_content, expected_content)

        if not added:
            skipped_identical += 1
            continue

        total_added += len(added)
        if args.dry_run:
            print(
                f"  [dry-run] Would add {len(added)} symbol(s) to "
                f"{COMPAT_REL / module / target_name}:"
            )
            for sym in added:
                print(f"    + {sym}")
        else:
            target.write_text(merged)
            print(
                f"  MERGE: {COMPAT_REL / module / target_name} "
                f"(+{len(added)}: {', '.join(added)})"
            )
        merged_count += 1

    print(
        f"\nSummary: {merged_count} {'would be ' if args.dry_run else ''}merged "
        f"(+{total_added} symbols), "
        f"{skipped_identical} identical, {skipped_missing} missing target dir.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
