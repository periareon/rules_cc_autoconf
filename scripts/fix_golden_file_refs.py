#!/usr/bin/env python3
"""Fix BUILD.bazel files to correctly reference golden files based on what exists."""

import re
from pathlib import Path

DIRECTORIES = [
    "acos", "acosf", "aligned_alloc", "argp", "atan2", "atan2f", "btowc",
    "cbrtf", "ceil", "ceilf", "cos", "cosf", "coshf", "exp2f", "expf",
    "expm1f", "fabs", "fabsf", "faccessat", "filemode", "floorf", "fmodf",
    "free", "fseeko", "futimens", "getline", "group-member", "hypotf",
    "log1p", "log1pf", "logb", "logbf", "logf", "logp1", "logp1f", "lstat",
    "mbrlen", "mbrtowc", "mbsinit", "mbsrtowcs", "memmem", "mntent_h",
    "nproc", "obstack", "off64_t", "open", "physmem", "powf",
    "pthread_mutex_timedlock", "sigaction", "signal_h", "sinf", "sinhf",
    "sqrtf", "stdlib_h", "strerror_r", "strsignal", "symlink", "symlinkat",
    "sys_cdefs_h", "sys_types_h", "tanf", "tanhf", "thread", "threadlib",
    "timegm", "trunc", "truncf", "unlinkat", "unlockpt", "wcrtomb",
    "wcsrtombs", "wctob",
]

REPO_ROOT = Path(__file__).parent.parent
COMPATIBILITY_DIR = REPO_ROOT / "gnulib" / "tests" / "compatibility"


def get_golden_format(dir_path: Path, base_name: str) -> str:
    """Determine the correct format for golden file reference.
    
    Args:
        dir_path: Path to the test directory
        base_name: Either "golden_config" or "golden_subst"
    
    Returns:
        Either a simple string like '"golden_config.h.in"' or a dict format
    """
    common_file = dir_path / f"{base_name}.h.in"
    linux_file = dir_path / f"{base_name}_linux.h.in"
    macos_file = dir_path / f"{base_name}_macos.h.in"
    
    has_common = common_file.exists()
    has_linux = linux_file.exists()
    has_macos = macos_file.exists()
    
    # If we have platform-specific files, use dict format
    if has_linux and has_macos:
        return '''{
        "linux": "''' + base_name + '''_linux.h.in",
        "macos": "''' + base_name + '''_macos.h.in",
    }'''
    elif has_linux:
        # Only linux variant - use common name
        return f'"{base_name}.h.in"'
    elif has_macos:
        # Only macos variant - use common name
        return f'"{base_name}.h.in"'
    elif has_common:
        return f'"{base_name}.h.in"'
    else:
        print(f"  WARNING: No golden file found for {base_name} in {dir_path}")
        return f'"{base_name}.h.in"'


def fix_build_file(dir_name: str) -> bool:
    """Fix a single BUILD.bazel file."""
    dir_path = COMPATIBILITY_DIR / dir_name
    build_file = dir_path / "BUILD.bazel"
    
    if not build_file.exists():
        print(f"  WARNING: {build_file} does not exist")
        return False
    
    content = build_file.read_text()
    original = content
    
    # Get the correct format for each golden file type
    config_format = get_golden_format(dir_path, "golden_config")
    subst_format = get_golden_format(dir_path, "golden_subst")
    
    # Replace golden_config_h (handle both dict and string formats)
    content = re.sub(
        r'golden_config_h\s*=\s*\{[^}]+\}',
        f'golden_config_h = {config_format}',
        content
    )
    content = re.sub(
        r'golden_config_h\s*=\s*"[^"]+"',
        f'golden_config_h = {config_format}',
        content
    )
    
    # Replace golden_subst_h (handle both dict and string formats)
    content = re.sub(
        r'golden_subst_h\s*=\s*\{[^}]+\}',
        f'golden_subst_h = {subst_format}',
        content
    )
    content = re.sub(
        r'golden_subst_h\s*=\s*"[^"]+"',
        f'golden_subst_h = {subst_format}',
        content
    )
    
    if content != original:
        build_file.write_text(content)
        return True
    return False


def main():
    updated = 0
    for dir_name in DIRECTORIES:
        dir_path = COMPATIBILITY_DIR / dir_name
        
        # Show what files exist
        files = list(dir_path.glob("golden_*.h.in")) if dir_path.exists() else []
        file_names = [f.name for f in files]
        
        if fix_build_file(dir_name):
            print(f"  Updated: {dir_name} (files: {', '.join(sorted(file_names))})")
            updated += 1
        else:
            print(f"  Skipped: {dir_name}")
    
    print(f"\nTotal: Updated {updated} BUILD files")


if __name__ == "__main__":
    main()
