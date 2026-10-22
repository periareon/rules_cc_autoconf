"""`check`: run GNU configure on a project with the Bazel C++ toolchain and
compare its config header with one Bazel built from the converted BUILD.

This is the oracle the migration guide's conformance tests use, applied to
a whole project: `aclocal`, `autoconf` and `./configure` run with the pinned
autotools and with the compiler, flags and environment of the current C++
toolchain (`CONVERT_CC_ENV`, written by `autotools_cc_binary`).  The GNU
results are left in `<out>/gnu/` (config headers, config.log, the output
variables); when `--bazel-config-h` is given the two headers are diffed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

from autoconf.tests.diff_tester import diff
from autoconf.tests.gnu.autotools import IS_WINDOWS, Autotools, posix, space_free
from tools.convert import expand as expand_mod

try:
    from python.runfiles import Runfiles

    RUNFILES: type[Runfiles] | None = Runfiles
except ImportError:  # outside Bazel: only absolute compiler paths resolve
    RUNFILES = None

# The conformance tests' cache presets (keep configure in the language mode
# Bazel compiles in), plus no automake dependency tracking: its bootstrap
# needs GNU make, which the oracle does not (and Windows hosts lack).
DEFAULT_CONFIGURE_ARGS = ["ac_cv_prog_cc_c23=no", "ac_cv_prog_cc_c11=", "--disable-dependency-tracking"]
_IGNORED_DIRS = {".git", ".hg", ".svn", "bazel-bin", "bazel-out", "bazel-testlogs", "node_modules"}
_CONFIG_HEADERS_RE = re.compile(r"AC_CONFIG_HEADERS?\(\[?([^\])]*)\]?\)")


@dataclass
class CheckResult:
    """What one `check` run left behind (`run_check`)."""

    work_dir: Path
    """The staged copy of the sources configure ran in: config.log, the headers, the output variables."""

    headers: list[Path]
    """The rendered config headers (in `work_dir`)."""

    substs: dict[str, str]
    """configure's output variables, `NAME -> value`, from config.log."""

    differences: dict[str, str] = field(default_factory=dict)
    """Header path (relative to `work_dir`) -> the unified diff against the Bazel-built header."""


def filter_flags(values: list[str]) -> list[str]:
    """Drop warning-as-error flags and the rules' own markers (as the conformance test does)."""
    kept = []
    for flag in values:
        if flag in ("{rules_cc_autoconf:copts}", "{rules_cc_autoconf:linkopts}"):
            continue
        if flag.startswith("-Werror") or flag in ("/WX", "-Wincompatible-library-redeclaration"):
            continue
        if re.fullmatch(r"/we\d+", flag):
            continue
        kept.append(flag)
    return kept


class CcEnv(TypedDict):
    """The C++ toolchain description `autotools_cc_binary` writes (`cc_env.bzl`): the tools as
    rlocationpaths (absolute paths when the toolchain names them so), their flags and the
    environment the toolchain's actions run with."""

    c_compiler: str
    c_flags: list[str]
    c_link_flags: list[str]
    compiler_type: str
    cpp_compiler: str
    cpp_flags: list[str]
    cpp_link_flags: list[str]
    env: dict[str, str]
    linker: str


