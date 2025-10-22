# Migrating from M4 macros

GNU Autoconf uses M4 macros in `configure.ac` files to generate configuration
checks. This module provides equivalent Starlark functions that produce the same
results but are integrated with Bazel's build system.

## Key Differences

1. **Syntax**: M4 uses square brackets `[]` for arguments; Starlark uses parentheses `()` and quotes `""`
2. **Multiple items**: M4 macros like `AC_CHECK_HEADERS([h1 h2 h3])` become multiple separate calls
3. **Action blocks**: M4 uses action blocks `[action-if-found]` and `[action-if-not-found]`; Bazel uses `if_true`/`if_found` and `if_false`/`if_not_found` parameters (see Pattern 11c)
4. **Language selection**: M4 uses `AC_LANG_PUSH([C++])`; Bazel uses `language = "cpp"` parameter
5. **Custom code**: M4 uses `AC_LANG_PROGRAM([includes], [body])`; Bazel uses `code` or `file` parameters
6. **Dependencies**: M4 uses nested macro calls; Bazel uses explicit `requires` parameter or `if_true`/`if_false` for conditional execution

## Migration Patterns

### Pattern 1: Simple Header/Function/Type Checks

**M4:**

```m4
AC_CHECK_HEADER([stdio.h])
AC_CHECK_FUNC([malloc])
AC_CHECK_TYPE([size_t])
```

**Bazel:**

```python
macros.AC_CHECK_HEADER("stdio.h")
macros.AC_CHECK_FUNC("malloc")
macros.AC_CHECK_TYPE("size_t")
```

**Rules:**

- Remove square brackets, add quotes
- Convert to function call: `macros.MACRO_NAME("argument")`
- Define names are auto-generated (e.g., `HAVE_STDIO_H`, `HAVE_MALLOC`, `HAVE_SIZE_T`)

### Pattern 2: Multiple Items in One Call

**M4:**

```m4
AC_CHECK_HEADERS([stdio.h stdlib.h string.h])
AC_CHECK_FUNCS([malloc free printf])
AC_CHECK_TYPES([int8_t int64_t])
```

**Bazel:**

```python
macros.AC_CHECK_HEADER("stdio.h")
macros.AC_CHECK_HEADER("stdlib.h")
macros.AC_CHECK_HEADER("string.h")
macros.AC_CHECK_FUNC("malloc")
macros.AC_CHECK_FUNC("free")
macros.AC_CHECK_FUNC("printf")
macros.AC_CHECK_TYPE("int8_t")
macros.AC_CHECK_TYPE("int64_t")
```

**Rules:**

- Split space-separated lists into individual macro calls
- Each item becomes a separate function call

### Pattern 3: Custom Define Names

**M4:**

```m4
AC_CHECK_HEADER([sys/stat.h], [AC_DEFINE([HAVE_SYS_STAT_H], [1])])
AC_CHECK_FUNC([printf], [AC_DEFINE([HAVE_PRINTF], [1])])
```

**Bazel:**

```python
macros.AC_CHECK_HEADER("sys/stat.h", define = "HAVE_SYS_STAT_H")
macros.AC_CHECK_FUNC("printf", define = "HAVE_PRINTF")
```

**Rules:**

- Extract define name from `AC_DEFINE` in action block
- Use `define` parameter to override auto-generated name
- If no custom define, omit the parameter (auto-generation handles it)

### Pattern 4: Headers for Type/Symbol Checks

**M4:**

```m4
AC_CHECK_TYPE([int64_t], [], [], [[#include <stdint.h>]])
AC_CHECK_SYMBOL([NULL], [], [], [[#include <stddef.h>]])
AC_CHECK_TYPES([int8_t, int64_t], [], [], [[#include <stdint.h>]])
```

**Bazel:**

```python
macros.AC_CHECK_TYPE("int64_t", code = "#include <stdint.h>")
macros.AC_CHECK_SYMBOL("NULL", code = "#include <stddef.h>")
macros.AC_CHECK_TYPE("int8_t", code = "#include <stdint.h>")
macros.AC_CHECK_TYPE("int64_t", code = "#include <stdint.h>")
```

**Rules:**

- Extract include directives from the 4th argument (includes parameter)
- Use `code` parameter with the include statement
- For `AC_CHECK_TYPES` with multiple types, split and repeat the code for each

