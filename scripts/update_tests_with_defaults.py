#!/usr/bin/env python3
"""Script to update test BUILD files to use :with_defaults target when available."""

import os
import re
from pathlib import Path


def check_has_with_defaults(m4_path: Path) -> bool:
    """Check if the m4 BUILD.bazel file has a with_defaults target."""
    build_file = m4_path / "BUILD.bazel"
    if not build_file.exists():
        return False
    
    content = build_file.read_text()
    return 'name = "with_defaults"' in content


def update_test_build(test_path: Path) -> bool:
    """Update test BUILD.bazel to use :with_defaults if available.
    
    Returns True if updated, False otherwise.
    """
    build_file = test_path / "BUILD.bazel"
    if not build_file.exists():
        return False
    
    content = build_file.read_text()
    
    # Find the bazel_autoconf_target
    match = re.search(r'bazel_autoconf_target\s*=\s*"([^"]+)"', content)
    if not match:
        return False
    
    target = match.group(1)
    
    # Parse the target
    # Format: //gnulib/m4/module_name:target_name or //gnulib/m4/module_name
    if target.startswith("//gnulib/m4/"):
        if ":" in target:
            base, target_name = target.rsplit(":", 1)
        else:
            base = target
            target_name = target.split("/")[-1]
        
        # Check if already using with_defaults
        if target_name == "with_defaults":
            return False
        
        # Get the module path
        module_name = base.replace("//gnulib/m4/", "")
        m4_path = Path(__file__).parent.parent / "gnulib" / "m4" / module_name
        
        if not check_has_with_defaults(m4_path):
            return False
        
        # Update to use with_defaults
        new_target = f"{base}:with_defaults"
        new_content = content.replace(f'bazel_autoconf_target = "{target}"',
                                       f'bazel_autoconf_target = "{new_target}"')
        
        if new_content != content:
            build_file.write_text(new_content)
            return True
    
    return False


def main():
    base_dir = Path(__file__).parent.parent / "gnulib" / "tests" / "compatibility"
    
    if not base_dir.exists():
        print(f"Directory not found: {base_dir}")
        return
    
    updated_count = 0
    checked_count = 0
    
    for test_dir in sorted(base_dir.iterdir()):
        if not test_dir.is_dir():
            continue
        if test_dir.name.startswith("."):
            continue
        
        checked_count += 1
        if update_test_build(test_dir):
            print(f"Updated: {test_dir.name}")
            updated_count += 1
    
    print(f"\nChecked {checked_count} tests, updated {updated_count}")


if __name__ == "__main__":
    main()
