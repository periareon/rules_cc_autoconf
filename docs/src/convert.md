# Converting a project

`//tools/convert` turns an autoconf project into Bazel files that use these
rules, following the [migration guide](./migration.md). It is a development
tool: it runs from a clone of this repository and uses the pinned GNU
autotools that the repository's own tests use. Nothing it needs is part of
the rules' dependency tree, so consumers of `rules_cc_autoconf` are not
affected by it.

## What it does

The converter does not parse m4 itself. It runs the real `autom4te` on the
project's `configure.ac` with an instrumentation prelude that marks every
`AC_DEFUN` body and every check macro call in the generated configure
script, then reads the result back:

- Each `AC_DEFUN` body becomes an `autoconf` target. `AC_REQUIRE` and calls
  of macros that already have a port under `//gnulib/m4` or
  `//autoconf/macros` become `deps` on those targets instead of being
  translated again.
- Check macros with a direct equivalent (`AC_CHECK_HEADERS`,
  `AC_CHECK_FUNCS`, `AC_CHECK_DECLS`, `AC_CHECK_TYPE`, `AC_CHECK_SIZEOF`,
  `AC_COMPILE_IFELSE`, `AC_LINK_IFELSE`, `AC_DEFINE`, `AC_SUBST`, ...)
  become `checks.*` and `macros.*` calls.
- The shell between calls is parsed and evaluated symbolically. The
  condition under which a call runs becomes its `requires`; `case $host_os`
  becomes a `select()` on `@platforms//os:*`; a configure option
  (`AC_ARG_ENABLE`, `AC_ARG_WITH`) becomes a `bool_flag` with a
  `config_setting`; assignments to substituted variables become
  `AC_SUBST` / `M4_VARIABLE` checks with `condition`, `if_true` and
  `if_false`.
- Runtime probes (`AC_RUN_IFELSE`) are never executed. Their cache variable
  takes the m4's own cross-compiling guess per platform and the program is
  kept in a comment, so a compile-time probe can replace it, as the
  migration guide prescribes.
- A check that is conditional on a variable no check produces (the
  configure environment: `cross_compiling`, `CXX`, a shell temporary the
  converter could not follow) is a `guard` decision rather than a
  `requires` entry Bazel would reject. A variable tested before the m4
  assigns it (`test -n "$ARFLAGS" || ARFLAGS=cr`) is empty: a Bazel build
  has no configure environment.
- Everything defined in libtool's macro files (`LT_INIT`, `_LT_*`) is
  dropped: Bazel's C++ toolchain links the libraries. Autoconf builtins
  without a port under `//autoconf/macros` are translated where the
  translation is fixed (`AC_HEADER_DIRENT`, `AC_FUNC_STRCOLL`,
  `AC_FUNC_GETPGRP`, `AC_HEADER_TIOCGWINSZ`, `AC_C_VARARRAYS`, ...) and are `macro` decisions
  otherwise; program and tool lookups (`AC_PROG_*`) are only noted.
- A project whose `aclocal.m4` is maintained by hand (readline, bash) keeps
  it: its macros are translated like the project's own m4 files.
- The project's own macros expand into the `configure.ac` target in call
  order, as configure runs them. `AC_CACHE_VAL` commands with shell logic
  are followed: the nested probes become checks, and a cache variable that
  takes several values on several paths (`bash_cv_signal_vintage`) is not
  a check of its own; every `test $var = value` on it becomes the matching
  condition over the probes, which is what configure's shell computes.
- Probes see what configure's confdefs.h carried. The rules make that
  explicit: a compiling check lists in `compile_defines` the defines it
  refers to, and a dependency has to produce them, so the `configure.ac`
  target is split (`autoconf_1`, `autoconf_2`, ... before `autoconf`) where
  a probe needs a result of an earlier check of the same body. After
  `AC_USE_SYSTEM_EXTENSIONS` every declaration, type, member, compile and
  link check lists the extension macros (`SYSTEM_EXTENSIONS`); header
  existence and function linkage checks do not depend on them and stay
  identical to the ports', so the toolchain cache shares the result.