**Note on Implicit Defines:**

In GNU autoconf, `AC_CHECK_TYPES` may implicitly define `HAVE_*` values for headers
it checks. For example, when checking types that require `<stdint.h>`, autoconf may
also check for `<inttypes.h>` and define `HAVE_INTTYPES_H`. Similarly, `STDC_HEADERS`
is typically defined by `AC_HEADER_STDC` which checks for standard C89 headers.

In rules_cc_autoconf, these implicit checks are not performed automatically. If your
template file (e.g., `config.h.in`) contains `#undef` statements for these defines,
you must explicitly add the corresponding checks to your BUILD file:

```python
# If checking types that require stdint.h, also check for inttypes.h
macros.AC_CHECK_HEADER("inttypes.h")

# If STDC_HEADERS is in your template, define it explicitly
macros.AC_DEFINE("STDC_HEADERS", "1")
```

### Pattern 5: Custom Compilation Tests

**M4:**

```m4
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[
#include <stdatomic.h>
]], [[
atomic_int x = 0;
(void)x;
return 0;
]])], [AC_DEFINE([HAVE_STDATOMIC], [1])], [])
```

**Bazel:**

```python
macros.AC_TRY_COMPILE(
    code = """\
#include <stdatomic.h>
int main(void) { atomic_int x = 0; (void)x; return 0; }
""",
    define = "HAVE_STDATOMIC",
)
```

**Rules:**

- `AC_COMPILE_IFELSE` → `macros.AC_TRY_COMPILE`
- Combine `AC_LANG_PROGRAM` includes and body into single `code` string
- Extract define from action block
- Add `int main(void) { ... }` wrapper if not present
- Use `\n` for line breaks in code string

### Pattern 6: Language Selection

**M4:**

```m4
AC_LANG_PUSH([C++])
AC_CHECK_HEADER([iostream])
AC_LANG_POP([C++])
```

**Bazel:**

```python
macros.AC_CHECK_HEADER("iostream", language = "cpp")
```

**Rules:**

- `AC_LANG_PUSH([C++])` / `AC_LANG_POP([C++])` → add `language = "cpp"` to all macros in between
- `AC_LANG_PUSH([C])` is the default, so usually no parameter needed
- Apply `language = "cpp"` to all macros between `AC_LANG_PUSH([C++])` and `AC_LANG_POP([C++])`

### Pattern 7: Library Checks

**M4:**

```m4
AC_CHECK_LIB([m], [cos])
AC_CHECK_LIB([pthread], [pthread_create])
```

**Bazel:**

```python
macros.AC_CHECK_LIB("m", "cos")
macros.AC_CHECK_LIB("pthread", "pthread_create")
```

**Rules:**

- First argument is library name (without `-l` prefix)
- Second argument is function name to check
- Define auto-generated as `HAVE_LIB<LIBRARY>` (e.g., `HAVE_LIBM`)

### Pattern 8: Compiler Flag Checks

**M4:**

```m4
AC_MSG_CHECKING([whether $CC accepts -Wall])
save_CFLAGS="$CFLAGS"
CFLAGS="$CFLAGS -Wall"
AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[]], [[]])],
  [AC_MSG_RESULT([yes])
   AC_DEFINE([HAVE_FLAG_WALL], [1])],
  [AC_MSG_RESULT([no])])
CFLAGS="$save_CFLAGS"
```

**Bazel:**

```python
macros.AC_CHECK_C_COMPILER_FLAG("-Wall", define = "HAVE_FLAG_WALL")
```

**Rules:**

- Pattern of `AC_MSG_CHECKING` + flag manipulation + `AC_COMPILE_IFELSE` → `AC_CHECK_C_COMPILER_FLAG`
- Extract flag from `CFLAGS` or `CXXFLAGS` assignment
- Extract define from action block
- Use `AC_CHECK_C_COMPILER_FLAG` for C, `AC_CHECK_CXX_COMPILER_FLAG` for C++

### Pattern 9: Size and Alignment Checks

**M4:**

```m4
AC_CHECK_SIZEOF([int])
AC_CHECK_SIZEOF([size_t], [], [[#include <stddef.h>]])
AC_CHECK_ALIGNOF([double])
```

**Bazel:**

