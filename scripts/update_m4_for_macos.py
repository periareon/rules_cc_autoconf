#!/usr/bin/env python3
"""Update m4 macros and golden files to match GNU autoconf on macOS.

Based on the diffs between GNU autoconf output and current golden files,
this script updates the m4 BUILD.bazel files and golden files.
"""

import os
import re
from pathlib import Path

# Map of module -> {define: macos_value}
# Format: expected value = golden value, actual value = GNU autoconf macOS value
MACOS_DIFFS = {
    "copy-file-range": {"HAVE_COPY_FILE_RANGE": "/* undef */"},
    "eaccess": {"eaccess": "access"},
    "fchdir": {"REPLACE_FCHDIR": "/* undef */"},
    "fstatat": {"HAVE_WORKING_FSTATAT_ZERO_FLAG": "1"},
    "fsusage": {"STAT_STATFS2_BSIZE": "1"},
    "getaddrinfo": {"HAVE_GETADDRINFO": "1"},
    "getcwd": {"HAVE_MINIMALLY_WORKING_GETCWD": "1"},
    "getdomainname": {"HAVE_GETDOMAINNAME": "1"},
    "gethostname": {"HOST_NAME_MAX": "256"},
    "getloadavg": {"N_NAME_POINTER": "1"},
    "getopt": {"__GETOPT_PREFIX": "rpl_"},
    "getpass": {"NO_INLINE_GETPASS": "1"},
    "ieee754-h": {"_GL_REPLACE_IEEE754_H": "1"},
    "isapipe": {"HAVE_FIFO_PIPES": "1", "PIPE_LINK_COUNT_MAX": "(0)"},
    "isnanf": {"HAVE_ISNANF_IN_LIBC": "1"},
    "linkat": {"LINKAT_SYMLINK_NOTSUP": "0", "LINKAT_TRAILING_SLASH_BUG": "1"},
    "lock": {"HAVE_PTHREAD_MUTEX_RECURSIVE": "1"},
    "math_h": {"HAVE_SAME_LONG_DOUBLE_AS_DOUBLE": "1"},
    "memset_explicit": {"HAVE_MEMSET_S_SUPPORTS_ZERO": "1"},
    "mkfifo": {"MKFIFO_TRAILING_SLASH_BUG": "1"},
    "mktime": {"NEED_MKTIME_INTERNAL": "1", "NEED_MKTIME_WORKING": "1", "mktime_internal": "/* undef */"},
    "mode_t": {"PROMOTED_MODE_T": "int"},
    "mountlist": {"MOUNTED_GETMNTINFO": "1"},
    "mprotect": {"HAVE_WORKING_MPROTECT": "1"},
    "msvc-inval": {"HAVE_MSVC_INVALID_PARAMETER_HANDLER": "/* undef */"},
    "nanosleep": {"HAVE_BUG_BIG_NANOSLEEP": "1"},
    "net_if_h": {"HAVE_NET_IF_H": ""},
    "netinet_in_h": {"HAVE_NETINET_IN_H": ""},
    "nl_langinfo": {"NL_LANGINFO_MTSAFE": "0", "REPLACE_NL_LANGINFO": "1"},
    "poll": {"HAVE_POLL": "/* undef */"},
    "posixver": {"DEFAULT_POSIX2_VERSION": "/* undef */"},
    "printf": {"CHECK_PRINTF_SAFE": "/* undef */"},
    "pthread-rwlock": {"PTHREAD_RWLOCK_LACKS_TIMEOUT": "1"},
    "pthread_h": {"HAVE_PTHREAD_MUTEX_ROBUST": "0"},
    "pty": {"HAVE_FORKPTY": "1", "HAVE_OPENPTY": "1"},
    "readlink": {"READLINK_TRAILING_SLASH_BUG": "1"},
    "regex": {
        "_REGEX_INCLUDE_LIMITS_H": "/* undef */",
        "_REGEX_LARGE_OFFSETS": "/* undef */",
        "re_comp": "/* undef */",
        "re_compile_fastmap": "/* undef */",
        "re_compile_pattern": "/* undef */",
        "re_exec": "/* undef */",
        "re_match": "/* undef */",
        "re_match_2": "/* undef */",
    },
    "sched_h": {"HAVE_STRUCT_SCHED_PARAM": "1"},
    "selinux-selinux-h": {"USE_SELINUX_SELINUX_H": "0"},
    "sigaltstack": {"HAVE_WORKING_SIGALTSTACK": "1"},
    "stddef_h": {"HAVE_C_UNREACHABLE": "1"},
    "strtod": {"HAVE_LDEXP_IN_LIBC": "1"},
    "strtof": {"HAVE_LDEXPF_IN_LIBC": "1"},
    "sys_times_h": {"HAVE_STRUCT_TMS": "1"},
    "sys_un_h": {"HAVE_SYS_UN_H": ""},
    "sys_utsname_h": {"HAVE_STRUCT_UTSNAME": "1"},
    "termios_h": {"SYS_IOCTL_H_DEFINES_STRUCT_WINSIZE": "0"},
    "time_h": {"TIME_H_DEFINES_STRUCT_TIMESPEC": "1", "TIME_H_DEFINES_TIME_UTC": "1"},
    "time_rz": {"HAVE_LOCALTIME_INFLOOP_BUG": "/* undef */"},
    "ttyname_r": {"HAVE_POSIXDECL_TTYNAME_R": "1"},
    "unistd_h": {"HAVE_DECL_EXECVPE": "0"},
    "utimes": {"HAVE_WORKING_UTIMES": "1"},
    "valgrind-helper": {"ENABLE_VALGRIND_SUPPORT": "0"},
    "vararrays": {"__STDC_NO_VLA__": "/* undef */"},
    "vasnprintf": {"NEED_PRINTF_DIRECTIVE_B": "1"},
}