def compiler_environment(cc: CcEnv, autotools: Autotools, work: Path, resolve: Callable[[str], str]) -> dict[str, str]:
    """CC/CXX/CFLAGS/... for configure from the toolchain description.

    `resolve` maps an rlocationpath to an absolute path.  Mirrors the
    conformance tester: `/X` flags become `-X` for cl.exe, link flags are
    passed through `-Wl,`, and cl is driven through gnulib's `compile` wrapper.
    """
    compiler_type = cc["compiler_type"]
    msvc = compiler_type.startswith("msvc") or cc["c_compiler"].lower().endswith(("cl", "cl.exe"))

    def dashed(values: list[str]) -> list[str]:
        kept = filter_flags(values)
        if msvc:
            kept = ["-" + f[1:] if f.startswith("/") else f for f in kept]
        return kept

    def shell_wrapper(name: str, target: str) -> Path:
        wrapper = autotools.bindir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{posix(target)}" "$@"\n', encoding="utf-8", newline="\n")
        wrapper.chmod(0o755)
        return wrapper

    cc_path = resolve(cc["c_compiler"])
    cxx_path = resolve(cc["cpp_compiler"])
    if msvc:
        compile_wrapper = next((p for p in (work / "build-aux/compile", work / "compile") if p.exists()), None)
        if compile_wrapper is None:
            raise RuntimeError("configure with cl.exe needs gnulib's build-aux/compile wrapper in the project")
        cl = shell_wrapper("cl", cc_path)
        cc_cmd = f"{posix(compile_wrapper)} {posix(cl)}"
        cxx_cmd = cc_cmd
    else:
        cc_cmd = cc_path
        cxx_cmd = cxx_path
    link = dashed(cc["c_link_flags"])
    if msvc:
        link = ["-Wl," + f for f in link]
    env = {
        "CC": cc_cmd,
        "CXX": cxx_cmd,
        "CFLAGS": " ".join(dashed(cc["c_flags"])),
        "CXXFLAGS": " ".join(dashed(cc["cpp_flags"])),
        "LDFLAGS": " ".join(link),
    }
    if msvc and cc.get("linker"):
        env["LD"] = posix(shell_wrapper("msvc-link", resolve(cc["linker"])))
    env.update(cc.get("env", {}))
    return env


def config_headers(configure_ac: Path) -> list[str]:
    """The output paths of AC_CONFIG_HEADERS (`out:template` -> `out`)."""
    text = configure_ac.read_text(encoding="utf-8", errors="replace")
    out = []
    for match in _CONFIG_HEADERS_RE.finditer(text):
        for entry in match.group(1).split():
            out.append(entry.partition(":")[0])
    return out or ["config.h"]


def output_variables(config_log: Path) -> dict[str, str]:
    """`NAME -> value` from the `## Output variables. ##` section of config.log."""
    content = config_log.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"## -+ ##\n## Output variables\. ##\n## -+ ##\n(.*?)(?=\n## -+ ##|\Z)", content, re.DOTALL)
    values: dict[str, str] = {}
    if not match:
        return values
    for line in match.group(1).splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        values[name.strip()] = value
    return values