```python
macros.AC_CHECK_SIZEOF("int")
macros.AC_CHECK_SIZEOF("size_t", headers = ["stddef.h"])
macros.AC_CHECK_ALIGNOF("double")
```

**Rules:**

- Direct translation with type name
- If includes provided, use `headers` parameter (list of header names without `<>` or quotes)
- Define auto-generated as `SIZEOF_<TYPE>` or `ALIGNOF_<TYPE>`

### Pattern 10: Unconditional Defines (AC_DEFINE)

**M4:**

```m4
AC_DEFINE([CUSTOM_VALUE], [42])
AC_DEFINE([ENABLE_FEATURE], [1])
AC_DEFINE([PROJECT_NAME], ["MyProject"])
```

**Bazel:**

```python
macros.AC_DEFINE("CUSTOM_VALUE", "42")
macros.AC_DEFINE("ENABLE_FEATURE", "1")
macros.AC_DEFINE("PROJECT_NAME", '"MyProject"')
```

**Rules:**

- Direct translation
- Second argument becomes `value` parameter
- Preserve quotes in string values (use `'"string"'` for string literals)

### Pattern 10b: M4 Shell Variables (M4_DEFINE)

Many M4 macros use shell variables like `REPLACE_*` or `HAVE_*` that are set via
assignment (not `AC_DEFINE`). These are tracked separately using `M4_DEFINE`.

**M4:**

```m4
# These are shell variable assignments, NOT AC_DEFINE calls
REPLACE_FSTAT=1
HAVE_WORKING_MKTIME=0
```

**Bazel:**

```python
# Use M4_DEFINE for shell variables
macros.M4_DEFINE("REPLACE_FSTAT", "1")
macros.M4_DEFINE("HAVE_WORKING_MKTIME", "0")
```

**Rules:**

- Use `macros.M4_DEFINE` for M4 shell variable assignments (e.g., `REPLACE_*=1`)
- Use `macros.AC_DEFINE` only for actual `AC_DEFINE()` calls in the M4 source
- Both produce the same result, but `M4_DEFINE` helps track the semantic difference

### Pattern 11: Conditional Dependencies

**M4:**

```m4
AC_CHECK_HEADER([stdio.h])
AC_CHECK_FUNC([fopen], [], [], [[#include <stdio.h>]])
```

**Bazel:**

```python
macros.AC_CHECK_HEADER("stdio.h")
macros.AC_CHECK_FUNC("fopen", requires = ["HAVE_STDIO_H"])
```

**Rules:**

- If a check includes a header that was previously checked, add `requires` parameter
- Use the auto-generated define name from the previous check
- Extract include dependencies and map to corresponding `HAVE_*` defines

### Pattern 11b: Value-Based Requirements

Sometimes a check should only run if a previous check resulted in a specific value.
Use `"DEFINE=value"` syntax in the `requires` list.

**M4:**

```m4
if test $REPLACE_FSTAT = 1; then
  # Only run this check if fstat needs replacement
  AC_CHECK_FUNC([fstat64])
fi
```

**Bazel:**

```python
macros.AC_CHECK_FUNC(
    "fstat64",
    requires = ["REPLACE_FSTAT=1"],  # Only if REPLACE_FSTAT equals "1"
)
```

**Rules:**

- `requires = ["FOO"]` - check succeeds if FOO succeeded (any value)
- `requires = ["FOO=1"]` - check succeeds if FOO succeeded AND has value "1"
- Useful for conditional checks based on platform detection results

### Pattern 11c: Conditional Action Blocks (`if_true`/`if_found`)

Many M4 macros support action blocks that run conditionally based on whether a check succeeds or fails. The `[action-if-found]` or `[action-if-true]` block runs when the check succeeds, and `[action-if-not-found]` or `[action-if-false]` runs when it fails.

**M4:**

```m4
AC_CHECK_HEADER([stdio.h],
    [
        # Action if found: run additional checks
        AC_CHECK_FUNC([printf])
        AC_CHECK_FUNC([fprintf])
    ],
    [
        # Action if not found: define fallback
        AC_DEFINE([NO_STDIO], [1])
    ]
)

AC_CHECK_FUNCS([ffs],
    [
        # If ffs() exists, check if it's declared in strings.h
        AC_CHECK_DECL([ffs], 
            [AC_DEFINE([STRINGS_H_DECLARES_FFS], [1])],
            [],
            [[#include <strings.h>]]
        )
    ]
)
```

