#!/usr/bin/env python3
"""Script to fix missing variables in config.h.in and subst.h.in files.

Runs the gnu_autoconf tests and parses the error output to find missing variables,
then adds them to the appropriate template files.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
COMPATIBILITY_DIR = REPO_ROOT / "gnulib" / "tests" / "compatibility"


def run_tests_and_capture_output() -> str:
    """Run all gnu_autoconf tests and capture output."""
    print("Running bazel tests to find missing variables...")
    result = subprocess.run(
        [
            "bazel",
            "test",
            "//gnulib/tests/compatibility/...:all",
            "--test_output=errors",
            "-k",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return result.stdout


def parse_missing_variables(output: str) -> dict[str, dict[str, set[str]]]:
    """Parse the test output to extract missing variables per test.
    
    Returns:
        Dict mapping test directory name to {"defines": set, "substs": set}
    """
    results: dict[str, dict[str, set[str]]] = {}
    
    # Pattern to match test target
    test_pattern = re.compile(r"//gnulib/tests/compatibility/([^:]+):([^_]+)_test_gnu_autoconf")
    
    # Pattern to match missing defines/substs sections
    defines_pattern = re.compile(r"config\.log has \d+ defines not found in `config\.h\.in`:")
    substs_pattern = re.compile(r"config\.log has \d+ substs not found in `subst\.h\.in`:")
    variable_pattern = re.compile(r"^\s+-\s+([A-Za-z_][A-Za-z0-9_]*)$", re.MULTILINE)
    
    lines = output.split("\n")
    current_test = None
    in_defines_section = False
    in_substs_section = False
    
    for i, line in enumerate(lines):
        # Check for test target
        test_match = test_pattern.search(line)
        if test_match:
            current_test = test_match.group(1)
            if current_test not in results:
                results[current_test] = {"defines": set(), "substs": set()}
        
        # Check for defines section start
        if defines_pattern.search(line):
            in_defines_section = True
            in_substs_section = False
            continue
        
        # Check for substs section start
        if substs_pattern.search(line):
            in_substs_section = True
            in_defines_section = False
            continue
        
        # Parse variable names
        var_match = variable_pattern.match(line)
        if var_match and current_test:
            var_name = var_match.group(1)
            if in_defines_section:
                results[current_test]["defines"].add(var_name)
            elif in_substs_section:
                results[current_test]["substs"].add(var_name)
        
        # End of section detection
        if line.strip() and not line.startswith("  -") and (in_defines_section or in_substs_section):
            if not defines_pattern.search(line) and not substs_pattern.search(line):
                in_defines_section = False
                in_substs_section = False
    
    return results


def update_config_h_in(test_dir: Path, missing_defines: set[str]) -> int:
    """Add missing #undef statements to config.h.in."""
    config_h_in = test_dir / "config.h.in"
    if not config_h_in.exists():
        print(f"  WARNING: {config_h_in} does not exist")
        return 0
    
    content = config_h_in.read_text()
    
    # Find existing undefs
    existing_undefs = set(re.findall(r"#undef\s+([A-Za-z_][A-Za-z0-9_]*)", content))
    
    # Find truly missing ones
    to_add = missing_defines - existing_undefs
    
    if not to_add:
        return 0
    
    # Add missing undefs at the end (before any trailing whitespace)
    content = content.rstrip()
    for var in sorted(to_add):
        content += f"\n#undef {var}"
    content += "\n"
    
    config_h_in.write_text(content)
    return len(to_add)


def update_subst_h_in(test_dir: Path, missing_substs: set[str]) -> int:
    """Add missing #define SUBST_VAR "@VAR@" statements to subst.h.in."""
    subst_h_in = test_dir / "subst.h.in"
    if not subst_h_in.exists():
        print(f"  WARNING: {subst_h_in} does not exist")
        return 0
    
    content = subst_h_in.read_text()
    
    # Find existing substs (pattern: @VAR@)
    existing_substs = set(re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)@", content))
    
    # Find truly missing ones
    to_add = missing_substs - existing_substs
    
    if not to_add:
        return 0
    
    # Add missing substs at the end
    content = content.rstrip()
    for var in sorted(to_add):
        content += f'\n#define SUBST_{var} "@{var}@"'
    content += "\n"
    
    subst_h_in.write_text(content)
    return len(to_add)


def main():
    # Run tests and capture output
    output = run_tests_and_capture_output()
    
    # Save output for debugging
    output_file = REPO_ROOT / "scripts" / "test_output.txt"
    output_file.write_text(output)
    print(f"Saved test output to {output_file}")
    
    # Parse missing variables
    missing_vars = parse_missing_variables(output)
    
    if not missing_vars:
        print("No missing variables found!")
        return 0
    
    print(f"\nFound missing variables in {len(missing_vars)} tests:")
    
    total_defines_added = 0
    total_substs_added = 0
    
    for test_name, vars_dict in sorted(missing_vars.items()):
        test_dir = COMPATIBILITY_DIR / test_name
        
        if not test_dir.exists():
            print(f"  WARNING: {test_dir} does not exist")
            continue
        
        defines_added = 0
        substs_added = 0
        
        if vars_dict["defines"]:
            defines_added = update_config_h_in(test_dir, vars_dict["defines"])
        
        if vars_dict["substs"]:
            substs_added = update_subst_h_in(test_dir, vars_dict["substs"])
        
        if defines_added or substs_added:
            print(f"  {test_name}: +{defines_added} defines, +{substs_added} substs")
            total_defines_added += defines_added
            total_substs_added += substs_added
    
    print(f"\nTotal: Added {total_defines_added} defines and {total_substs_added} substs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
