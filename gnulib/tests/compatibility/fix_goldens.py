#!/usr/bin/env python3
"""Auto-fix golden files by copying actual Bazel output.

This script:
1. Builds all bazel_config.h and bazel_gnulib_*.h targets
2. Copies the outputs to the golden files

Usage: python3 fix_goldens.py [module_name]
       If module_name is provided, only fix that module.
       Otherwise, fix all modules.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_workspace_root() -> Path:
    """Get the workspace root directory."""
    return Path(__file__).parent.parent.parent.parent


def get_bazel_bin() -> Path:
    """Get bazel-bin directory."""
    return get_workspace_root() / 'bazel-bin'


def get_test_modules(variables_dir: Path) -> list:
    """Get list of test module directories."""
    skip = {'BUILD.bazel', 'batch_generate.py', 'fix_goldens.py', 
            'generate_tests.py', 'generate_all_tests.sh',
            'gnu_gnulib_diff_test.bzl', 'gnu_gnulib_diff_tester.py'}
    modules = []
    for item in variables_dir.iterdir():
        if item.is_dir() and item.name not in skip:
            modules.append(item.name)
    return sorted(modules)


def build_bazel_outputs(module: str) -> bool:
    """Build Bazel output files for a module."""
    targets = [
        f'//gnulib/tests/variables/{module}:{module}_test_bazel_config_h',
        f'//gnulib/tests/variables/{module}:{module}_test_bazel_gnulib_h',
    ]
    
    result = subprocess.run(
        ['bazel', 'build'] + targets,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def fix_module_goldens(module: str, variables_dir: Path, bazel_bin: Path) -> bool:
    """Fix golden files for a single module."""
    module_dir = variables_dir / module
    if not module_dir.exists():
        print(f"SKIP: {module} (directory not found)")
        return False
    
    # Build Bazel outputs
    if not build_bazel_outputs(module):
        print(f"FAIL: {module} (build failed)")
        return False
    
    # Copy bazel_config.h to golden_config.h
    bazel_config = bazel_bin / 'gnulib' / 'tests' / 'variables' / module / 'bazel_config.h'
    golden_config = module_dir / 'golden_config.h'
    
    if bazel_config.exists():
        shutil.copy2(bazel_config, golden_config)
    
    # Copy bazel_gnulib_*.h to golden_subst.h
    for f in (bazel_bin / 'gnulib' / 'tests' / 'variables' / module).glob('bazel_gnulib_*.h'):
        golden_subst = module_dir / 'golden_subst.h'
        shutil.copy2(f, golden_subst)
        break
    
    print(f"FIXED: {module}")
    return True


def main():
    workspace = get_workspace_root()
    variables_dir = workspace / 'gnulib' / 'tests' / 'variables'
    bazel_bin = get_bazel_bin()
    
    os.chdir(workspace)
    
    if len(sys.argv) > 1:
        # Fix specific module
        modules = sys.argv[1:]
    else:
        # Fix all modules
        modules = get_test_modules(variables_dir)
    
    fixed = 0
    failed = 0
    
    for module in modules:
        if fix_module_goldens(module, variables_dir, bazel_bin):
            fixed += 1
        else:
            failed += 1
    
    print(f"\nFixed: {fixed}, Failed: {failed}")


if __name__ == '__main__':
    main()
