"""Instrumented expansion of an autoconf project with the bundled autom4te.

The pipeline is the one `autoconf/tests/gnu/conformance_tester.py` uses to
drive the GNU oracle, minus `./configure`: stage the sources into a scratch
directory, run `aclocal` to collect the needed m4 files into `aclocal.m4`,
then run `autom4te` with `instrument.m4` loaded ahead of them.  The result
is the configure script with marker lines (see `markers.py`).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from autoconf.tests.gnu.autotools import IS_WINDOWS, Autotools, posix

SUPPORTED_AUTOCONF_VERSION = "2.73"

_CONFIGURE_NAMES = ("configure.ac", "configure.in")
# The header aclocal writes; an aclocal.m4 without it is maintained by hand
# (readline, bash: every project macro lives there) and must be kept.
_ACLOCAL_GENERATED = "generated automatically by aclocal"
PROJECT_ACLOCAL = "aclocal.m4"
_GENERATED_ACLOCAL = "aclocal.gen.m4"
ACINCLUDE_PRELUDE = (
    "dnl rules_cc_autoconf convert: sources are not staged for the expansion,\n"
    "dnl so the `test -r` file checks of AC_LIBSOURCES / gl_LIBSOURCES are disabled.\n"
    "m4_define([m4_syscmd], [])\n"
    "dnl The expansion is read for its macro calls, never run: a script's\n"
    "dnl m4_fatal (postgres and binutils insist on autoconf 2.69) is reported\n"
    "dnl in the conversion report instead of ending the expansion.\n"
    "m4_define([m4_fatal], [m4_warn([syntax], [m4_fatal ignored by the converter: $1])])\n"
)
# `m4_include([version])`, `sinclude(config/acx.m4)`: files read at
# expansion time, relative to configure.ac's directory.
# `m4_include([x])`, `sinclude(x)` and tcl's `builtin(include,../unix/tcl.m4)`.
_INCLUDED_FILE_RE = re.compile(r"\b(?:(?:m4_s?include|s?include)\(|builtin\(\s*\[?s?include\]?\s*,\s*)\[?([^\]\),]+?)\]?\)")
_CONFIG_HEADERS_RE = re.compile(r"\bA[CM]_CONFIG_HEADERS?\b")
_MACRO_DIRS_RE = re.compile(r"AC_CONFIG_MACRO_DIRS?\(\[?([^\])]*)\]?\)")
# `aclocal -I DIR` in a bootstrap script: where a project without
# AC_CONFIG_MACRO_DIRS keeps its macros (tftp-hpa: autoconf/m4).
_ACLOCAL_INCLUDE_RE = re.compile(r"aclocal\b[^\n]*?\s-I\s*([^\s\"']+)")
_BOOTSTRAP_SCRIPTS = ("autogen.sh", "bootstrap", "bootstrap.sh", "autogen", "buildconf")
_DEFAULT_MACRO_DIRS = ("m4", "autoconf/m4", "config/m4", "acm4", "macros")
# The converter's own stand-ins (see pkg_stub.m4); a project's pkg.m4 is not staged.
STUB_DIR = "rccac_m4"
_STUBBED_FILES = {"pkg.m4"}


class ExpandError(RuntimeError):
    """autom4te could not produce an expansion."""


@dataclass
class Expansion:
    """Where the expansion landed and what the tools said."""

    work_dir: Path
    expanded: Path
    configure_ac: Path
    aclocal_m4: Path | None
    m4_files: list[str]  # relative paths aclocal.m4 includes, in order
    aclocal_log: str = ""
    autom4te_log: str = ""
    warnings: list[str] = field(default_factory=list)
    # autoheader's config.h.in for a configure.ac without AC_CONFIG_HEADERS (mpfr).
    autoheader_template: Path | None = None

    def read(self) -> str:
        return self.expanded.read_text(encoding="utf-8", errors="replace")


def instrument_template() -> Path:
    """The `instrument.m4` data file (runfiles, falling back to the source tree)."""
    return data_file("instrument.m4")


def data_file(name: str) -> Path:
    """A data file of the converter (runfiles, falling back to the source tree)."""
    rloc = f"_main/tools/convert/{name}"
    try:
        from python.runfiles import Runfiles  # pylint: disable=import-outside-toplevel

        runfiles = Runfiles.Create()
        if runfiles:
            found = runfiles.Rlocation(rloc, "_main" if IS_WINDOWS else None)
            if found and Path(found).exists():
                return Path(found)
    except ImportError:
        pass
    sibling = Path(__file__).resolve().parent / name
    if sibling.exists():
        return sibling
    raise FileNotFoundError(f"Cannot locate {rloc}")


def write_instrument(path: Path, extra_wraps: list[str] | None = None) -> Path:
    """Render `instrument.m4` with extra `_rccac_wrap` lines for `extra_wraps`."""
    template = instrument_template().read_text(encoding="utf-8")
    lines = "".join(f"_rccac_wrap([{name}])\n" for name in sorted(set(extra_wraps or [])))
    text = template.replace("dnl @RCCAC_EXTRA_WRAPS@\n", lines)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def find_configure(src: Path) -> Path:
    for name in _CONFIGURE_NAMES:
        candidate = src / name
        if candidate.is_file():
            return candidate
    raise ExpandError(f"{src}: no configure.ac or configure.in")


_ACLOCAL_AMFLAGS_RE = re.compile(r"^ACLOCAL_AMFLAGS\s*=(.*)$", re.M)
_INCLUDE_FLAG_RE = re.compile(r"-I\s*(\S+)")


def macro_dirs(configure_ac: Path) -> list[str]:
    """The macro directories of a project: `AC_CONFIG_MACRO_DIRS`, else the
    `aclocal -I` directories of its bootstrap script, else the usual names."""
    text = configure_ac.read_text(encoding="utf-8", errors="replace")
    root = configure_ac.parent
    dirs: list[str] = []
    for match in _MACRO_DIRS_RE.finditer(text):
        dirs.extend(part for part in match.group(1).split() if part)
    # `ACLOCAL_AMFLAGS = -I m4 -I src/m4` (libpaper keeps gnulib-comp.m4 in a
    # second directory configure.ac does not name).
    if (root / "Makefile.am").is_file():
        for flags in _ACLOCAL_AMFLAGS_RE.findall((root / "Makefile.am").read_text(encoding="utf-8", errors="replace")):
            dirs.extend(_INCLUDE_FLAG_RE.findall(flags))
    if not dirs:
        for script in _BOOTSTRAP_SCRIPTS:
            candidate = root / script
            if candidate.is_file():
                dirs.extend(_ACLOCAL_INCLUDE_RE.findall(candidate.read_text(encoding="utf-8", errors="replace")))
    if not dirs:
        dirs = [d for d in _DEFAULT_MACRO_DIRS if any((root / d).glob("*.m4"))]
    return list(dict.fromkeys(d.strip("/") for d in dirs if (root / d).is_dir()))


def _stage_included_files(text: str, src: Path, work: Path, renamed: dict[str, str], depth: int = 0) -> None:
    """Copy the files `text` includes by relative path into the staging directory, and the
    files those include in turn (texlive's web2c: configure.ac includes `synctexdir/ac/
    synctex.ac`, which includes a .txt next to it); paths are relative to the project root,
    as autoconf resolves them.  `renamed` maps paths that climb out of the project to the
    staged names the text already uses."""
    if depth > 3:
        return
    for included in _INCLUDED_FILE_RE.findall(text):
        included = included.strip()
        for original, staged_name in renamed.items():  # the text already says the staged name
            if included.startswith(staged_name):
                included_src = original + included[len(staged_name):]
                break
        else:
            included_src = included
        source = src / included_src
        target = work / _staged_name(included)
        if "$" in included or not source.is_file():
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        _stage_included_files(source.read_text(encoding="utf-8", errors="replace"), src, work, renamed, depth + 1)


def _rename_climbing_includes(text: str, renamed: dict[str, str]) -> str:
    """Make `text` say the staged names of the paths it includes that climb out of the project,
    adding the ones it introduces to `renamed`."""
    for included in _INCLUDED_FILE_RE.findall(text):
        included = included.strip()
        if _staged_name(included) != included and not any(included.startswith(d) for d in renamed):
            renamed[included] = _staged_name(included)
    for original, staged_name in sorted(renamed.items(), key=lambda item: -len(item[0])):
        text = text.replace(original, staged_name)
    return text


def _staged_name(relative: str) -> str:
    """Where a project-relative path lands in the staging directory: itself, unless it climbs
    out of the project (texlive's sub-configures say `AC_CONFIG_MACRO_DIRS([../../m4])`),
    in which case a name inside the staging directory stands in for it."""
    parts = relative.replace("\\", "/").split("/")
    if ".." not in parts:
        return relative
    return "_outside_" + re.sub(r"[^A-Za-z0-9]+", "_", relative.strip("/")).strip("_")


def stage(src: Path, work: Path, *, m4_files: list[Path] | None = None) -> tuple[Path, list[str]]:
    """Copy what aclocal/autom4te need from `src` into `work`.

    Args:
        src: project root containing configure.ac.
        work: empty scratch directory.
        m4_files: explicit m4 files to stage into `work/m4` instead of the
            project's own macro directories (the gnulib golden tests pass
            the whole `@gnulib//:all_m4`).

    Returns:
        The staged configure.ac and the macro directories to pass as `-I`.
    """
    configure_ac = find_configure(src)
    staged = work / configure_ac.name
    text = configure_ac.read_text(encoding="utf-8", errors="replace")
    text, version_notes = pin_version_commands(text, src)
    dirs = macro_dirs(configure_ac) if m4_files is None else []
    # Paths that climb out of the project (`AC_CONFIG_MACRO_DIRS([../../m4])`,
    # `m4_include([../../version.ac])` in texlive's sub-configures) are staged
    # under names inside the work directory, and the text says those names.
    renamed = {d: _staged_name(d) for d in dirs if _staged_name(d) != d}
    text = _rename_climbing_includes(text, renamed)
    staged.write_text(text, encoding="utf-8", newline="\n")
    (work / "staging_notes.txt").write_text("\n".join(version_notes), encoding="utf-8", newline="\n")
    # aclocal runs its own autom4te passes without instrument.m4 but always
    # includes acinclude.m4: the file-existence checks (AC_LIBSOURCES,
    # gl_LIBSOURCES: `test -r` through m4_syscmd, AC_FATAL on failure) are
    # disabled there too, since no sources are staged.
    original = ""
    if (src / "acinclude.m4").is_file():
        original = (src / "acinclude.m4").read_text(encoding="utf-8", errors="replace")
    (work / "acinclude.m4").write_text(ACINCLUDE_PRELUDE + original, encoding="utf-8", newline="\n")
    for directory in ("build-aux",):
        if (src / directory).is_dir():
            shutil.copytree(src / directory, work / directory, dirs_exist_ok=True)
    # Files configure.ac pulls in by relative path (`m4_include([version])`,
    # `m4_include(config/acx.m4)`): aclocal and autom4te read them from the
    # staging directory, so they have to be there.
    _stage_included_files(text, src, work, renamed)
    if hand_written_aclocal(src) is not None:
        # The same for the files a hand-written aclocal.m4 includes (postgres:
        # `m4_include([config/ax_pthread.m4])`; tcl: `../unix/tcl.m4`).
        aclocal_text = (src / PROJECT_ACLOCAL).read_text(encoding="utf-8", errors="replace")
        aclocal_text = _rename_climbing_includes(aclocal_text, renamed)
        (work / PROJECT_ACLOCAL).write_text(aclocal_text, encoding="utf-8", newline="\n")
        _stage_included_files(aclocal_text, src, work, renamed)

    if m4_files is not None:
        (work / "m4").mkdir(exist_ok=True)
        for m4_file in m4_files:
            shutil.copyfile(m4_file, work / "m4" / Path(m4_file).name)
        return staged, ["m4"]

    staged_dirs = []
    for directory in dirs:
        source = src / directory
        staged_name = _staged_name(directory)
        if source.is_dir():
            shutil.copytree(source, work / staged_name, dirs_exist_ok=True,
                            ignore=lambda _dir, names: [n for n in names if n in _STUBBED_FILES])
            staged_dirs.append(staged_name)
    (work / STUB_DIR).mkdir(exist_ok=True)
    shutil.copyfile(data_file("pkg_stub.m4"), work / STUB_DIR / "pkg.m4")
    return staged, staged_dirs + [STUB_DIR]


def hand_written_aclocal(src: Path) -> Path | None:
    """The project's aclocal.m4 when it is not an aclocal output."""
    candidate = src / PROJECT_ACLOCAL
    if not candidate.is_file():
        return None
    with candidate.open(encoding="utf-8", errors="replace") as handle:
        head = handle.read(4096)
    return None if _ACLOCAL_GENERATED in head else candidate


_ESYSCMD_RE = re.compile(r"m4_esyscmd(?:_s)?\(\s*(?:\[([^\]]*)\]|([^()\[\]]*))\s*\)")


def pin_version_commands(text: str, src: Path) -> tuple[str, list[str]]:
    """Replace `m4_esyscmd([build-aux/git-version-gen ...])` with the release
    version, so the expansion does not depend on git, a shell, or the host.

    The version comes from `.tarball-version` (what git-version-gen itself
    reads in a release tarball), then `.version`, else "0.0.0".
    """
    notes: list[str] = []

    def version() -> str:
        # tcpdump: `m4_esyscmd_s(cat VERSION)`.
        for name in (".tarball-version", ".version", "VERSION"):
            candidate = src / name
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8", errors="replace").strip()
                if value:
                    return value
        return "0.0.0"

    def replace(match: re.Match) -> str:
        command = match.group(1) if match.group(1) is not None else match.group(2)
        if "git-version-gen" in command or "tarball-version" in command or "version" in command.lower():
            value = version()
            notes.append(f"configure.ac: `m4_esyscmd([{command}])` replaced by [{value}]")
            return f"[{value}]" if match.group(1) is not None else value
        notes.append(f"configure.ac: `m4_esyscmd([{command}])` left as is (runs at expansion time)")
        return match.group(0)

    return _ESYSCMD_RE.sub(replace, text), notes


def _aclocal_includes(aclocal_m4: Path) -> list[str]:
    text = aclocal_m4.read_text(encoding="utf-8", errors="replace")
    return [
        match.group(1).replace("\\", "/")
        for match in re.finditer(r"m4_include\(\[([^\]]+)\]\)", text)
    ]


_PUBLIC_MACRO_RE = re.compile(
    r"^A[CU]_DEFUN(?:_ONCE)?\(\[(AC_(?:PROG|TYPE|FUNC|HEADER|STRUCT|SYS|C|CXX|PATH|CHECK|SEARCH|LIB|USE|"
    r"CANONICAL|OPENMP|EXEEXT|OBJEXT|F77|FC|OBJC|ARG)_[A-Za-z0-9_]*)\]", re.M)
_NEVER_WRAPPED = {"AC_CHECK_INCLUDES_DEFAULT"}
_STATIC_WRAP_RE = re.compile(r"\[(A[CM]_[A-Z0-9_]+)\]")
# Internals that AC_REQUIRE hoists to the top level of configure.ac, where
# their own checks would otherwise pass for the project's.
_HOISTED_INTERNALS = ["_AC_PROG_EGREP_TRADITIONAL", "_AC_PROG_CC_C89", "AC_PROG_EGREP"]


def library_macros(autotools: Autotools) -> list[str]:
    """Public autoconf macros (AC_PROG_CC, AC_TYPE_OFF_T, ...) worth recording.

    They are not DEFUN-marked (defined before the prelude), so without a
    CALL range their internals would look like the caller's own checks.
    """
    names: set[str] = set()
    for path in sorted((autotools.autoconf_lib / "autoconf").glob("*.m4")):
        text = path.read_text(encoding="utf-8", errors="replace")
        names.update(_PUBLIC_MACRO_RE.findall(text))
    names.update(_HOISTED_INTERNALS)
    # The prelude's own list (wrapping twice would nest two CALL ranges).
    static = set(_STATIC_WRAP_RE.findall(instrument_template().read_text(encoding="utf-8")))
    return sorted(names - _NEVER_WRAPPED - static)


def expand(
    autotools: Autotools,
    src: Path,
    work: Path,
    *,
    m4_files: list[Path] | None = None,
    extra_wraps: list[str] | None = None,
    wrap_library: bool = False,
) -> Expansion:
    """Stage `src` into `work` and produce `work/expanded.sh`."""
    if autotools.autoconf_version != SUPPORTED_AUTOCONF_VERSION:
        raise ExpandError(
            f"instrument.m4 mirrors autoconf {SUPPORTED_AUTOCONF_VERSION} internals; "
            f"the bundled autoconf is {autotools.autoconf_version}"
        )
    work.mkdir(parents=True, exist_ok=True)
    configure_ac, dirs = stage(src, work, m4_files=m4_files)
    wraps = list(extra_wraps or [])
    if wrap_library:
        wraps += library_macros(autotools)
    instrument = write_instrument(work / "instrument.m4", wraps)

    warnings: list[str] = []
    staging_notes = work / "staging_notes.txt"
    if staging_notes.is_file():
        warnings += [line for line in staging_notes.read_text(encoding="utf-8").splitlines() if line]
    include_args: list[str] = []
    for directory in dirs:
        include_args += ["-I", directory]

    # A hand-written aclocal.m4 was staged under its own name; aclocal's
    # collection of the -I macro files then goes to a separate output and
    # both are fed to autom4te, the project's file last.
    project_aclocal = (work / PROJECT_ACLOCAL).is_file()
    generated_name = _GENERATED_ACLOCAL if project_aclocal else PROJECT_ACLOCAL
    aclocal = autotools.run("aclocal", include_args + [f"--output={generated_name}"], cwd=work)
    aclocal_log = aclocal.stdout
    if aclocal.returncode != 0:
        raise ExpandError(f"aclocal failed ({aclocal.returncode}):\n{aclocal_log}")
    aclocal_m4: Path | None = work / generated_name
    if not aclocal_m4.exists():
        aclocal_m4 = None
    m4_list = _aclocal_includes(aclocal_m4) if aclocal_m4 else []
    if not (src / "acinclude.m4").is_file():
        m4_list = [f for f in m4_list if f != "acinclude.m4"]  # only the staged prelude
    if project_aclocal:
        m4_list.append(PROJECT_ACLOCAL)
        warnings.append(f"{PROJECT_ACLOCAL} is maintained by hand (not an aclocal output): "
                        "its macros are translated like the project's m4 files")

    expanded = work / "expanded.sh"
    args = [
        "--language=Autoconf-without-aclocal-m4",
        "--cache=autom4te.cache",
        f"--output={expanded.name}",
        *include_args,
        instrument.name,
    ]
    if aclocal_m4 is not None:
        args.append(aclocal_m4.name)
    if project_aclocal:
        args.append(PROJECT_ACLOCAL)
    args.append(configure_ac.name)
    autom4te = autotools.run("autom4te", args, cwd=work)
    autom4te_log = autom4te.stdout
    if not expanded.exists():
        raise ExpandError(f"autom4te failed ({autom4te.returncode}):\n{autom4te_log}")
    for line in autom4te_log.splitlines():
        if "warning:" in line or "error:" in line:
            warnings.append(line.strip())
    if autom4te.returncode != 0:
        # Forbidden-token warnings exit 1 after the output has been written.
        warnings.append(f"autom4te exited {autom4te.returncode} (output kept)")

    # No AC_CONFIG_HEADERS: configure passes every define to the compiler as
    # `-D` flags (DEFS).  The rules render defines into a header, so autoheader
    # writes the template such a header needs (what the BCR mpfr overlay keeps
    # by hand as `config.in`).
    autoheader_template: Path | None = None
    configure_text = configure_ac.read_text(encoding="utf-8", errors="replace")
    configure_text = re.sub(r"(?m)(^|\s)(dnl|#).*$", "", configure_text)  # mpfr: `dnl AC_CONFIG_HEADERS(...)`
    if not _CONFIG_HEADERS_RE.search(configure_text):
        # autoheader writes the template of AC_CONFIG_HEADERS, so a copy of
        # configure.ac names one (ahead of AC_OUTPUT, where configure.ac has it).
        template = work / "autoheader_config.h.in"
        copy_text = configure_ac.read_text(encoding="utf-8", errors="replace")
        header_line = f"AC_CONFIG_HEADERS([{template.name[:-3]}])\n"
        copy_text, n = re.subn(r"(?m)^(\s*AC_OUTPUT\b)", lambda m: header_line + m.group(1), copy_text, count=1)
        if not n:
            copy_text += "\n" + header_line
        copy = work / "autoheader_configure.ac"
        copy.write_text(copy_text, encoding="utf-8", newline="\n")
        autoheader = autotools.run("autoheader", [*include_args, copy.name], cwd=work)
        # `AC_DEFINE([X])` without a description has no template: autoheader
        # names each such symbol and writes nothing.  Give them one and retry.
        missing = re.findall(r"missing template: (\w+)", autoheader.stdout)
        if missing and not template.is_file():
            copy_text = "".join(f"AH_TEMPLATE([{name}], [Defined by configure.ac without a description.])\n"
                                for name in missing) + copy_text
            copy.write_text(copy_text, encoding="utf-8", newline="\n")
            autoheader = autotools.run("autoheader", [*include_args, copy.name], cwd=work)
        if template.is_file():
            autoheader_template = template
        else:
            warnings.append(f"autoheader exited {autoheader.returncode}; no config.h template for the defines: "
                            + autoheader.stdout.strip().splitlines()[-1][:200] if autoheader.stdout.strip() else "")

    return Expansion(
        work_dir=work,
        expanded=expanded,
        configure_ac=configure_ac,
        aclocal_m4=aclocal_m4,
        m4_files=m4_list,
        aclocal_log=aclocal_log,
        autom4te_log=autom4te_log,
        warnings=warnings,
        autoheader_template=autoheader_template,
    )


def scratch_root(name: str) -> Path:
    """A fresh, space-free scratch directory for `bazel run` and tests.

    Unique per call: two conversions may run at the same time (autom4te's
    cache and m4's diversion files would otherwise collide).
    """
    base = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp")
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base))


__all__ = [
    "Expansion",
    "ExpandError",
    "expand",
    "find_configure",
    "hand_written_aclocal",
    "macro_dirs",
    "posix",
    "scratch_root",
    "stage",
    "write_instrument",
]