**Bazel:**

```python
macros.AC_CHECK_HEADER(
    "stdio.h",
    if_true = [
        # These checks run only if stdio.h exists
        macros.AC_CHECK_FUNC("printf"),
        macros.AC_CHECK_FUNC("fprintf"),
    ],
    if_false = [
        # This runs only if stdio.h doesn't exist
        macros.AC_DEFINE("NO_STDIO", "1"),
    ],
)

macros.AC_CHECK_FUNC(
    "ffs",
    if_true = [
        # This runs only if ffs() exists
        macros.AC_CHECK_DECL(
            "ffs",
            define = "STRINGS_H_DECLARES_FFS",
            headers = ["strings.h"],
        ),
    ],
)
```

**Rules:**

- `if_true` and `if_found` are aliases - use either one (they're equivalent)
- `if_false` and `if_not_found` are aliases - use either one (they're equivalent)
- You cannot provide both `if_true` and `if_found` (same for `if_false`/`if_not_found`)
- Action blocks become lists of macro calls passed to `if_true`/`if_false` parameters
- Nested checks in `if_true` automatically receive a `requires` dependency on the parent check's define
- Nested checks in `if_false` automatically receive a `requires` dependency on `!PARENT_DEFINE` (negated)
- The flattening process ensures nested checks are executed as separate Bazel actions with proper dependencies
- Use this pattern instead of manually adding `requires` when you want conditional execution based on a check result

**Relationship to `requires`:**

The `if_true`/`if_false` parameters are syntactic sugar that automatically generate `requires` dependencies. The following are equivalent:

```python
# Using if_true (preferred for conditional checks)
macros.AC_CHECK_HEADER(
    "stdio.h",
    if_true = [
        macros.AC_CHECK_FUNC("printf"),
    ],
)

# Equivalent manual requires
macros.AC_CHECK_HEADER("stdio.h")
macros.AC_CHECK_FUNC(
    "printf",
    requires = ["HAVE_STDIO_H"],
)
```

However, `if_true`/`if_false` is cleaner when you have conditional action blocks because it clearly shows the intent and keeps related checks together.

**Advanced: Nested Conditionals:**

You can nest conditional checks to create complex dependency chains:

```python
macros.AC_CHECK_HEADER(
    "stdio.h",
    if_true = [
        macros.AC_CHECK_FUNC(
            "printf",
            if_true = [
                # This runs only if both stdio.h exists AND printf exists
                macros.AC_DEFINE("HAVE_PRINTF_IN_STDIO", "1"),
            ],
        ),
    ],
)
```

### Pattern 12: Member Checks

**M4:**

```m4
AC_CHECK_MEMBER([struct stat.st_rdev], [AC_DEFINE([HAVE_STRUCT_STAT_ST_RDEV], [1])], [], [[#include <sys/stat.h>]])
```

**Bazel:**

```python
macros.AC_CHECK_MEMBER("struct stat", "st_rdev", headers = ["sys/stat.h"])
```

**Rules:**

- Split `struct/union.member` into two arguments: aggregate and member
- Extract includes to `headers` parameter
- Define auto-generated as `HAVE_<AGGREGATE>_<MEMBER>`

### Pattern 13: Declaration Checks

**M4:**

```m4
AC_CHECK_DECL([NULL], [AC_DEFINE([HAVE_DECL_NULL], [1])], [], [[#include <stddef.h>]])
```

**Bazel:**

```python
macros.AC_CHECK_DECL("NULL", headers = ["stddef.h"])
```

**Rules:**

- Similar to `AC_CHECK_SYMBOL` but checks for declarations (not just definitions)
- Use `headers` parameter for includes
- Define auto-generated as `HAVE_DECL_<SYMBOL>`

### Pattern 13b: Conditional Declaration Checks with Function Pre-checks

Sometimes you need to check if a function exists first, then check if it's declared in a specific header. The original M4 code uses shell conditionals to chain these checks.

**M4:**

```m4
if test "$ac_cv_func_ether_hostton" = yes; then
    #
    # OK, we have ether_hostton().  Is it declared in <net/ethernet.h>?
    #
    # This test fails if we don't have <net/ethernet.h> or if we do
    # but it doesn't declare ether_hostton().
    #
    AC_CHECK_DECL(ether_hostton,
        [
            AC_DEFINE(NET_ETHERNET_H_DECLARES_ETHER_HOSTTON,,
                [Define to 1 if net/ethernet.h declares `ether_hostton'])
        ],,
        [
#include <net/ethernet.h>
        ])
