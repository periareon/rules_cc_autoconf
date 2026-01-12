#!/usr/bin/env python3
"""Split golden files into platform-specific variants.

This script:
1. Renames existing golden files to *_linux.h (assumes they're Linux-based)
2. Runs GNU autoconf test to generate macOS output
3. Copies generated output as *_macos.h
4. Updates BUILD.bazel to use dict format
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Modules that need splitting (from test failures)
FAILING_MODULES = [
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

COMPAT_DIR = Path("gnulib/tests/compatibility")


def find_test_outputs(module: str) -> Path | None:
    """Find the test outputs directory for a module."""
    # Check bazel-testlogs symlink first
    testlogs = Path("bazel-testlogs/gnulib/tests/compatibility") / module / f"{module}_test_gnu_autoconf"
    outputs = testlogs / "test.outputs"
    if outputs.exists():
        return outputs
    return None


def run_test(module: str) -> bool:
    """Run the GNU autoconf test for a module."""
    test_target = f"//gnulib/tests/compatibility/{module}:{module}_test_gnu_autoconf"
    result = subprocess.run(
        ["bazel", "test", test_target, "--test_output=errors", "--nocache_test_results"],
        capture_output=True,
        text=True,
    )
    return True  # Test will fail but outputs are still generated


def update_build_bazel(module_dir: Path) -> bool:
    """Update BUILD.bazel to use dict format for golden files."""
    build_file = module_dir / "BUILD.bazel"
    if not build_file.exists():
        return False
    
    content = build_file.read_text()
    original = content
    
    # Check if already using dict format
    if '"linux":' in content or "'linux':" in content:
        return True
    
    # Replace golden_config_h = "golden_config.h" with dict
    content = re.sub(
        r'golden_config_h\s*=\s*"golden_config\.h"',
        '''golden_config_h = {
        "linux": "golden_config_linux.h",
        "macos": "golden_config_macos.h",
    }''',
        content
    )
    
    # Replace golden_gnulib_h = "golden_subst.h" with dict
    content = re.sub(
        r'golden_gnulib_h\s*=\s*"golden_subst\.h"',
        '''golden_gnulib_h = {
        "linux": "golden_subst_linux.h",
        "macos": "golden_subst_macos.h",
    }''',
        content
    )
    
    if content != original:
        build_file.write_text(content)
        return True
    return False


def copy_generated_output(outputs_dir: Path, module_dir: Path) -> tuple[bool, bool]:
    """Copy generated config.h and subst output as macOS golden files."""
    config_copied = False
    subst_copied = False
    
    # Copy config.h
    config_h = outputs_dir / "config.h"
    if config_h.exists():
        content = config_h.read_text()
        # Strip trailing whitespace from each line
        lines = [line.rstrip() for line in content.splitlines()]
        (module_dir / "golden_config_macos.h").write_text('\n'.join(lines) + '\n')
        config_copied = True
    
    # Find and copy gnulib output (might be named gnulib_*.h)
    gnulib_files = list(outputs_dir.glob("gnulib_*.h"))
    if gnulib_files:
        content = gnulib_files[0].read_text()
        # Strip trailing whitespace from each line
        lines = [line.rstrip() for line in content.splitlines()]
        (module_dir / "golden_subst_macos.h").write_text('\n'.join(lines) + '\n')
        subst_copied = True
    
    return config_copied, subst_copied


def process_module(module: str) -> bool:
    """Process a single module to split its golden files."""
    module_dir = COMPAT_DIR / module
    if not module_dir.exists():
        print(f"  ERROR: Module directory not found")
        return False
    
    golden_config = module_dir / "golden_config.h"
    golden_subst = module_dir / "golden_subst.h"
    golden_config_linux = module_dir / "golden_config_linux.h"
    golden_subst_linux = module_dir / "golden_subst_linux.h"
    golden_config_macos = module_dir / "golden_config_macos.h"
    golden_subst_macos = module_dir / "golden_subst_macos.h"
    
    # Skip if already processed
    if golden_config_linux.exists() and golden_config_macos.exists():
        print(f"  Already processed (has linux + macos files)")
        update_build_bazel(module_dir)
        return True
    
    # Check if golden files exist
    if not golden_config.exists():
        print(f"  ERROR: golden_config.h not found")
        return False
    if not golden_subst.exists():
        print(f"  ERROR: golden_subst.h not found")
        return False
    
    # Step 1: Rename existing golden files to _linux variants
    shutil.copy2(golden_config, golden_config_linux)
    shutil.copy2(golden_subst, golden_subst_linux)
    print(f"  Created *_linux.h files")
    
    # Step 2: Run test to generate macOS output
    print(f"  Running GNU autoconf test...")
    run_test(module)
    
    # Step 3: Find and copy test output as macOS golden files
    outputs_dir = find_test_outputs(module)
    if outputs_dir:
        config_ok, subst_ok = copy_generated_output(outputs_dir, module_dir)
        if config_ok:
            print(f"  Created golden_config_macos.h from test output")
        else:
            # Fallback: copy linux as macos
            shutil.copy2(golden_config_linux, golden_config_macos)
            print(f"  Created golden_config_macos.h (copied from linux)")
        
        if subst_ok:
            print(f"  Created golden_subst_macos.h from test output")
        else:
            # Fallback: copy linux as macos
            shutil.copy2(golden_subst_linux, golden_subst_macos)
            print(f"  Created golden_subst_macos.h (copied from linux)")
    else:
        # Fallback: copy linux files as macos
        print(f"  WARNING: No test outputs found, copying linux as macos")
        shutil.copy2(golden_config_linux, golden_config_macos)
        shutil.copy2(golden_subst_linux, golden_subst_macos)
    
    # Step 4: Remove original golden files
    golden_config.unlink()
    golden_subst.unlink()
    print(f"  Removed original golden_*.h files")
    
    # Step 5: Update BUILD.bazel
    if update_build_bazel(module_dir):
        print(f"  Updated BUILD.bazel")
    
    return True


def main():
    os.chdir(Path(__file__).parent.parent)
    
    print(f"Processing {len(FAILING_MODULES)} modules...")
    print("=" * 60)
    print()
    
    success_count = 0
    failed_modules = []
    
    for i, module in enumerate(FAILING_MODULES, 1):
        print(f"[{i}/{len(FAILING_MODULES)}] {module}")
        try:
            if process_module(module):
                success_count += 1
            else:
                failed_modules.append(module)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            failed_modules.append(module)
        print()
    
    print("=" * 60)
    print(f"Done! {success_count}/{len(FAILING_MODULES)} modules processed.")
    if failed_modules:
        print(f"Failed: {', '.join(failed_modules)}")
    
    return 0 if not failed_modules else 1


if __name__ == "__main__":
    sys.exit(main())
