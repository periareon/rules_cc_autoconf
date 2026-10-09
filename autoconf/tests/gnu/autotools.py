"""Run-time environment for the pinned GNU autotools.

The toolchain (see `toolchain.bzl`) publishes runfiles paths through
`AUTOTOOLS_*` environment variables.  This module turns them into a working
autoconf and aclocal installation rooted in a scratch directory, using only
the environment overrides the tools themselves provide, so no install-time
paths are involved.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from python.runfiles import Runfiles

_PERL_TOOLS = {
    "aclocal": "aclocal",
    "autoconf": "autoconf",
    "autoheader": "autoheader",
    "autom4te": "autom4te",
    "autoreconf": "autoreconf",
}


IS_WINDOWS = platform.system() == "Windows"


def posix(path: Path | str) -> str:
    """Forward-slash form of a path (what bash and perl expect on Windows too)."""
    return Path(path).as_posix()


def space_free(path: Path | str) -> str:
    """A spelling of `path` without spaces, for tools that word-split it.

    autoconf's generated configure re-execs itself with an unquoted
    `$CONFIG_SHELL`, and perl splits `PERL5SHELL` on whitespace, so a shell
    such as `C:/Program Files/Git/bin/bash.exe` has to be passed as its 8.3
    short name on Windows. Elsewhere the path is returned unchanged.
    """
    text = posix(path)
    if not IS_WINDOWS or " " not in text:
        return text
    import ctypes  # pylint: disable=import-outside-toplevel

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    if length and " " not in buffer.value:
        return posix(buffer.value)
    raise EnvironmentError(f"No space-free spelling available for {text!r}")


class Autotools:
    """A ready to use autotools installation.

    Args:
        tooldir: scratch directory for wrappers and configuration.
        shell: POSIX shell used to run the wrappers (`CONFIG_SHELL`). Required on
            Windows, where it is the MSYS2 bash from rules_shell; defaults to
            `/bin/sh` elsewhere.
    """

    def __init__(self, tooldir: Path, shell: str | None = None) -> None:
        runfiles = Runfiles.Create()
        if not runfiles:
            raise EnvironmentError("Failed to locate runfiles")

        source_repo = "_main" if IS_WINDOWS else None
        self.shell = shell or os.environ.get("CONFIG_SHELL") or "/bin/sh"
        if IS_WINDOWS and not Path(self.shell).exists():
            raise EnvironmentError(
                f"A POSIX shell is required on Windows (CONFIG_SHELL={self.shell!r} not found)"
            )
        # The spelling handed to configure and perl, which word-split it.
        self.shell_for_env = space_free(self.shell)

        def locate(var: str) -> Path:
            rloc = os.environ[var]
            path = runfiles.Rlocation(rloc, source_repo)
            if not path or not Path(path).exists():
                raise FileNotFoundError(f"{var}: failed to locate runfile `{rloc}`")
            return Path(path).resolve()

        self.perl = locate("AUTOTOOLS_PERL")
        self.m4 = locate("AUTOTOOLS_M4")
        autoconf_script = locate("AUTOTOOLS_AUTOCONF")
        aclocal_script = locate("AUTOTOOLS_ACLOCAL")

        self.autoconf_root = autoconf_script.parent.parent
        self.autoconf_lib = self.autoconf_root / "lib"
        self.automake_root = aclocal_script.parent.parent
        self.automake_lib = self.automake_root / "lib"
        self.automake_acdir = self.automake_root / "m4"

        self.autoconf_version = os.environ["AUTOTOOLS_AUTOCONF_VERSION"]
        self.automake_version = os.environ["AUTOTOOLS_AUTOMAKE_VERSION"]
        self.automake_api_version = os.environ["AUTOTOOLS_AUTOMAKE_API_VERSION"]

        self.tooldir = tooldir.resolve()
        self.bindir = self.tooldir / "bin"
        self.home = self.tooldir / "home"
        self.tmpdir = self.tooldir / "tmp"
        # An empty system acdir keeps host `/usr/share/aclocal` macros out.
        self.system_acdir = self.tooldir / "system-acdir"
        for directory in (self.bindir, self.home, self.tmpdir, self.system_acdir):
            directory.mkdir(parents=True, exist_ok=True)

        self.autom4te_cfg = self.tooldir / "autom4te.cfg"
        template = (self.autoconf_lib / "autom4te.in").read_text(encoding="utf-8")
        self.autom4te_cfg.write_text(
            template.replace("@pkgdatadir@", posix(self.autoconf_lib)),
            encoding="utf-8",
            newline="\n",
        )

        self.wrappers: dict[str, Path] = {}
        # Wrappers that perl can exec() directly (autoconf runs autom4te with a
        # list-form exec, which bypasses PERL5SHELL). On Windows that has to be
        # a real executable or a .cmd file; everywhere else the sh wrapper is.
        self.exec_wrappers: dict[str, Path] = {}
        for tool, script in _PERL_TOOLS.items():
            if tool == "aclocal":
                script_path = aclocal_script
            else:
                script_path = self.autoconf_root / "bin" / script
            self.wrappers[tool] = self._write_wrapper(tool, script_path)
            self.exec_wrappers[tool] = (
                self._write_cmd_wrapper(tool, script_path) if IS_WINDOWS else self.wrappers[tool]
            )
        self.wrappers["m4"] = self._write_wrapper("m4", None)
        self.wrappers["perl"] = self._write_wrapper("perl", None)

    def _write_wrapper(self, name: str, script: Path | None) -> Path:
        """Write a tiny shell wrapper so other tools can exec the tool by path."""
        path = self.bindir / name
        if name == "m4":
            command = f'exec "{posix(self.m4)}" "$@"\n'
            if IS_WINDOWS:
                # See `_m4_command`: perl may hand us autom4te's shell-quoted
                # file name verbatim. Peel one layer of single quotes.
                command = (
                    "for arg do\n"
                    "  case $arg in\n"
                    "    \\'*\\') arg=${arg#\\'}; arg=${arg%\\'} ;;\n"
                    "  esac\n"
                    '  set -- "$@" "$arg"\n'
                    "  shift\n"
                    "done\n" + command
                )
        elif name == "perl":
            command = f'exec "{posix(self.perl)}" "$@"\n'
        else:
            command = f'exec "{posix(self.perl)}" "{posix(script)}" "$@"\n'
        path.write_text("#!/bin/sh\n" + command, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        return path

    def _write_cmd_wrapper(self, name: str, script: Path) -> Path:
        """A Windows .cmd wrapper, executable without a shell."""
        path = self.bindir / f"{name}.cmd"
        path.write_text(
            f'@"{self.perl}" "{script}" %*\r\n',
            encoding="utf-8",
            newline="",
        )
        return path

    def environment(self, tool_path: str | None = None) -> dict[str, str]:
        """Environment variables that make the tools find their pieces.

        Args:
            tool_path: extra `PATH` entries a compiler toolchain needs (MSVC's
                `link.exe` and DLL directories). They go right after the wrapper
                directory, ahead of the shell's userland, so the toolchain's
                tools win over same-named commands such as MSYS2's `link`.
        """
        host_path = os.environ.get("PATH", "/usr/bin:/bin")
        entries = [str(self.bindir)]
        if tool_path:
            entries.append(tool_path)
        if IS_WINDOWS:
            # The shell's directory supplies sed, expr and the rest of the
            # userland configure needs; the Windows PATH has cl.exe and friends.
            entries.append(str(Path(self.shell).parent))
        entries.append(host_path)
        env = {
            "PATH": os.pathsep.join(entries),
            "HOME": posix(self.home),
            "TMPDIR": posix(self.tmpdir),
            "LC_ALL": "C",
            "LANG": "C",
            "CONFIG_SHELL": self.shell_for_env,
            # autoconf
            "autom4te_perllibdir": posix(self.autoconf_lib),
            "AC_MACRODIR": posix(self.autoconf_lib),
            "AUTOM4TE_CFG": posix(self.autom4te_cfg),
            "trailer_m4": posix(self.autoconf_lib / "autoconf" / "trailer.m4"),
            # autom4te runs `$M4` both through the shell and (on Windows) by
            # splitting the string itself; see `_write_wrapper`.
            "M4": self._m4_command(),
            "AUTOM4TE": posix(self.exec_wrappers["autom4te"]),
            "AUTOCONF": posix(self.exec_wrappers["autoconf"]),
            "AUTOHEADER": posix(self.exec_wrappers["autoheader"]),
            # automake
            "AUTOMAKE_UNINSTALLED": "1",
            "AUTOMAKE_LIBDIR": posix(self.automake_lib),
            "ACLOCAL": posix(self.exec_wrappers["aclocal"]),
            "ACLOCAL_PATH": "",
            "PERL5LIB": f"{posix(self.automake_lib)}{os.pathsep}{posix(self.autoconf_lib)}",
        }
        if IS_WINDOWS:
            # Native perl runs system()/backticks/pipe-opens through cmd.exe by
            # default; autom4te and aclocal build POSIX command lines, so route
            # them through the same bash that runs configure. perl splits
            # PERL5SHELL on whitespace, hence the space-free spelling.
            env["PERL5SHELL"] = f"{self.shell_for_env} -c"
            # Don't let MSYS2 rewrite arguments such as `-I C:/...` or `/W3`.
            env["MSYS2_ARG_CONV_EXCL"] = "*"
            env["MSYS_NO_PATHCONV"] = "1"
            # The MSYS2 runtime re-parses the command line it receives from a
            # native parent (perl) through a glob/quote pass that truncates
            # any single argument beyond 8 KiB: the `-c` command aclocal hands
            # bash for a large module (one `--trace` per macro) is longer, and
            # bash then fails with "unexpected EOF while looking for matching
            # `'`". `noglob` makes bash take the command line as given.
            env["MSYS"] = " ".join(filter(None, [os.environ.get("MSYS", ""), "noglob"]))
            # config.guess derives the host triple from `uname -s`, which the
            # MSYS2 runtime in turn derives from MSYSTEM: unset, the raw bash
            # reports MSYS_NT and configure classifies the host as
            # `x86_64-pc-msys`, a Cygwin-like environment whose gnulib cases
            # do not apply to cl.exe. Git's `bin/bash.exe` launcher (what CI
            # picks up) sets MINGW64; pin it so every machine gets
            # `x86_64-pc-mingw64` and gnulib's `mingw* | windows*` branches.
            env["MSYSTEM"] = "MINGW64"
        return env

    def _m4_command(self) -> str:
        """The `M4` value for autom4te.

        Native perl runs a command string through PERL5SHELL only when it
        contains shell metacharacters. autom4te's pipe-open of `$M4 'traces.m4'`
        has none, so perl splits it on whitespace itself and the shell-quoted
        file name would reach m4.exe quotes and all. Going through the bash
        wrapper fixes that: the MSYS2 runtime dequotes arguments it receives
        from a native parent, and the wrapper peels any quotes that survive
        before it execs m4 with the plain path.
        """
        if not IS_WINDOWS:
            return posix(self.m4)
        return f"{self.shell_for_env} {space_free(self.wrappers['m4'])}"

    def aclocal_args(self) -> list[str]:
        """Options that pin aclocal to the bundled macro directories."""
        return [
            f"--automake-acdir={posix(self.automake_acdir)}",
            f"--system-acdir={posix(self.system_acdir)}",
        ]

    def command(self, tool: str) -> list[str]:
        """The argv prefix for a tool."""
        if tool in ("m4", "perl"):
            return [str(getattr(self, tool))]
        if tool not in self.wrappers:
            raise KeyError(f"Unknown autotools tool: {tool}")
        if IS_WINDOWS:
            # Shell scripts are not directly executable on Windows.
            return [str(self.shell), posix(self.wrappers[tool])]
        return [str(self.wrappers[tool])]

    def run(
        self,
        tool: str,
        args: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Run a tool, capturing combined output."""
        full_env = self.environment()
        if env:
            full_env.update(env)
        argv = self.command(tool)
        if tool == "aclocal":
            argv += self.aclocal_args()
            if IS_WINDOWS:
                # aclocal only ever reaches autom4te through bash (its command
                # lines carry shell metacharacters), and a large module's trace
                # command outgrows the 8191 characters cmd.exe allows a `.cmd`
                # wrapper. The shell wrapper has no such limit.
                full_env["AUTOM4TE"] = posix(self.wrappers["autom4te"])
        argv += list(args)
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"`{' '.join(argv)}` failed with exit code {result.returncode}:\n{result.stdout}"
            )
        return result

    def versions(self, cwd: Path) -> dict[str, str]:
        """Report the version line of every tool in the environment."""

        def first_line(tool: str, args: list[str]) -> str:
            result = self.run(tool, args, cwd=cwd)
            return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""

        return {
            "autoconf": first_line("autoconf", ["--version"]),
            "aclocal": first_line("aclocal", ["--version"]),
            "m4": first_line("m4", ["--version"]),
            "perl": first_line("perl", ["-e", "print $^V"]),
        }


def scratch_dir(name: str) -> Path:
    """A fresh scratch directory under Bazel's test or run temporary space."""
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or "/tmp"
    path = Path(base) / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path
