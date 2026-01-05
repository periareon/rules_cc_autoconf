# Simple Example

This example demonstrates how to use `rules_cc_autoconf` to generate a `config.h` header
file and consume it in a `cc_test`.

## Overview

The example:
1. Defines a `package_info` with package metadata
2. Creates an `autoconf` rule that checks for common standard library functions and headers
3. Generates a `config.h` from a template using `autoconf_hdr`
4. Uses the generated header in a `cc_test` that verifies the checks worked

## Files

- `BUILD.bazel` - Bazel build file defining the autoconf rules and test
- `config.h.in` - Template file for the generated config header
- `simple_test.c` - Test program that uses the generated config.h
- `MODULE.bazel` - Module file for Bazel module system
- `WORKSPACE.bazel` - Workspace file for legacy Bazel workspaces

## Running the Example

To build and run the test:

```bash
bazel test //examples/simple:simple_test
```

Or if using the module system:

```bash
cd examples/simple
bazel test :simple_test
```

## What It Checks

The example performs the following autoconf checks:
- `AC_CHECK_FUNC("malloc")` - Checks if `malloc()` function exists
- `AC_CHECK_FUNC("free")` - Checks if `free()` function exists
- `AC_CHECK_FUNC("printf")` - Checks if `printf()` function exists
- `AC_CHECK_HEADER("stdio.h")` - Checks if `<stdio.h>` header exists
- `AC_CHECK_HEADER("stdlib.h")` - Checks if `<stdlib.h>` header exists
- `AC_CHECK_HEADER("string.h")` - Checks if `<string.h>` header exists

These checks result in preprocessor defines like `HAVE_MALLOC`, `HAVE_STDIO_H`, etc.
that are written to `config.h` based on the template in `config.h.in`.