fi
```

**Bazel:**

```python
macros.AC_CHECK_FUNC("ether_hostton"),
macros.AC_CHECK_DECL(
    "ether_hostton",
    define = "NET_ETHERNET_H_DECLARES_ETHER_HOSTTON_TEST",
    headers = ["net/ethernet.h"],
    requires = ["HAVE_ETHER_HOSTTON=1"],
),
macros.AC_DEFINE(
    "NET_ETHERNET_H_DECLARES_ETHER_HOSTTON",
    requires = ["NET_ETHERNET_H_DECLARES_ETHER_HOSTTON_TEST=1"],
),
```

**Rules:**

- First check if the function exists with `AC_CHECK_FUNC`
- Then check if it's declared in the header with `AC_CHECK_DECL`, using a temporary define name (e.g., `*_TEST`)
- Use `requires = ["HAVE_ETHER_HOSTTON=1"]` on the `AC_CHECK_DECL` to ensure it only runs if the function check succeeded
- Finally, use `AC_DEFINE` with `requires = ["*_TEST=1"]` to conditionally define the final macro only if the declaration check passed
- The `requires` chains ensure defines are not created unless they're supposed to, matching the original M4 shell conditional behavior

### Pattern 13c: Reusing Common Checks from `@rules_cc_autoconf//gnulib/m4`

Many common autoconf checks are already implemented as reusable targets in `@rules_cc_autoconf//gnulib/m4`.
Instead of manually writing checks for standard functions, headers, or types, you should
use these pre-built targets whenever possible. This ensures consistency, reduces duplication,
and avoids "duplicate check" errors.

**Before (Manual Checks):**

```python
autoconf(
    name = "autoconf",
    checks = [
        macros.AC_CHECK_FUNC("lstat"),
        macros.AC_CHECK_HEADER("sys/stat.h"),
        macros.AC_CHECK_FUNC("access"),
        macros.AC_CHECK_HEADER("unistd.h"),
    ],
    deps = [":package"],
)
```

**After (Using `//gnulib/m4` Targets):**

```python
autoconf(
    name = "autoconf",
    checks = [
        # Only add checks that aren't available in gnulib
        macros.AC_DEFINE("CUSTOM_FEATURE", "1"),
    ],
    deps = [
        ":package",
        "@rules_cc_autoconf//gnulib/m4/lstat",      # Provides AC_CHECK_FUNC("lstat") and related checks
        "//gnulib/m4/sys_stat_h",  # Provides AC_CHECK_HEADER("sys/stat.h")
        "@rules_cc_autoconf//gnulib/m4/access",      # Provides AC_CHECK_FUNC("access") and platform-specific logic
        "@rules_cc_autoconf//gnulib/m4/unistd_h",    # Provides AC_CHECK_HEADER("unistd.h")
    ],
)
```

**Rules:**

- **Check for existing targets first**: Before writing a check, look in `@rules_cc_autoconf//gnulib/m4/` for a target
  that provides the same functionality (e.g., `@rules_cc_autoconf//gnulib/m4/lstat` for `lstat` checks)
- **Use as dependencies**: Add `@rules_cc_autoconf//gnulib/m4/<name>` targets to the `deps` list of your `autoconf` rule
- **Don't duplicate checks**: If a `//gnulib/m4` target already performs a check, don't add the same
  check manually - this will cause a "duplicate check" error
- **Benefits**:
  - Common checks are tested and maintained centrally
  - Platform-specific logic is already handled (e.g., Windows/macOS special cases)
  - Reduces code duplication across projects
  - Ensures consistent define names and behavior

**Finding Available Targets:**

