#!/usr/bin/env python3
"""Add //gnulib/m4/extensions dependency to BUILD files that need it.

This script:
1. Finds all gnulib m4 files that require gl_USE_SYSTEM_EXTENSIONS
2. Checks if the corresponding BUILD.bazel already has the dependency
3. Adds it if missing
"""

import os
import re
import subprocess
from pathlib import Path

GNULIB_M4_SRC = Path(os.path.expanduser("~/Code/gnulib/m4"))
GNULIB_M4_DST = Path("/Users/andrebrisco/Code/rules_cc_autoconf/gnulib/m4")

EXTENSIONS_DEP = '"//gnulib/m4/extensions"'


def find_modules_needing_extensions() -> set[str]:
    """Find all m4 files that require gl_USE_SYSTEM_EXTENSIONS."""
    result = subprocess.run(
        ["grep", "-l", "gl_USE_SYSTEM_EXTENSIONS", *GNULIB_M4_SRC.glob("*.m4")],
        capture_output=True,
        text=True,
    )
    
    modules = set()
    for line in result.stdout.strip().split("\n"):
        if line:
            # Extract module name from path like /path/to/acosf.m4
            name = Path(line).stem
            # Skip extensions itself and gnulib-common
            if name not in ("extensions", "gnulib-common"):
                modules.add(name)
    
    return modules


def has_extensions_dep(build_content: str) -> bool:
    """Check if BUILD.bazel already has the extensions dependency."""
    return "//gnulib/m4/extensions" in build_content


def add_extensions_dep(build_path: Path) -> bool:
    """Add extensions dep to BUILD.bazel if missing. Returns True if modified."""
    content = build_path.read_text()
    
    if has_extensions_dep(content):
        return False
    
    # Find the deps = [ ... ] block and add the dependency
    # Pattern: deps = [ ... ]
    deps_pattern = r'(deps\s*=\s*\[)'
    
    if re.search(deps_pattern, content):
        # Add to existing deps
        new_content = re.sub(
            deps_pattern,
            r'\1\n        # AC_REQUIRE([gl_USE_SYSTEM_EXTENSIONS])\n        "//gnulib/m4/extensions",',
            content,
            count=1
        )
    else:
        # No deps yet - need to add deps = [...] before the closing )
        # Find the closing ) of autoconf(
        # This is tricky - look for pattern like:
        #   visibility = ["//visibility:public"],
        # )
        visibility_pattern = r'(visibility\s*=\s*\["//visibility:public"\],\s*)\)'
        if re.search(visibility_pattern, content):
            new_content = re.sub(
                visibility_pattern,
                r'\1    deps = [\n        # AC_REQUIRE([gl_USE_SYSTEM_EXTENSIONS])\n        "//gnulib/m4/extensions",\n    ],\n)',
                content,
                count=1
            )
        else:
            # Can't auto-add, skip
            print(f"  WARNING: Could not add deps to {build_path}")
            return False
    
    build_path.write_text(new_content)
    return True


def main():
    modules = find_modules_needing_extensions()
    print(f"Found {len(modules)} modules requiring gl_USE_SYSTEM_EXTENSIONS")
    
    modified = 0
    skipped = 0
    missing = 0
    
    for module in sorted(modules):
        # Convert module name to directory (e.g., sys_socket_h -> sys_socket_h)
        # Handle special characters in names
        module_dir = module.replace("-", "-")  # Keep as-is for now
        
        build_path = GNULIB_M4_DST / module_dir / "BUILD.bazel"
        
        if not build_path.exists():
            # Try with underscores instead of hyphens
            module_dir_alt = module.replace("-", "_")
            build_path = GNULIB_M4_DST / module_dir_alt / "BUILD.bazel"
        
        if not build_path.exists():
            print(f"  MISSING: {module} (no BUILD.bazel)")
            missing += 1
            continue
        
        content = build_path.read_text()
        if has_extensions_dep(content):
            skipped += 1
            continue
        
        if add_extensions_dep(build_path):
            print(f"  ADDED: {module}")
            modified += 1
        else:
            print(f"  SKIPPED: {module} (could not modify)")
            skipped += 1
    
    print(f"\nSummary:")
    print(f"  Modified: {modified}")
    print(f"  Already had dep: {skipped}")
    print(f"  Missing BUILD: {missing}")


if __name__ == "__main__":
    main()
