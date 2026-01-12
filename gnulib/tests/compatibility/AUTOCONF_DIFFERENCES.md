# Autoconf Compatibility Test Results

This document tracks differences between Bazel's autoconf implementation and GNU autoconf.

## Current Status (2026-01-12)

**Test Results:**
- **1414 tests pass**
- **11 Bazel-related failures** (config.h/subst.h differences)
- **154 GNU autoconf failures** (missing m4 macro dependencies)
- **192 tests skipped** (platform-specific tests for other platforms)

## Recent Changes

### Defaults Mechanism

Added new attributes to `autoconf_hdr` and `autoconf_src` rules:

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `defaults` | `bool` | `False` | Whether to include toolchain defaults |
| `defaults_include` | `label_list` | `[]` | Only include defaults matching these labels |
| `defaults_exclude` | `label_list` | `[]` | Exclude defaults matching these labels |

**Behavior:**
- `defaults = False` (default): No toolchain defaults are included
- `defaults = True`, both lists empty: All toolchain defaults included
- `defaults_include` and `defaults_exclude` are mutually exclusive

### Quoted Substitution Values

All `@VAR@` patterns in `subst.h.in` files are now quoted (e.g., `"@VAR@"`).
This ensures valid C code even when patterns are not substituted.

### Removed AC_UNDEF

The `AC_UNDEF` macro has been removed. Instead, use:
- `defaults = False` on `autoconf_hdr` to exclude all toolchain defaults
- Empty `[]` in `select()` branches to produce no checks for specific platforms

## Remaining Bazel Failures (11)

### config_diff Failures (2)
1. `gettime_test_bazel_config_diff` - Needs investigation
2. `msvc-inval_test_bazel_config_diff_macos` - Config.h mismatch

### gnulib_diff Failures (9) - Empty Substitution Value Issues
These fail because empty substitution values produce `""` instead of no value:
- `pthread_h_test_bazel_gnulib_diff_macos`
- `sched_h_test_bazel_gnulib_diff_macos`
- `spawn_h_test_bazel_gnulib_diff`
- `stddef_h_test_bazel_gnulib_diff_macos`
- `sys_times_h_test_bazel_gnulib_diff_macos`
- `sys_utsname_h_test_bazel_gnulib_diff_macos`
- `termios_h_test_bazel_gnulib_diff_macos`
- `time_h_test_bazel_gnulib_diff_macos`
- `unistd_h_test_bazel_gnulib_diff_macos`

**Root cause:** When `AC_SUBST("VAR", "")` substitutes into `"@VAR@"`, 
the result is `""` (empty quotes) instead of nothing. GNU autoconf produces
`#define VAR` (no trailing value) for empty substitutions.

**Fix options:**
1. Update golden files to expect `""` for empty values
2. Modify resolver to strip empty quoted values

## GNU Autoconf Failures (154)

These tests fail when running GNU autoconf because of missing m4 macro 
dependencies. This is a test infrastructure issue, not a Bazel implementation 
issue.

## Test Target Structure

Tests now use `:with_defaults` targets when available to get both the
checks and the `AC_SUBST` default values. For example:

```python
gnu_gnulib_diff_test_suite(
    name = "arpa_inet_h_test",
    bazel_autoconf_target = "//gnulib/m4/arpa_inet_h:with_defaults",
    ...
)
```

The `autoconf_hdr` targets in tests use `defaults = False` to test module
output in isolation without toolchain defaults.
