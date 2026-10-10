# M4 to Bazel Migration Guide

This guide teaches how to convert GNU Autoconf M4 macros (from `configure.ac` files) to the equivalent Bazel rules in this repository. It serves two readers:

- **Users migrating their own project.** Read [Quick Start](#quick-start) through [Platform Conditionals](#platform-conditionals). Load `autoconf` from `@rules_cc_autoconf//autoconf:autoconf.bzl` as shown there.
- **Contributors porting gnulib `.m4` modules into this repository.** Also read [Porting Strategy](#porting-strategy). Ports under `//gnulib/m4` and `//autoconf/macros` load `autoconf_cache` instead (see [`autoconf` vs `autoconf_cache`](#autoconf-vs-autoconf_cache)) and are verified by running GNU autoconf itself against the Bazel output.

**Constraint for gnulib ports in this repository:** When porting gnulib modules or fixing failing conformance tests, you may **only** modify `BUILD.bazel` files under `//gnulib/m4`. The following are **not** allowed:

- Test fixtures under `//gnulib/tests/compat/<module>`: `configure.ac`, `config.h.in`, `subst.h.in`, `test_*.c`
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
8. [Consuming Results Downstream](#consuming-results-downstream)
9. [Porting Strategy](#porting-strategy)
10. [Best Practices](#best-practices)
11. [Cross-Compilation Considerations](#cross-compilation-considerations)
12. [Complete Examples](#complete-examples)
13. [Migration Checklist](#migration-checklist)
14. [Common Pitfalls](#common-pitfalls)

---

## Quick Start

### Minimal Example

**M4 (configure.ac):**

```m4
AC_INIT([myproject], [1.0.0])
AC_CONFIG_HEADERS([config.h])
AC_CHECK_HEADERS([stdio.h stdlib.h])
AC_CHECK_FUNCS([malloc printf])
REPLACE_FOO=1
AC_SUBST([REPLACE_FOO])
AC_OUTPUT
```

**Bazel (BUILD.bazel):**

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks", "macros")
load("@rules_cc_autoconf//autoconf:autoconf_package_info.bzl", "autoconf_package_info")

autoconf_package_info(
    name = "package",
    module_bazel = "MODULE.bazel"
)

autoconf(
    name = "autoconf",
    checks = macros.AC_CHECK_HEADERS(["stdio.h", "stdlib.h"]) + [
        checks.AC_SUBST("REPLACE_FOO", 1)
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

### 3. `autoconf_package_info` Rule
Provides package metadata (`PACKAGE_NAME`, `PACKAGE_VERSION`, etc.) equivalent to `AC_INIT`.

### 4. `autoconf_linkopts` Rule (optional)
Turns **subst** values that hold linker flags (often from `AC_SEARCH_LIBS`) into **`CcInfo`** for `cc_library` / `cc_binary` / `cc_test` dependencies. See [Pattern 7b](#pattern-7b-ac_search_libs-and-autoconf_linkopts).

### `autoconf` vs `autoconf_cache`

`autoconf_cache` (from `//autoconf:autoconf_toolchain.bzl`) is identical to `autoconf` except that it does **not** resolve the `autoconf_toolchain`. Targets that the toolchain itself depends on (its `cache_deps` and `defaults`) must use it; using `autoconf` there would create a dependency cycle. Every port under `//gnulib/m4` and `//autoconf/macros` therefore loads it under the name `autoconf`:

```python
load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")
```

Projects that consume the rules load `autoconf` from `@rules_cc_autoconf//autoconf:autoconf.bzl` as in the Quick Start. Everything else in this guide (checks, `deps`, `select()`) is the same for both.

### Data Flow

```
configure.ac         →    BUILD.bazel
     ↓                         ↓
AC_INIT           →    autoconf_package_info
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
    includes = ["#include <argz.h>"],
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

#### Expression syntax

`requires` entries and `condition` strings share one grammar, evaluated by the checker:

| Form | Meaning |
|------|---------|
| `NAME` | True if the named check passed / the value is truthy |
| `!NAME` | Negation |
| `NAME==1`, `NAME!=0`, `NAME<3`, `NAME>=2` | Compare the recorded value (`==`, `!=`, `<`, `>`, `<=`, `>=`) |
| `A && B`, `A \|\| B`, `( ... )` | Boolean combination and grouping |

`NAME` is looked up across three buckets: cache variable names (`ac_cv_func_foo`, `gl_cv_...`), define names (`HAVE_FOO`) and subst / M4 variable names (`REPLACE_FOO`). The **cache name is the consistent form**: it always means that one check, so prefer it in `requires` and `condition`. A bare define or subst name is also accepted and is convenient when you want whichever check (or toolchain default) publishes that name. If a bare name resolves to two different results across the dependency graph, the build fails and lists the candidates; switch to the cache name of the check you mean. See [Name results by their cache variable](#name-results-by-their-cache-variable) for the naming table. Example from the `strerror` port:

```python
checks.AC_SUBST(
    "REPLACE_STRERROR",
    condition = "!gl_cv_header_errno_h_complete || !_gl_cv_func_strerror_0_works",
)
```

Always write `==`; a bare `=` is accepted only for backward compatibility.

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
# Singular AC_CHECK_HEADER → checks.AC_CHECK_HEADER
checks.AC_CHECK_HEADER("stdio.h", define = "HAVE_STDIO_H")

# Plural AC_CHECK_HEADERS → macros.AC_CHECK_HEADERS (preferred)
# Returns a list of checks with auto-generated HAVE_<HEADER> defines.
macros.AC_CHECK_HEADERS(["stdlib.h", "string.h", "unistd.h"])
```

**Rules:**
- Remove square brackets, add quotes
- For plural `AC_CHECK_HEADERS`, prefer `macros.AC_CHECK_HEADERS([...])` over splitting into individual `checks.AC_CHECK_HEADER` calls — the macro returns a list of checks with auto-generated `HAVE_<HEADER>` defines, matching upstream behavior with less boilerplate
- Splitting into individual `checks.AC_CHECK_HEADER` calls is only needed when you must customize per-header parameters (e.g., different `requires`, `includes`, or a non-standard `define` name)
- Add `define = "HAVE_<HEADER>"` to singular calls to create defines in `config.h`

---

### Pattern 2: Function Checks

**M4:**

```m4
AC_CHECK_FUNC([malloc])
AC_CHECK_FUNCS([printf scanf fopen])
```

**Bazel:**

```python
# Singular AC_CHECK_FUNC → checks.AC_CHECK_FUNC
checks.AC_CHECK_FUNC("malloc", define = "HAVE_MALLOC")

# Plural AC_CHECK_FUNCS → macros.AC_CHECK_FUNCS (preferred)
macros.AC_CHECK_FUNCS(["printf", "scanf", "fopen"])
```

**Rules:**
- For plural `AC_CHECK_FUNCS`, prefer `macros.AC_CHECK_FUNCS([...])`; it generates `HAVE_<FUNCTION>` defines for each entry
- Use individual `checks.AC_CHECK_FUNC` calls only when per-function customization is required
- Add `define = "HAVE_<FUNCTION>"` for singular calls
- Do **not** replace an upstream `AC_CHECK_FUNC` with a hand-written `AC_TRY_LINK` / `AC_TRY_COMPILE` because the probe's answer looks wrong on some platform. That changes the cache name (`ac_cv_func_<function>`) that downstream conditions and published overlays reference, and it hides a checker bug. Rerun with `--action_env=RULES_CC_AUTOCONF_DEBUG=debug` and file an issue with the probe output instead; `AC_CHECK_FUNC` is meant to answer exactly as GNU autoconf does

---

### Pattern 3: Type Checks

**M4:**

```m4
AC_CHECK_TYPE([size_t])
AC_CHECK_TYPES([int8_t, int64_t], [], [], [[#include <stdint.h>]])
```

**Bazel:**

```python
# Singular: without explicit includes (uses AC_INCLUDES_DEFAULT)
checks.AC_CHECK_TYPE("size_t", define = "HAVE_SIZE_T")

# Plural AC_CHECK_TYPES → macros.AC_CHECK_TYPES (preferred)
# Shared includes apply to every type in the list.
macros.AC_CHECK_TYPES(["int8_t", "int64_t"], includes = ["#include <stdint.h>"])
```

---

### Pattern 4: Declaration Checks

**M4:**

```m4
AC_CHECK_DECL([NULL], [], [], [[#include <stddef.h>]])
AC_CHECK_DECLS([execvpe, secure_getenv], [], [], [[#include <unistd.h>]])
```

**Bazel:**

```python
# Singular AC_CHECK_DECL → checks.AC_CHECK_DECL
checks.AC_CHECK_DECL("NULL", define = "HAVE_DECL_NULL", includes = ["#include <stddef.h>"])

# Plural AC_CHECK_DECLS → macros.AC_CHECK_DECLS (preferred)
# Auto-generates HAVE_DECL_<SYMBOL> defines for each entry.
macros.AC_CHECK_DECLS(["execvpe", "secure_getenv"], includes = ["#include <unistd.h>"])
```

**Note:** `AC_CHECK_DECL` differs from `AC_CHECK_FUNC` — it checks if something is *declared* (not just defined as a macro).

---

### Pattern 5: Member Checks

**M4:**

```m4
AC_CHECK_MEMBER([struct stat.st_rdev], [], [], [[#include <sys/stat.h>]])
AC_CHECK_MEMBERS([struct tm.tm_zone, struct tm.tm_gmtoff], [], [], [[#include <time.h>]])
```

**Bazel:**

```python
# Singular AC_CHECK_MEMBER → checks.AC_CHECK_MEMBER
checks.AC_CHECK_MEMBER(
    "struct stat.st_rdev",
    define = "HAVE_STRUCT_STAT_ST_RDEV",
    includes = ["#include <sys/stat.h>"],
)

# Plural AC_CHECK_MEMBERS → macros.AC_CHECK_MEMBERS (preferred)
# Auto-generates HAVE_STRUCT_<AGGREGATE>_<MEMBER> defines for each entry.
macros.AC_CHECK_MEMBERS(
    ["struct tm.tm_zone", "struct tm.tm_gmtoff"],
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

**Note:** In `rules_cc_autoconf`, `AC_CHECK_SIZEOF` / `AC_CHECK_ALIGNOF` are implemented with **compile-time** probing (not by executing a test binary). They still encode facts about the **target** toolchain, so interpret results carefully when cross-compiling (see [Cross-Compilation](#cross-compilation-considerations)).

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

### Pattern 7b: `AC_SEARCH_LIBS` and `autoconf_linkopts`

**M4:**

```m4
AC_SEARCH_LIBS([clock_gettime], [rt posix4], [], [], [AC_MSG_ERROR([...])])
# LIBS now contains -lrt when needed
```

**Bazel (check + propagate linker flags):**

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_linkopts.bzl", "autoconf_linkopts")
load("@rules_cc_autoconf//autoconf:checks.bzl", "checks")

autoconf(
    name = "autoconf",
    checks = [
        checks.AC_SEARCH_LIBS(
            "clock_gettime",
            ["rt", "posix4"],
            subst = "CLOCK_TIME_LIB",
        ),
    ],
    deps = [":package"],
)

autoconf_linkopts(
    name = "linkopts",
    vars = ["CLOCK_TIME_LIB"],
    deps = [":autoconf"],
)

cc_library(
    name = "mylib",
    srcs = ["lib.c"],
    deps = [":linkopts"],
)
```

The checker records a **subst** value: empty string if the symbol links without extra libraries, otherwise a flag such as `-lrt`. The `autoconf_linkopts` rule reads those result files and exposes **`CcInfo`** so dependents get the right link flags. On **GCC/Clang**, flags are passed via a linker response file; on **`msvc-cl`** / **`clang-cl`**, the rule uses an MSVC-compatible path (see [autoconf_linkopts](./autoconf_linkopts.md)).

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

#### When `M4_VARIABLE` is required (not just nice-to-have)

A value belongs in `M4_VARIABLE` whenever **other M4 modules read it to make decisions**, even if the producing module never calls `AC_DEFINE` or `AC_SUBST` on it.

Common signals that an M4 shell variable must be tracked with `M4_VARIABLE`:

- The producing `.m4` file sets it as a plain shell assignment (`HAVE_FOO=1`, `GL_GENERATE_FOO_H=true`) and **other** `.m4` files branch on it (`if test $HAVE_FOO = 1; then ...`).
- The variable controls whether a sibling module installs a replacement header (`GL_GENERATE_<NAME>_H`).
- The variable feeds a consumer's `requires = ["HAVE_FOO==1"]` — without `M4_VARIABLE` the requirement can never be satisfied because no check produces the value.

Examples that are easy to miss:

```python
# M4 sets `HAVE_OBSTACK=1` (or 0) and `GL_GENERATE_OBSTACK_H=true|false`
# as bare shell variables. They are NOT AC_SUBSTed, but other gnulib
# modules branch on them — so they must be tracked.
checks.M4_VARIABLE("HAVE_OBSTACK", 1),
checks.M4_VARIABLE("GL_GENERATE_OBSTACK_H", "false"),
```

**Rule of thumb:** if removing the variable from the original M4 would change the behavior of a *different* `AC_DEFUN`, it is a consumer-visible decision input and needs `M4_VARIABLE`. Use `AC_SUBST` instead only when the value is also meant to land in a generated template file (`@VAR@`).

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
    # Only check if stdio.h exists. The cache name is the consistent form;
    # the bare define name "HAVE_STDIO_H" also resolves while a single
    # check publishes it.
    requires = ["ac_cv_header_stdio_h"],
)
```

**Value-based requirements:**

```python
checks.AC_CHECK_FUNC(
    "fstat64",
    define = "HAVE_FSTAT64",
    requires = ["REPLACE_FSTAT==1"],  # Only if REPLACE_FSTAT equals "1"
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
| `AC_SEARCH_LIBS(function, libraries, ...)` | Find which library provides a symbol; set **subst** to `""` or `-lname` (use with [`autoconf_linkopts`](./autoconf_linkopts.md)) |
| `AC_TRY_COMPILE(code=..., ...)` | Try to compile custom code |
| `AC_TRY_LINK(code=..., ...)` | Try to compile and link custom code |
| `AC_DEFINE(define, value=1, ...)` | Define a preprocessor macro |
| `AC_DEFINE_UNQUOTED(define, ...)` | Define with unquoted value |
| `AC_SUBST(variable, value=1, ...)` | Create a substitution variable |
| `M4_VARIABLE(define, value=1, ...)` | Track M4 shell variables |
| `AC_PROG_CC()` | Check for C compiler |
| `AC_PROG_CXX()` | Check for C++ compiler |
| `AC_PROG_CC_C_O()` | Check `cc -c -o` support |
| `AC_FAIL(define, ...)` | Check that always fails; emit `#undef` (e.g. little-endian branch for `WORDS_BIGENDIAN`; GNU `AC_C_BIGENDIAN` is usually modeled with `select()` + `AC_DEFINE` / `AC_FAIL`) |
| `AC_C_RESTRICT()` | Check for restrict keyword |
| `AC_COMPUTE_INT(define, expression, ...)` | Compute integer at compile time (compile-time probe in this implementation) |
| `AC_CHECK_C_COMPILER_FLAG(flag, ...)` | Check C compiler flag |
| `AC_CHECK_CXX_COMPILER_FLAG(flag, ...)` | Check C++ compiler flag |
| `AC_BUILD_SETTING(target=..., name=..., ...)` | Expose a Bazel build setting (`string_flag`, `bool_flag`, `int_flag`) as a define and/or subst without compiling anything. Passed through the `build_settings` attribute of `autoconf`, **not** `checks` |

Composite GNU macros such as `AC_C_INLINE`, `AC_C_BIGENDIAN`, `AC_SYS_LARGEFILE`, `AC_FUNC_ALLOCA` or `AC_TYPE_SIZE_T` are **not** members of `checks`. They are ready-made targets under `//autoconf/macros/` (e.g. `//autoconf/macros/AC_C_INLINE`); add them to `deps`. The generated [checks reference](./checks.md) lists every member of `checks`, `macros` and `utils` with its full signature.

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

### gnulib `macros` Struct

Ports under `//gnulib/m4` also have gnulib-specific helpers. They are loaded under a different name to avoid clashing with `macros` from `checks.bzl`:

```python
load("//gnulib:macros.bzl", gl_macros = "macros")
```

| Macro | M4 equivalent |
|-------|---------------|
| `GL_CHECK_FUNCS_ANDROID(functions, includes = ...)` | `gl_CHECK_FUNCS_ANDROID` |
| `GL_CHECK_FUNCS_MACOS(functions, includes = ...)` | `gl_CHECK_FUNCS_MACOS` |
| `GL_CHECK_FUNCS_ANDROID_MACOS(functions, includes = ...)` | `gl_CHECK_FUNCS_ANDROID_MACOS` |
| `GL_CHECK_NEXT_HEADERS(headers)` | `gl_CHECK_NEXT_HEADERS` |
| `GL_NEXT_HEADERS(headers)` | `gl_NEXT_HEADERS` |
| `AC_LIB_HAVE_LINKFLAGS(...)` | `AC_LIB_HAVE_LINKFLAGS` |

### Common Parameters

Most macros support these parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | string | Cache variable name (auto-generated if omitted) |
| `define` | string/bool | Define name or `True` to use cache var name |
| `includes` | list | Include directives (e.g., `["#include <stdio.h>"]`) |
| `language` | string | `"c"` or `"cpp"` |
| `requires` | list | Dependencies that must be satisfied |
| `compile_defines` | list | Names of **results from earlier checks** (e.g. `["_GNU_SOURCE"]`); each is prepended to the probe as `#define NAME value`. Not raw macro definitions: `_GNU_SOURCE` and friends come from `//gnulib/m4/extensions` |
| `copts` | list | Extra compiler flags for this probe only (e.g. `["-std=c11"]`), appended after the toolchain's flags |
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

1. **Bazel equivalent check** — e.g. `gl_macros.GL_CHECK_FUNCS_ANDROID(["func"], includes = ["#include <header.h>"])` (loaded from `//gnulib:macros.bzl`, see [gnulib `macros`](#gnulib-macros-struct)) or `checks.AC_CHECK_FUNC("func", define = "HAVE_FUNC", subst = "HAVE_FUNC")`.
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

### Select on the OS or on the compiler?

Decide by what the M4 branch is actually about:

- **`case $host_os` branches describe the libc and OS** (`mingw* | windows*` covers both MSVC and MinGW builds). Keep those on `@platforms//os:*` as in the table above.
- **Anything about the compiler driver** selects on `@rules_cc//cc/compiler:*`: compiler flags (`/w` vs `-w`), defines spelled as options (`/D` vs `-D`), link libraries (`advapi32.lib` vs `-ladvapi32`), and probes that are really `#ifdef _MSC_VER` checks.

Clang targeting MinGW reports itself as plain `clang` on Windows, so a Windows arm that is really an MSVC arm must not be keyed on the OS. The pattern used by the integration overlays:

```python
load("@bazel_skylib//lib:selects.bzl", "selects")

# clang driving the MinGW target (rules_cc reports it as plain `clang`).
config_setting(
    name = "windows_clang",
    constraint_values = ["@platforms//os:windows"],
    flag_values = {"@rules_cc//cc/compiler": "clang"},
)

# MSVC-style drivers.
selects.config_setting_group(
    name = "msvc_like",
    match_any = [
        "@rules_cc//cc/compiler:msvc-cl",
        "@rules_cc//cc/compiler:clang-cl",
    ],
)

cc_library(
    name = "mylib",
    linkopts = select({
        ":msvc_like": ["advapi32.lib", "bcrypt.lib"],
        ":windows_clang": ["-ladvapi32", "-lbcrypt"],
        "//conditions:default": [],
    }),
)
```

Most existing gnulib ports select Windows behaviour on `@platforms//os:windows` and have been validated against MSVC only. When touching one, ask which of the two kinds its branch is.

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

## Consuming Results Downstream

Everything above produces results. A downstream `BUILD.bazel` (your project, or a Bazel Central Registry overlay) consumes them through `autoconf_hdr`, `autoconf_srcs` and `autoconf_linkopts`. Four rules matter there.

### Name results by their cache variable

Every check records one result under a unique cache name. Header templates use the define or subst name, but `requires`, `condition` and `autoconf_srcs` conditions look a name up across the cache, define and subst buckets:

| Check | Cache name |
|-------|------------|
| `AC_CHECK_HEADER("foo.h")` | `ac_cv_header_foo_h` |
| `AC_CHECK_FUNC("foo")` | `ac_cv_func_foo` |
| `AC_CHECK_DECL("foo")` | `ac_cv_have_decl_foo` |
| `AC_CHECK_TYPE("foo_t")` | `ac_cv_type_foo_t` |
| `AC_DEFINE("HAVE_FOO")` | `ac_cv_define_HAVE_FOO` |
| `AC_SUBST("REPLACE_FOO")` | `ac_cv_subst_REPLACE_FOO` |
| `M4_VARIABLE("GL_GENERATE_FOO_H")` | `GL_GENERATE_FOO_H` |
| any check with `name = "gl_cv_x"` | `gl_cv_x` |

The cache name is the consistent form and always means that one check. A bare `HAVE_FOO` or `REPLACE_FOO` also resolves, and is convenient when you want whichever check, or toolchain default, publishes that name. If a bare name resolves to two distinct results across your deps, the build fails and prints the candidates (and, when nothing matches, the full list of available names); switch to the cache name of the check you mean. Cache names and check shapes are **public API**: published overlays reference the ports' probe names, including underscore-prefixed ones such as `_gl_cv_func_opendir`, so a port must not rename a probe or change which check kind produces a name.

### Conditional sources with `autoconf_srcs`

```python
load("@rules_cc_autoconf//autoconf:autoconf_srcs.bzl", "autoconf_srcs")

autoconf_srcs(
    name = "gnulib_conditional_srcs",
    srcs = {
        # gnulib compiles a replacement when HAVE_X = 0 || REPLACE_X = 1
        "lib/readlink.c": "!ac_cv_func_readlink || REPLACE_READLINK",
        # GL_GENERATE_ERRNO_H holds the words true/false, both truthy: compare it
        "lib/strerror-override.c": "GL_GENERATE_ERRNO_H==true || !_gl_cv_func_strerror_0_works",
    },
    deps = [
        "@rules_cc_autoconf//gnulib/m4/readlink",
        "@rules_cc_autoconf//gnulib/m4/strerror",
    ],
)
```

Each source is compiled only when its expression is true; the grammar is the one in [Expression syntax](#expression-syntax). The gnulib rule is `HAVE_X = 0 || REPLACE_X = 1`; checking only `REPLACE_X` misses every libc that lacks the function (MSVC, often MinGW).

### Toolchain defaults yield to your values

The `*_H_DEFAULTS` values (`HAVE_FOO=1`, `REPLACE_FOO=0`, `GNULIB_FOO=0`) are supplied by the autoconf toolchain, not by `deps`. In `autoconf_hdr` and `autoconf_srcs` a value coming from `deps` overrides a default, so a downstream target may set any value a default would otherwise supply without a duplicate error. The duplicate check applies only between explicit checks. Two consequences:

- Do not design around the defaults targets; they are an implementation detail of this repository. Define what your project needs.
- When a later release's port starts publishing a value you set locally, your copy becomes a duplicate-result error. Comment such overrides with the port they stand in for so they are easy to remove.

The "existing defaults must not be changed" rule in [Step 6](#step-6-understand-global-defaults-vs-check-results) is about ports inside this repository, not about consumers.

### Refining platform selects

`checks` accepts `select()` keyed on any `config_setting`. Where a port had to choose a default for a fact only a runtime test could establish (see [Cross-Compilation](#cross-compilation-considerations)), your own targets can key on custom `constraint_value`s that describe your environments more precisely and set the value accordingly.

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

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
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

1. **Duplicate check errors:** A define or subst is produced by two targets, or twice within one target
   - Within one target, or between a target and its deps, the build fails with ``Define variable `X` is duplicated on `//pkg:target` `` followed by `LEFT:` / `RIGHT:` lines naming both sources (likewise `Subst variable ...`)
   - Between two dependencies it fails with `Define 'X' is defined in multiple dependencies with different result files`
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

**Key principle:** Existing defaults should NOT be changed to make a new module pass. Defaults are fine as long as nothing in the dependency graph explicitly depends on those targets with conflicting values. This rule is for ports in this repository; downstream projects may override any default (see [Toolchain defaults yield to your values](#toolchain-defaults-yield-to-your-values)).

**Conformance disagreements:** Because of this architectural difference, the GNU conformance test may report a subst that differs between Bazel and `configure`. This happens when:
- Autoconf runs a specific check (e.g., `gl_FUNC_C32RTOMB` → `HAVE_C32RTOMB=0` on macOS)
- Bazel uses the global default (e.g., `uchar_h` → `HAVE_C32RTOMB=1`)

Resolve these in order of preference:
- If the Bazel dependency graph should include those checks, add the appropriate dependency
- If a `*_h:defaults` value leaks into a test whose `configure.ac` never `AC_REQUIRE`s that `gl_*_H_DEFAULTS` macro, list the defaults target in the suite's `defaults_exclude`
- Only when the difference is inherent (GNU probes the host `PATH`, runs a test binary whose answer is machine-dependent, or two macros legitimately compute one variable differently) list the variable in the suite's `known_divergences` with the reason. It is masked from the comparison and always printed in the test log.

### Step 7: Run Tests and Iterate

```bash
# Build the module
bazel build //gnulib/m4/c32rtomb:c32rtomb

# Run the module's conformance suite
bazel test //gnulib/tests/compat/c32rtomb:all --test_output=errors
```

Every module has a suite under `//gnulib/tests/compat/<module>` declared with
`gnu_gnulib_diff_test_suite`:

```python
load("//gnulib/tests:gnu_gnulib_diff_test_suite.bzl", "gnu_gnulib_diff_test_suite")

gnu_gnulib_diff_test_suite(
    name = "c32rtomb_test",
    bazel_autoconf_target = "//gnulib/m4/c32rtomb",
    config_h_in = "config.h.in",
    configure_ac = "configure.ac",
    m4_files = ["@gnulib//:all_m4"],
    subst_h_in = "subst.h.in",
    test_c = "test_c32rtomb.c",
    # Only for inherent differences; each entry needs a reason.
    known_divergences = {
        "CLIX_PATH": "AC_PATH_PROG probes the host PATH; the Bazel port does not search the host",
    },
)
```

The `<name>_gnu_conformance` test is the oracle: it runs the pinned GNU
`aclocal`/`autoconf`/`configure` with the Bazel C++ toolchain's compiler and flags and
requires the Bazel-generated headers to match byte for byte. Fix the port until it
passes; list only intentional differences from upstream m4 in `known_divergences`.
No platform needs a checked-in expected output; the oracle produces it. See
[How the gnulib ports are tested](./gnulib.md#how-the-gnulib-ports-are-tested).

**Debugging a failing probe.** The checker does not record compiler diagnostics. To see
why a check failed, rerun with the debug variable passed to the actions:

```bash
bazel build //gnulib/m4/c32rtomb --action_env=RULES_CC_AUTOCONF_DEBUG=debug
```

The conformance test's undeclared outputs directory (`bazel-testlogs/.../test.outputs/`)
contains GNU's `config.log`, the Bazel headers, and a `config.h.diff` / `subst.h.diff`
when they differ.

### Step 8: Test on Linux (if needed)

When porting modules that have platform-specific behavior from macOS or Windows, you may need to test on Linux to verify correctness. `tools/docker_test/docker_test.sh` runs Bazel tests inside a Docker container that mirrors the GitHub CI runner:

```bash
# Default target set
./tools/docker_test/docker_test.sh

# One module's conformance suite
./tools/docker_test/docker_test.sh //gnulib/tests/compat/c32rtomb/...

# Emulated x86_64 (on an arm64 host)
./tools/docker_test/docker_test.sh --amd64 //gnulib/tests/compat/c32rtomb/...
```

#### When to use Linux testing

- **Platform-specific checks**: Modules with `select()` or Linux-only assumptions
- **Cross-platform verification**: Confirm conditionals on a real Linux toolchain
- **Test failures**: Debug Linux-only CI issues

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

For gnulib ports, `gnu_gnulib_diff_test_suite` runs GNU autoconf itself against the
Bazel output (see [How the gnulib ports are tested](./gnulib.md#how-the-gnulib-ports-are-tested)).
For your own checks, run diff tests against golden files to verify your migration
produces correct output:

```python
diff_test(
    name = "config_diff_test",
    file1 = "golden_config.h.in",
    file2 = ":config.h",
)
```

## Cross-Compilation Considerations

### Design Philosophy: Avoiding Runtime Checks

Bazel intentionally avoids runtime checks to ensure:
1. **Consistent behavior** across builds
2. **Cross-compilation support** without target system access
3. **Hermetic builds** that don't depend on the build machine's locale, environment, etc.

The rules will **never** execute a compiled test program. When M4 uses `AC_TRY_EVAL` or `AC_RUN_IFELSE`, you have two options:

1. If the fact can be established without running code, replace the probe with another check, typically `AC_TRY_COMPILE` with `#if` / `#error` on platform macros (see the `strerror` example). The result still has to match what GNU autoconf reports natively on the platforms the conformance test runs on.
2. Otherwise, choose a sane default per target platform from your understanding of it, expressed with `select()`. The M4's own cross-compiling branch (the last argument of `AC_RUN_IFELSE`, often a `case $host_os`) is a good starting point; quote its lines in the comment. Downstream projects can refine the answer for their environments with custom constraints (see [Refining platform selects](#refining-platform-selects)).

### Runtime vs compile-time checks

GNU Autoconf often uses **`AC_RUN_IFELSE`** and similar macros that **execute** test programs on the build machine. That model does not translate to cross-compilation: the binary may not run on the host when the target is different.

In **`rules_cc_autoconf`**, the checker runs **compile** and **link** actions for the **target** toolchain; it does not execute generated programs. Several macros that Autoconf sometimes implements with runtime probes are implemented here with **compile-time** logic instead:

- **`AC_CHECK_SIZEOF`**, **`AC_CHECK_ALIGNOF`**, **`AC_COMPUTE_INT`** — Probed at compile time (e.g. via compile-time techniques in the checker), **not** by running a test executable on the host. They still record properties of the **target** toolchain; when cross-compiling, ensure the configured toolchain matches the intended target and that the probe is meaningful for that environment.

**Still problematic or not modeled like GNU Autoconf:**

- **`AC_TRY_EVAL` / `AC_RUN_IFELSE`** (and M4 that assumes running the binary on the build machine) — Replace with **`select()`**, explicit **`AC_SUBST`**, or other target-appropriate defaults.
- **Locale and similar environment probes** (e.g. `gt_LOCALE_FR`) — Usually replace with **`select()`** on OS/CPU, or fixed subst values, rather than executing locale code at configure time.
- **`WORDS_BIGENDIAN` / `AC_C_BIGENDIAN`** — There is no single runtime probe; depend on **`//autoconf/macros/AC_C_BIGENDIAN`**, which models endianness with **`select()`** and **`AC_DEFINE`**, and uses **`AC_FAIL`** on branches where a define must stay absent (see **`AC_FAIL`** in the API table).

**Generally “safe” in the sense of not requiring a runnable host binary** (still subject to toolchain correctness when cross-compiling):

- `AC_CHECK_HEADER` — compile-only
- `AC_CHECK_FUNC` — link check
- `AC_CHECK_DECL` — compile-only
- `AC_CHECK_TYPE` — compile-only
- `AC_TRY_COMPILE` — compile-only
- `AC_TRY_LINK` — link check
- `AC_DEFINE` / `AC_SUBST` — no compilation probe

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

`WORDS_BIGENDIAN` is a presence macro (code tests it with `#ifdef`), so defining it to `0` on little-endian targets would be wrong. Use `AC_FAIL` on the branch where the define must stay absent: it records a failed check so the header keeps `/* #undef WORDS_BIGENDIAN */`. This is how `//autoconf/macros/AC_C_BIGENDIAN` is implemented; depend on that target rather than re-creating it:

```python
autoconf(
    name = "AC_C_BIGENDIAN",
    checks = select({
        "@platforms//cpu:ppc": [checks.AC_DEFINE("WORDS_BIGENDIAN", "1")],
        "@platforms//cpu:s390x": [checks.AC_DEFINE("WORDS_BIGENDIAN", "1")],
        "//conditions:default": [checks.AC_FAIL("WORDS_BIGENDIAN")],
    }),
)
```

---

## Complete Examples

### Example 1: Shared Probe + Conditional Subst (posix_memalign)

**Original M4:** `gnulib/m4/posix_memalign.m4`

**Bazel** (`//gnulib/m4/posix_memalign/BUILD.bazel`, abridged):

```python
"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/posix_memalign.m4"""

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")
load("//gnulib:macros.bzl", gl_macros = "macros")

# gl_CHECK_FUNCS_ANDROID([posix_memalign], [[#include <stdlib.h>]]): probe and
# config.h define, shared with gl_ALIGNALLOC and gl_PREREQ_PAGEALIGN_ALLOC, which
# call it without gl_FUNC_POSIX_MEMALIGN (see Step 4).
autoconf(
    name = "gl_CHECK_FUNCS_ANDROID_posix_memalign",
    checks = gl_macros.GL_CHECK_FUNCS_ANDROID(
        ["posix_memalign"],
        includes = ["#include <stdlib.h>"],
    ),
    visibility = ["//visibility:public"],
)

autoconf(
    name = "gl_FUNC_POSIX_MEMALIGN",
    checks = [
        # if test $ac_cv_func_posix_memalign = yes; then ... else HAVE_POSIX_MEMALIGN=0; fi
        checks.AC_SUBST(
            "HAVE_POSIX_MEMALIGN",
            condition = "ac_cv_func_posix_memalign",
            if_false = 0,
            if_true = 1,
        ),
    ] + select({
        # On OpenBSD >= 6.2 the runtime test passes.
        "@platforms//os:openbsd": [
            checks.AC_SUBST("REPLACE_POSIX_MEMALIGN", 0),
        ],
        # glibc and macOS fail the AC_RUN_IFELSE test, so an existing
        # posix_memalign is replaced; where it is absent the default 0 stays.
        "//conditions:default": [
            checks.AC_SUBST(
                "REPLACE_POSIX_MEMALIGN",
                condition = "ac_cv_func_posix_memalign",
                if_false = 0,
                if_true = 1,
            ),
        ],
    }),
    visibility = ["//visibility:public"],
    deps = [
        ":gl_CHECK_FUNCS_ANDROID_posix_memalign",
        # gl_STDLIB_H_DEFAULTS is a _DEFAULTS macro: not tracked as a dependency
        "//autoconf/macros/AC_CANONICAL_HOST",  # AC_REQUIRE([AC_CANONICAL_HOST])
        "//gnulib/m4/extensions",  # AC_REQUIRE([AC_USE_SYSTEM_EXTENSIONS])
    ],
)

# Package-level aggregator: no checks, deps on the AC_DEFUN targets.
autoconf(
    name = "posix_memalign",
    visibility = ["//visibility:public"],
    deps = [
        ":gl_FUNC_POSIX_MEMALIGN",
        "//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT",
    ],
)
```

### Example 2: Platform-Specific Module (fstat)

**Original M4:** `gnulib/m4/fstat.m4`

**Bazel:**

```python
"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/fstat.m4"""

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks")

# REPLACE_FSTAT=1 is set unconditionally on macOS (stat can return a negative
# tv_nsec) and Windows (MinGW's stat() timestamps depend on the time zone).
# Use the same check kind (AC_SUBST) on every branch of the select().
autoconf(
    name = "gl_FUNC_FSTAT",
    checks = select({
        "@platforms//os:macos": [
            checks.AC_SUBST("REPLACE_FSTAT", "1"),
        ],
        "@platforms//os:windows": [
            checks.AC_SUBST("REPLACE_FSTAT", "1"),
        ],
        "//conditions:default": [],
    }),
    visibility = ["//visibility:public"],
    deps = [
        "//gnulib/m4/fchdir:gl_FUNC_FCHDIR_FOR_CLOSE",  # gl_TEST_FCHDIR
        "//gnulib/m4/sys_stat_h",  # AC_REQUIRE([gl_SYS_STAT_H])
        "//gnulib/m4/sys_types_h",
    ],
)

# Prerequisites of lib/fstat.c and lib/stat-w32.c.
autoconf(
    name = "gl_PREREQ_FSTAT",
    visibility = ["//visibility:public"],
    deps = [
        "//gnulib/m4/stat:gl_PREREQ_STAT_W32",  # AC_REQUIRE([gl_PREREQ_STAT_W32])
        "//gnulib/m4/sys_stat_h:gl_SYS_STAT_H",
    ],
)

autoconf(
    name = "fstat",
    visibility = ["//visibility:public"],
    deps = [
        ":gl_FUNC_FSTAT",
        ":gl_PREREQ_FSTAT",
    ],
)
```

### Example 3: Conditional Checks (strerror)

**Original M4:** `gnulib/m4/strerror.m4`

**Bazel:**

```python
"""https://github.com/coreutils/gnulib/blob/1039a5f2cee3cda1c11f64a5eb3a15b2e87cd2f0/m4/strerror.m4"""

load("//autoconf:autoconf_toolchain.bzl", autoconf = "autoconf_cache")
load("//autoconf:checks.bzl", "checks", "utils")

autoconf(
    name = "gl_FUNC_STRERROR_0",
    checks = [
        # AC_CACHE_CHECK([whether strerror(0) succeeds], [gl_cv_func_strerror_0_works])
        # Upstream uses AC_RUN_IFELSE; the port fails this compile probe on exactly
        # the platforms the m4's runtime test reports as broken.
        checks.AC_TRY_COMPILE(
            name = "_gl_cv_func_strerror_0_works",
            code = utils.AC_LANG_PROGRAM(
                [
                    "#if defined(__APPLE__) && defined(__MACH__)",
                    "  #error \"macOS strerror needs REPLACE_STRERROR_0\"",
                    "#elif defined(__FreeBSD__) && __FreeBSD__ < 13",
                    "  #error \"FreeBSD < 13 strerror needs REPLACE_STRERROR_0\"",
                    "#elif defined(__sun) && defined(__SVR4)",
                    "  #error \"Solaris strerror needs REPLACE_STRERROR_0\"",
                    "#endif",
                ],
                "",
            ),
        ),
        # case "$gl_cv_func_strerror_0_works" in *yes) ;; *)
        #   REPLACE_STRERROR_0=1; AC_DEFINE([REPLACE_STRERROR_0], [1], ...) ;;
        # esac
        # A shell variable plus AC_DEFINE; the m4 never AC_SUBSTs it.
        # Gate with `requires`, not `condition` (see Core Concepts).
        checks.AC_DEFINE(
            "REPLACE_STRERROR_0",
            "1",
            requires = ["!_gl_cv_func_strerror_0_works"],
        ),
    ],
    visibility = ["//visibility:public"],
    deps = [
        "//autoconf/macros/AC_CANONICAL_HOST",
        "//autoconf/macros/AC_CHECK_INCLUDES_DEFAULT",
        "//gnulib/m4/errno_h",
        "//gnulib/m4/extensions",
        "//gnulib/m4/string_h",
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
- [ ] Use plural `macros.AC_CHECK_*` for grouped checks (split into individual `checks.AC_CHECK_*` calls only when per-entry customization is needed)
- [ ] Add `define =` parameters for config.h defines
- [ ] Add `subst =` parameters for @VAR@ substitutions
- [ ] Add `M4_VARIABLE` entries for any shell-only variables that downstream M4 modules branch on (e.g. `HAVE_<NAME>`, `GL_GENERATE_<NAME>_H`)
- [ ] Handle platform conditionals with `select()`
- [ ] Add comments referencing original M4 file and line numbers
- [ ] Add dependencies to `deps` list
- [ ] For ports in this repository: load `autoconf_cache` and run `bazel test //gnulib/tests/compat/<module>:all`; for your own project, run a `diff_test` against an expected header
- [ ] Verify no duplicate checks between direct checks and deps

---

## Common Pitfalls

| Problem | Solution |
|---------|----------|
| Translating plural macros one-by-one | Use `macros.AC_CHECK_HEADERS([...])` / `AC_CHECK_FUNCS` / `AC_CHECK_TYPES` / `AC_CHECK_DECLS` / `AC_CHECK_MEMBERS` instead of expanding into individual `checks.AC_CHECK_*` calls (split only when per-entry params differ) |
| Missing `M4_VARIABLE` for consumer-visible decisions | M4 shell variables that other modules branch on (e.g. `HAVE_OBSTACK`, `GL_GENERATE_OBSTACK_H`) must be tracked with `checks.M4_VARIABLE`, or downstream `requires = [...]` clauses will never match |
| Missing `define =` parameter | Add `define = "HAVE_FOO"` to create defines in config.h |
| Wrong define names | Follow autoconf conventions: `HAVE_<NAME>`, `SIZEOF_<TYPE>`, etc. |
| Duplicate check errors | Use `//gnulib/m4` targets instead of manual checks |
| Missing main() wrapper | `AC_TRY_COMPILE` / `AC_TRY_LINK` `code` must be a complete program, unless `includes` is also given (then `code` is the body of `main()`); or build it with `utils.AC_LANG_PROGRAM` |
| Probe fails and you cannot see why | The checker drops compiler output; rerun with `--action_env=RULES_CC_AUTOCONF_DEBUG=debug` |
| `autoconf` used inside `//gnulib/m4` or `//autoconf/macros` | Load `autoconf = "autoconf_cache"` from `//autoconf:autoconf_toolchain.bzl` to avoid a toolchain dependency cycle |
| Different check kinds for one variable across `select()` arms | Use the same kind (`AC_SUBST` vs `M4_VARIABLE` vs `AC_DEFINE`) on every branch, matching what the m4 does with it |
| MSVC flags, `.lib` libs or `/D` defines selected on `@platforms//os:windows` | Clang targeting MinGW is also `os:windows`. Select compiler-driver things on `@rules_cc//cc/compiler:*`; keep only libc/OS behaviour on the OS constraint |
| Hand-written `AC_TRY_LINK` standing in for an upstream `AC_CHECK_FUNC` | Renames the cache variable consumers reference and hides a checker bug; keep `AC_CHECK_FUNC` and report the wrong answer |
| Condition names only `REPLACE_X` for a gnulib replacement source | gnulib compiles it when `HAVE_X = 0 \|\| REPLACE_X = 1`; write `!ac_cv_func_x \|\| REPLACE_X` |
| Bare `HAVE_X` resolves to the wrong result or is ambiguous | Name the probe's cache variable (`ac_cv_func_x`, `_gl_cv_...`) or `ac_cv_define_X` / `ac_cv_subst_X` for the exact check you mean |
| String literals in defines | Use `'"string"'` (outer single, inner double quotes) |
| Platform conditionals | Use `select()` for `case "$host_os"` patterns |
| Cross-compilation failures | Validate sizeof/alignof/compute probes for the real target; replace true **run**-time Autoconf checks with `select()` or explicit substs |
| Conformance test disagrees with GNU autoconf | Add the missing dependency, or `defaults_exclude` a leaking `*_h:defaults`; use `known_divergences` only for inherent differences (see Step 6) |
| Changing defaults to fix one module | Don't change existing defaults; they may break other modules |
| Unnecessary `name` + separate `AC_DEFINE` | Use `define =` directly unless the cache variable is needed in `requires` or you need custom values |
| Using `condition` with `if_false = None` | Use `requires = ["cache_var==1"]` to gate defines; `condition` is for value selection, not gating |
| Hiding a fixable port behind `known_divergences` | Fix the port so Bazel matches GNU; a divergence entry needs a reason that explains why no port can match |