- Browse the `@rules_cc_autoconf//gnulib/m4/` directory to see available targets
- Common patterns:
  - Function checks: `@rules_cc_autoconf//gnulib/m4/<function_name>` (e.g., `@rules_cc_autoconf//gnulib/m4/lstat`, `@rules_cc_autoconf//gnulib/m4/access`)
  - Header checks: `@rules_cc_autoconf//gnulib/m4/<header_name>` (e.g., `@rules_cc_autoconf//gnulib/m4/sys_stat_h`, `@rules_cc_autoconf//gnulib/m4/unistd_h`)
  - Type checks: `@rules_cc_autoconf//gnulib/m4/<type_name>` (e.g., `@rules_cc_autoconf//gnulib/m4/off_t`, `@rules_cc_autoconf//gnulib/m4/mode_t`)

**Example with Platform-Specific Logic:**

The `//gnulib/m4` targets often include platform-specific conditionals that you'd otherwise
need to implement manually:

```python
# Instead of manually implementing:
autoconf(
    name = "autoconf",
    checks = select({
        "@platforms//os:windows": [
            macros.M4_DEFINE("REPLACE_ACCESS", "1"),
        ],
        "//conditions:default": [
            macros.AC_CHECK_FUNC("access"),
        ],
    }),
    deps = [":package"],
)

# Use the pre-built target:
autoconf(
    name = "autoconf",
    checks = [
        # Your custom checks here
    ],
    deps = [
        ":package",
        "//gnulib/m4/access",  # Handles Windows and other platform logic automatically
    ],
)
```

### Pattern 14: Platform-Specific Conditionals

Many M4 files use `case "$host_os"` to conditionally set variables based on the
target platform. In Bazel, these should be translated to `select()` statements.

**M4:**

```m4
AC_REQUIRE([AC_CANONICAL_HOST])
case "$host_os" in
  mingw* | windows*)
    REPLACE_ACCESS=1
    ;;
  *)
    # Other platform checks...
    ;;
esac
```

**Bazel:**

```python
autoconf(
    name = "access",
    checks = select({
        "@platforms//os:windows": [
            # On Windows, unconditionally set REPLACE_ACCESS=1
            # Use M4_DEFINE for M4 shell variables (not AC_DEFINE values)
            macros.M4_DEFINE("REPLACE_ACCESS", "1"),
        ],
        "//conditions:default": [
            # Non-Windows checks
            macros.AC_CHECK_FUNC("access"),
        ],
    }),
    visibility = ["//visibility:public"],
    deps = [...],
)
```

**Rules:**

- Use `macros.M4_DEFINE` for M4 shell variables (e.g., `REPLACE_*`, `HAVE_*` set via assignment)
- Use `macros.AC_DEFINE` only for actual `AC_DEFINE()` calls in the M4 source
- `case "$host_os" in mingw* | windows*)` → `select({"@platforms//os:windows": [...]})`
- `case "$host_os" in darwin*)` → `select({"@platforms//os:macos": [...]})`
- `case "$host_os" in linux*)` → `select({"@platforms//os:linux": [...]})`
- Default case (`*`) → `"//conditions:default": [...]`
- Multiple platform conditions can be combined in a single `select()`
- Checks inside the `select()` must be lists that can be concatenated with `+`

**Common Platform Constraint Labels:**

| M4 Pattern | Bazel Constraint |
|------------|------------------|
| `mingw*`, `windows*` | `@platforms//os:windows` |
| `darwin*` | `@platforms//os:macos` |
| `linux*`, `linux-*` | `@platforms//os:linux` |
| `freebsd*` | `@platforms//os:freebsd` |
| `openbsd*` | `@platforms//os:openbsd` |
| `netbsd*` | `@platforms//os:netbsd` |
| `solaris*` | (use custom constraint or `//conditions:default`) |
| `cygwin*` | (use custom constraint or `//conditions:default`) |

**Example with Multiple Platforms:**

```python
autoconf(
    name = "fstat",
    checks = select({
        "@platforms//os:windows": [
            # Windows: stat returns timezone-affected timestamps
            macros.M4_DEFINE("REPLACE_FSTAT", "1"),
        ],
        "@platforms//os:macos": [
            # macOS: stat can return negative tv_nsec
            macros.M4_DEFINE("REPLACE_FSTAT", "1"),
        ],
        "//conditions:default": [
            # Other platforms: no replacement needed
        ],
    }),
    visibility = ["//visibility:public"],
)
```

**Combining Platform Checks with Common Checks:**

