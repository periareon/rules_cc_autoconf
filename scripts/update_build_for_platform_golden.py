#!/usr/bin/env python3
"""Update BUILD.bazel files to use platform-specific golden files."""

import re
from pathlib import Path

DIRECTORIES = [
    "accept4", "access", "acl", "acos", "acosf", "aligned_alloc", "arpa_inet_h",
    "asin", "asinf", "assert_h", "atan", "atan2", "atan2f", "atanf", "btowc",
    "c32rtomb", "call_once", "canonicalize", "cbrt", "cbrtf", "cbrtl", "ceil",
    "ceilf", "chown", "cnd", "copysignl", "cos", "cosf", "coshf", "dup3",
    "duplocale", "endian_h", "error_h", "euidaccess", "exp2f", "exp2l", "expf",
    "explicit_bzero", "expm1f", "fabs", "fabsf", "fabsl", "faccessat",
    "fchownat", "floorf", "floorl", "fmodf", "fmodl", "fnmatch", "fprintf-posix",
    "fpurge", "free", "frexpl", "fseeko", "ftello", "futimens", "getline",
    "getprogname", "glob", "group-member", "hypotf", "hypotl", "isfinite",
    "isinf", "isnanl", "langinfo_h", "lchown", "ldexpl", "locale_h", "log10f",
    "log1p", "log1pf", "log1pl", "logb", "logbf", "logf", "logp1", "logp1f",
    "logp1l", "lstat", "mbrlen", "mbrtoc16", "mbrtoc32", "mbrtowc", "mbsinit",
    "mbsrtowcs", "memmem", "mempcpy", "memrchr", "mkfifoat", "mntent_h",
    "monetary_h", "newlocale", "obstack", "off64_t", "openat", "pipe2",
    "posix_spawn", "powf", "pthread_mutex_timedlock", "pty_h", "random_r",
    "rawmemchr", "reallocarray", "renameat", "rintf", "sched_yield",
    "secure_getenv", "semaphore", "setenv", "sigabbrev_np", "sigdescr_np",
    "signal_h", "sinf", "sinhf", "sqrtf", "stat", "stdlib_h", "strcasestr",
    "strchrnul", "strerror", "strverscmp", "symlink", "symlinkat", "sys_cdefs_h",
    "sys_types_h", "tanf", "tanhf", "thrd", "thread", "threadlib", "timegm",
    "trunc", "truncf", "truncl", "uchar_h", "unlinkat", "vfprintf-posix",
    "wchar_h", "wcrtomb", "wcsrtombs", "wctob", "wmempcpy",
]

REPO_ROOT = Path(__file__).parent.parent
COMPATIBILITY_DIR = REPO_ROOT / "gnulib" / "tests" / "compatibility"


def update_build_file(dir_name: str) -> bool:
    """Update a single BUILD.bazel file to use platform-specific golden files."""
    build_file = COMPATIBILITY_DIR / dir_name / "BUILD.bazel"
    
    if not build_file.exists():
        print(f"  WARNING: {build_file} does not exist")
        return False
    
    content = build_file.read_text()
    original = content
    
    # Replace golden_config_h = "golden_config.h.in" with dict
    content = re.sub(
        r'golden_config_h\s*=\s*"golden_config\.h\.in"',
        '''golden_config_h = {
        "linux": "golden_config_linux.h.in",
        "macos": "golden_config_macos.h.in",
    }''',
        content
    )
    
    # Replace golden_subst_h = "golden_subst.h.in" with dict
    content = re.sub(
        r'golden_subst_h\s*=\s*"golden_subst\.h\.in"',
        '''golden_subst_h = {
        "linux": "golden_subst_linux.h.in",
        "macos": "golden_subst_macos.h.in",
    }''',
        content
    )
    
    if content != original:
        build_file.write_text(content)
        return True
    return False


def main():
    updated = 0
    for dir_name in DIRECTORIES:
        if update_build_file(dir_name):
            print(f"  Updated: {dir_name}")
            updated += 1
        else:
            print(f"  Skipped: {dir_name} (already updated or not found)")
    
    print(f"\nTotal: Updated {updated} BUILD files")


if __name__ == "__main__":
    main()
