# M4 to Bazel Migration Guide

This guide teaches how to convert GNU Autoconf M4 macros (from `configure.ac` files) to the equivalent Bazel rules in this repository.

**Important Constraint:** When porting gnulib modules or fixing failing tests, you may **only** modify `BUILD.bazel` files. The following are **not** allowed:

- Golden files (e.g. `golden_config*.h.in`, `golden_subst*.h.in`)
- `configure.ac` files
- `.bzl` files (autoconf rules, checks, macros, etc.)
- Duplicates targets (e.g. `//gnulib/tests/duplicates:gnulib` and its dependency list)

**Resolving duplicate check conflicts:** If two modules define the same check and cause a conflict when aggregated (e.g. in the duplicates test), do *not* remove either module from the duplicates list. Instead, create an isolated `autoconf` target that contains *only* the conflicting check, then add that target as a dependency to both consumers.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [Core Concepts](#core-concepts)
4. [Migration Patterns](#migration-patterns)
5. [API Reference](#api-reference)
6. [Platform Conditionals](#platform-conditionals)
7. [Dependencies and Reusable Modules](#dependencies-and-reusable-modules)
8. [Porting Strategy](#porting-strategy)
9. [Best Practices](#best-practices)
10. [Cross-Compilation Considerations](#cross-compilation-considerations)
11. [Complete Examples](#complete-examples)

---

## Quick Start

### Minimal Example

**M4 (configure.ac):**

```m4
AC_INIT([myproject], [1.0.0])
AC_CONFIG_HEADERS([config.h])
AC_CHECK_HEADERS([stdio.h stdlib.h])
AC_CHECK_FUNCS([malloc printf])
AC_OUTPUT
```

**Bazel (BUILD.bazel):**

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")
load("@rules_cc_autoconf//autoconf:package_info.bzl", "package_info")

package_info(
    name = "package",
    package_name = "myproject",
    package_version = "1.0.0",
)

autoconf(
    name = "autoconf",
    checks = [
        checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H"),
        checks.AC_CHECK_HEADER("stdlib.h", define = "HAVE_STDLIB_H"),
        checks.AC_CHECK_FUNC("malloc", define = "HAVE_MALLOC"),
        checks.AC_CHECK_FUNC("printf", define = "HAVE_PRINTF"),
    ],
    deps = [":package"],
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    deps = [":autoconf"],
)
```

---

## Architecture Overview

The migration system consists of three main components:

### 1. `autoconf` Rule
Runs compilation checks against the configured `cc_toolchain` and produces check results. Each check creates a **cache variable** (e.g., `ac_cv_header_stdio_h`) and optionally a **define** (e.g., `HAVE_STDIO_H`) or **subst** value.

### 2. `autoconf_hdr` Rule
Takes check results from `autoconf` targets and generates header files by processing templates. Supports two modes:
- `mode = "defines"` — For `config.h` files (processes `#undef` directives)
- `mode = "subst"` — For substitution files (processes `@VAR@` placeholders)

### 3. `package_info` Rule
Provides package metadata (`PACKAGE_NAME`, `PACKAGE_VERSION`, etc.) equivalent to `AC_INIT`.

### Data Flow

```
configure.ac         →    BUILD.bazel
     ↓                         ↓
AC_INIT           →    package_info
AC_CHECK_*        →    autoconf (checks = [...])
AC_CONFIG_HEADERS →    autoconf_hdr
```

---

## Core Concepts

### Cache Variables vs Defines vs Subst

Understanding the three types of outputs is crucial:

| Type | Purpose | Naming Convention | Output |
|------|---------|-------------------|--------|
| **Cache Variable** | Internal check result | `ac_cv_*` (e.g., `ac_cv_header_stdio_h`) | JSON result file |
| **Define** | C preprocessor define | `HAVE_*`, `SIZEOF_*`, etc. | `config.h` via `#define` |
| **Subst** | Template substitution | `@VAR@` patterns | `subst.h` via placeholder replacement |

**Example:**

```python
# Creates ONLY a cache variable (no define, no subst)
checks.AC_CHECK_HEADER("stdio.h")
# → Cache: ac_cv_header_stdio_h

# Creates cache variable AND define
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")
# → Cache: ac_cv_header_stdio_h
# → Define: HAVE_STDIO_H (in config.h)

# Creates cache variable, define, AND subst
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H", subst = True)
# → Cache: ac_cv_header_stdio_h
# → Define: HAVE_STDIO_H (in config.h)
# → Subst: HAVE_STDIO_H (replaces @HAVE_STDIO_H@ in subst.h)
```

### The `name` Parameter

Every check has a `name` parameter (auto-generated if not specified) that becomes the cache variable name:

```python
# Auto-generated name: "ac_cv_header_stdio_h"
checks.AC_CHECK_HEADER("stdio.h")

# Custom name
checks.AC_CHECK_HEADER("stdio.h", name = "my_custom_cache_var")
```

### The `define` Parameter

Controls whether and how a define is created:

```python
# No define (default) — only creates cache variable
checks.AC_CHECK_HEADER("stdio.h")

# Use cache variable name as define name
checks.AC_CHECK_HEADER("stdio.h", define = True)
# → Define name: ac_cv_header_stdio_h

# Explicit define name
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")
# → Define name: HAVE_STDIO_H
```

### Efficiency: `define` vs. `name` + separate `AC_DEFINE`

There are two ways to conditionally define a value based on a check result. **Prefer `define` when possible** — it's more efficient because it generates fewer internal operations.

#### Use `define` directly (preferred)

When you just need to set `HAVE_X` based on whether a check succeeds:

```python
# EFFICIENT: One check, one define
checks.AC_CHECK_HEADER("argz.h", define = "HAVE_ARGZ_H")
```

This is equivalent to M4's `AC_CHECK_HEADER([argz.h])` which automatically defines `HAVE_ARGZ_H` if the header is found.

#### Use `name` + `AC_DEFINE(requires=...)` only when necessary

Split the check and define **only** when one of these conditions applies:

1. **Other checks reference the cache variable** via `requires`:

```python
# REQUIRED: The cache variable is used by another check's `requires`
checks.AC_CHECK_HEADER("argz.h", name = "ac_cv_header_argz_h")
checks.AC_DEFINE("HAVE_ARGZ_H", requires = ["ac_cv_header_argz_h==1"])

# This check depends on the header being found
checks.AC_CHECK_TYPE(
    "error_t",
    code = "#include <argz.h>\nint main(void) { if (sizeof(error_t)) return 0; return 1; }",
    name = "ac_cv_type_error_t",
    requires = ["ac_cv_header_argz_h==1"],  # <-- Only run if header check passed
)
```

2. **Non-standard values** (use `condition` for value selection):

```python
# Use condition when you need different values based on a check
checks.AC_CHECK_FUNC("foo", name = "ac_cv_func_foo")
checks.AC_DEFINE("HAVE_FOO", condition = "ac_cv_func_foo", if_true = "yes", if_false = "no")
```

3. **Multiple outputs from one check** (both `AC_DEFINE` and `AC_SUBST`):

```python
# One check drives multiple outputs
checks.AC_CHECK_FUNC("lstat", name = "ac_cv_func_lstat")
checks.AC_DEFINE("HAVE_LSTAT", requires = ["ac_cv_func_lstat==1"])
checks.AC_SUBST("HAVE_LSTAT", condition = "ac_cv_func_lstat", if_true = "1", if_false = "0")
```

#### `requires` vs. `condition`

Use the correct parameter for the right purpose:

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `requires` | **Gate whether the check runs** — if requirements aren't met, the define is not created | `requires = ["ac_cv_func_foo==1"]` |
| `condition` | **Select between two values** — the check always runs, but produces different values | `condition = "ac_cv_func_foo", if_true = "1", if_false = "0"` |

**Anti-pattern:** Don't use `condition` with `if_false = None` to gate a define:

```python
# BAD: Using condition to gate (if_false = None behavior may change)
checks.AC_DEFINE("HAVE_FOO", condition = "ac_cv_func_foo", if_true = 1, if_false = None)

# GOOD: Use requires to gate
checks.AC_DEFINE("HAVE_FOO", requires = ["ac_cv_func_foo==1"])
```

#### Anti-pattern: Unnecessary splitting

**Don't do this** — it's wasteful and harder to read:

```python
# BAD: Unnecessary split when `define` would suffice
checks.AC_CHECK_HEADER("stdio.h", name = "ac_cv_header_stdio_h")
checks.AC_DEFINE("HAVE_STDIO_H", requires = ["ac_cv_header_stdio_h==1"])

# GOOD: Use `define` directly
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")
```

---

## Migration Patterns

### Pattern 1: Simple Header Checks

**M4:**

```m4
AC_CHECK_HEADER([stdio.h])
AC_CHECK_HEADERS([stdlib.h string.h unistd.h])
```

**Bazel:**

```python
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")
checks.AC_CHECK_HEADER("stdlib.h", define = "HAVE_STDLIB_H")
checks.AC_CHECK_HEADER("string.h", define = "HAVE_STRING_H")
checks.AC_CHECK_HEADER("unistd.h", define = "HAVE_UNISTD_H")
```

**Rules:**
- Remove square brackets, add quotes
- Split `AC_CHECK_HEADERS` (plural) into individual `AC_CHECK_HEADER` calls
- Add `define = "HAVE_<HEADER>"` to create defines in `config.h`

---

### Pattern 2: Function Checks

**M4:**

```m4
AC_CHECK_FUNC([malloc])
AC_CHECK_FUNCS([printf scanf fopen])
```

**Bazel:**

```python
checks.AC_CHECK_FUNC("malloc", define = "HAVE_MALLOC")
checks.AC_CHECK_FUNC("printf", define = "HAVE_PRINTF")
checks.AC_CHECK_FUNC("scanf", define = "HAVE_SCANF")
checks.AC_CHECK_FUNC("fopen", define = "HAVE_FOPEN")
```

**Rules:**
- Use the GNU Autoconf extern declaration pattern automatically
- Add `define = "HAVE_<FUNCTION>"` for config.h defines

---

### Pattern 3: Type Checks

**M4:**

```m4
AC_CHECK_TYPE([size_t])
AC_CHECK_TYPES([int8_t, int64_t], [], [], [[#include <stdint.h>]])
```

**Bazel:**

```python
# Without explicit includes (uses AC_INCLUDES_DEFAULT)
checks.AC_CHECK_TYPE("size_t", define = "HAVE_SIZE_T")

# With explicit includes
checks.AC_CHECK_TYPE("int8_t", define = "HAVE_INT8_T", includes = ["#include <stdint.h>"])
checks.AC_CHECK_TYPE("int64_t", define = "HAVE_INT64_T", includes = ["#include <stdint.h>"])
```

---

### Pattern 4: Declaration Checks

**M4:**

```m4
AC_CHECK_DECL([NULL], [], [], [[#include <stddef.h>]])
AC_CHECK_DECLS([execvpe], [], [], [[#include <unistd.h>]])
```

**Bazel:**

```python
checks.AC_CHECK_DECL("NULL", define = "HAVE_DECL_NULL", includes = ["#include <stddef.h>"])
checks.AC_CHECK_DECL("execvpe", define = "HAVE_DECL_EXECVPE", includes = ["#include <unistd.h>"])
```

**Note:** `AC_CHECK_DECL` differs from `AC_CHECK_FUNC` — it checks if something is *declared* (not just defined as a macro).

---

### Pattern 5: Member Checks

**M4:**

```m4
AC_CHECK_MEMBER([struct stat.st_rdev], [], [], [[#include <sys/stat.h>]])
AC_CHECK_MEMBERS([struct tm.tm_zone, struct stat.st_blocks])
```

**Bazel:**

```python
checks.AC_CHECK_MEMBER(
    "struct stat.st_rdev",
    define = "HAVE_STRUCT_STAT_ST_RDEV",
    includes = ["#include <sys/stat.h>"],
)
checks.AC_CHECK_MEMBER(
    "struct tm.tm_zone",
    define = "HAVE_STRUCT_TM_TM_ZONE",
    includes = ["#include <time.h>"],
)
```

---

### Pattern 6: Size and Alignment Checks

**M4:**

```m4
AC_CHECK_SIZEOF([int])
AC_CHECK_SIZEOF([size_t], [], [[#include <stddef.h>]])
AC_CHECK_ALIGNOF([double])
```

**Bazel:**

```python
checks.AC_CHECK_SIZEOF("int", define = "SIZEOF_INT")
checks.AC_CHECK_SIZEOF("size_t", define = "SIZEOF_SIZE_T", includes = ["#include <stddef.h>"])
checks.AC_CHECK_ALIGNOF("double", define = "ALIGNOF_DOUBLE")
```

**Warning:** These macros are NOT cross-compile friendly (see [Cross-Compilation](#cross-compilation-considerations)).

---

### Pattern 7: Library Checks

**M4:**

```m4
AC_CHECK_LIB([m], [cos])
AC_CHECK_LIB([pthread], [pthread_create])
```

**Bazel:**

```python
checks.AC_CHECK_LIB("m", "cos", define = "HAVE_LIBM")
checks.AC_CHECK_LIB("pthread", "pthread_create", define = "HAVE_LIBPTHREAD")
```

---

### Pattern 8: Compiler Flag Checks

**M4:**

```m4
AC_MSG_CHECKING([whether $CC accepts -Wall])
save_CFLAGS="$CFLAGS"
CFLAGS="$CFLAGS -Wall"
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[]])],
  [AC_MSG_RESULT([yes]); AC_DEFINE([HAVE_FLAG_WALL], [1])],
  [AC_MSG_RESULT([no])])
CFLAGS="$save_CFLAGS"
```

**Bazel:**

```python
checks.AC_CHECK_C_COMPILER_FLAG("-Wall", define = "HAVE_FLAG_WALL")
checks.AC_CHECK_CXX_COMPILER_FLAG("-std=c++17", define = "HAVE_FLAG_STD_C__17")
```

---

### Pattern 9: Custom Compile Tests

**M4:**

```m4
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[
#include <stdatomic.h>
]], [[
atomic_int x = 0;
(void)x;
]])], [AC_DEFINE([HAVE_STDATOMIC], [1])], [])
```

**Bazel:**

```python
checks.AC_TRY_COMPILE(
    code = """
#include <stdatomic.h>
int main(void) {
    atomic_int x = 0;
    (void)x;
    return 0;
}
""",
    define = "HAVE_STDATOMIC",
)
```

**Alternative using `utils.AC_LANG_PROGRAM`:**

```python
load("//autoconf:checks.bzl", "checks", "utils")

checks.AC_TRY_COMPILE(
    code = utils.AC_LANG_PROGRAM(
        ["#include <stdatomic.h>"],  # prologue
        "atomic_int x = 0; (void)x;",  # body of main()
    ),
    define = "HAVE_STDATOMIC",
)
```

---

### Pattern 10: Link Tests

**M4:**

```m4
AC_LINK_IFELSE([AC_LANG_PROGRAM([[
#include <langinfo.h>
]], [[
char* cs = nl_langinfo(CODESET);
return !cs;
]])], [AC_DEFINE([HAVE_LANGINFO_CODESET], [1])], [])
```

**Bazel:**

```python
checks.AC_TRY_LINK(
    code = utils.AC_LANG_PROGRAM(
        ["#include <langinfo.h>"],
        "char* cs = nl_langinfo(CODESET); return !cs;",
    ),
    define = "HAVE_LANGINFO_CODESET",
)
```

---

### Pattern 11: Unconditional Defines

**M4:**

```m4
AC_DEFINE([CUSTOM_VALUE], [42])
AC_DEFINE([ENABLE_FEATURE], [1])
AC_DEFINE([PROJECT_NAME], ["MyProject"])
```

**Bazel:**

```python
checks.AC_DEFINE("CUSTOM_VALUE", "42")
checks.AC_DEFINE("ENABLE_FEATURE", "1")
checks.AC_DEFINE("PROJECT_NAME", '"MyProject"')  # Note: inner quotes for string literal
```

---

### Pattern 12: Conditional Defines

**M4:**

```m4
if test "$ac_cv_func_lstat" = yes; then
  AC_DEFINE([HAVE_LSTAT], [1])
fi
```

**Bazel:**

```python
# Gate the define on the check result
checks.AC_DEFINE(
    "HAVE_LSTAT",
    requires = ["ac_cv_func_lstat==1"],  # Only define if check passed
)
```

---

### Pattern 13: Substitution Variables (AC_SUBST)

**M4:**

```m4
REPLACE_FSTAT=1
AC_SUBST([REPLACE_FSTAT])
```

**Bazel:**

```python
checks.AC_SUBST("REPLACE_FSTAT", "1")
```

For conditional subst values:

```python
checks.AC_SUBST(
    "REPLACE_STRERROR",
    condition = "_gl_cv_func_strerror_0_works",
    if_true = "0",
    if_false = "1",
)
```

---

### Pattern 14: M4 Shell Variables

Many M4 macros use shell variables (not `AC_DEFINE` calls). Use `M4_VARIABLE` to track these:

**M4:**

```m4
REPLACE_FSTAT=1
HAVE_WORKING_MKTIME=0
```

**Bazel:**

```python
checks.M4_VARIABLE("REPLACE_FSTAT", "1")
checks.M4_VARIABLE("HAVE_WORKING_MKTIME", "0")
```

---

### Pattern 15: Language Selection

**M4:**

```m4
AC_LANG_PUSH([C++])
AC_CHECK_HEADER([iostream])
AC_LANG_POP([C++])
```

**Bazel:**

```python
checks.AC_CHECK_HEADER("iostream", define = "HAVE_IOSTREAM", language = "cpp")
```

---

### Pattern 16: Dependencies with `requires`

When a check depends on a previous check's result:

**M4:**

```m4
AC_CHECK_HEADER([stdio.h])
AC_CHECK_FUNC([fopen], [], [], [[#include <stdio.h>]])
```

**Bazel:**

```python
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")
checks.AC_CHECK_FUNC(
    "fopen",
    define = "HAVE_FOPEN",
    requires = ["HAVE_STDIO_H"],  # Only check if stdio.h exists
)
```

**Value-based requirements:**

```python
checks.AC_CHECK_FUNC(
    "fstat64",
    define = "HAVE_FSTAT64",
    requires = ["REPLACE_FSTAT=1"],  # Only if REPLACE_FSTAT equals "1"
)
```

---

### Pattern 17: Compile Defines

When test code needs defines from previous checks:

```python
checks.AC_TRY_COMPILE(
    code = """
#include <sys/stat.h>
int main(void) {
    struct stat s;
    return s.st_rdev;
}
""",
    define = "HAVE_ST_RDEV",
    compile_defines = ["_GNU_SOURCE", "_DARWIN_C_SOURCE"],
)
```

---

## API Reference

### `checks` Struct — Singular Macros

| Macro | Description |
|-------|-------------|
| `AC_CHECK_HEADER(header, ...)` | Check for a header file |
| `AC_CHECK_FUNC(function, ...)` | Check for a function |
| `AC_CHECK_TYPE(type_name, ...)` | Check for a type |
| `AC_CHECK_DECL(symbol, ...)` | Check for a declaration |
| `AC_CHECK_MEMBER(aggregate.member, ...)` | Check for a struct/union member |
| `AC_CHECK_SIZEOF(type_name, ...)` | Check size of a type |
| `AC_CHECK_ALIGNOF(type_name, ...)` | Check alignment of a type |
| `AC_CHECK_LIB(library, function, ...)` | Check for a function in a library |
| `AC_TRY_COMPILE(code=..., ...)` | Try to compile custom code |
| `AC_TRY_LINK(code=..., ...)` | Try to compile and link custom code |
| `AC_DEFINE(define, value=1, ...)` | Define a preprocessor macro |
| `AC_DEFINE_UNQUOTED(define, ...)` | Define with unquoted value |
| `AC_SUBST(variable, value=1, ...)` | Create a substitution variable |
| `M4_VARIABLE(define, value=1, ...)` | Track M4 shell variables |
| `AC_PROG_CC()` | Check for C compiler |
| `AC_PROG_CXX()` | Check for C++ compiler |
| `AC_C_BIGENDIAN()` | Check byte order |
| `AC_C_INLINE()` | Check for inline keyword |
| `AC_C_RESTRICT()` | Check for restrict keyword |
| `AC_COMPUTE_INT(define, expression, ...)` | Compute integer at compile time |
| `AC_CHECK_C_COMPILER_FLAG(flag, ...)` | Check C compiler flag |
| `AC_CHECK_CXX_COMPILER_FLAG(flag, ...)` | Check C++ compiler flag |

### `macros` Struct — Plural Macros

These return lists of checks with auto-generated define names:

| Macro | Description |
|-------|-------------|
| `AC_CHECK_HEADERS(headers, ...)` | Check multiple headers |
| `AC_CHECK_FUNCS(functions, ...)` | Check multiple functions |
| `AC_CHECK_TYPES(types, ...)` | Check multiple types |
| `AC_CHECK_DECLS(symbols, ...)` | Check multiple declarations |
| `AC_CHECK_MEMBERS(members, ...)` | Check multiple struct members |

### `utils` Struct — Helper Functions

| Function | Description |
|----------|-------------|
| `AC_LANG_PROGRAM(prologue, body)` | Build program code from prologue and body |
| `AC_INCLUDES_DEFAULT` | Default includes (stdio.h, stdlib.h, etc.) |

### Common Parameters

Most macros support these parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Cache variable name (auto-generated if omitted) |
| `define` | string/bool | Define name or `True` to use cache var name |
| `includes` | list | Include directives (e.g., `["#include <stdio.h>"]`) |
| `language` | string | `"c"` or `"cpp"` |
| `requires` | list | Dependencies that must be satisfied |
| `compile_defines` | list | Defines to add before compilation |
| `condition` | string | Condition for value selection |
| `if_true` | any | Value when condition is true |
| `if_false` | any | Value when condition is false |
| `subst` | bool/string | Also create substitution variable |

---

## Platform Conditionals

### Prefer actual checks over select()+AC_DEFINE

**Do not** use `select()` to gate `AC_DEFINE` or `AC_SUBST` for feature macros (e.g. `HAVE_FOO`) when the M4 performs a real check. Use the actual check so the result reflects the toolchain/platform.

When M4 uses a check macro such as:

- `gl_CHECK_FUNCS_ANDROID([func], [[#include <header.h>]])`
- `AC_CHECK_FUNC([func])`
- `AC_CHECK_HEADER([header.h])`

**Prefer:**

1. **Bazel equivalent check** — e.g. `gl_macros.GL_CHECK_FUNCS_ANDROID(["func"], includes = ["#include <header.h>"])` or `checks.AC_CHECK_FUNC("func", define = "HAVE_FUNC", subst = "HAVE_FUNC")`.
2. **Depend on the gnulib module** that already implements the check — e.g. `deps = ["//gnulib/m4/timespec_getres:gl_FUNC_TIMESPEC_GETRES"]` instead of defining `HAVE_TIMESPEC_GETRES` via `select()`.

**Avoid:**

- `select({ "@platforms//os:linux": [checks.AC_DEFINE("HAVE_FOO", "1")], "//conditions:default": [] })` to hardcode a feature per platform.
- Duplicating the same check in multiple targets; depend on the canonical module that performs it.

This keeps config consistent with the actual build environment and avoids duplicate definitions when targets are aggregated (e.g. `//gnulib/tests/duplicates:gnulib`).

### Using `select()` for Platform-Specific Checks

**M4:**

```m4
AC_REQUIRE([AC_CANONICAL_HOST])
case "$host_os" in
  mingw* | windows*)
    REPLACE_ACCESS=1
    ;;
  darwin*)
    REPLACE_FSTAT=1
    ;;
  *)
    ;;
esac
```

**Bazel:**

```python
autoconf(
    name = "fstat",
    checks = select({
        "@platforms//os:windows": [
            checks.AC_SUBST("REPLACE_FSTAT", "1"),
        ],
        "@platforms//os:macos": [
            checks.AC_SUBST("REPLACE_FSTAT", "1"),
        ],
        "//conditions:default": [],
    }),
    visibility = ["//visibility:public"],
)
```

### Common Platform Constraints

| M4 Pattern | Bazel Constraint |
|------------|------------------|
| `mingw*`, `windows*` | `@platforms//os:windows` |
| `darwin*` | `@platforms//os:macos` |
| `linux*` | `@platforms//os:linux` |
| `freebsd*` | `@platforms//os:freebsd` |
| `openbsd*` | `@platforms//os:openbsd` |
| Default (`*`) | `"//conditions:default"` |

### Combining Platform-Specific and Common Checks

```python
autoconf(
    name = "lstat",
    checks = [
        # Common checks for all platforms
        checks.AC_CHECK_FUNC("lstat", define = "HAVE_LSTAT"),
    ] + select({
        "@platforms//os:macos": [
            checks.AC_SUBST("REPLACE_LSTAT", "1"),
        ],
        "//conditions:default": [
            checks.AC_SUBST("REPLACE_LSTAT", "0"),
        ],
    }),
    visibility = ["//visibility:public"],
)
```

---

## Dependencies and Reusable Modules

### Using Pre-built `//gnulib/m4` Targets

Many common checks are already implemented in `@rules_cc_autoconf//gnulib/m4/`. Use these to avoid duplication:

**Before (manual checks):**

```python
autoconf(
    name = "autoconf",
    checks = [
        checks.AC_CHECK_FUNC("lstat", define = "HAVE_LSTAT"),
        checks.AC_CHECK_HEADER("sys/stat.h", define = "HAVE_SYS_STAT_H"),
    ],
)
```

**After (using gnulib modules):**

```python
autoconf(
    name = "autoconf",
    checks = [
        # Only add checks not provided by gnulib modules
    ],
    deps = [
        "//gnulib/m4/lstat",      # Provides lstat checks
        "//gnulib/m4/sys_stat_h", # Provides sys/stat.h checks
    ],
)
```

### Common Patterns for gnulib Target Names

| Check Type | Target Pattern | Example |
|------------|----------------|---------|
| Function | `//gnulib/m4/<func>` | `//gnulib/m4/lstat` |
| Header | `//gnulib/m4/<header>_h` | `//gnulib/m4/sys_stat_h` |
| Type | `//gnulib/m4/<type>` | `//gnulib/m4/off_t` |

### Declaring Dependencies Between Modules

When creating reusable modules, use `deps` to express `AC_REQUIRE` relationships:

```python
# gl_FUNC_LSTAT from lstat.m4
autoconf(
    name = "lstat",
    checks = [
        checks.AC_CHECK_FUNC("lstat", define = "HAVE_LSTAT"),
    ],
    deps = [
        ":gl_FUNC_LSTAT_FOLLOWS_SLASHED_SYMLINK",  # AC_REQUIRE
        "//autoconf/macros/AC_CANONICAL_HOST",
    ],
)
```

---

## Porting Strategy

When porting a gnulib M4 module to Bazel, follow this systematic approach:

### Step 1: Fetch and Analyze the Original M4 File

First, read the original M4 file to understand its structure. The M4 file typically contains multiple `AC_DEFUN` macro definitions.

```m4
# Example: c32rtomb.m4
AC_DEFUN([gl_FUNC_C32RTOMB],           # Line 10 - Main function
[
  AC_REQUIRE([gl_UCHAR_H_DEFAULTS])    # Dependency (ignore _DEFAULTS)
  AC_REQUIRE([AC_CANONICAL_HOST])       # Dependency
  AC_REQUIRE([gl_MBRTOC32_SANITYCHECK]) # Dependency
  AC_REQUIRE([gl_C32RTOMB_SANITYCHECK]) # Dependency
  AC_REQUIRE([gl_CHECK_FUNC_C32RTOMB])  # Dependency
  ...
])

AC_DEFUN([gl_CHECK_FUNC_C32RTOMB],     # Line 59 - Helper function
[
  ...
])

AC_DEFUN([gl_C32RTOMB_SANITYCHECK],    # Line 94 - Sanity check
[
  AC_REQUIRE([gl_TYPE_CHAR32_T])
  AC_REQUIRE([gl_CHECK_FUNC_C32RTOMB])
  ...
])
```

### Step 2: Build the Dependency Graph

**Critical:** Extract all `AC_REQUIRE` statements to build the dependency graph, with one important exception:

> **Ignore `_DEFAULTS` functions.** Functions like `gl_UCHAR_H_DEFAULTS`, `gl_WCHAR_H_DEFAULTS`, etc. set initial shell variable values that get overridden by the actual check functions. In Bazel, these defaults cause duplicate check errors if both the defaults and the actual check try to set the same variable.

**Dependency extraction example:**

```
gl_FUNC_C32RTOMB requires:
  - gl_UCHAR_H_DEFAULTS    ← IGNORE (ends in _DEFAULTS)
  - AC_CANONICAL_HOST      ← Include
  - gl_MBRTOC32_SANITYCHECK ← Include (from different module)
  - gl_C32RTOMB_SANITYCHECK ← Include (local target)
  - gl_CHECK_FUNC_C32RTOMB  ← Include (local target)

gl_C32RTOMB_SANITYCHECK requires:
  - gl_TYPE_CHAR32_T        ← Include (provided by uchar_h)
  - gl_CHECK_FUNC_C32RTOMB  ← Include (local target)
```

### Step 3: Create Bazel Targets in M4 Order

Create one `autoconf` target for each `AC_DEFUN`, **in the same order as they appear in the M4 file**. This makes it easier to compare the Bazel code with the original M4.

**Important convention:** The target matching the package name (e.g., `c32rtomb` in the `c32rtomb` package) should:
- Have **no checks** (`checks = []` or omitted)
- Have **deps on all other targets** in the same package
- **Exclude `*_DEFAULTS` targets** from deps

This pattern separates the "what this module provides" (the package-named aggregator target) from "how it works" (the individual AC_DEFUN targets with checks).

```python
"""https://github.com/coreutils/gnulib/blob/.../m4/c32rtomb.m4"""

load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:checks.bzl", "checks")

# gl_FUNC_C32RTOMB - lines 10-56 (FIRST AC_DEFUN in M4)
autoconf(
    name = "gl_FUNC_C32RTOMB",
    checks = [
        checks.AC_SUBST("HAVE_C32RTOMB", condition = "gl_cv_func_c32rtomb", ...),
        checks.AC_SUBST("REPLACE_C32RTOMB", ...),
    ],
    deps = [
        "//autoconf/macros/AC_CANONICAL_HOST",
        "//gnulib/m4/mbrtoc32:gl_MBRTOC32_SANITYCHECK",
        ":gl_C32RTOMB_SANITYCHECK",
        ":gl_CHECK_FUNC_C32RTOMB",
    ],
)

# gl_CHECK_FUNC_C32RTOMB - lines 59-92 (SECOND AC_DEFUN)
autoconf(
    name = "gl_CHECK_FUNC_C32RTOMB",
    checks = [
        checks.AC_CHECK_DECL("c32rtomb", ...),
        checks.AC_TRY_LINK(name = "gl_cv_func_c32rtomb", ...),
    ],
)

# gl_C32RTOMB_SANITYCHECK - lines 94-171 (THIRD AC_DEFUN)
autoconf(
    name = "gl_C32RTOMB_SANITYCHECK",
    checks = [
        checks.AC_DEFINE("HAVE_WORKING_C32RTOMB", ...),
        checks.AC_SUBST("HAVE_WORKING_C32RTOMB", ...),
    ],
    deps = [
        "//gnulib/m4/uchar_h",
        ":gl_CHECK_FUNC_C32RTOMB",
    ],
)

# Package-level aggregator target (matches package name)
# No checks - just deps on all other targets (excluding *_DEFAULTS)
autoconf(
    name = "c32rtomb",
    visibility = ["//visibility:public"],
    deps = [
        ":gl_FUNC_C32RTOMB",
        ":gl_CHECK_FUNC_C32RTOMB",
        ":gl_C32RTOMB_SANITYCHECK",
    ],
)
```

### Step 4: Handle Shared Checks Without AC_REQUIRE

Multiple M4 files often perform the same check (e.g., `AC_CHECK_HEADERS_ONCE([utmp.h])`) without having an `AC_REQUIRE` relationship between them. In Bazel, this would cause duplicate check errors.

**Solution:** Create an isolated `autoconf` target that contains *only* the conflicting check, then add it as a dependency to both consumers. Do *not* remove modules from the duplicates test as a workaround.

**Example: utmp.h header check**

The `utmp_h` module checks for `utmp.h`:
```m4
# utmp_h.m4
AC_DEFUN([gl_UTMP_H], [
  AC_CHECK_HEADERS([utmp.h])
  ...
])
```

The `readutmp` module also checks for `utmp.h`:
```m4
# readutmp.m4
AC_DEFUN([gl_READUTMP], [
  AC_CHECK_HEADERS_ONCE([utmp.h utmpx.h])  # Same check, no AC_REQUIRE!
  ...
])
```

There's no `AC_REQUIRE([gl_UTMP_H])` in `readutmp`, but both need the same header check. Create a separate target for just the header check:

```python
# gnulib/m4/utmp_h/BUILD.bazel

# Isolated header check - can be shared by multiple modules
autoconf(
    name = "HAVE_UTMP_H",
    checks = [
        checks.AC_CHECK_HEADER("utmp.h", define = "HAVE_UTMP_H"),
    ],
    visibility = ["//visibility:public"],
)

# Main utmp_h module
autoconf(
    name = "utmp_h",
    checks = [
        # Other checks specific to utmp_h...
    ],
    deps = [
        ":HAVE_UTMP_H",  # Use the shared check
    ],
)
```

```python
# gnulib/m4/readutmp/BUILD.bazel

autoconf(
    name = "readutmp",
    checks = [
        # Other checks specific to readutmp...
    ],
    deps = [
        "//gnulib/m4/utmp_h:HAVE_UTMP_H",  # Use the same shared check
    ],
)
```

**Key principle:** Keep the isolated check target in the most semantically relevant package (e.g., the header check for `utmp.h` lives in the `utmp_h` package), and have other modules depend on it.

### Step 5: Verify and Fix Transitive Dependencies

The rules provide **built-in duplication detection**. When you build, you'll get clear error messages if a cache variable is defined in multiple places.

After building, you may find that transitive dependencies are incorrect. Common issues:

1. **Duplicate check errors:** A variable is defined both locally and in a dependency
   - The build will fail with an error like: `Cache variable 'X' is defined both locally and in dependencies`
   - Solution: Remove the local definition and let the dependency provide it
   - Or: Create a shared target (see Step 4 above)

2. **Missing values:** Expected substitution variables are not being set
   - Solution: Add the missing dependency or add the check locally

3. **Unexpected values:** A transitive dependency is providing values you don't want
   - Solution: Remove the unnecessary dependency or restructure the dependency chain

### Step 6: Understand Global Defaults vs Check Results

**Important architectural difference:** Bazel uses a shared dependency graph with global defaults, while autoconf runs each `configure.ac` independently.

In autoconf:
- `gl_UCHAR_H_DEFAULTS` sets `HAVE_C32RTOMB=1` as a shell variable
- If `gl_FUNC_C32RTOMB` is later called, it may override this to `0`
- Different `configure.ac` files may or may not call `gl_FUNC_C32RTOMB`

In Bazel:
- Global defaults (like `HAVE_C32RTOMB="1"` in `uchar_h`) are shared across all modules
- There's only ONE value for each variable in the dependency graph
- Changing a default to make one module's test pass may break other modules

**Key principle:** Existing defaults should NOT be changed to make a new module pass. Defaults are fine as long as nothing in the dependency graph explicitly depends on those targets with conflicting values.

**Subst test disagreements:** Because of this architectural difference, subst tests may sometimes disagree between Bazel and autoconf+configure. This happens when:
- Autoconf runs a specific check (e.g., `gl_FUNC_C32RTOMB` → `HAVE_C32RTOMB=0` on macOS)
- Bazel uses the global default (e.g., `uchar_h` → `HAVE_C32RTOMB=1`)

These cases should be evaluated on a case-by-case basis:
- If the golden file was generated from an autoconf run that included specific checks not in the Bazel dependency graph, the golden may need updating
- If the Bazel dependency graph should include those checks, add the appropriate dependency
- Sometimes the disagreement is acceptable - document it and move on

### Step 7: Run Tests and Iterate

```bash
# Build the module
bazel build //gnulib/m4/c32rtomb:c32rtomb

# Run compatibility tests
bazel test //gnulib/tests/compatibility/c32rtomb:all
```

Compare test output against golden files and fix any discrepancies.

### Step 8: Test on Linux (if needed)

When porting modules that have platform-specific behavior, you may need to test on Linux to verify correctness. The repository provides several scripts for running tests in Linux Docker containers:

#### Available Linux Test Scripts

**1. `test_linux_docker.sh`** — Comprehensive testing on multiple Linux distributions
- Tests on Ubuntu 22.04 and Rocky Linux 9
- Usage: `./test_linux_docker.sh [--ubuntu-only | --rocky-only]`
- Runs full test suite: `bazel test //autoconf/... //gnulib/...`
- Saves results to `docker_test_results/` directory
- Generates summary files with test statistics

**2. `test_modules.sh`** — Test specific modules
- Usage: `./test_modules.sh [--docker] module1 module2 ...`
- Without `--docker`: runs tests locally (macOS)
- With `--docker`: runs tests in Docker (Linux)
- Example: `./test_modules.sh --docker fsusage renameat`

**3. `docker_bazel.sh`** — Generic Bazel command runner
- Usage: `./docker_bazel.sh [--amd64] <bazel_command>`
- Builds/reuses Docker image and runs any Bazel command
- Supports `--amd64` flag for x86_64 emulation
- Example: `./docker_bazel.sh "test //gnulib/tests/compatibility/c32rtomb:all --test_output=errors"`

**4. `update_linux_golden.sh`** — Update Linux golden files
- Usage: `./update_linux_golden.sh module1 module2 ...`
- Builds targets in Docker, extracts `config.h` and `subst.h` outputs
- Updates `golden_config_linux.h.in` and `golden_subst_linux.h.in` files
- Use when Linux-specific golden files need updating

**5. `scripts/run_linux_tests.sh`** — Run tests and update golden files
- Builds Docker image from `Dockerfile`
- Runs tests and executes `update.py` to extract results
- Mounts `gnulib` directory so updates write directly to host

#### When to Use Linux Testing

- **Platform-specific checks**: Modules with `select()` statements for Linux-specific behavior
- **Golden file updates**: When Linux golden files (`golden_config_linux.h.in`, `golden_subst_linux.h.in`) need updating
- **Cross-platform verification**: Verify that platform conditionals work correctly
- **Test failures**: Debug Linux-specific test failures

#### Example Workflow

```bash
# Test a specific module on Linux
./test_modules.sh --docker c32rtomb

# Update Linux golden files after making changes
./update_linux_golden.sh c32rtomb

# Run full test suite on Linux
./test_linux_docker.sh --ubuntu-only
```

---

## Best Practices

### 1. Always Read the Original M4 File

Before migrating, understand what the M4 macro actually does:
- What checks does it perform?
- What defines/subst values does it create?
- What are its dependencies (`AC_REQUIRE`)?
- Are there platform-specific conditionals?

### 2. Use Cache Variable Names Consistently

Follow autoconf naming conventions:
- Headers: `ac_cv_header_<header>`
- Functions: `ac_cv_func_<function>`
- Declarations: `ac_cv_have_decl_<symbol>`
- Types: `ac_cv_type_<type>`

### 3. Prefer `//gnulib/m4` Targets

Check if a gnulib module already exists before writing manual checks. This:
- Avoids duplicate check errors
- Ensures consistent behavior
- Handles platform-specific logic

### 4. Use Meaningful Comments

Reference the original M4 file and line numbers:

```python
"""https://github.com/coreutils/gnulib/blob/635dbdcf501d52d2e42daf6b44261af9ce2dfe38/m4/lstat.m4"""

autoconf(
    name = "lstat",
    checks = [
        # AC_CHECK_FUNCS_ONCE([lstat]) - line 17
        checks.AC_CHECK_FUNC("lstat", define = "HAVE_LSTAT"),
    ],
    deps = [
        # AC_REQUIRE([gl_FUNC_LSTAT_FOLLOWS_SLASHED_SYMLINK]) - line 19
        ":gl_FUNC_LSTAT_FOLLOWS_SLASHED_SYMLINK",
    ],
)
```

### 5. Test Your Migration

Run diff tests against golden files to verify your migration produces correct output:

```python
diff_test(
    name = "config_diff_test",
    file1 = "golden_config.h.in",
    file2 = ":config.h",
)
```

### 6. Only Split Golden Files When Necessary

Golden files should only be split into platform-specific versions (`golden_config_linux.h.in`, `golden_config_macos.h.in`) when there are **genuine differences** in the expected output between platforms.

**Use a single golden file when:**
- The output is identical across all platforms
- Any platform differences are handled in the Bazel targets (not the expected output)

```python
# GOOD: Single golden file when content is the same
gnu_gnulib_diff_test_suite(
    name = "sig_atomic_t_test",
    golden_config_h = "golden_config.h.in",
    golden_subst_h = "golden_subst.h.in",
    ...
)
```

**Split golden files only when:**
- The `gnu_autoconf` test produces genuinely different outputs on different platforms
- Platform-specific defines have different values (e.g., `REPLACE_FSTAT` is `1` on macOS, `0` on Linux)

```python
# ONLY when content genuinely differs between platforms
gnu_gnulib_diff_test_suite(
    name = "fstat_test",
    golden_config_h = {
        "linux": "golden_config_linux.h.in",
        "macos": "golden_config_macos.h.in",
    },
    ...
)
```

**Anti-pattern:** Don't split golden files just because you're unsure — first verify the content differs by running `gnu_autoconf` tests on both platforms.

---

## Cross-Compilation Considerations

### Design Philosophy: Avoiding Runtime Checks

Bazel intentionally avoids runtime checks to ensure:
1. **Consistent behavior** across builds
2. **Cross-compilation support** without target system access
3. **Hermetic builds** that don't depend on the build machine's locale, environment, etc.

When M4 macros use `AC_TRY_EVAL` or `AC_RUN_IFELSE` (running compiled code at configure time), these should be replaced with `select()` statements that provide platform-specific defaults.

### Runtime vs Compile-Time Checks

Some macros require running compiled code, which doesn't work when cross-compiling:

**NOT cross-compile safe (use `select()` instead):**
- `AC_CHECK_SIZEOF` — Computes `sizeof()` by running code
- `AC_CHECK_ALIGNOF` — Computes alignment by running code
- `AC_COMPUTE_INT` — Evaluates expressions by running code
- `AC_C_BIGENDIAN` — Detects endianness at runtime
- `AC_TRY_EVAL` / `AC_RUN_IFELSE` — Runs compiled test programs
- Locale detection macros (e.g., `gt_LOCALE_FR`) — Tests locale availability at runtime

**Cross-compile safe:**
- `AC_CHECK_HEADER` — Compile-only
- `AC_CHECK_FUNC` — Link check
- `AC_CHECK_DECL` — Compile-only
- `AC_CHECK_TYPE` — Compile-only
- `AC_TRY_COMPILE` — Compile-only
- `AC_TRY_LINK` — Link check
- `AC_DEFINE` — No check, just defines

### Strategies for Cross-Compilation

1. **Use `select()` for runtime checks (preferred):**

When M4 uses runtime checks to detect platform-specific behavior, replace with `select()`:

**M4 (uses runtime locale detection):**

```m4
AC_DEFUN([gt_LOCALE_FR], [
  # Complex runtime locale testing with AC_TRY_EVAL
  # Tests various locale names by actually running setlocale()
  case "$host_os" in
    mingw* | windows*) gt_cv_locale_fr=French_France.1252 ;;
    *) gt_cv_locale_fr=fr_FR.ISO8859-1 ;;
  esac
  AC_SUBST([LOCALE_FR])
])
```

**Bazel (uses select() for platform-specific defaults):**

```python
autoconf(
    name = "gt_LOCALE_FR",
    checks = select({
        "@platforms//os:windows": [
            checks.AC_SUBST("LOCALE_FR", "French_France.1252"),
        ],
        "//conditions:default": [
            checks.AC_SUBST("LOCALE_FR", "fr_FR.ISO8859-1"),
        ],
    }),
)
```

2. **Use compile-time detection for feature checks:**

```python
checks.AC_TRY_COMPILE(
    code = """
#if defined(__APPLE__) && defined(__MACH__)
  #error "macOS detected"
#endif
int main(void) { return 0; }
""",
    define = "_IS_MACOS",
)
```

3. **Use platform selects for known values:**

```python
autoconf(
    name = "endian",
    checks = select({
        "@platforms//cpu:x86_64": [
            checks.AC_DEFINE("WORDS_BIGENDIAN", "0"),
        ],
        "@platforms//cpu:arm64": [
            checks.AC_DEFINE("WORDS_BIGENDIAN", "0"),  # ARM64 is little-endian
        ],
        "//conditions:default": [],
    }),
)
```

---

## Complete Examples

### Example 1: Simple Module (posix_memalign)

**Original M4:** `gnulib/m4/posix_memalign.m4`

**Bazel:**

```python
"""https://github.com/coreutils/gnulib/blob/635dbdcf501d52d2e42daf6b44261af9ce2dfe38/m4/posix_memalign.m4"""

load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:checks.bzl", "checks")

autoconf(
    name = "posix_memalign",
    checks = [
        checks.AC_CHECK_FUNC("posix_memalign", define = "HAVE_POSIX_MEMALIGN"),
        checks.AC_SUBST("REPLACE_POSIX_MEMALIGN", "1"),
    ],
    visibility = ["//visibility:public"],
    deps = [
        "//autoconf/macros/AC_CANONICAL_HOST",
        "//gnulib/m4/extensions",
        "//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT",
    ],
)
```

### Example 2: Platform-Specific Module (fstat)

**Original M4:** `gnulib/m4/fstat.m4`

**Bazel:**

```python
"""https://github.com/coreutils/gnulib/blob/635dbdcf501d52d2e42daf6b44261af9ce2dfe38/m4/fstat.m4"""

load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:checks.bzl", "checks")

autoconf(
    name = "fstat",
    checks = select({
        "@platforms//os:macos": [
            # macOS: stat can return negative tv_nsec
            checks.AC_SUBST("REPLACE_FSTAT", "1"),
        ],
        "@platforms//os:windows": [
            # Windows: stat returns timezone-affected timestamps
            checks.M4_VARIABLE("REPLACE_FSTAT", "1"),
        ],
        "//conditions:default": [],
    }),
    visibility = ["//visibility:public"],
    deps = [
        "//gnulib/m4/sys_stat_h",
        "//gnulib/m4/sys_types_h",
    ],
)
```

### Example 3: Conditional Checks (strerror)

**Original M4:** `gnulib/m4/strerror.m4`

**Bazel:**

```python
"""https://github.com/coreutils/gnulib/blob/635dbdcf501d52d2e42daf6b44261af9ce2dfe38/m4/strerror.m4"""

load("//autoconf:autoconf.bzl", "autoconf")
load("//autoconf:checks.bzl", "checks", "utils")

autoconf(
    name = "gl_FUNC_STRERROR_0",
    checks = [
        # Compile-time platform detection for strerror(0) behavior
        checks.AC_TRY_COMPILE(
            name = "_gl_cv_func_strerror_0_works",
            code = utils.AC_LANG_PROGRAM(
                [
                    "#if defined(__APPLE__) && defined(__MACH__)",
                    "  #error \"macOS strerror needs replacement\"",
                    "#endif",
                ],
                "",
            ),
        ),
        # Set REPLACE_STRERROR_0 if check fails
        checks.AC_DEFINE(
            "REPLACE_STRERROR_0",
            condition = "_gl_cv_func_strerror_0_works",
            if_false = 1,
        ),
    ] + select({
        "@platforms//os:macos": [
            checks.AC_SUBST("REPLACE_STRERROR_0", "1"),
        ],
        "//conditions:default": [
            checks.AC_SUBST("REPLACE_STRERROR_0", "0"),
        ],
    }),
    visibility = ["//visibility:public"],
    deps = [
        "//autoconf/macros/AC_CANONICAL_HOST",
        "//gnulib/m4/errno_h",
        "//gnulib/m4/extensions",
    ],
)
```

### Example 4: Link Test with AC_LANG_PROGRAM

```python
load("//autoconf:checks.bzl", "checks", "utils")

autoconf(
    name = "autoconf",
    checks = [
        checks.AC_TRY_LINK(
            code = utils.AC_LANG_PROGRAM(
                [
                    "/* Prologue: includes and declarations */",
                    "#include <langinfo.h>",
                ],
                "/* Body: code inside main() */\n"
                "char* cs = nl_langinfo(CODESET);\n"
                "return !cs;",
            ),
            define = "HAVE_LANGINFO_CODESET",
        ),
    ],
)
```

---

## Migration Checklist

When migrating an M4 file, follow this checklist:

- [ ] Read and understand the original M4 file
- [ ] Check for existing `//gnulib/m4` targets that provide needed checks
- [ ] Identify all `AC_REQUIRE` dependencies
- [ ] Map each M4 macro to its Bazel equivalent
- [ ] Convert argument syntax (brackets → quotes)
- [ ] Split plural macros into individual calls
- [ ] Add `define =` parameters for config.h defines
- [ ] Add `subst =` parameters for @VAR@ substitutions
- [ ] Handle platform conditionals with `select()`
- [ ] Add comments referencing original M4 file and line numbers
- [ ] Add dependencies to `deps` list
- [ ] Test with diff tests against golden files
- [ ] Verify no duplicate checks between direct checks and deps

---

## Common Pitfalls

| Problem | Solution |
|---------|----------|
| Forgetting to split plural macros | `AC_CHECK_HEADERS([a b c])` needs 3 separate `AC_CHECK_HEADER` calls |
| Missing `define =` parameter | Add `define = "HAVE_FOO"` to create defines in config.h |
| Wrong define names | Follow autoconf conventions: `HAVE_<NAME>`, `SIZEOF_<TYPE>`, etc. |
| Duplicate check errors | Use `//gnulib/m4` targets instead of manual checks |
| Missing main() wrapper | `AC_TRY_COMPILE` code must include `int main(void) { ... }` or use `utils.AC_LANG_PROGRAM` |
| String literals in defines | Use `'"string"'` (outer single, inner double quotes) |
| Platform conditionals | Use `select()` for `case "$host_os"` patterns |
| Cross-compilation failures | Avoid `AC_CHECK_SIZEOF` and similar runtime checks |
| Subst test disagrees with autoconf | Evaluate case-by-case: may be due to global defaults vs specific checks (see Step 6) |
| Changing defaults to fix one module | Don't change existing defaults; they may break other modules |
| Unnecessary `name` + separate `AC_DEFINE` | Use `define =` directly unless the cache variable is needed in `requires` or you need custom values |
| Using `condition` with `if_false = None` | Use `requires = ["cache_var==1"]` to gate defines; `condition` is for value selection, not gating |
| Unnecessary golden file split | Only split into `_linux.h.in` / `_macos.h.in` when content genuinely differs between platforms |