When you need both platform-specific and common checks, concatenate them with `+`:

```python
autoconf(
    name = "open",
    checks = [
        # Common checks for all platforms
        macros.AC_CHECK_FUNC("open"),
    ] + select({
        "@platforms//os:windows": [
            # Windows-specific: unconditionally replace open()
            macros.M4_DEFINE("REPLACE_OPEN", "1"),
        ],
        "//conditions:default": [],
    }),
    visibility = ["//visibility:public"],
    deps = [...],
)
```

## Special Cases

### AC_INIT and AC_CONFIG_HEADERS

**M4:**

```m4
AC_INIT([package_name], [1.0.0])
AC_CONFIG_HEADERS([config.h])
```

**Bazel:**

```python
package_info(
    name = "package",
    package_name = "package_name",
    package_version = "1.0.0",
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    deps = [":autoconf"],
)
```

**Rules:**

- `AC_INIT` → `package_info` rule (separate from macros)
- `AC_CONFIG_HEADERS` → `autoconf_hdr` rule (separate from macros)

### AC_OUTPUT

**M4:**

```m4
AC_OUTPUT
```

**Bazel:**

- No equivalent - handled by Bazel build system automatically

### AC_MSG_CHECKING / AC_MSG_RESULT

**M4:**

```m4
AC_MSG_CHECKING([for something])
AC_MSG_RESULT([yes])
```

**Bazel:**

- No equivalent - informational only, not needed in Bazel

## Complete Example Migration

**M4 (configure.ac):**

```m4
AC_INIT([myproject], [1.0.0])
AC_CONFIG_HEADERS([config.h])

AC_PROG_CC
AC_CHECK_HEADERS([stdio.h stdlib.h])
AC_CHECK_FUNCS([malloc printf])
AC_CHECK_TYPE([int64_t], [], [], [[#include <stdint.h>]])
AC_CHECK_LIB([m], [cos])
AC_DEFINE([MY_FEATURE], [1])
AC_OUTPUT
```

**Bazel (BUILD.bazel) - Using `//gnulib/m4` targets where possible:**

```python
load("@rules_cc_autoconf//autoconf:autoconf.bzl", "autoconf")
load("@rules_cc_autoconf//autoconf:autoconf_hdr.bzl", "autoconf_hdr")
load("@rules_cc_autoconf//autoconf:macros.bzl", "macros")
load("@rules_cc_autoconf//autoconf:package_info.bzl", "package_info")

package_info(
    name = "package",
    package_name = "myproject",
    package_version = "1.0.0",
)

autoconf(
    name = "autoconf",
    checks = [
        macros.AC_PROG_CC(),
        # Note: stdio.h and stdlib.h checks may be provided by gnulib targets
        # if you depend on them, but we'll keep them explicit here for clarity
        macros.AC_CHECK_HEADER("stdio.h"),
        macros.AC_CHECK_HEADER("stdlib.h"),
        # Use gnulib targets for common function checks when available
        # (malloc and printf are standard, so we keep them explicit)
        macros.AC_CHECK_FUNC("malloc"),
        macros.AC_CHECK_FUNC("printf"),
        macros.AC_CHECK_TYPE("int64_t", code = "#include <stdint.h>"),
        macros.AC_CHECK_LIB("m", "cos"),
        macros.AC_DEFINE("MY_FEATURE", "1"),
    ],
    deps = [
        ":package",
        # Add gnulib targets for any common checks you need
        # Example: if you also needed lstat, you'd add:
        # "//gnulib/m4/lstat",
    ],
)

autoconf_hdr(
    name = "config",
    out = "config.h",
    template = "config.h.in",
    deps = [":autoconf"],
)
```

**Note:** In practice, if your project uses functions like `lstat`, `access`, or other common POSIX
functions, check `//gnulib/m4/` first and add those targets to `deps` instead of writing the checks
manually. This example shows the basic migration pattern, but always prefer `//gnulib/m4` targets
for common checks to ensure consistency and avoid duplication.

## Migration Checklist

When migrating, follow these steps:

1. **Check for existing `//gnulib/m4` targets** - Before writing checks, look for reusable targets
   in `//gnulib/m4/` that provide the same functionality
