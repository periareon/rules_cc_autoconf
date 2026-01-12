#!/usr/bin/env python3
"""Fix golden files by restoring from git index and creating proper platform variants.

The split_golden_files.py script incorrectly copied autoconf output instead of
preserving the original golden files that match Bazel output.

This script:
1. Restores original golden files from git index 
2. Copies them as both *_linux.h and *_macos.h (since Bazel output is platform-independent)
3. Removes the incorrectly generated files
"""

import os
import subprocess
from pathlib import Path

COMPAT_DIR = Path("gnulib/tests/compatibility")

# All modules that were processed (need fixing)
PROCESSED_MODULES = [
    "copy-file-range", "eaccess", "fchdir", "fstatat", "fsusage", "getaddrinfo",
    "getcwd", "getdomainname", "gethostname", "getloadavg", "getopt", "getpass",
    "iconv", "ieee754-h", "inttypes", "isapipe", "isnanf", "linkat", "lock",
    "longlong", "math_h", "memset_explicit", "mkfifo", "mktime", "mode_t",
    "mountlist", "mprotect", "msvc-inval", "nanosleep", "net_if_h",
    "netinet_in_h", "nl_langinfo", "poll", "posixver", "printf",
    "pthread-rwlock", "pthread_h", "pty", "readline", "readlink", "regex",
    "sched_h", "selinux-selinux-h", "sigaltstack", "signbit", "std-gnu11",
    "stddef_h", "stdint", "strtod", "strtof", "sys_times_h", "sys_un_h",
    "sys_utsname_h", "termcap", "termios_h", "time_h", "time_rz", "ttyname_r",
    "unistd_h", "utimes", "valgrind-helper", "vararrays", "vasnprintf",
]


def restore_from_git(module_dir: Path, filename: str) -> str | None:
    """Restore file content from git index."""
    git_path = f"gnulib/tests/compatibility/{module_dir.name}/{filename}"
    result = subprocess.run(
        ["git", "show", f":{git_path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout
    return None


def fix_module(module: str) -> bool:
    """Fix golden files for a single module."""
    module_dir = COMPAT_DIR / module
    if not module_dir.exists():
        print(f"  ERROR: Module directory not found")
        return False
    
    # Try to restore original golden files from git index
    config_content = restore_from_git(module_dir, "golden_config.h")
    subst_content = restore_from_git(module_dir, "golden_subst.h")
    
    if config_content is None:
        # Try to get from _linux file (already renamed correctly by earlier script)
        linux_config = module_dir / "golden_config_linux.h"
        if linux_config.exists():
            # Check if it looks like Bazel output (not full autoconf output)
            content = linux_config.read_text()
            if not content.startswith("/* config.h.  Generated from"):
                config_content = content
    
    if subst_content is None:
        linux_subst = module_dir / "golden_subst_linux.h"
        if linux_subst.exists():
            content = linux_subst.read_text()
            if not content.startswith("/* config.h.  Generated from"):
                subst_content = content
    
    if config_content is None or subst_content is None:
        print(f"  ERROR: Could not restore golden files")
        return False
    
    # Write platform-specific files (Bazel output is the same on all platforms)
    (module_dir / "golden_config_linux.h").write_text(config_content)
    (module_dir / "golden_config_macos.h").write_text(config_content)
    print(f"  Created golden_config_*.h")
    
    (module_dir / "golden_subst_linux.h").write_text(subst_content)
    (module_dir / "golden_subst_macos.h").write_text(subst_content)
    print(f"  Created golden_subst_*.h")
    
    # Ensure original golden files are removed
    for f in ["golden_config.h", "golden_subst.h"]:
        p = module_dir / f
        if p.exists():
            p.unlink()
    
    return True


def main():
    os.chdir(Path(__file__).parent.parent)
    
    print(f"Fixing {len(PROCESSED_MODULES)} modules...")
    print("=" * 60)
    print()
    
    success_count = 0
    failed_modules = []
    
    for i, module in enumerate(PROCESSED_MODULES, 1):
        print(f"[{i}/{len(PROCESSED_MODULES)}] {module}")
        try:
            if fix_module(module):
                success_count += 1
            else:
                failed_modules.append(module)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed_modules.append(module)
        print()
    
    print("=" * 60)
    print(f"Done! {success_count}/{len(PROCESSED_MODULES)} modules fixed.")
    if failed_modules:
        print(f"Failed: {', '.join(failed_modules)}")


if __name__ == "__main__":
    main()
