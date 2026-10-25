# Converting a project

`//tools/convert` turns an autoconf project into a Bazel module built on
these rules. It reads the project's `configure.ac` (expanded by the real
`autom4te`) and the file lists of its build (`Makefile.am`, or a hand-written
`Makefile.in`), and writes `BUILD.bazel`, `MODULE.bazel` and a report. What
it cannot translate on its own it asks about in one file, `decisions.bzl`,
which a person answers and keeps.

The result is a module in the shape of the hand-written BCR overlays: an
`autoconf` target per configure body with `checks.*` calls, `config.h` and the
other configured headers, the gnulib library with its conditional sources
and wrapper headers, a `cc_library` per library and a `cc_binary` per program
of the automake tree, `bool_flag`s for the configure options, and `select()`s
where configure branched on the platform.

## Which projects

What sits next to `configure.ac` decides how much of a module comes out. The
first note of the report names the shape it found.

| Shape | Identified by | What comes out |
|---|---|---|
| 1. autoconf + automake, usually gnulib | `configure.ac`, `Makefile.am`, often `m4/gnulib-comp.m4` | The whole module. coreutils, grep, sed, gzip, m4, bison, gawk, make, patch, mpfr, flex. |
| 2. autoconf + hand-written Makefiles | `configure.ac`, no `Makefile.am`; `Makefile.in` files, or plain `Makefile`s that `include` a file configure writes (tftp-hpa's `config/MCONFIG`), with literal source lists and `$(CC) -o` rules | The whole module, read from the Makefile rules: archives are libraries, link rules programs, the compile rule's `-D` and `-I` the flags, an `$(includedir)/<name>` install directory an include prefix; an included configured file is read from its `.in` template. readline, libpcap, tcpdump, tftp-hpa. The reader follows literal lists and `$(VAR)` references, not make functions. |
| 3. autoconf + make as a program | `configure.ac`; Makefiles built from `$(wildcard ...)`, `$(patsubst ...)`, generated fragments, or rules written by awk at configure time | The configuration only: the `autoconf` target, its headers and flags. glibc, gcc, binutils, ncurses. The libraries and programs are written by hand in `BUILD.bazel`. |
| 4. hand-written `configure` | no `configure.ac` | Nothing; the converter stops. ffmpeg, musl, zlib, nginx, openssl. The [migration guide](./migration.md) applies by hand. |

A Makefile.am that switches compilers (`CC = $(TARGET_CC)`, grub's firmware
side) builds nothing for the host: its programs and libraries are left out
and reported, whatever the shape of the rest.

## Running it

From a clone of this repository:

```sh
bazel run //tools/convert -- convert --src https://ftp.gnu.org/gnu/gzip/gzip-1.15.tar.xz --out /path/to/output --dev
```

`--src` takes a release archive URL, a local archive or a source tree.
`--out` receives `BUILD.bazel`, `MODULE.bazel`, `REPO.bazel` (the module's defaults: license metadata, the standard as a future toolchain feature), `CONVERSION_REPORT.md` and
`decisions.bzl`. The banner of each generated file repeats the command, which
is what regenerates the conversion. Options:

| Option | Effect |
|---|---|
| `--dev` | `MODULE.bazel` overrides `rules_cc_autoconf` with this checkout. Without it the module depends on the released version. |
| `--decisions FILE` | Read and extend this decisions file instead of `decisions.bzl` in the output directory. |
| `--annotate` | Comment every check, dep and rule with the m4 call or Makefile line it came from. |
| `--subdir DIR` | Convert the `configure.ac` in a subdirectory of the tree. |
| `--vendor-ports` | Copy the gnulib ports the conversion uses into the module, see [Vendoring the ports](#vendoring-the-ports). |
| `--only FILE` | Convert one m4 file in the layout of the hand ports, see [Converting single m4 files](#converting-single-m4-files). |
| `--bcr-entry DIR` | Write a registry entry in place and take the inputs from it, see [A registry entry](#a-registry-entry). |
| `--http-archive-entry DIR` | Write the one generated BUILD file into a consumer's `3rdparty/<name>/` for `http_archive`, see [An http_archive](#an-http_archive). |
| `--layout per-file\|package` | One package per m4 file, or a single package. The default is `per-file` with `--only` and `package` otherwise. |
| `--external`, `--in-repo` | Emit for an external project (`@rules_cc_autoconf//...` labels), or for this repository (`//...` labels and `autoconf_cache`). The default follows the layout: external for a package, in-repo per file. |
| `--buildifier auto\|none\|PATH` | The buildifier that formats the output. `auto` takes the one on `PATH`. |
| `--strip-prefix PREFIX` | The release archive's `strip_prefix` for `source.json`. By default `<name>-<version>`. |
| `--keep-work` | Keep the scratch directory of the expansion; its path is printed. |

`--help` lists the rest. `--profile FILE` belongs to the tool rather than to
the `convert` subcommand, so it goes before the subcommand:
`bazel run //tools/convert -- --profile run.prof convert ...`. It writes a
cProfile of the run to `FILE` and a text summary to `FILE.txt`.

What to do with the output depends on where the module is going, see
[Using the output](#using-the-output).

## Decisions

The first run usually exits with status 3 and lists the constructs it could
not translate under "Decisions required" in the report. Each is an entry of
`decisions.bzl` with `decision.UNDECIDED` as its value: replace the value and
run the same command again. The outputs are written even while decisions are
missing, so the project can be looked at, but a conversion is complete only
when the file answers every question.

The file is read with a parser, never evaluated; its header names the
rules_cc_autoconf that wrote it. New entries are added in key order with a
comment block showing the construct: the upstream text in a fenced block tagged
with its language, where it was found, and why it is asked. Entries the
converter stops asking for are reported as unused. A `checks.X(...)` value reads
as in a BUILD file: add `load("@rules_cc_autoconf//autoconf:checks.bzl",
"checks")` at the top.

A `DEFAULTS` table answers every undecided key of a kind at once, for a
construct that never matters for the module (a firmware compiler's probes, a
toolchain's warning flags); the report lists what a default answered:

```python
DEFAULTS = {
    "probe": decision.IGNORE,
}
```

The kinds the converter asks for:

| Key | Asked when | Accepts |
|---|---|---|
| `probe:<cache variable>` | A probe has no compile-time form: a runtime test, a cache value computed by shell, a check whose actions decide the variable, a program reading shell variables the rules cannot expand (`#include "$srcdir/gmp-h.in"`).  Never requested for a header, function, type or library check, but applied when present: the value stands in for the probe (`"no"` for a library the registry does not have, which configure would probe the host for) | `IGNORE`, `ACCEPT` (the converter's own guess), a literal, a `select()` of literals (a value-recording check such as `AC_CHECK_SIZEOF` defines its `SIZEOF_*` to it as well), or a replacement `checks.AC_TRY_COMPILE(name = ...)` / `AC_TRY_LINK` |
| `variable:<NAME>` | A substituted or M4 variable is assigned in ways no one check expresses. For `AC_DEFINE_UNQUOTED` a string decision is the C literal's contents, without its quotes | `IGNORE`, `ACCEPT_PARTIAL`, a literal, a `select()`, or `checks.AC_SUBST(...)` with `condition` / `if_true` / `if_false` |
| `guard:<target>:<check>` | A check runs under a condition `requires` cannot spell: a variable no check produces, two platform dimensions, an option mixed with the platform | `ALWAYS`, `NEVER`, a `requires` list, or a `select()` of such lists |
| `macro:<NAME>` | A macro has no port and no translation | `IGNORE`, the label of a port, or the checks that stand in for it |
| `actions:<macro>:<argument>` | A check's action arguments hold shell or calls the converter does not translate | `IGNORE` or the checks that stand in for them |
| `am_conditional:<NAME>` | Sources sit under an automake conditional that is neither an option nor a platform | `ALWAYS`, `NEVER`, or a `config_setting` label whose `select()` arm takes the sources |
| `src_condition:<NAME>` | A gnulib source's `gl_CONDITIONAL` cannot be derived from the ports | `ALWAYS`, `NEVER`, or a condition expression |
| `generated:<path>` | A source is not in the tree and no translatable recipe writes it | `IGNORE`, the rule that writes it (inline, with its `load()` lines), or `":<target>"` for a rule in `BUILD.bazel` |
| `cppflag:<dir>:<NAME>` | A `-D` value only configure computes | `IGNORE` or the C token |
| `link:<dir>/<target>:<item>` | An `LDADD` item is neither a library of the tree nor a `-l` option | `IGNORE`, a dep label, or a linkopt |

The kinds it never asks for, added by hand when wanted:

| Key | Effect | Accepts |
|---|---|---|
| `flag:<option>` | Overrides the default of an option's `bool_flag`, which otherwise comes from the not-given arm's assignment, the variable the value is copied into, or the help string | `True`, `False`, `IGNORE` |
| `program:<path>` | A program configure adds only when a probe holds is emitted with `tags = ["manual"]` and listed in the report with its key (`program:src/hostid`: the program's path from the project root, which is also the target's name); this names the platforms it builds on | `ALWAYS`, `NEVER`, `MANUAL` (defined, `tags = ["manual"]`), or a platform label list |
| `subdir:<dir>` | configure enters an `AC_CONFIG_SUBDIRS` directory only sometimes, and neither the condition nor the Makefile's `if` around `SUBDIRS += <dir>` lowers to a platform or a config setting (gawk enters extension/ when its dlfcn.h probe succeeded); this names where the package's targets build, as their `target_compatible_with`. Until it is decided the package's C/C++ targets are defined with `tags = ["manual"]`, as a probe-conditioned program is, so `//...` leaves them out | `ALWAYS` (built everywhere, the condition dropped), `NEVER` (not converted), `MANUAL` (the condition is not expressible: keep the targets out of `//...`), or a list of platform or config-setting labels; one `"!<label>,<label>"` entry for everywhere but those (`["!@rules_cc//cc/compiler:msvc-cl,//:extensions_off"]`) |
| `copts:<package>`, `conlyopts:<package>`, `cxxopts:<package>` | Compiler options every target of the package compiles with (`.` is the root), after `NO_WARNINGS`: a diagnostic a newer compiler turns into an error in code written for an older one, which no consumer can set on a module it depends on | `["-Wno-error=incompatible-pointer-types"]` |
| `license:<file>` | The SPDX identifier of an exported license file (`COPYING`, `LICENSE`). The converter reads the family and version from the text and the `-only` / `-or-later` suffix from the source headers; a file that does not settle (a custom notice, headers that disagree) is requested with its opening lines. The identifier adds the registry's package metadata: a `license()` over the file, the `package_metadata` with the module's purl, and `package(default_package_metadata = ...)` | `"GPL-3.0-or-later"`, `IGNORE` (exported without metadata) |
| `src_condition:<NAME>` | Overrides the condition the conversion derived for a gnulib source | as above |
| `library:<package>:<name>` | Leaves a registry library out of one package (`.` is the root): the project is built as on a host without it, the not-found path of its probes and pkg-config checks taken. `<name>` is the library as configure spells it, the `-l` name (`lzma`), the pkg-config package (`sdl2`) or the registry module; the report's notes name the key for each library found | `IGNORE` |
| `subst:<NAME>`, `check:<cache variable>` | Drops the project's copy of a result a dependency already provides, when Bazel reports it as produced twice | `IGNORE` |
| `define:<NAME>`, `define:<package>:<NAME>` | Drops the project's copy of a define a dependency already provides (`IGNORE`), or pins the define: a value rewrites the config header's `#undef` line, over the port's result, for a bundled gnulib older than the ports' whose sources want its own m4's answer (grep's `NL_LANGINFO_MTSAFE` on macOS). A bare name pins every config header of the project; with a package (`.` the root) only that package's, where the labels a `select()` names live (gawk's pc/config.h answers on a setting its root `BUILD.bazel` defines) | `IGNORE`, a value, or a `select()` of values |

The [migration guide](./migration.md) says how each autoconf construct maps
to a check, a rule or a `select`, which is what a replacement is written in.

### Bundled decisions

The questions every gnulib project raises the same way, the gettext probes
and substitutions of a project that builds no message catalogs, gnulib's
own witness variables, are answered once, in `gnulib/m4/decisions.bzl`
beside the ports. The converter reads it beneath the project's decisions
file: a key the project's file answers wins, a key it leaves out or
`UNDECIDED` takes the bundled answer, and only then does `DEFAULTS` apply.
The report lists every answer taken from it under "Decisions inherited",
so nothing applies silently; the bundled file's own keys never count as
unused. `--bundled-decisions FILE` reads another file, `none` reads none;
by default the converter finds the checkout's, or the copy in its runfiles
when it runs from elsewhere.

### BUILD.bazel and autoconf.bzl

In project mode every package the conversion writes to has two files.
`autoconf.bzl` is generated: the package's targets are the body of one
macro, `autoconf_targets()`, the constants several rules share stand at the
top level, and `visibility("private")` keeps the file loadable from its own
package alone. The converter writes it whole on every run, formatted twice,
as a BUILD file first (where buildifier expands every rule call and sorts
the lists it sorts) and then as the `.bzl` file the statements move into, so
it reads like a BUILD file. `BUILD.bazel` is hand-written: the converter
writes it once, as a stub that loads the macro and calls it, and never
touches it again. In a registry entry both sit in `overlay/`,
`extension/BUILD.bazel` and `extension/autoconf.bzl` beside a sub-configure
package's. A package the conversion does not write (a `tests/` package of
hand-written tests) is a hand-written `BUILD.bazel` in the same place, which
the converter leaves alone.

`BUILD.bazel` does three things:

- **Adds targets.** Tests, tools, aliases, anything the conversion does not
  produce. A rule it loads from another module makes that module a
  `bazel_dep` of the generated MODULE.bazel. A constant `autoconf.bzl`
  defines at the top level (`NO_WARNINGS`, the option table) can be loaded
  from it; one defined inside the macro (a `glob()`, or a list built from
  one) cannot.
- **Replaces generated targets.** A rule with the same `name` as a generated
  one replaces it: at the next conversion the generated rule is left out of
  `autoconf.bzl`, whose macro docstring names it with a "Targets replaced"
  section of the report, and consumers are unchanged.
- **Provides generated files.** A `generated:<path>` decision of the form
  `":<target>"` names a rule of `BUILD.bazel` that writes the file; the
  converter lists the file among the consumer's sources and emits no rule.
  This is the home for a tool shared by several outputs or a `.bzl` macro of
  its own; a one-line `genrule` fits inline in the decisions file.

For a shape 3 project `BUILD.bazel` holds the libraries and programs
outright: there is no generated output to replace.

**Visibility.** Every generated target carries an explicit `visibility`, so
a `package(default_visibility = ...)` in `BUILD.bazel` never publishes them.
Public is deliberate and small: what `make install` ships, the libraries,
programs and headers and their aliases, and the option flags, which are the
module's configure options. Everything else, the `autoconf` targets, the
config headers, `autoconf_linkopts`, the option settings, the gnulib and
private header libraries and the generated-file rules, is private, or
`//:__subpackages__` when the module has other packages (a sub-configure,
a hand-written `tests/`), which then see them. A tree rendered into a
subdirectory of a larger workspace is confined to that directory instead,
`//third_party/foo:__subpackages__`, and vendored ports are confined the
same way.

A tree or entry converted before this layout, with a generated `BUILD.bazel`
and a hand-written `BUILD.additions.bazel` beside it, is migrated by the
next run: the additive file's contents become the new `BUILD.bazel`, its
loads merged with the `autoconf.bzl` load (which names the top-level
constants the text reads), and the additive file is removed; the report says
so. A constant it read that `autoconf.bzl` now defines inside the macro has to
be declared in `BUILD.bazel` by hand, and the report names those too.

## How it translates

One bullet per construct; the table after them holds the details a reviewer
of the output may want.

- **Expansion.** `autom4te` expands `configure.ac` with a prelude that marks
  every `AC_DEFUN` body and check macro call. The converter never parses m4;
  an `m4_fatal` becomes a warning so the expansion completes.
- **Targets and deps.** Each `AC_DEFUN` body is an `autoconf` target.
  `AC_REQUIRE` and calls of ported macros become `deps` on the ports under
  `//gnulib/m4` and `//autoconf/macros`, the headers' `:defaults` targets
  `default_deps`. A local check a port already produces is left out and
  reported.
- **Checks.** Check macros become `checks.*` and `macros.*` calls, their
  action arguments read as configure runs them. Runtime probes are never
  executed. The language standard configure switches the compiler into is pinned
  on the package, and recorded by a check.
- **Shell.** The shell between calls is evaluated symbolically: the
  condition a call runs under becomes its `requires`, platform cases become
  `select()`s, options become flags with config settings, assignments to
  substituted variables become `AC_SUBST` checks. What the analysis cannot
  follow becomes a decision, never a guess.
- **Registry libraries.** A library the registry provides, that configure
  probes for or pkg-config asks about, becomes a `bazel_dep` and a dep of
  what links it. The checks that compile or link against it move to an
  `autoconf` target of their own with the library as its one `cc_deps`
  entry, which the main target depends on; every other check stays
  toolchain-only, so its result does not depend on the dependency graph.
  A library configure only probes under one of its options (gawk's
  `-lmpfr -lgmp` under `--enable-mpfr`) is a dep in that option's
  `select()` arm, so turning the flag off drops it from the graph.
  configure's own probes stay probes; `PKG_CHECK_MODULES` has none to run
  and is decided at conversion time from the registry's versions.
- **The gnulib tree.** `gnulib-comp.m4` and `gnulib.mk` give the module
  deps, the wrapper headers, the recipe-written headers and the
  conditional sources of one gnulib library.
- **The automake tree.** Every `Makefile.am` gives a `cc_library` per
  library and a `cc_binary` per program, with `LDADD`, `LIBADD` and the
  flag variables resolved into deps, defines, includes and copts.
- **Generated files.** Recipes the converter can read become `write_file`,
  substitution or template rules; `AC_CONFIG_FILES` templates are rendered;
  everything else is a `generated:` decision.
- **Sub-configures.** Each `AC_CONFIG_SUBDIRS` directory is a package of
  the same module, converted from its own tree, sharing the decisions file.
  One configure enters only sometimes builds where configure enters it: a
  host-OS condition or a decided automake conditional becomes the package's
  `target_compatible_with`; a probe-dependent one (gawk's extension/ needs
  dlfcn.h) is a `subdir:` decision, and its targets are tagged `manual`
  until it is made.
- **Licensing.** The license files the release ships are exported, with
  the registry's package metadata over them: the SPDX identifier is read
  from the text and the source headers, and asked for as a `license:`
  decision when they do not settle it.

### Details by construct

| Construct | Becomes | Why it is done that way |
|---|---|---|
| A probe without a cache variable | A check named by its place in the source, `autoconf_probe_configure_475` | A decision keyed by it holds across converter versions |
| `AC_REPLACE_FUNCS` | A function check and a replacement compiled only when the function is missing | As the macro behaves |
| A check that refers to earlier defines | `compile_defines` on the check; the body split into `autoconf_1`, `autoconf_2`, ... where a probe needs an earlier result of the same body | Probes see `confdefs.h` as configure's do |
| `AC_USE_SYSTEM_EXTENSIONS` | The extension macros on every later declaration, type, member, compile and link check | configure defines them before those checks |
| `AC_RUN_IFELSE` | The m4's cross-compiling guess per platform, the program kept in a comment; a guess that is the same everywhere is a `probe:` decision | Nothing is executed at analysis time |
| `AC_PROG_CC_C99`, `AC_PROG_CC_STDC`, `AX_CXX_COMPILE_STDCXX`, `-std=` in `CFLAGS` or a Makefile | The mode configure switches the compiler into is pinned on every target and check of the package (`C_STD_OPTS` / `CXX_STD_OPTS`: `-std=gnu99`, or `/std:c11` on MSVC and clang-cl), and recorded by a `checks.AC_LANG_STANDARD` that does not stop the build; the script's own `test "$ac_cv_prog_cc_c99" = no` + `AC_MSG_ERROR` becomes the check's `error`. `REPO.bazel` carries the same standard as a commented-out toolchain feature, for when rules_cc names them (bazelbuild/rules_cc#936) | Autoconf's macros switch the compiler and record whether they could; only the script stops. The root module's `--conlyopt` / `--cxxopt` come later on the command line and still win |
| `test $x = yes` on a probe's variable | The probe's success | The value is what configure tested |
| A variable copied from a probe's result (`found="$pa_cv_func_x"`), an alias of its name (`AS_VAR_PUSHDEF([cache], ...)` then `AC_CACHE_VAL([cache], ...)`), `AS_VAR_SET` / `AS_VAR_IF` | The probe, under the name the expansion spells | nasm's PA macros name every cache variable through an alias |
| A variable assigned inside a macro the body expands (`PA_ARG_ENABLED([x], ..., [PDFOPT=...])`) | Seen by the calls after the macro and by the body's `AC_SUBST` of it, under the call's condition | configure runs the macro's shell in place |
| A probe over an empty program (`AX_CHECK_COMPILE_FLAG`, `PA_ADD_CFLAGS`, libpcap's `AC_LBL_CHECK_COMPILER_OPT`) | Left out; its cache variable reads `no`, so the flag's defines and additions do not happen | A Bazel toolchain owns the compiler options; the check tested the flag, not the code |
| A probe whose two arms substitute one name to constants (`AC_SUBST([X], [1])` / `[0]`) | One `AC_SUBST(X, condition = <probe>, if_true, if_false)`; a constant an enclosing branch substitutes merges into `if_false` | One producer per name |
| `AC_LIBOBJ` / `AC_REPLACE_FUNCS` objects in a hand-written Makefile's `@LIBOBJS@` | The source from `AC_CONFIG_LIBOBJ_DIR`, next to the Makefile, or where the Makefile's own rule for the object points (`strlcpy.o: $(srcdir)/missing/strlcpy.c`), compiled when the function is missing | The object name says nothing about the source's directory; the rule does |
| gnulib-tool's conditional dependencies (`func_gl_gnulib_m4code_<module> ()` in gl_INIT) | The function bodies read where they are defined, their `gl_CONDITIONAL`s and `AC_LIBOBJ`s under the tests inside them | A static build enables every module, so each body runs once |
| A wrapper-header placeholder no port provides (`@EMULTIHOP_HIDDEN@` in errno.in.h) | `AC_SUBST(name, 0)` in the main target | An unsubstituted `@NAME@` does not compile where the wrapper is generated |
| `gl_MODULE_INDICATOR_INIT_VARIABLE([GNULIB_STDIO_H_SIGPIPE], [1])` / `gl_MODULE_INDICATOR_SET_VARIABLE` in gl_INIT | `AC_SUBST(GNULIB_X, 1)` beside the module indicators, under the call's condition where it has one; the header ports' own `_MODULE_INDICATOR_DEFAULTS` calls and the 0s are left to the ports' defaults | stdio.h renames printf to the SIGPIPE-emulating replacement and signal.h defines SIGPIPE only when these read 1 |
| Shell variables in a probe's program (`#define GMP_NAIL_BITS $GMP_NAIL_BITS`, `#include "$srcdir/gmp-h.in"`) | A literal the body assigns is expanded in place; `$ac_includes_default` is `utils.AC_INCLUDES_DEFAULT`; anything else makes the probe a `probe:` decision | configure expands them before compiling; the rules compile the text as written |
| A platform arm's constant define beside a probe's conditional define of the same name and value (make's `HAVE_DOS_PATHS` from `AS_CASE([$host])` and from a `_WIN32` compile test) | The probe's define; the arm's constant is left out | configure defines it once either way; the rules take one producer per define |
| A macro of a gnulib-tool file whose name no port carries (bison's `m4/printf-posix-rpl.m4`, since merged into printf-posix.m4) | The port target of that name | gnulib-comp.m4's gl_FILE_LIST says gnulib wrote the file, so the macro is the port's, not the project's |
| gnulib.mk's `test $HAVE_X = 0` over a HAVE_* a port probe alone produces | The probe's cache variable (`!ac_cv_func_wcwidth`) | The bare name resolves to the header port's default (`HAVE_WCWIDTH=1`) where the failed probe defined nothing, and `ac_cv_subst_HAVE_X` names that default outright; a name a port's own `AC_SUBST` produces keeps that spelling |
| An automake `else` branch, or `if !COND` (make's `src/posixos.c` under `else` of `if WINDOWSENV`) | The conditional's negation: the other arms of the select the conditional lowers to (`{"@platforms//os:windows": [], "//conditions:default": ["src/posixos.c"]}`), a decision's ALWAYS and NEVER swapped | An else branch is compiled exactly where the if branch is not |
| `AM_CPPFLAGS += -I<dir>` under an automake conditional | The directory in that arm of `includes` | As a `-D` under a conditional goes in that arm of local_defines |
| `AC_SEARCH_LIBS` with actions | The actions as body shell under the search's result, their nested calls translated (an `AC_LIBOBJ` fallback compiled when the search fails) | As for the other singular checks |
| `AC_SEARCH_LIBS` / `AC_CHECK_LIB` of a system library (`-ldl`, `-lrt`, `-lm`) | The `-l` flag configure prepends to `LIBS` (the search's value; a sibling `AC_SUBST` for a library check with the default action) as a substitution the shared `autoconf_linkopts` carries to every program and library | `LIBS` is on every link of the Makefile; on glibc before 2.34 `dlopen` needs `-ldl` |
| `AC_LIB_HAVE_LINKFLAGS([ssl], [crypto], [includes], [code])` | A link probe against `-lssl -lcrypto` defining `HAVE_LIBSSL`, with `HAVE_LIBSSL`, `LIBSSL` and `LTLIBSSL` substituted; the registry's module as the dependency, as for `AC_CHECK_LIB` | gettext's lib-link.m4 is that probe plus a prefix search |
| `-lm`, `-lpthread`, `-ldl`, `-lrt`, `-lws2_32` and the other system libraries | `linkopts`, never a registry module | The toolchain provides them |
| `AC_CANONICAL_HOST` and other once-only library macros called inside a project macro | Their port, as when called at the top level | m4sugar hoists such a body before the caller's output; the call markers travel inside it |
| A call of an autoconf builtin in a shell comment, or an unbracketed one in a `case` arm the shell never ran | Not a definition the expansion missed; noted and left out | The expansion attributes what it expanded |
| `PKG_CHECK_MODULES` under a configure option | The dependency in the option's arm of the targets that link `$(X_LIBS)` | The library is wanted only with the option |
| A variable another module provides with words (an ALLCAPS substitution, a port's M4 variable) | Compared by value | Those values are the interface |
| `AC_MSG_ERROR` | Ends the path; a variable assigned on every continuing path counts as always assigned | configure would not reach the rest |
| `case $host_os` / `$host_cpu` / `$GCC` | `select()`s on `@platforms//os:*`, `@platforms//cpu:*`, `@rules_cc//cc/compiler:*`, one dimension per select | One axis per select keeps the arms readable |
| `AC_ARG_ENABLE` / `AC_ARG_WITH` compared with yes/no | A `BOOL_OPTIONS` table of defaults with a `bool_flag` and a `config_setting` comprehension over it | Thirteen lines an option otherwise |
| An option compared with other values | A `string_flag` (an `int_flag` for numbers) with a `config_setting` per value | The script branches on the value |
| `PKG_CHECK_MODULES([X], [pkg >= 1.2])` | The found action when the registry provides every package at a satisfying version, with `X_CFLAGS` and `X_LIBS` holding what that action gives them (empty otherwise, the dependency carries headers and archive); the not-found action otherwise. `PKG_CHECK_EXISTS` likewise; `PKG_CHECK_VAR` stays a `variable:` decision | There is no probe to run; the registry's version is the one compared and the report says an override is not checked |
| A registry library a target links | `implementation_deps`, after the project's own include directories, unless a public header includes it | Its headers are not part of the target's interface |
| A check that names a registry library (`AC_CHECK_LIB`, `AC_SEARCH_LIBS`, a header check or a probe including one of its headers) | Moved to `autoconf_<lib>`, with every library it links as `cc_deps` (`-lssl -lcrypto`: both openssl targets); the bodies after it depend on it, a check of an earlier body gets `autoconf_<lib>_<n>`, a check under a `select()` arm (libssh2's openssl probe under `--with-crypto=openssl`) the same arm of the library target. A library only ever probed in one option's arm is a `cc_deps` entry, and a dep of the Makefile targets, in that arm alone (gawk's gmp and mpfr under `--enable-mpfr`) | Only the checks that need the dependency see it; the rest share results across modules. An optional library leaves the graph with its option |
| gnulib's conditional sources | `autoconf_srcs(wrap = "include")` | A source's own `#include "sibling.h"` keeps working |
| gnulib's generated wrapper headers | Written under `_gnulib_gen/` (the conditional ones under `_gnulib_cond/`), served ahead of the source directory | A maintainer's copy next to the `.in.h` template must not shadow the rendered one |
| A wrapper header assembled from two files (gnulib's `assert.h`: `assert.in.h`, then `verify.h` through a sed) | A second `autoconf_hdr` rendering the appended file with the recipe's sed as exact substitutions (a deleted range fenced with `#if 0`), spliced in at the template's closing comment through `GNULIB_COND_HDR_INLINES`; a sed the converter cannot translate is reported | The appended half is what gives compilers predating C23 (gcc < 9) a one-argument `_Static_assert` |
| An `EXTRA_*_SOURCES` object | Follows `HAVE_X = 0 \|\| REPLACE_X = 1` over the header's substitutions, or the port's `case $host_os`, or a `src_condition:` decision; `REPLACE_X = 1` lowers to `REPLACE_X != 0` | gnulib's `REPLACE_*` are zero or non-zero, and a newer gnulib answers 2 where an older `gnulib.mk` wrote 1 |
| `SUBDIRS` | Every `Makefile.am` reached, `po`, `doc`, `tests` and the like excepted | Those build no code |
| `LDADD` order | An archive depends on the archives every `LDADD` lists after it | A static link keeps the Makefiles' order |
| `AM_CPPFLAGS`, `_CPPFLAGS`, `_CFLAGS` | Defines, includes and `-f` code-generation options, expanded through the Makefile's own flag variables with automake's installation directories at their defaults; a variable only configure sets is reported | What make would pass, without configure's part |
| `-I<dir>` naming a source directory with headers | A dependency on a shared library of those headers with the directory as its include path, in `implementation_deps` for a library | The headers are declared for the sandbox and the directory stays private to the targets whose flags named it |
| A library or program and the gnulib library | A dependency when its flags put gnulib's directory on the include path or its `LDADD` links the archive; otherwise built without it, as upstream builds it | gnulib's wrapper headers change what a compile sees |
| Several gnulib directories (libpaper's `libgnu/` and `src/libgnu/`, each with its `Makefile.gnulib`) | One gnulib library each, sharing the `autoconf` target; the second's top-level constants carry its name (`GNUPAPER_SRC_CONDITIONS`) | One `gl_INIT` is one set of results |
| A library listed only under an automake conditional | Built when the conditional is a configure option or a decided config setting (`target_compatible_with`), left out when decided `NEVER`, an `am_conditional:` decision otherwise | As for programs |
| Sources under both a `case $host_os` arm and a probe | An `autoconf_srcs` target per platform arm, in that arm's `srcs` and in `deps` | One `select()` dimension at a time |
| `@top_builddir@/dir/file.c`, `$(top_srcdir)/dir/file.c` in a Makefile.am | The file at that path of the tree | What make would compile |
| Installed headers | The library's `hdrs`; the rest compile with the sources | Private names never reach a consumer |
| `-I$(top_srcdir)` (a root header included as `<config.h>`) | `strip_include_prefix = "/"` with `include_prefix = "."` on the root libraries with headers, the config headers library first in `deps` | Bazel 9 serves the virtual include directory for every header; Bazel 7 and 8 build it for a generated header only under an `include_prefix`, and refuse `includes = ["."]` at a main repository's root, which gcc would in any case drop from the quote chain it holds through `-iquote` (a dependency's `config.h` would be found first) |
| `libfoo.a` | A rule named `foo` (`lib/foo` plus a `libfoo` alias when a program `foo` exists); an installed libtool library also a `cc_shared_library` (without `-no-undefined` in its LDFLAGS it links with `-undefined dynamic_lookup` on macOS and is not built on Windows, as libtool does); programs of subdirectories get top-level aliases | The names consumers expect |
| The headers next to the programs of a directory | One top-level glob constant named after the directories, `LIB_SRC_HDRS`, in each program's `srcs` | One glob a hundred programs share |
| Warnings | Every target's `copts` start with `NO_WARNINGS`, a `select()` giving `/w` to MSVC and clang-cl and `-w` to every other compiler; `bison()` runs with `-Wnone`, `flex()` with `--nowarn` | A consumer cannot act on upstream's warnings and cannot change the flags of a module it depends on |
| A program listed only under an automake conditional decided as a config setting, or whose every source is | `target_compatible_with` on that setting | A `select()` with an empty arm would be a binary without sources |
| `bin_PROGRAMS = @bin_PROGRAMS@` | Read from `EXTRA_PROGRAMS` and configure's assignments | The list lives in configure |
| Yacc and lex sources | The registry's `bison()` and `flex()` rules, under `bison/` and `flex/` | The shipped generated files stay out of the way |
| A recipe of `printf` or `echo` lines | A `write_file`, through `autoconf_hdr(mode = "subst")` when it carries configure's values | The content is known |
| A sed rewrite of a header | Substitutions | The content is known |
| A header assembled by `echo` lines under conditionals | One template with `#if` variants | One file, every arm |
| `AC_CONFIG_FILES` templates that are not Makefiles | Rendered by `autoconf_hdr`; a rendered header joins `config_headers` beside the `AC_CONFIG_HEADERS` output | Sources include it as they do `config.h` |
| No `AC_CONFIG_HEADERS` | `autoheader`'s `config.h.in` written next to the BUILD file and rendered as `config.h` behind `HAVE_CONFIG_H` | configure would pass the defines as `-D` flags |
| A sub-configure's headers library | Without the root `config.h` | Bazel searches every dependency repository's root for quoted includes |

## Vendoring the ports

By default the module depends on the gnulib ports of this repository
(`@rules_cc_autoconf//gnulib/m4/...`), which change as the repository tracks
gnulib. `--vendor-ports` copies every port the conversion resolved, and
every port those depend on, into
`third_party/rules_cc_autoconf/<package>/BUILD.bazel` of the module, with
labels rewritten and the in-repository rule replaced by the public
`autoconf` rule. The module is then pinned to the snapshot it was converted
against; regenerating against a newer checkout refreshes the copies and the
diff shows what gnulib changed. The rules themselves, and the two ports that
are rule implementations (`AC_C_INLINE`, `AC_C_RESTRICT`), still come from
rules_cc_autoconf; the report names them. The converter needs the checkout
the ports come from: run it there with `bazel run`, or pass `--index
<checkout>`.

## Converting single m4 files

To port one m4 file the way the hand ports under `//gnulib/m4` are written,
name the file and give a `configure.ac` that invokes its macros:

```sh
bazel run //tools/convert -- convert --src /path/to/project --out /tmp/port --only foo.m4 --in-repo
```

Every other macro resolves to the existing ports. The output is one package
per m4 file with an aggregator target named after the file, in the layout
of `//gnulib/m4/<name>`.

## Checking against GNU autoconf

The `check` subcommand runs the project's real `configure` with the pinned
autotools and the compiler, flags and environment of the current Bazel C++
toolchain, as the conformance tests of this repository do, and diffs the
resulting config header against one Bazel built from the converted
`BUILD.bazel`:

```sh
bazel run //tools/convert -- check --src /path/to/project --out /tmp/check \
    --bazel-config-h lib/config.h=/path/to/output/bazel-bin/lib/config.h
```

The GNU results stay in `/tmp/check/gnu/`. This validates the host platform
only; the conformance tests under `//gnulib/tests` are the reference for the
rest.

## Using the output

There are three ways to take a conversion into a build, from the most to
the least packaged. In each, the files a person edits are `decisions.bzl`
and the `BUILD.bazel` files; everything else is regenerated.

In all three the output directory holds hand-written files beside the
generated ones, and the converter owns only its own. Every file it writes
starts with a banner that says `@generated` and names rules_cc_autoconf;
that mark is what it goes by:

- A file in the way that lacks the mark and differs from what would be
  written stops the run, before anything is written, with the paths listed.
  Hand-written files are kept where they are: move the file aside, or
  delete it. A hand-written `BUILD.bazel` is never in the way, since the
  conversion writes `autoconf.bzl` beside it.
- After writing, the converter's files that this run did not produce are
  removed, and nothing else: a package that left the automake tree leaves
  no stale `autoconf.bzl` behind, while a hand-written file is never
  touched. The report lists what was removed.

A project's own generated files (a parser that says `@generated`) are not
the converter's, since the mark needs both words. `MODULE.bazel` is the one
exception to the first rule: it is always regenerated, and the `module()`
attributes of the file that was there, `bazel_compatibility` and
`compatibility_level` among them, are kept in the new one.

### A registry entry

`--bcr-entry` names the entry in a Bazel Central Registry checkout and takes
everything else from it:

```sh
# the first time: the archive and its integrity
bazel run //tools/convert -- convert --bcr-entry bcr/modules/foo/1.2.bcr.1 \
    --src https://ftp.gnu.org/gnu/foo/foo-1.2.tar.gz --integrity sha256-...
# every time after
bazel run //tools/convert -- convert --bcr-entry bcr/modules/foo/1.2.bcr.1
```

The path gives the module name and version. In the entry directory:

| File | Role |
|---|---|
| `decisions.bzl` | The decisions file: read if present, created if not. |
| `source.json` | Written on the first run from `--src` and `--integrity`; later runs take the archive URL, integrity and `strip_prefix` from it. Its overlay map lists every file under `overlay/`, as the registry's own integrity tool does. |
| `overlay/` | The module's files, hand-written and generated side by side: the generated `autoconf.bzl` files (nested ones included, since a registry overlay may add files anywhere in the archive) and `MODULE.bazel`, the hand-written `BUILD.bazel` beside each, and whatever else the module needs (a `.bzl` a BUILD file loads, a `tests/BUILD.bazel` with its sources). Generated files are replaced, the rest is kept, under the rules above. |
| `MODULE.bazel`, `presubmit.yml`, `../metadata.json` | The registry's own files. `MODULE.bazel` is regenerated, keeping the `module()` attributes an existing one had besides name and version (`bazel_compatibility`, `compatibility_level`); other statements it held are listed in the report. `presubmit.yml` is written when missing and otherwise left alone; the homepage comes from `--homepage`, `AC_INIT` or the existing `metadata.json`. |

An entry converted before this layout, with a generated `BUILD.bazel` and a
`BUILD.additions.bazel` beside it, is migrated by the next run, see
[BUILD.bazel and autoconf.bzl](#buildbazel-and-autoconfbzl).

The report and the scratch output go to `--out`, by default a temporary
directory whose path the run prints, so nothing but the entry lands in the
checkout. The banner of the generated files repeats the `--bcr-entry`
command. Without a checkout, `--bcr` with `--out`, `--source-url`,
`--integrity` and optionally `--bcr-version` writes the same entry under
`<out>/modules/`.

### An http_archive

For a project whose conversion is one BUILD file, a consumer can keep the
generated file in its own repository and inject it with `http_archive`.
`--http-archive-entry` names the directory, conventionally
`3rdparty/<name>/`, and reads `decisions.bzl` and `source.json` from it as
`--bcr-entry` does:

```sh
bazel run //tools/convert -- convert --http-archive-entry 3rdparty/foo \
    --src https://ftp.gnu.org/gnu/foo/foo-1.2.tar.gz --integrity sha256-...
```

It writes the generated BUILD file there as `BUILD.foo.bazel`, the name
`http_archive` users expect for an injected build file (a hand-written file
of that name is not overwritten), records the archive
in `source.json`, and prints the lines the consumer's `MODULE.bazel` needs:
the `bazel_dep`s the generated module declared and the `http_archive` call
with `build_file = "//3rdparty/foo:BUILD.foo.bazel"`. The report carries the
same lines. The consumer also depends on `rules_cc_autoconf`. The injected
file is all generated: there is no hand-written `BUILD.bazel` beside it, so
a project that needs targets of its own takes one of the other two ways.

`http_archive` injects one file. A conversion that writes more, a
sub-configure package, a `config.h.in` the release lacks, vendored ports,
stops with a message naming the extra files: that project is rendered into
its tree instead.

### Rendering into the project

For anything larger than one BUILD file, or when the sources are a checkout
rather than a release archive, point `--out` at the source tree, or copy the
output over it:

```sh
bazel run //tools/convert -- convert --src /path/to/foo --out /path/to/foo \
    --decisions /path/to/foo/decisions.bzl
```

The tree is then a Bazel module: `MODULE.bazel`, the `autoconf.bzl` and
`BUILD.bazel` files and `decisions.bzl` live in it, and a consumer
reaches it with `local_path_override`, `git_override` or `archive_override`,
or by vendoring the tree. Packaging and redistribution are the project's
own concern from there; the converter's part is the same regeneration
command, with the tree as both input and output. A source tree that ships
its own `BUILD.bazel` files keeps them: the converter writes `autoconf.bzl`
beside each, and the report names the BUILD files that do not call
`autoconf_targets()` yet.

## Keeping the converter current

- `tools/convert/index.toml` is a snapshot of the hand ports: which macro
  lives in which target and what each provides. Regenerate it after adding
  or renaming ports with `bazel run //tools/convert:update_index`; CI fails
  when the committed snapshot differs from the ports.
- `tools/convert/libraries.toml` maps `-l` names to registry modules, with
  the version an entry depends on. `bazel run //tools/convert:update_libraries`
  refreshes it from the public registry (`--registry <clone>` reads a clone's
  `origin/main` instead): a module keeps the upstream release the table holds
  and takes its newest `.bcr.N`; moving to another upstream release is per
  module, `--allow-upstream <module>`. A registry entry's
  regeneration pins its dependencies the same way, from the checkout, with
  `convert --allow-upstream <module>` for the exceptions; the newer releases
  left alone are listed as notes.
- The golden tests under `//tools/convert` fix the output for a few gnulib
  modules and for gzip, each with its decisions file next to the expected
  output, and run buildifier with all warnings on it. After an intentional
  change, refresh a golden with
  `bazel run //tools/convert:<name>_golden_test -- --update`.
- The Python is type-checked by mypy in strict mode (`.mypy.ini`) and linted
  and format-checked by ruff (`.ruff.toml`) on every build: the `.bazelrc`
  applies rules_venv's `py_mypy_aspect` and `py_ruff_aspect`, with the
  toolchains in `tools/convert/3rdparty`, so `bazel build //...` fails on a
  type error, a lint finding or an unformatted file. `bazel run
  @rules_venv//python/ruff:format` and `:fix` apply what ruff can fix; a
  target tagged `no_mypy` or `no_ruff` is skipped.
- The prelude mirrors autoconf 2.73 internals. Updating the pinned autoconf
  means revisiting `tools/convert/instrument.m4` and the shape regexes next
  to it.