- Conditions on the build platform become `select()`s, one dimension per
  select: `$host_os` on `@platforms//os:*`, `$host_cpu` (and the CPU field
  of `$host`) on `@platforms//cpu:*`, and `$GCC` on `@rules_cc//cc/compiler:*`
  (false for `msvc-cl` and `clang-cl`). A condition that mixes two
  dimensions is a `guard` decision, where a `select()` over your own
  `config_setting` can express it.
- A language standard the script insists on does not become a flag. configure
  switches the compiler into the mode a macro asks for and stops when it
  cannot; a Bazel build's standard is the root module's, set with `--copt` /
  `--cxxopt` or a toolchain feature, and a module pinning `-std` on its own
  targets would override that choice for those targets alone while the gnulib
  ports' probes ran under the build's. So `AC_PROG_CC_C99` becomes
  `checks.AC_LANG_STANDARD("c99", error = True)`, `AX_CXX_COMPILE_STDCXX`
  becomes `checks.AX_CXX_COMPILE_STDCXX(...)`, and a `CFLAGS="$CFLAGS
  -std=gnu11"` or an `AM_CFLAGS = -std=gnu99` becomes a requirement check on
  the main target: each probes the mode the compiler is in and stops the build
  with the flag to add when it is older than asked for.
- Configure options become build settings only when a `select()` in the
  output keys on them, each with just the `config_setting`s it uses. An
  option the script only compares with yes / no is a `bool_flag` with
  `<option>_enabled`; one compared with other values (`--with-ssl=openssl`)
  is a `string_flag` whose `values` list them, with `<option>_<value>`
  settings, or an `int_flag` when they are all numbers.
- `--src` takes a directory, a release archive, or the archive's URL; a URL
  is fetched once into the checkout's git-ignored `.convert-cache/` (checked
  against `--integrity` when given) and extracted, so a conversion leaves
  nothing outside the repository. Convert from the URL: the exact
  `bazel run` line then reproduces the conversion from any clone of this
  repository.
- BUILD.bazel, MODULE.bazel and CONVERSION_REPORT.md open with a banner
  carrying that command and the `@generated` marker review tools collapse
  files on; `decisions.bzl` is the one hand-maintained file of a conversion
  and says so in its header. A reviewer can read a conversion as "generated,
  except decisions.bzl", and regenerate it by copying the command.
- The output never carries a TODO. A construct with no mechanical
  translation is looked up in a decisions file by a stable key; without a
  decision it is left out and reported. Regenerating from the same sources
  and the same decisions file reproduces the same output, so hand edits are
  never needed to keep a conversion current.

For a gnulib-based project the converter also reads `m4/gnulib-comp.m4`,
`lib/gnulib.mk` (with the `lib/Makefile.am` that includes it) and every
`Makefile.am` reached through `SUBDIRS` (`po`, `doc`, `tests` and the like
excepted) and emits the shape of the existing BCR overlays:
`autoconf_package_info`, the gnulib module deps, the module indicators,
`autoconf_hdr` for `AC_CONFIG_HEADERS` together with a `config_headers`
library that serves them and the top-level headers with `includes = ["."]`
(gnulib sources write `#include <config.h>`; the generated project is
consumed as an external module through `bazel_dep` and an override, where
that is allowed), the generated and conditional
wrapper headers, `autoconf_srcs` with the `gl_CONDITIONAL` conditions, a
`select()` of the sources whose conditional is a `case $host_os`
(`OS_IS_NATIVE_WINDOWS`, `GL_COND_OBJ_WINDOWS_*`), the library,
`autoconf_linkopts`, a `cc_binary` per program (`src/grep`) and a
`cc_library` per library of every `*_PROGRAMS`, `*_LIBRARIES` and
`*_LTLIBRARIES` variable (`check_` and `EXTRA_` excepted), with `LDADD` and
`LIBADD` resolved to the libraries of the tree, `-l` options and the `$(X_LIB)`
variables. rules_cc names a library's output `lib<name>.a`, so `libfoo.a` is
a rule called `foo`; when a program `foo` already takes that name the
library becomes `lib/foo` (output `lib/libfoo.a`) and an alias with the
archive name (`libfoo`) points at it. Modules without the prefix
(`plugin.la`) keep their name. An installed libtool library (`lib_LTLIBRARIES`,
a `-module` plugin) also gets a `cc_shared_library` named `<name>_shared`
wrapping the `cc_library`, with the platform's shared object name. Every
program and library of a subdirectory gets a top-level alias with its base
name (`grep` for `src/grep`) when that name is free.