def run_check(
    autotools: Autotools,
    src: Path,
    work: Path,
    *,
    cc_env: CcEnv,
    resolve: Callable[[str], str],
    configure_args: list[str] | None = None,
    bazel_headers: dict[str, Path] | None = None,
    regenerate: bool = False,
) -> CheckResult:
    """Stage `src` into `work`, run the GNU pipeline, compare headers.

    `regenerate` runs aclocal and autoconf even when the tree ships a
    `configure` script (release tarballs do; checkouts do not).
    """
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(src, work, ignore=lambda d, names: [n for n in names if n in _IGNORED_DIRS])
    # configure word-splits $CC and friends: the tree needs a spelling
    # without spaces (the 8.3 short name on Windows).
    work = Path(space_free(work.resolve()))
    configure_ac = expand_mod.find_configure(work)
    text, _ = expand_mod.pin_version_commands(configure_ac.read_text(encoding="utf-8", errors="replace"), src)
    configure_ac.write_text(text, encoding="utf-8", newline="\n")
    # The `test -r` file checks of AC_LIBSOURCES go through m4_syscmd, which
    # runs under cmd.exe with the Windows m4 and fails although the sources
    # are present; they add nothing here (see expand.ACINCLUDE_PRELUDE).
    acinclude = work / "acinclude.m4"
    original = acinclude.read_text(encoding="utf-8", errors="replace") if acinclude.is_file() else ""
    acinclude.write_text(expand_mod.ACINCLUDE_PRELUDE + original, encoding="utf-8", newline="\n")

    # A checkout: generate configure as the project's maintainers would.  A release tarball
    # ships the configure its maintainers generated: that script, not one regenerated from
    # the m4 directory, is the oracle.
    if regenerate or not (work / "configure").is_file():
        include_args: list[str] = []
        for directory in expand_mod.macro_dirs(configure_ac):
            if (work / directory).is_dir():
                include_args += ["-I", directory]
        aclocal = autotools.run("aclocal", include_args, cwd=work)
        if aclocal.returncode != 0:
            raise RuntimeError(f"aclocal failed ({aclocal.returncode}):\n{aclocal.stdout}")
        autoconf = autotools.run("autoconf", [], cwd=work)
        if autoconf.returncode != 0 or not (work / "configure").exists():
            raise RuntimeError(f"autoconf failed ({autoconf.returncode}):\n{autoconf.stdout}")

    cc = compiler_environment(cc_env, autotools, work, resolve)
    env = autotools.environment(tool_path=cc.pop("PATH", None))
    env["FORCE_UNSAFE_CONFIGURE"] = "1"
    env.update(cc)
    (work / "environment.json").write_text(json.dumps(env, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = subprocess.run(
        [autotools.shell, "./configure"] + (configure_args if configure_args is not None else DEFAULT_CONFIGURE_ARGS),
        cwd=work,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (work / "configure.out").write_text(result.stdout, encoding="utf-8", newline="\n")
    if result.returncode != 0:
        raise RuntimeError(
            f"configure failed ({result.returncode}); see {work / 'config.log'}:\n"
            + "\n".join(result.stdout.splitlines()[-30:])
        )

    headers = [work / h for h in config_headers(configure_ac) if (work / h).is_file()]
    substs = output_variables(work / "config.log") if (work / "config.log").is_file() else {}
    (work / "output_variables.txt").write_text(
        "".join(f"{k}={v}\n" for k, v in sorted(substs.items())), encoding="utf-8", newline="\n"
    )

    differences: dict[str, str] = {}
    for header in headers:
        bazel = (bazel_headers or {}).get(header.relative_to(work).as_posix()) or (bazel_headers or {}).get(header.name)
        if bazel is None:
            continue
        gnu_lines = header.read_text(encoding="utf-8", errors="replace").splitlines()[1:]
        bazel_lines = bazel.read_text(encoding="utf-8", errors="replace").splitlines()
        delta = diff(
            gnu_lines,
            bazel_lines,
            file1_name=f"gnu/{header.relative_to(work).as_posix()}",
            file2_name=f"bazel/{bazel.name}",
        )
        if delta:
            differences[header.relative_to(work).as_posix()] = delta
    return CheckResult(work, headers, substs, differences)


def load_cc_env(path: str | None) -> tuple[CcEnv, Callable[[str], str]]:
    """The toolchain description and an rlocationpath resolver."""
    runfiles = RUNFILES.Create() if RUNFILES is not None else None
    source_repo = "_main" if IS_WINDOWS else None

    def resolve(tool: str) -> str:
        if os.path.isabs(tool):
            return tool
        found = runfiles.Rlocation(tool, source_repo) if runfiles else None
        if not found or not Path(found).exists():
            raise FileNotFoundError(f"compiler not found in runfiles: {tool}")
        return str(Path(found).resolve())

    rloc = path or os.environ.get("CONVERT_CC_ENV")
    if not rloc:
        raise RuntimeError(
            "no C++ toolchain description: run through `bazel run //tools/convert` (autotools_cc_binary)"
        )
    cc_env_path = Path(rloc) if Path(rloc).is_file() else Path(resolve(rloc))
    loaded = json.loads(cc_env_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(f"{cc_env_path} is not a JSON object")
    # json.loads is untyped: the file is the rule's own output, with the keys CcEnv lists.
    return cast(CcEnv, loaded), resolve