COMPAT_DIR = Path("gnulib/tests/compatibility")


def format_golden_value(define: str, value: str) -> str:
    """Format a define/value pair for golden file."""
    if value == "/* undef */":
        return f"/* #undef {define} */"
    elif value == "":
        return f"#define {define}"
    else:
        return f"#define {define} {value}"


def update_golden_file(module: str, defines: dict[str, str]):
    """Update the golden_config_macos.h file for a module."""
    module_dir = COMPAT_DIR / module
    golden_file = module_dir / "golden_config_macos.h"
    
    if not module_dir.exists():
        print(f"  WARNING: Module directory not found: {module_dir}")
        return
    
    # Read existing golden file to preserve order and other defines
    existing_content = ""
    if golden_file.exists():
        existing_content = golden_file.read_text()
    
    # Parse existing defines
    existing_defines = {}
    for line in existing_content.split("\n"):
        # Match #define NAME VALUE or /* #undef NAME */
        define_match = re.match(r"#define\s+(\w+)(?:\s+(.*))?", line)
        undef_match = re.match(r"/\*\s*#undef\s+(\w+)\s*\*/", line)
        if define_match:
            name = define_match.group(1)
            value = define_match.group(2) or ""
            existing_defines[name] = value.strip()
        elif undef_match:
            existing_defines[undef_match.group(1)] = "/* undef */"
    
    # Update with new values
    for define, value in defines.items():
        existing_defines[define] = value
    
    # Generate new content
    lines = ["/* config.h.in - AC_DEFINE placeholders */"]
    for define, value in sorted(existing_defines.items()):
        lines.append(format_golden_value(define, value))
    
    content = "\n".join(lines) + "\n"
    golden_file.write_text(content)
    print(f"  Updated {golden_file}")


def main():
    os.chdir(Path(__file__).parent.parent)
    
    print(f"Updating {len(MACOS_DIFFS)} modules...")
    print("=" * 60)
    
    for module, defines in sorted(MACOS_DIFFS.items()):
        print(f"\n{module}:")
        update_golden_file(module, defines)
    
    print("\n" + "=" * 60)
    print("Done! Golden files updated.")
    print("\nNote: M4 BUILD.bazel files need manual updates for complex cases.")
    print("The golden files now match expected GNU autoconf output on macOS.")


if __name__ == "__main__":
    main()