A project that uses autoconf without automake (readline, libpcap, tcpdump:
hand-written `Makefile.in` files) gets its programs and libraries from the
rules of those files instead: a rule whose target is an archive
(`libreadline.a: $(OBJECTS)`) is a `cc_library`, one whose recipe links with
`$(CC) ... -o $@` is a `cc_binary` with the archives among its prerequisites
as deps, and the objects become the sources of the same name. `$(VAR)` is
read from the Makefile's own assignments and `@VAR@` from configure's
literal substitutions; a target or source neither resolves (`@LIBOBJS@`,
`@PLATFORM_C_SRC@`) is reported and left out. Nothing else of a Makefile.in
is interpreted: that is as far as support for build systems other than
automake goes.

A configure.ac without `AC_CONFIG_HEADERS` makes configure pass every
define to the compiler as `-D` flags. The rules render defines into a
header, so for such a project the converter runs `autoheader`, writes the
resulting `config.h.in` next to the BUILD file and renders it as `config.h`,
which the sources see through `HAVE_CONFIG_H` (the BCR overlay of mpfr keeps
the same template by hand as `config.in`).

Where configure.ac runs a check whose define or substitution a gnulib port
dependency already provides (`AC_CHECK_FUNCS([fchown])` next to
`gl_FUNC_CHOWN`, which probes fchown itself), the local check is left out
and reported: configure produces the result twice, consistently, and the
rules reject two producers of one name. Defines and substitutions are
separate namespaces here, as in the rules.

A library exposes only what automake installs. Its `hdrs` are the headers of
the `*_HEADERS` variables that install (`include_HEADERS`,
`nobase_pkginclude_HEADERS`, ...), with `includes` for their directories;
headers a directory installs without building a library there (libssh2's
`include/`) go to the tree's one installed library, or to a shared
`exported_headers` library when there are several. Every other header of
the directory goes to a `<name>_private_headers` library the library
compiles with (`implementation_deps`), visible to this package only, so an
outside consumer never sees it and its name cannot collide with the
consumer's own; the project's own programs and tests that link the library
depend on it too, as automake's `-I.` lets them include anything (pkgconf's
tests include `libpkgconf/path.h`). Dependencies are
split the same way: one whose headers an installed header `#include`s stays
in `deps`, since consumers need it to compile against the interface; the
config headers, the gnulib library, `autoconf_linkopts` and any other
library only the sources use become `implementation_deps`, which link but
do not export headers. A dependency a decision named is kept in `deps`,
since what it exports is not known. Programs keep everything in `deps`.

