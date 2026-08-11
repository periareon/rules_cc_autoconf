# MSVC support

GNU Autoconf does not really support MSVC. Its posture toward `cl.exe` is,
in full:

- `AC_PROG_CC` will fall back to `cl.exe` if no `gcc`, `cc`, or prefixed
  Unix compiler is on `PATH`.
- `AC_OBJEXT` / `AC_EXEEXT` abstract `.o`/`.obj` and (empty)/`.exe` so a few
  recipes work on either side.
- A handful of macros contain `#if defined _MSC_VER` snippets in their
  emitted output (alloca, restrict).

Everything else that makes `cl.exe` usable in a typical autoconf project —
translating `-o foo.o` to `/Fofoo.o`, mapping `-l foo` to a `.lib` search,
forwarding `-Wl,` as `/link …`, wrapping `lib.exe` as `ar` — lives in
Automake's `compile` and `ar-lib` shim scripts, not in autoconf itself.
Autoconf's own tests assume a POSIX-ish compiler dialect and a Unix
filesystem layout.

Because `rules_cc_autoconf` does not depend on Automake, and because Bazel
Windows builds routinely target the Microsoft toolchain, we have made a
number of deliberate decisions to bridge those gaps directly. The sections
below document those decisions and the behaviors that follow from them.

## Toolchain detection

The MSVC dialect is engaged when `CcToolchainInfo.compiler_type` is one of:

- `msvc-cl` — Microsoft `cl.exe`
- `clang-cl` — LLVM's MSVC-compatible driver

Any other value (`gcc`, `clang`, cross-compilers, …) takes the GCC/Clang
code path. Ensure your registered Windows toolchain reports one of the
supported values. There is no autodetection from the compiler binary name
or from probing the compiler's output; the toolchain is authoritative.

## Deviations and decisions

### `AC_SEARCH_LIBS` -> `#pragma comment(lib, …)`

On GCC/Clang, [`autoconf_linkopts`](./autoconf_linkopts.md) resolves subst
values (typically produced by `AC_SEARCH_LIBS`) into `-l<name>` entries
and passes them to the linker through a response file. That approach does
not work on MSVC because `link.exe` does not support nested response
files.

Instead, on `msvc-cl` and `clang-cl` we generate a small `.c` file
containing `#pragma comment(lib, "<name>")` for each resolved library,
compile it to an object, and feed the resulting `.obj` to the link as an
input. Consumers see the correct libraries linked without needing to
know which dialect is in use.

### `#include_next` fallback

`AC_CHECK_GL_NEXT_HEADER` produces gnulib's `NEXT_<HEADER>_H` and
`INCLUDE_NEXT` pair. `#include_next` is a GCC extension that MSVC does not
implement, so the check has to produce output that still compiles on
`cl.exe`. Two things happen:

- When we can locate the "next" system header on the include path, its
  contents are inlined directly into the generated wrapper and any
  `#pragma once` line inside it is stripped so the inline copy does not
  suppress the real one.
- When no next header can be located, `INCLUDE_NEXT` becomes the empty
  string. That collapses the generated `# INCLUDE_NEXT NEXT_FOO_H` line
  to the null preprocessor directive `#`, which is valid but binds
  nothing.

The result is a wrapper that compiles under MSVC and behaves the same as
the GCC/Clang output whenever the requested header exists.

### Warning-as-error stripping in both dialects

If a caller passes `-Werror` (or its more targeted `-Werror=name` form)
or MSVC's `/WX` or `/we<num>`, a harmless warning inside a probe body
would fail the check and produce a false negative. We universally strip
these flags from the probe compile line — both dialects, unconditionally.
`/WX-` (the disable form) is preserved.

### Object and executable extensions

MSVC produces `.obj` object files, and Windows executables have a `.exe`
suffix. Probes on `msvc-cl` and `clang-cl` produce `.obj` intermediates
and, for link tests, `.exe` outputs. On GCC/Clang the probes produce `.o`
and extensionless binaries.

### Windows path robustness

Two Windows-specific concerns get explicit handling:

- **Long paths.** Neither `cl.exe` nor `link.exe` accepts paths above the
  legacy `MAX_PATH` limit (~260 characters). When a probe's conftest path
  would exceed a safe threshold, its name is truncated and a short hash
  is appended to preserve uniqueness. Internally, our own file I/O uses
  the `\\?\` extended-length prefix on Windows so we can read and write
  paths above `MAX_PATH` ourselves even when we cannot ask the compiler
  to do the same.
- **Spaces in the compiler path.** MSVC installations typically live under
  `C:\Program Files\Microsoft Visual Studio\…`, which `cmd.exe` mishandles
  without careful quoting. On Windows we resolve the compiler binary to
  its 8.3 short-name form before invoking it, so the space-containing
  path never reaches `cmd.exe`.

### Environment propagation

Bazel's Windows `cc_toolchain` publishes `INCLUDE`, `LIB`, and `PATH`
through `get_environment_variables`. `rules_cc_autoconf` merges those
into every probe action's environment. This is how `cl.exe` finds the
Windows SDK and CRT headers without us ever having to synthesize `/I`
flags for them — and it is why a Windows toolchain that fails to publish
these variables will behave badly (see below).

## Behaviors to be aware of

### `AC_CHECK_LIB` does not translate Unix library names

MSVC's linker accepts library names as `<name>.lib`. `AC_CHECK_LIB("m",
"cos")` on MSVC becomes `cl.exe … m.lib`, which does not exist — the math
library on MSVC is folded into the CRT and needs no explicit link, and
there is no `m.lib` on the search path. The check simply fails and
`HAVE_LIBM` stays undefined.

We deliberately do *not* silently rewrite common Unix library names
(`m`, `pthread`, `dl`, `rt`) to Windows equivalents. Users who need
MSVC-friendly link tests should pass the Windows-native library name
directly (e.g. `"ws2_32"`, `"advapi32"`).

### `AC_CHECK_C_COMPILER_FLAG` / `AC_CHECK_CXX_COMPILER_FLAG` — known bug

These macros currently do **not** actually test the flag. The probe body
is `int main(void) { return 0; }`, and the flag under test is never
added to the compile line. The check therefore reports success under
any working toolchain regardless of whether the flag would be accepted.

This bug is not MSVC-specific but shows up most visibly on MSVC because
users reach for it to probe `/Wall`, `/std:c99`, `/std:c++17`, and so on
— all of which currently report as supported without being tested.
Treat the result of these macros as unreliable until the bug is fixed.

### No compiler response-file fallback

Probes are dispatched as a single shell command. Windows `cmd.exe`
imposes an ~8191-character command-line limit, and Bazel's default
`msvc-cl` toolchain often produces long `/I` lists. On very large
targets with many transitive header roots, a single probe can exceed
that limit and fail. We do not currently fall back to writing an
`@response-file` for the compiler invocation itself.

### Cross-compilation from a non-Windows host to MSVC is not supported

Some of the MSVC-specific handling (file-extension conventions, output
redirection, long-path wrappers) keys off the *host* platform the
checker was built on rather than the target the resolved `cc_toolchain`
targets. Building on Linux with an MSVC cross toolchain will not
produce correct results. Build on Windows.

### Silent failure if `INCLUDE` / `LIB` are stripped

Because we rely on the toolchain's published environment for system
include paths, any `--action_env` policy or custom toolchain that
strips `INCLUDE` or `LIB` will cause `cl.exe` to run with no system
include paths. Every `AC_CHECK_HEADER` will then report "not found"
with no error message. If you see a wall of missing headers on
Windows, check that `INCLUDE` is present in the action environment
before assuming the checks themselves are broken.

### Windows exit-code semantics differ

Success vs. failure is reliable on both platforms. What is *not*
reliable on Windows is distinguishing between different kinds of
failure (compiler crashed, compiler rejected the source, `cmd.exe`
mangled the command). Only `= 0` and `≠ 0` are trustworthy today.

### Paths containing spaces

The 8.3-short-name workaround is applied to the compiler binary only,
not to probe output paths. Bazel execroots that contain spaces have
been known to trip Windows toolchains. Keep your execroot on a
space-free path.

## What we chose *not* to reimplement

Upstream autoconf projects that build under MSVC lean heavily on
Automake's shim scripts:

- `compile` translates GCC-style flags to MSVC-style flags on the fly:
  `-o foo.o` → `/Fofoo.o`, `-Idir` → `/I dir` (with path conversion),
  `-l foo` → a search of `%LIB%` for `foo.dll.lib` / `foo.lib` /
  `libfoo.a`, `-Wl,x,y` → `/link x y`, and so on.
- `ar-lib` wraps Microsoft's `lib.exe` so a Makefile that calls `$(AR)`
  keeps working.

`rules_cc_autoconf` does not ship or invoke either. We avoid the need
for `compile` by constructing MSVC-dialect compile and link commands
natively, and we avoid `ar-lib` entirely because we never invoke `ar`.
The upshot is that MSVC-idiomatic flags (`/Wall`, `/std:c++17`,
`/machine:x64`, …) must be produced by the resolved Bazel toolchain, not
translated on our side from GCC-style spellings.

## Recommendations for users on MSVC

- Use Bazel's shipped `msvc-cl` or `clang-cl` toolchain unmodified where
  possible; custom toolchains must publish `INCLUDE`, `LIB`, and `PATH`
  via `get_environment_variables` and set `compiler` to `msvc-cl` or
  `clang-cl`.
- Prefer Windows-native library names in `AC_CHECK_LIB` /
  `AC_SEARCH_LIBS` — do not expect `m` or `pthread` to resolve.
- Avoid `AC_CHECK_C_COMPILER_FLAG` / `AC_CHECK_CXX_COMPILER_FLAG`
  results until the bug above is fixed.
- Keep your Bazel execroot on a path without spaces.
- Build MSVC targets from a Windows host, not via cross-compilation.
