#!/usr/bin/env python3
"""Fix BUILD files using AC_TRY_COMPILE for simple function checks.

Many modules use AC_TRY_COMPILE for patterns like:
  gl_CHECK_FUNCS_ANDROID([func], [[#include <header.h>]])

These should use AC_CHECK_FUNC instead.
"""

import os
import re
from pathlib import Path

GNULIB_M4_SRC = Path(os.path.expanduser("~/Code/gnulib/m4"))
GNULIB_M4_DST = Path("/Users/andrebrisco/Code/rules_cc_autoconf/gnulib/m4")


def get_m4_function_check(module_name: str) -> tuple[str, list[str]] | None:
    """Parse the original m4 to find gl_CHECK_FUNCS_ANDROID or AC_CHECK_FUNCS patterns."""
    m4_path = GNULIB_M4_SRC / f"{module_name}.m4"
    if not m4_path.exists():
        return None
    
    content = m4_path.read_text()
    
    # Pattern: gl_CHECK_FUNCS_ANDROID([func], [[#include <header.h>]])
    pattern = r'gl_CHECK_FUNCS_(?:ANDROID|MACOS)\(\[([^\]]+)\]'
    match = re.search(pattern, content)
    if match:
        func = match.group(1)
        return (func, [])
    
    # Pattern: AC_CHECK_FUNCS([func])
    pattern = r'AC_CHECK_FUNCS?\(\[([^\]]+)\]'
    match = re.search(pattern, content)
    if match:
        func = match.group(1)
        return (func, [])
    
    return None


def should_convert(build_content: str) -> bool:
    """Check if this BUILD file has AC_TRY_COMPILE that looks like a function check."""
    # Pattern: AC_TRY_COMPILE with code that just declares an extern function
    # and calls it
    if "AC_TRY_COMPILE" not in build_content:
        return False
    
    # Look for the typical pattern
    pattern = r'''AC_TRY_COMPILE\(\s*
\s*code\s*=\s*""".*?
extern\s+"C".*?
char\s+(\w+)\s*\(\s*\)\s*;'''
    
    return bool(re.search(pattern, build_content, re.DOTALL))


def convert_to_check_func(build_path: Path, module_name: str) -> bool:
    """Convert AC_TRY_COMPILE to AC_CHECK_FUNC if appropriate."""
    m4_info = get_m4_function_check(module_name)
    if not m4_info:
        return False
    
    func_name, headers = m4_info
    
    content = build_path.read_text()
    
    # Check if it's a simple function check pattern
    if not should_convert(content):
        return False
    
    # Find the AC_TRY_COMPILE block
    pattern = r'''macros\.AC_TRY_COMPILE\(\s*
\s*code\s*=\s*""".*?""",\s*
\s*define\s*=\s*"(HAVE_\w+)",\s*
\s*language\s*=\s*"\w+",?\s*
\s*\)'''
    
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return False
    
    define_name = match.group(1)
    
    # Replace with AC_CHECK_FUNC
    replacement = f'macros.AC_CHECK_FUNC("{func_name}")'
    
    new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    if new_content != content:
        build_path.write_text(new_content)
        return True
    
    return False


def main():
    modified = 0
    skipped = 0
    
    for build_path in sorted(GNULIB_M4_DST.glob("*/BUILD.bazel")):
        module_name = build_path.parent.name
        content = build_path.read_text()
        
        if "AC_TRY_COMPILE" not in content:
            continue
        
        if convert_to_check_func(build_path, module_name):
            print(f"  CONVERTED: {module_name}")
            modified += 1
        else:
            skipped += 1
    
    print(f"\nSummary:")
    print(f"  Converted: {modified}")
    print(f"  Skipped (not simple func check): {skipped}")


if __name__ == "__main__":
    main()