2. Identify all M4 macro calls in `configure.ac`
3. Map each macro to its Bazel equivalent (see function docstrings)
4. Convert argument syntax (brackets → quotes, lists → multiple calls)
5. Extract define names from action blocks
6. Convert include directives to `code` or `headers` parameters
7. Handle language selection (`AC_LANG_PUSH/POP` → `language` parameter)
8. Identify dependencies and add `requires` parameters, or use `if_true`/`if_false` for conditional action blocks
9. Convert action blocks (`[action-if-found]`/`[action-if-not-found]`) to `if_true`/`if_false` parameters (see Pattern 11c)
10. Split multi-item macros into individual calls
11. **Handle platform-specific conditionals** (`case "$host_os"` → `select()`)
12. Create `package_info` from `AC_INIT`
13. Create `autoconf_hdr` from `AC_CONFIG_HEADERS`
14. Wrap all checks in `autoconf()` rule with `checks` list
15. **Add `//gnulib/m4` targets to `deps`** - Use pre-built targets for common checks
16. Verify no duplicate checks between direct checks and dependencies
17. Preserve comments and structure where possible

## Common Pitfalls

- **Forgetting to split lists**: `AC_CHECK_HEADERS([a b c])` needs 3 separate calls
- **Missing main() wrapper**: Custom code in `AC_TRY_COMPILE` needs `int main(void) { ... }`
- **Wrong define names**: Auto-generated names follow patterns (see each macro's docstring)
- **Language context**: Remember to propagate `language = "cpp"` from `AC_LANG_PUSH([C++])`
- **Dependencies**: Check if includes in code correspond to previous header checks
- **Conditional action blocks**: Use `if_true`/`if_false` parameters instead of manually managing `requires` when you have `[action-if-found]`/`[action-if-not-found]` blocks - this is cleaner and shows intent more clearly (see Pattern 11c)
- **String values**: Use `'"string"'` for string literals in `AC_DEFINE` values
- **Platform conditionals**: `case "$host_os"` patterns require `select()` with platform constraints
- **Duplicate checks**: Don't add a check (e.g., `AC_CHECK_FUNC("lstat")`) if you already depend on a module that provides it (e.g., `//gnulib/m4/lstat`). This will cause a "duplicate check" error.
- **Not using `//gnulib/m4` targets**: Before writing manual checks for common functions, headers, or types, check if a `//gnulib/m4/<name>` target already exists. These targets provide tested, platform-aware implementations and should be preferred over manual checks. Add them to your `deps` list instead of duplicating the checks.

## Macro Reference

For detailed information about each macro's parameters, default behavior, and examples,
see the [macros documentation](./macros.md). Each function includes:

- Original M4 example
- Bazel usage example
- Parameter descriptions
- Default define name generation rules

## Cross-Compilation Warning

Some of the macros in this module behave like their GNU Autoconf counterparts
and are **not cross‑compile friendly**. In particular, any macro that needs to
_run_ a compiled test binary will not behave well when the Bazel execution
platform differs from the target platform.

Macros that **run** test programs (and therefore are not safe for
cross‑compilation) include:

- `AC_CHECK_SIZEOF` / `ac_check_sizeof` (computes `sizeof(...)` by running code)
- `AC_CHECK_ALIGNOF` / `ac_check_alignof` (computes alignment by running code)
- `AC_COMPUTE_INT` / `ac_compute_int` (evaluates an expression by running code)
- `AC_C_BIGENDIAN` / `ac_c_bigendian` (detects endianness at runtime)
- `AC_PROG_CC_C_O` / `ac_prog_cc_c_o` (probes compiler behaviour)

These are still useful when you build and test on the **same** architecture as
your deployment target (e.g. local development, non‑cross CI), but you should
avoid or gate them when doing true cross‑compilation.

For cross‑compile scenarios prefer **compile‑only** checks such as:

- `AC_CHECK_HEADER`, `AC_CHECK_FUNC`, `AC_CHECK_LIB`, `AC_CHECK_TYPE`, `AC_CHECK_SYMBOL`
- the various compiler‑flag checks (`AC_CHECK_C_COMPILER_FLAG`,
  `AC_CHECK_CXX_COMPILER_FLAG`)
- unconditional defines via `AC_DEFINE`

You can also conditionally enable the runtime‑based macros using Bazel
`select()`s, platforms, or build flags so they only run where they are safe.
