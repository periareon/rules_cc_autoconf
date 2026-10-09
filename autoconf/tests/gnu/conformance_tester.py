"""Test runner for `gnu_autoconf_conformance_test`.

Pipeline (each step fails fast):

1. Stage `configure.ac`, templates, aux files and candidate m4 files.
2. `aclocal -I m4` with the bundled automake macros only.
3. `autoconf`, with `m4_pattern_forbid` so unexpanded gnulib macros are errors.
4. `configure` with the compiler, flags and environment of the Bazel C++
   toolchain (as computed by the `autoconf` rule itself).
5. Compare the rendered headers with the Bazel-generated ones.

Everything needed to review a result is written to `TEST_UNDECLARED_OUTPUTS_DIR`:
`gnu/` (the configure work tree: config.h, subst.h, config.log, tool logs,
environment), `bazel/` (the Bazel headers), `<name>.diff` for every
comparison that differs, `versions.json` and `fingerprint.json`.  The staged
inputs that come from pinned repositories (the m4 files, the aux scripts) and
`autom4te.cache` are removed once configure has run; `aclocal.m4` still names
the m4 files that were pulled in.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from python.runfiles import Runfiles

from autoconf.tests.diff_tester import diff
from autoconf.tests.gnu.autotools import IS_WINDOWS, Autotools, posix

COPTS_MARKER = "{rules_cc_autoconf:copts}"
LINKOPTS_MARKER = "{rules_cc_autoconf:linkopts}"

# Variables that are allowed to be missing from the templates. These are
# produced by autoconf/automake machinery rather than by the m4 macro under test.
SKIPPED_OUTPUT_VARIABLES = frozenset(
    [
        "ECHO_C",
        "ECHO_N",
        "ECHO_T",
        "CXX",
        "CXXDEPMODE",
        "CXX_CHOICE",
        "DEPDIR",
        "CXXFLAGS",
        "PROTOTYPES",
        "LIBDL",
        "IGNORE_UNUSED_LIBRARIES_CFLAGS",
        "__PROTOTYPES",
        "ac_ct_CXX",
        "ac_ct_CC",
        "CC",
        "CFLAGS",
        "CPP",
        "CPPFLAGS",
        "DEFS",
        "EXEEXT",
        "LDFLAGS",
        "EGREP",
        "GREP",
        "LIBOBJS",
        "LIBS",
        "LTLIBOBJS",
        "OBJEXT",
        "PATH_SEPARATOR",
        "SHELL",
        "AWK",
        "LIB_ACL",
        "LIB_CLOCK_GETTIME",
        "LIB_DUPLOCALE",
        "LIB_EACCESS",
        "LIB_EXECINFO",
        "LIB_GETHRXTIME",
        "LIB_GETLOGIN",
        "LIB_MBRTOWC",
        "LIB_NANOSLEEP",
        "LIB_NL_LANGINFO",
        "LIB_POLL",
        "LIB_POSIX_SPAWN",
        "LIB_PTHREAD",
        "LIB_SCHED_YIELD",
        "LIB_SELECT",
        "LIB_SELINUX",
        "LIB_SEMAPHORE",
        "LIB_SETLOCALE_NULL",
        "LIB_TIMER_TIME",
        "LIB_XATTR",
        "LIBICONV",
        "LIBMULTITHREAD",
        "LIBPMULTITHREAD",
        "LIBPTHREAD",
        "LIBREADLINE",
        "LIBSOCKET",
        "OBJDUMP",
        "LIBSTDTHREAD",
        "LIBTERMCAP",
        "LIBTHREAD",
        "LIBTHREADLOCAL",
        "am__fastdepCXX_FALSE",
        "am__fastdepCXX_TRUE",
        "PRAGMA_COLUMNS",
        "GPGRT_CONFIG",
        "GETTEXT_MACRO_VERSION",
        "LIB_CRYPTO",
        "LTLIBGCRYPT",
        "LIBGMP",
        "LIBTEXTSTYLE",
        "LTLIBGMP",
        "LTLIBSIGSEGV",
        "LIBTEXTSTYLE_PREFIX",
        "LTLIBTEXTSTYLE",
        "SED",
        "MKDIR_P",
        "SET_MAKE",
        "POSUB",
        "INTL_MACOSX_LIBS",
        "YFLAGS",
        "INTLLIBS",
        "LIBINTL",
        "USE_NLS",
        "AM_VALGRINDFLAGS",
        "DEFAULT_VALGRINDFLAGS",
        "LOG_VALGRIND",
        "PARSE_DATETIME_BISON",
    ]
)

SKIPPED_VARIABLE_PREFIXES = (
    "MSGMERGE",
    "PACKAGE_",
    "VALGRIND",
    "GL_MODULE_INDICATOR_PREFIX_",
    "GNULIB_TEST_",
    "LIBGCRYPT",
    "GL_GENERATE_",
    "ANSICXX",
    "RELOCATABLE",
    "LIBSIGSEGV",
    "AMDEP",
    "LIB_",
    "INSTALL",
    "LTLIB",
    "BISON",
    "HAVE_LD_OUTPUT_DEF",
    "HAVE_LD_VERSION_SCRIPT",
    "LDD",
    "ac_ct_",
    "GMSGFMT",
    "MSGFMT",
    "XGETTEXT",
    "am_",
    "YACC",
    "LOCALE_",
)

SKIPPED_VARIABLE_SUFFIXES = ("_LIBM",)

KEEP_VARIABLES = ["HAVE_LIBM"]

# Prepended to configure.ac so that an unexpanded gnulib macro aborts autoconf
# rather than becoming a silent shell `command not found` at configure time.
PATTERN_FORBID = (
    "m4_pattern_forbid([^gl_[A-Z]])\n"
    "m4_pattern_allow([^gl_ES$])\n"
    "m4_pattern_allow([^gl_LIBOBJS$])\n"
    "m4_pattern_allow([^gl_LTLIBOBJS$])\n"
)


def _should_skip_variable(name: str) -> bool:
    if name in SKIPPED_OUTPUT_VARIABLES:
        return True
    for prefix in SKIPPED_VARIABLE_PREFIXES:
        if name.startswith(prefix) and name not in KEEP_VARIABLES:
            return True
    for suffix in SKIPPED_VARIABLE_SUFFIXES:
        if name.endswith(suffix) and name not in KEEP_VARIABLES:
            return True
    # Lowercase names are internal autoconf variables.
    return name.lower() == name


def _parse_undef_placeholders(content: str) -> set[str]:
    return set(re.findall(r"#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)", content))


def _parse_subst_placeholders(content: str) -> set[str]:
    return set(re.findall(r"@([A-Za-z_][A-Za-z0-9_]*)@", content))


def _extract_config_log_section(content: str, section_name: str) -> str | None:
    pattern = re.compile(
        r"## -+ ##\n## "
        + re.escape(section_name)
        + r" ##\n## -+ ##\n(.*?)(?=\n## -+ ##|\Z)",
        re.DOTALL,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else None


def _parse_expected_variables(config_log: Path) -> tuple[set[str], set[str]]:
    """Variable names from the `Output variables` and `confdefs.h` sections."""
    content = config_log.read_text(encoding="utf-8", errors="replace")

    output_vars: set[str] = set()
    confdef_vars: set[str] = set()

    section = _extract_config_log_section(content, "Output variables.")
    if section is None:
        raise ValueError("Unable to find `Output variables` section in config.log")
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.partition("=")[0].strip()
        if name and not _should_skip_variable(name):
            output_vars.add(name)

    section = _extract_config_log_section(content, "confdefs.h.")
    if section is None:
        raise ValueError("Unable to find `confdefs.h` section in config.log")
    for line in section.splitlines():
        line = line.strip()
        if line.startswith("/* confdefs.h"):
            continue
        if not line:
            break
        match = re.match(r"#define\s+([\w\d_]+)", line)
        if not match:
            if re.match(r"#ifndef |#endif", line):
                continue
            raise ValueError(f"Unexpected confdefs.h value: `{line}`")
        if not _should_skip_variable(match.group(1)):
            confdef_vars.add(match.group(1))

    return output_vars, confdef_vars


def _parse_cache_variables(config_log: Path) -> dict[str, str]:
    """`name=value` pairs from the `Cache variables` section of config.log."""
    content = config_log.read_text(encoding="utf-8", errors="replace")
    section = _extract_config_log_section(content, "Cache variables.")
    values: dict[str, str] = {}
    if not section:
        return values
    for line in section.splitlines():
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _loose(value: object) -> str:
    """Normalise a Bazel or GNU cache value for a loose comparison."""
    if value is None:
        return "no"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).strip()
    if text in ("1", "true", "True"):
        return "yes"
    if text in ("0", "false", "False", ""):
        return "no"
    return text


# cl.exe has no #include_next. GNU gnulib then puts the header's absolute path
# into NEXT_*_H (empty here, as gl_ABSOLUTE_HEADER cannot read cl's
# preprocessor output) and `include` into INCLUDE_NEXT, while the Bazel port
# inlines the system header into NEXT_*_H and leaves INCLUDE_NEXT empty so the
# directive becomes a null directive. The two designs cannot agree, so the
# include-next family is not compared on MSVC.
MSVC_INCLUDE_NEXT_VARIABLES = re.compile(
    r"^(?:[A-Za-z0-9]+_)*"
    r"(?:INCLUDE_NEXT(?:_AS_FIRST_DIRECTIVE)?|NEXT(?:_AS_FIRST_DIRECTIVE)?_[A-Z0-9_]+_H)$"
)
MSVC_INCLUDE_NEXT_NOTE = (
    "INCLUDE_NEXT and NEXT_*_H are not compared on MSVC: cl has no #include_next, "
    "GNU falls back to absolute header paths and Bazel inlines the header."
)

_DEFINED_NAME = re.compile(r"\s*(?:/\*\s*)?#\s*(?:define|undef)\s+([A-Za-z_][A-Za-z0-9_]*)")


_INLINED_HEADER_START = re.compile(r"#ifndef (_RCCAC_GL_NEXT_INLINED_\w+)$")


def _logical_lines(lines: list[str]) -> list[list[str]]:
    """Group each line with the continuation of a multi-line value.

    A system header the Bazel port inlined into NEXT_*_H on MSVC follows its
    `#define` line, bracketed by an `_RCCAC_GL_NEXT_INLINED_*` include guard.
    The value ends with a newline, so the template's closing quote (or, in an
    unquoted template, an empty line) trails the guard.
    """
    groups: list[list[str]] = []
    i = 0
    while i < len(lines):
        end = i
        start = _INLINED_HEADER_START.match(lines[i + 1]) if i + 1 < len(lines) else None
        if re.match(r"\s*#\s*define\s+\w+", lines[i]) and start:
            closing = f"#endif /* {start.group(1)} */"
            end = i + 1
            while end < len(lines) and lines[end] != closing:
                end += 1
            if end + 1 < len(lines) and lines[end + 1] in ('"', ""):
                end += 1
        groups.append(lines[i : end + 1])
        i = end + 1
    return groups


def _mask(
    lines: list[str],
    names: list[str],
    patterns: tuple[re.Pattern[str], ...] = (),
) -> list[str]:
    """Drop lines mentioning any of `names` as a whole word or defining a name matching `patterns`.

    A multi-line string value goes with its `#define` line.
    """
    if not names and not patterns:
        return lines
    # Allow a prefix such as `SUBST_` (subst templates) but not a suffix.
    name_pattern = (
        re.compile(
            r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_])"
        )
        if names
        else None
    )

    def hidden(first: str) -> bool:
        if name_pattern and name_pattern.search(first):
            return True
        match = _DEFINED_NAME.match(first)
        return bool(match) and any(p.match(match.group(1)) for p in patterns)

    kept: list[str] = []
    for group in _logical_lines(lines):
        if not hidden(group[0]):
            kept.extend(group)
    return kept


def _filter_flags(flags: list[str]) -> list[str]:
    """Mirror `CheckRunner::filter_error_flags` from the Bazel checker.

    The checker strips warning-as-error promotions (and clang's
    `-Wincompatible-library-redeclaration`, which breaks autoconf's K&R
    function probes) before running probes, so the oracle must too.
    """
    kept = []
    for flag in flags:
        if flag in (COPTS_MARKER, LINKOPTS_MARKER):
            continue
        if flag in ("-Werror", "-Werror=all", "/WX") or flag.startswith("-Werror="):
            continue
        if flag.startswith("/we") and len(flag) > 3 and flag[3].isdigit():
            continue
        if flag == "-Wincompatible-library-redeclaration":
            continue
        kept.append(flag)
    return kept


def _normalise_cpu(machine: str) -> str:
    return {"arm64": "aarch64", "AMD64": "x86_64", "amd64": "x86_64"}.get(machine, machine)


class GnuAutoconfConformanceTest(unittest.TestCase):
    """Compare GNU autoconf output with Bazel output."""

    msvc = False

    @classmethod
    def setUpClass(cls) -> None:
        cls._locate()
        try:
            cls._stage()
            cls._run_autotools()
            cls._run_configure()
        finally:
            # Also on failure: the failed-test artifacts CI collects should
            # hold the evidence, not another copy of the pinned inputs.
            cls._prune_work_dir()
        cls._fingerprint()
        cls._copy_bazel_headers()

    # ------------------------------------------------------------------ setup

    @classmethod
    def _locate(cls) -> None:
        runfiles = Runfiles.Create()
        if not runfiles:
            raise EnvironmentError("Failed to locate runfiles")
        cls.runfiles = runfiles
        cls.source_repo = "_main" if platform.system() == "Windows" else None

        def get_path(rloc: str) -> Path:
            path = runfiles.Rlocation(rloc, cls.source_repo)
            if not path or not Path(path).exists():
                raise FileNotFoundError(f"Failed to locate: {rloc}")
            return Path(path)

        def optional(var: str) -> Path | None:
            return get_path(os.environ[var]) if os.environ.get(var) else None

        cls.get_path = staticmethod(get_path)

        cls.configure_ac = get_path(os.environ["TEST_CONFIGURE_AC"])
        cls.config_h_in = get_path(os.environ["TEST_CONFIG_H_IN"])
        cls.subst_h_in = optional("TEST_SUBST_H_IN")
        cls.bazel_config_h = optional("TEST_BAZEL_CONFIG_H")
        cls.bazel_subst_h = optional("TEST_BAZEL_SUBST_H")
        cls.cache_manifest = optional("TEST_CACHE_MANIFEST")
        cls.cc_env_file = get_path(os.environ["TEST_CC_ENV"])
        cls.known_divergences: dict[str, str] = json.loads(
            os.environ.get("TEST_KNOWN_DIVERGENCES", "{}")
        )
        cls.configure_args: list[str] = json.loads(os.environ.get("TEST_CONFIGURE_ARGS", "[]"))

        m4_list = get_path(os.environ["TEST_M4_LIST"])
        cls.m4_files = [
            get_path(line.strip())
            for line in m4_list.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cls.aux_files = [get_path(p) for p in os.environ.get("TEST_AUX_FILES", "").split()]
        cls.build_aux_files = [
            get_path(p) for p in os.environ.get("TEST_BUILD_AUX_FILES", "").split()
        ]

        cls.outputs_dir = Path(
            os.environ.get(
                "TEST_UNDECLARED_OUTPUTS_DIR",
                os.environ.get("TEST_TMPDIR", tempfile.gettempdir()),
            )
        )
        cls.work_dir = cls.outputs_dir / "gnu"
        if cls.work_dir.exists():
            shutil.rmtree(cls.work_dir)
        cls.work_dir.mkdir(parents=True)

        tooldir = Path(os.environ.get("TEST_TMPDIR", tempfile.gettempdir())) / "autotools"
        if tooldir.exists():
            shutil.rmtree(tooldir)
        cls.autotools = Autotools(tooldir, shell=os.environ.get("CONFIG_SHELL"))

    @classmethod
    def _stage(cls) -> None:
        for aux in cls.aux_files + cls.build_aux_files:
            shutil.copy2(aux, cls.work_dir / aux.name)

        m4_dir = cls.work_dir / "m4"
        m4_dir.mkdir()
        for m4_file in cls.m4_files:
            shutil.copy2(m4_file, m4_dir / m4_file.name)

        content = cls.configure_ac.read_text(encoding="utf-8")
        if "AC_CONFIG_MACRO_DIRS" not in content:
            content = content.replace(
                "AC_CONFIG_HEADERS([config.h])",
                "AC_CONFIG_HEADERS([config.h])\nAC_CONFIG_MACRO_DIRS([m4])",
            )
        # Every real configure.ac runs AC_PROG_CC before any gnulib macro
        # (gnulib-tool requires it before gl_EARLY). Some gnulib macros rely on
        # that ordering rather than AC_REQUIRE-ing the compiler themselves:
        # gl_WCHAR_H_INLINE_OK links `conftest1.$ac_objext` and fails when
        # $ac_objext is still unset.
        if "AC_PROG_CC" not in content:
            content = content.replace(
                "AC_CONFIG_HEADERS([config.h])",
                "AC_CONFIG_HEADERS([config.h])\nAC_PROG_CC",
            )
        (cls.work_dir / "configure.ac").write_text(PATTERN_FORBID + content, encoding="utf-8")

        shutil.copy2(cls.config_h_in, cls.work_dir / cls.config_h_in.name)
        if cls.subst_h_in:
            shutil.copy2(cls.subst_h_in, cls.work_dir / cls.subst_h_in.name)

        (cls.work_dir / "Makefile.in").write_text(
            "# Minimal Makefile.in for autoconf testing\nall:\n\t@echo ok\n",
            encoding="utf-8",
        )

    @classmethod
    def _run_autotools(cls) -> None:
        aclocal = cls.autotools.run("aclocal", ["-I", "m4"], cwd=cls.work_dir)
        (cls.work_dir / "aclocal.log").write_text(aclocal.stdout, encoding="utf-8")
        assert aclocal.returncode == 0, f"aclocal failed:\n{aclocal.stdout}"
        missing = re.findall(r"macro '([^']+)' not found in library", aclocal.stdout)
        assert not missing, (
            f"aclocal could not find macros {sorted(set(missing))}; "
            "is the module's m4 file (and its dependencies) in `m4_files`?"
        )
        # aclocal writes nothing when configure.ac needs no third-party macros.

        autoconf = cls.autotools.run("autoconf", [], cwd=cls.work_dir)
        (cls.work_dir / "autoconf.log").write_text(autoconf.stdout, encoding="utf-8")
        assert autoconf.returncode == 0, f"autoconf failed:\n{autoconf.stdout}"
        assert (cls.work_dir / "configure").exists(), "configure script was not generated"

    @classmethod
    def _compiler_environment(cls) -> dict[str, str]:
        """CC/CXX/CFLAGS/... from the Bazel C++ toolchain."""
        cc = json.loads(cls.cc_env_file.read_text(encoding="utf-8"))

        def resolve(tool: str) -> str:
            if os.path.isabs(tool):
                return tool
            path = cls.runfiles.Rlocation(tool, cls.source_repo)
            if not path or not Path(path).exists():
                raise FileNotFoundError(f"Compiler not found in runfiles: {tool}")
            return str(Path(path).resolve())

        cls.compiler_type = cc["compiler_type"]
        msvc = cls.compiler_type.startswith("msvc") or cc["c_compiler"].lower().endswith(("cl", "cl.exe"))
        cls.msvc = msvc

        def dashed(values: list[str]) -> list[str]:
            kept = _filter_flags(values)
            if msvc:
                # cl.exe accepts `-X` for every `/X`; the dash form survives
                # bash and gnulib's `compile` wrapper untouched.
                kept = ["-" + f[1:] if f.startswith("/") else f for f in kept]
            return kept

        def flags(values: list[str]) -> str:
            return " ".join(dashed(values))

        def link_flags(values: list[str]) -> str:
            kept = dashed(values)
            if msvc:
                # cl.exe reads a bare `-DEFAULTLIB:x` as the macro definition
                # `-D EFAULTLIB:x`. The checker hands link flags to link.exe in
                # a trailing `/link` block; `-Wl,` makes the compile wrapper do
                # the same.
                kept = ["-Wl," + f for f in kept]
            return " ".join(kept)

        cls.cc_path = resolve(cc["c_compiler"])
        cxx_path = resolve(cc["cpp_compiler"])
        if msvc:
            # configure speaks `-o`/`-c`; gnulib's compile wrapper translates
            # for cl.exe. Wrap cl in a script so paths with spaces survive the
            # word splitting configure applies to $CC.
            compile_wrapper = cls.work_dir / "compile"
            assert compile_wrapper.exists(), "gnulib build-aux/compile is required for MSVC"
            cl = cls._shell_wrapper("cl", cls.cc_path)
            cc_cmd = f"{posix(compile_wrapper)} {posix(cl)}"
            cxx_cmd = cc_cmd
        else:
            cc_cmd = cls.cc_path
            cxx_cmd = cxx_path
        env = {
            "CC": cc_cmd,
            "CXX": cxx_cmd,
            "CFLAGS": flags(cc["c_flags"]),
            "CXXFLAGS": flags(cc["cpp_flags"]),
            "LDFLAGS": link_flags(cc["c_link_flags"]),
        }
        if msvc and cc.get("linker"):
            # gnulib's AC_LIB_PROG_LD searches PATH for `ld` and aborts
            # configure without one. cl drives link.exe; name it explicitly.
            env["LD"] = posix(cls._shell_wrapper("msvc-link", resolve(cc["linker"])))
        env.update(cc.get("env", {}))
        return env

    @classmethod
    def _shell_wrapper(cls, name: str, target: str) -> Path:
        """A `#!/bin/sh` script that execs `target` with all arguments."""
        wrapper = cls.autotools.bindir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{posix(target)}" "$@"\n', encoding="utf-8", newline="\n")
        wrapper.chmod(0o755)
        return wrapper

    @classmethod
    def _run_configure(cls) -> None:
        shell = cls.autotools.shell
        cc_env = cls._compiler_environment()
        # The C++ toolchain may carry its own PATH (MSVC does); merge it
        # instead of letting it replace the autotools and shell directories.
        env = cls.autotools.environment(tool_path=cc_env.pop("PATH", None))
        env["FORCE_UNSAFE_CONFIGURE"] = "1"
        env.update(cc_env)
        cls.configure_env = env
        (cls.work_dir / "environment.json").write_text(
            json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        result = subprocess.run(
            [shell, "./configure"] + cls.configure_args,
            cwd=cls.work_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        (cls.work_dir / "configure.log").write_text(result.stdout, encoding="utf-8")
        assert result.returncode == 0, f"configure failed:\n{result.stdout}"
        assert "command not found" not in result.stdout, (
            "configure hit an unexpanded macro:\n" + result.stdout
        )

        cls.gnu_config_h = cls.work_dir / "config.h"
        assert cls.gnu_config_h.exists(), "configure did not write config.h"
        # Drop the `/* config.h.  Generated from config.h.in by configure.  */` line.
        cls.gnu_config_lines = cls.gnu_config_h.read_text(encoding="utf-8").splitlines()[1:]

        cls.gnu_subst_lines: list[str] | None = None
        if cls.subst_h_in:
            cls.gnu_subst_h = cls.work_dir / "subst.h"
            assert cls.gnu_subst_h.exists(), "configure did not write subst.h"
            cls.gnu_subst_lines = cls.gnu_subst_h.read_text(encoding="utf-8").splitlines()

    @classmethod
    def _prune_work_dir(cls) -> None:
        """Drop staged inputs and caches that add nothing to a review.

        The m4 files are the pinned gnulib checkout, the aux scripts come from
        the pinned automake/gnulib, and `autom4te.cache` is autoconf's scratch
        space; together they are several MB per test.  What configure
        produced (config.h, subst.h, config.log, the logs, the environment)
        and the generated `configure`, which config.log cites by line, stay.
        """
        for name in ("m4", "autom4te.cache"):
            shutil.rmtree(cls.work_dir / name, ignore_errors=True)
        for staged in cls.aux_files + cls.build_aux_files:
            (cls.work_dir / staged.name).unlink(missing_ok=True)

    @classmethod
    def _fingerprint(cls) -> None:
        def first_line(argv: list[str], env: dict[str, str] | None = None) -> str:
            try:
                out = subprocess.run(
                    argv,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    check=False,
                ).stdout.strip()
            except OSError:
                return ""
            return out.splitlines()[0] if out else ""

        sdk_version = ""
        if platform.system() == "Darwin" and shutil.which("xcrun"):
            sdk_version = first_line(["xcrun", "--show-sdk-version"])
        elif IS_WINDOWS:
            sdk_version = os.environ.get("WindowsSDKVersion", cls.configure_env.get("WindowsSDKVersion", "")).strip("\\")

        versions = cls.autotools.versions(cls.work_dir)
        versions["shell"] = cls.configure_env["CONFIG_SHELL"]
        if cls.compiler_type.startswith("msvc"):
            # cl.exe prints its banner (with the version) to stderr when run bare.
            versions["compiler"] = first_line([cls.cc_path], cls.configure_env)
        else:
            versions["compiler"] = first_line([cls.cc_path, "--version"], cls.configure_env)
        (cls.outputs_dir / "versions.json").write_text(
            json.dumps(versions, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        cls.fingerprint = {
            "os": platform.system().lower(),
            "cpu": _normalise_cpu(platform.machine()),
            "compiler_type": cls.compiler_type,
            "compiler_version": versions["compiler"],
            "sdk_version": sdk_version,
            "autoconf_version": cls.autotools.autoconf_version,
            "automake_version": cls.autotools.automake_version,
        }
        (cls.outputs_dir / "fingerprint.json").write_text(
            json.dumps(cls.fingerprint, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @classmethod
    def _copy_bazel_headers(cls) -> None:
        """Keep the Bazel side of every comparison next to the GNU side."""
        bazel_dir = cls.outputs_dir / "bazel"
        bazel_dir.mkdir(exist_ok=True)
        if cls.bazel_config_h:
            shutil.copy2(cls.bazel_config_h, bazel_dir / "config.h")
        if cls.bazel_subst_h:
            shutil.copy2(cls.bazel_subst_h, bazel_dir / "subst.h")

    # ---------------------------------------------------------------- helpers

    def _cache_report(self) -> str:
        """Cache variables that differ between GNU and Bazel (diagnostic only)."""
        if self.cache_manifest is None:
            return ""
        manifest = json.loads(self.cache_manifest.read_text(encoding="utf-8"))
        gnu = _parse_cache_variables(self.work_dir / "config.log")
        rows = []
        for name in sorted(manifest):
            if name not in gnu:
                continue
            result_path = self.runfiles.Rlocation(manifest[name], self.source_repo)
            if not result_path or not Path(result_path).exists():
                continue
            result = json.loads(Path(result_path).read_text(encoding="utf-8"))
            bazel_value = None if not result.get("success", False) else result.get("value")
            if _loose(bazel_value) != _loose(gnu[name]):
                rows.append(f"  {name}: gnu={gnu[name]!r} bazel={bazel_value!r}")
        if not rows:
            return ""
        return "\nCache variables that differ (GNU config.log vs Bazel results):\n" + "\n".join(rows)

    def _divergence_note(self) -> str:
        lines = [f"  {k}: {v}" for k, v in sorted(self.known_divergences.items())]
        if self.msvc:
            lines.append("  " + MSVC_INCLUDE_NEXT_NOTE)
        if not lines:
            return ""
        return "\nKnown divergences masked from the comparison:\n" + "\n".join(lines)

    def _compare_with_bazel(self, gnu_lines: list[str], bazel: Path, label: str) -> None:
        masked = sorted(self.known_divergences)
        patterns = (MSVC_INCLUDE_NEXT_VARIABLES,) if self.msvc else ()
        bazel_lines = bazel.read_text(encoding="utf-8").splitlines()
        output = diff(
            file1_content=_mask(gnu_lines, masked, patterns),
            file2_content=_mask(bazel_lines, masked, patterns),
            file1_name=f"gnu/{label}",
            file2_name=f"bazel/{label}",
        )
        note = self._divergence_note()
        if note:
            print(note, file=sys.stderr)
        diff_file = self.outputs_dir / f"{label}.diff"
        if output:
            diff_file.write_text(output + "\n", encoding="utf-8")
            divider = "=" * 70
            self.fail(
                f"GNU autoconf and Bazel disagree on {label}\n{divider}\n"
                f"```diff\n{output}\n```{self._cache_report()}{note}"
            )

    # ------------------------------------------------------------------ tests

    def test_template_files_have_all_variables(self) -> None:
        """The templates must mention every variable configure produced."""
        if not os.getenv("VERIFY_VARIABLES"):
            self.skipTest("Verification was not requested")

        config_log = self.work_dir / "config.log"
        assert config_log.exists(), "config.log not found"

        output_variables, confdef_variables = _parse_expected_variables(config_log)
        all_variables = sorted(output_variables | confdef_variables)
        (self.outputs_dir / "parsed_variables.json").write_text(
            json.dumps(all_variables, indent=2), encoding="utf-8"
        )

        define_vars = _parse_undef_placeholders(self.config_h_in.read_text(encoding="utf-8"))
        missing_in_config = [v for v in all_variables if v not in define_vars]
        missing_in_subst: list[str] = []
        if self.subst_h_in:
            subst_vars = _parse_subst_placeholders(self.subst_h_in.read_text(encoding="utf-8"))
            missing_in_subst = [v for v in all_variables if v not in subst_vars]

        (self.outputs_dir / "expected_config.h.in").write_text(
            "\n".join(["/* config.h.in */"] + [f"#undef {v}" for v in all_variables] + [""]),
            encoding="utf-8",
        )
        (self.outputs_dir / "expected_subst.h.in").write_text(
            "\n".join(
                ["/* subst.h.in */"]
                + ['#define SUBST_{0} "@{0}@"'.format(v) for v in all_variables]
                + [""]
            ),
            encoding="utf-8",
        )

        errors = []
        target = os.environ.get("TEST_TARGET", "")
        if missing_in_config:
            errors.append(
                f"({target}) config.log has {len(missing_in_config)} defines not found in `config.h.in`:\n"
                + "\n".join(f"  - {v}" for v in missing_in_config)
            )
        if missing_in_subst:
            errors.append(
                f"({target}) config.log has {len(missing_in_subst)} substs not found in `subst.h.in`:\n"
                + "\n".join(f"  - {v}" for v in missing_in_subst)
            )
        if errors:
            self.fail("\n\n".join(errors))

    def test_config_h_matches_bazel(self) -> None:
        """Bazel's config.h must equal GNU autoconf's config.h."""
        if self.bazel_config_h is None:
            self.skipTest("No Bazel config.h provided")
        self._compare_with_bazel(self.gnu_config_lines, self.bazel_config_h, "config.h")

    def test_subst_h_matches_bazel(self) -> None:
        """Bazel's subst header must equal GNU autoconf's."""
        if self.bazel_subst_h is None or self.gnu_subst_lines is None:
            self.skipTest("No Bazel subst header provided")
        self._compare_with_bazel(self.gnu_subst_lines, self.bazel_subst_h, "subst.h")


if __name__ == "__main__":
    unittest.main()