`AC_CONFIG_SUBDIRS([libs utils texk])` is followed: each directory named
literally that has a configure.ac of its own is converted from that file and
its Makefile.am tree into a package at its directory of the same module
(`libs/mpfi/BUILD.bazel`), with one `MODULE.bazel`, one decisions file and
one report for all of them, and its own `AC_CONFIG_SUBDIRS` followed in
turn. A subdirectory configure computes (`AC_CONFIG_SUBDIRS([$subdirs])`) is
reported and left alone, with one exception: when a Makefile.am's `SUBDIRS`
is itself computed (`SUBDIRS = . $(MAKE_SUBDIRS)`, texlive's `libs/`), the
immediate child directories that carry a configure.ac of their own are taken
as the sub-configures, each noted in the report. A configure.ac that is not at the archive's root
at all (tcl's `unix/configure.ac`) is reached with `--subdir`. `.c` files other sources `#include`
(gnulib's `regex.c` reads `regcomp.c`) travel as `textual_hdrs`. A source
the Makefile.am writes itself with `printf` or `echo` (gzip's `version.c`)
becomes a `write_file`, through an `autoconf_hdr(mode = "subst")` when the
text carries configure's values (`$(PACKAGE_VERSION)`). The `-D`
and `-I` options of `DEFS`, `AM_CPPFLAGS`, `<target>_CPPFLAGS` and the
`CFLAGS` variables become `local_defines` and `copts`, with `$(VAR)` in a
value expanded from the Makefile.am, configure's unconditional literal
substitutions and automake's installation directories at their defaults
(`-DDEFPATH=$(DEFPATH)` with `pkgdatadir = $(datadir)/awk` gives
`DEFPATH=".:/usr/local/share/awk"`); a value only configure computes is
reported and left out. Yacc and lex sources (`parse.y`, `scan.l`) become
`bison()` and `flex()` rules named `bison/parse` and `flex/scan`, out of the
way of the generated files release tarballs ship, with `AM_YFLAGS`,
`<target>_YFLAGS` and the options of a hand-written `$(YACC)` recipe
(`-p zz`) carried over; the target compiles their outputs and finds the
header through `-I$(BINDIR)/bison`. The rules are the BCR `bison` and
`flex` modules' own (`@bison//:bison.bzl`, `@flex//:flex.bzl`), which run
the modules' binaries with m4 configured, so such a project's MODULE.bazel
only adds `bazel_dep`s on `bison` and `flex`. BUILD files already shipped in
the source tree (tcl's `compat/zlib/BUILD.bazel` loads `rules_license`) load
into the generated module, so the modules they load get a `bazel_dep` too;
a repository the converter has no module for is reported.
`GUARD_PREFIX` and the other values gnulib.mk's sed spells as literals are
substituted as such, and a module indicator a wrapper template reads that no
port defaults (a template newer than the port's gnulib snapshot) is set to 0.
The module indicators keep gnulib's values, including
`IN_<PACKAGE>_GNULIB_TESTS` for modules the tests alone use and the
`GNULIB_TEST_*` defines. Checks that
`gl_INIT` repeats at the top level (`AC_CHECK_FUNCS_ONCE`, `AC_CHECK_DECLS`,
`gl_CHECK_FUNCS_ANDROID`) become deps on the port that already carries
them, so a define is never produced twice.

## Converting a project

From a clone of this repository:

```sh
bazel run //tools/convert -- convert --src https://ftp.gnu.org/gnu/gzip/gzip-1.15.tar.xz --out /path/to/output --dev
```

This writes `BUILD.bazel`, `MODULE.bazel` and `CONVERSION_REPORT.md` into
the output directory (`--src` also takes a local directory or archive). The
banner of each generated file repeats the command without `--dev`, which is
what reproduces the conversion against the released rules. With `--dev`, `MODULE.bazel` carries a
`local_path_override` to this checkout so the result builds against the
rules as they are here; without it, it depends on the released version.
The BUILD file carries no comments by default; `--annotate` comments every
check, dep and rule with the m4 call or line it came from, for reviewing a
conversion against its sources.

The first run usually exits with status 3 and a `Decisions required`
section in the report: constructs the generator cannot translate on its own.
It also writes `decisions.bzl` next to the output with one entry per
construct: the m4 or Makefile text it replaces and what the converter found
in it, as comments, and `UNDECIDED` as the value. A legend at the top of the
file explains every value a decision can take. Replace each
`UNDECIDED` with a decision and run the same command again. Later runs read
the file, add entries for anything new and never touch what is already
there, so the file is kept with the project and reused for every
regeneration.

The m4 expansion exists only to collect what the analysis reads, so a
configure.ac that stops autoconf with `m4_fatal` (postgres and binutils
pin an autoconf version that way) is not stopped: the converter turns
`m4_fatal` into a warning, which the report repeats. `--profile FILE` writes
a cProfile of the run and a text summary next to it (`FILE.txt`) for finding
where a slow conversion spends its time.

The file is Starlark in the vocabulary of the generated BUILD file: a
replacement is written exactly as it will appear in the output, and the
converter checks it against the rest of the conversion (a replacement probe
must set the cache variable it replaces, for instance). A decision is one of
the words `IGNORE`, `ACCEPT`, `ACCEPT_PARTIAL`, `ALWAYS`, `NEVER`; a literal;
a `select()` over configuration labels; or `checks.*` / `macros.*` calls.
The legend at the top of the file says which of these each kind of key
takes. Keys are stable: `probe:<cache variable>`, `variable:<NAME>`, `macro:<NAME>`,
`guard:<target>:<check>`, `actions:<macro>:<argument>`,
`src_condition:<NAME>`, `am_conditional:<NAME>`, `flag:<option>`, and for
the Makefile.am side `generated:<path>` (a source no translatable recipe
writes: the `genrule(...)` or `run_binary(...)` that does, or IGNORE),
`cppflag:<dir>:<NAME>` (a `-D` value only configure computes) and
`link:<dir>/<target>:<item>` (an LDADD entry that is neither a library of
the tree nor a `-l` option: a dep label or a linkopt).

```python
"""Decisions for converting foo 1.2."""

DECISIONS = {
    # configure.ac:118: AC_CACHE_CHECK([for underline in external names], [gzip_cv_underline], ...)
    "probe:gzip_cv_underline": "no",
    "probe:my_cv_feature": select({
        "@platforms//os:windows": 0,
        "//conditions:default": 1,
    }),
    "probe:my_cv_works": checks.AC_TRY_COMPILE(
        name = "my_cv_works",
        includes = ["#include <x.h>"],
        code = "int main(void) { return 0; }",
    ),
    "variable:GZIP_TRANSFORMED": "gzip",
    "variable:WARN_CFLAGS": IGNORE,
    "macro:gl_FOO": "@rules_cc_autoconf//gnulib/m4/foo",
    "src_condition:GL_CRC_X86_64_PCLMUL": "gl_cv_crc_pclmul",
    "flag:gcc_warnings": False,
}
```

The file is read with a parser, never evaluated, and `buildifier` formats
it. Decisions the conversion no longer asks for are reported as unused, so
the file does not accumulate stale entries. `--decisions FILE` reads a file
elsewhere. While decisions are missing the converter still writes its
outputs, so the project can be looked at, but exits with status 3: the
decisions file is the one file a person reviews in this process, and a
conversion is complete only when it answers every question.

Libraries the registry provides are probed for against the module, not the
host. Where configure runs `AC_CHECK_LIB` or `AC_SEARCH_LIBS` on one of
them, adds it to `LIBS` by hand or includes its header in a probe, or a
Makefile.am links it (`-lgmp`), the converter wires the module in: the module
(the table is `tools/convert/libraries.toml`, generated from a registry clone
by `bazel run //tools/convert:update_libraries`, which verifies each module's
library target) becomes a `bazel_dep`, its target a `cc_deps`
entry of the root `autoconf` targets, so every check compiles against its
headers and links its archive as configure does with the library installed,
and a `dep` of what links it. The checks themselves stay real probes. A
`link:` decision still overrides a Makefile item. Likewise a gnulib.mk
condition over a result none of the project's gnulib modules provides, but
another port does (an older gnulib computed it in a different macro), pulls
that port into `GNULIB_M4_DEPS`; the report names it.

`--append BUILD` names a hand-written BUILD file whose contents go at the
bottom of the generated `BUILD.bazel`, before buildifier formats it, with
its `load()` statements hoisted to the top. The converter owns
`BUILD.bazel` and regenerates it whole; the fragment is where a project's
tests and anything else the conversion cannot produce live, so regenerating
never deletes them. Both files are part of the reproduce command in the
banner.

Three keys are never asked for and apply when present. When Bazel reports a
define, subst or check as "produced both by this target and by a dependency" (the
project re-checks or presets something one of its gnulib modules already
settles), drop the project's copy with `"define:HAVE_LONG_LONG_INT": IGNORE`,
`"subst:GNULIBHEADERS_OVERRIDE_WINT_T": IGNORE` or
`"check:ac_cv_func_vasprintf": IGNORE` and regenerate. A configure
option's default comes from its help string (`--disable-x` and
`--without-x` document a feature that is on); `flag:<option>` overrides
it.

Then, in the output directory, copy or symlink the project's sources next to
the generated files (or point `--out` at a copy of the source tree) and
build the config header:

```sh
bazel build //:config
```

## Vendoring the ports

By default the generated project depends on the gnulib ports of this
repository (`@rules_cc_autoconf//gnulib/m4/...`) for every gnulib macro its
configure.ac reaches, the headers' `:defaults` targets among them (listed in
`GNULIB_M4_DEFAULT_DEPS`, the main target's `default_deps`, for every
`gl_<HEADER>_H_DEFAULTS` the script `AC_REQUIRE`s). Those ports change as
this repository tracks gnulib, so a project converted against one snapshot
can later be built against another.
`--vendor-ports` removes that coupling: every port the conversion resolved,
and every port those depend on, is copied into
`third_party/rules_cc_autoconf/<package>/BUILD.bazel` of the project, with
labels rewritten and the in-repository `autoconf_cache` rule replaced by the
public `autoconf` rule. The project's ports are then pinned to the snapshot
it was converted against; regenerating against a newer checkout refreshes
them, and the diff shows exactly what gnulib changed.

What still comes from rules_cc_autoconf: the rules, and two ports that are
rule implementations rather than check data (`AC_C_INLINE` and
`AC_C_RESTRICT` carry resolver binaries). The report names them. The
converter needs the checkout the ports come from: run it from that checkout
with `bazel run`, or pass `--index <checkout>`.

## Converting single m4 files

To port one m4 file the way the hand ports under `//gnulib/m4` are written,
name the file and give a `configure.ac` that invokes its macros:

```sh
bazel run //tools/convert -- convert --src /path/to/project --out /tmp/port --only foo.m4 --in-repo
```

Every other macro resolves to the existing ports. The output is one package
per m4 file with an aggregator target named after the file, in the layout of
`//gnulib/m4/<name>`.

## Checking against GNU autoconf

The `check` subcommand runs the project's real `configure` with the pinned
autotools and the compiler, flags and environment of the current Bazel C++
toolchain, exactly as the conformance tests of this repository do, and
diffs the resulting config header against one Bazel built from the
converted `BUILD.bazel`:

```sh
bazel run //tools/convert -- check --src /path/to/project --out /tmp/check \
    --bazel-config-h lib/config.h=/path/to/output/bazel-bin/lib/config.h
```

The GNU results (`config.h`, `config.log`, the output variables) stay in
`/tmp/check/gnu/` for inspection. This validates the host platform only;
the conformance tests under `//gnulib/tests` are the reference for the rest.

## Publishing to the Bazel Central Registry

`--bcr` writes the conversion as a registry entry as well:

```sh
bazel run //tools/convert -- convert --src /path/to/project --out /tmp/entry --bcr \
    --source-url https://ftp.gnu.org/gnu/foo/foo-1.2.tar.gz --integrity sha256-...
```

The entry lands under `/tmp/entry/modules/<name>/<version>/` with
`MODULE.bazel`, `source.json` (the archive and the integrity of every
overlay file), `presubmit.yml` and the `overlay/` tree. The overlay only
adds files on top of the release archive. The archive URL and integrity are
required, and so is a homepage for `metadata.json`: it comes from the URL
argument of `AC_INIT` or from `--homepage`. The entry never contains a
value to fill in by hand.

## Keeping the converter current

- `tools/convert/index.toml` is a snapshot of the hand ports (which macro
  lives in which target, and which substitutions and defines each
  provides, and the automake conditionals they spell as `<NAME>_TRUE`
  substitutions: a source condition over a name that is both a subst and a
  define of the ports spells the subst result, `ac_cv_subst_HAVE_X`, a
  header variable such as `$ERRNO_H` becomes `GL_GENERATE_ERRNO_H==true`,
  and `if GL_CRC_X86_64_PCLMUL` takes the port's condition).
  Regenerate it after adding or renaming ports:
  `bazel run //tools/convert:update_index`.
- The golden tests under `//tools/convert` fix the converter's output for a
  few gnulib modules and for gzip, each with its decisions file next to the
  expected output. They also require every request to be decided and run
  buildifier with all warnings on the output. After an intentional change,
  refresh a golden with `bazel run //tools/convert:<name>_golden_test -- --update`.
- The prelude mirrors autoconf 2.73 internals (`AC_DEFUN_ONCE`, the shapes
  of the expanded shell). Updating the pinned autoconf means revisiting
  `tools/convert/instrument.m4` and the shape regexes next to it.
