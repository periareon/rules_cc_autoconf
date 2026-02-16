"""The test runner for `gnu_gnulib_diff_test`"""

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

# Variables that are allowed to be missing from config.log without causing test failure.
# These are common autoconf variables that may not be set by all m4 macros.
SKIPPED_OUTPUT_VARIABLES = frozenset(
    [
        # Build system variables
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
        "ECHO_C",
        "ECHO_N",
        "ECHO_T",
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
        # Pragma handling
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

# Prefixes for variables that should be skipped
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
)

SKIPPED_VARIABLE_SUFIXES = ("_LIBM",)

KEEP_VARIABLES = ["HAVE_LIBM"]


def _should_skip_variable(name: str) -> bool:
    """Check if a variable should be skipped (not required to be present)."""
    # Skip explicitly listed variables
    if name in SKIPPED_OUTPUT_VARIABLES:
        return True

    # Skip variables with certain prefixes
    for prefix in SKIPPED_VARIABLE_PREFIXES:
        if name.startswith(prefix):
            if name not in KEEP_VARIABLES:
                return True

    for suffix in SKIPPED_VARIABLE_SUFIXES:
        if name.endswith(suffix):
            if name not in KEEP_VARIABLES:
                return True

    # Skip all lowercase variables (typically internal autoconf vars)
    if name.lower() == name:
        return True

    return False


def _parse_undef_placeholders(content: str) -> set[str]:
    """Extract #undef MACRO patterns from config.h.in (AC_DEFINE targets)."""
    undef_pattern = re.compile(r"#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)")
    return set(undef_pattern.findall(content))


def _parse_subst_placeholders(content: str) -> set[str]:
    """Extract @PLACEHOLDER@ patterns from gnulib_*.h.in (AC_SUBST targets)."""
    subst_pattern = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)@")
    return set(subst_pattern.findall(content))


def _extract_config_log_section(content: str, section_name: str) -> str:
    """Extract a named section from config.log.

    Sections are delimited by:
    ## ------------ ##
    ## section_name ##
    ## ------------ ##

    Returns the content between the section header and the next section, or None.
    """
    # Build pattern to match section header
    # The dashes adjust to the section name length
    pattern = re.compile(
        r"## -+ ##\n## "
        + re.escape(section_name)
        + r" ##\n## -+ ##\n(.*?)(?=\n## -+ ##|\Z)",
        re.DOTALL,
    )
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return None


def _generate_makefile_in() -> str:
    """Generate a minimal Makefile.in."""
    return """# Minimal Makefile.in for autoconf testing
all:
\t@echo "Build complete"
"""


def _parse_expected_variables_from_config_log(
    config_log_path: Path,
) -> tuple[set[str], set[str]]:
    """Parse config.log to extract the list of expected variable names.

    Returns:
        Tuple of (output variables, confdef.h variables) - just the variable names, not values
    """
    content = config_log_path.read_text(encoding="utf-8", errors="replace")

    output_vars: set[str] = set()
    confdef_vars: set[str] = set()

    # Parse Output variables section for AC_SUBST variable names
    output_vars_section = _extract_config_log_section(content, "Output variables.")
    if not output_vars_section:
        raise ValueError("Unable to found `Output variables` section in log.")

    for line in output_vars_section.splitlines():
        line = line.strip()
        # Stop at empty line or line starting with #
        if not line or line.startswith("#"):
            continue
        # Partition on = to get variable name
        if "=" in line:
            name, _, _ = line.partition("=")
            name = name.strip()
            if name and not _should_skip_variable(name):
                output_vars.add(name)

    # Parse confdefs.h section for AC_DEFINE variable names
    confdefs_section = _extract_config_log_section(content, "confdefs.h.")
    if not confdefs_section:
        raise ValueError("Unable to found `confdef.h` section in log.")

    # Skip the "/* confdefs.h */" line if present
    for line in confdefs_section.splitlines():
        line = line.strip()
        if line.startswith("/* confdefs.h"):
            continue
        if not line:
            break
        # Match #define MACRO patterns
        define_match = re.match(r"#define\s+([\w\d_]+)", line)
        if not define_match:
            ifdef_match = re.match(r"#ifndef ", line)
            if ifdef_match:
                continue
            enddef_match = re.match(r"#endif", line)
            if enddef_match:
                continue
            raise ValueError(f"Unexpected confdefs.h value: `{line}`")

        name = define_match.group(1)
        if not _should_skip_variable(name):
            confdef_vars.add(name)

    return output_vars, confdef_vars


class GnuAutoconfConfigureTest(unittest.TestCase):
    """Test case for gnulib m4 file comparison."""

    @classmethod
    def setUpClass(cls) -> None:
        """Set up test class: locate files and run autoconf."""
        cls._locate_files()
        cls._setup_work_directory()
        cls._run_autoconf_and_configure()

    @classmethod
    def _locate_files(cls) -> None:
        """Locate all required files using runfiles."""
        r = Runfiles.Create()
        if not r:
            raise EnvironmentError("Failed to locate runfiles")

        source_repo = "_main" if platform.system() == "Windows" else None

        def get_path(env_var: str) -> Path:
            rloc = os.environ[env_var]
            path = r.Rlocation(rloc, source_repo)
            if not path:
                raise FileNotFoundError(f"Failed to locate: {rloc}")
            return Path(path)

        cls.configure_ac_path = get_path("TEST_CONFIGURE_AC")
        cls.config_h_in_path = get_path("TEST_CONFIG_H_IN")
        cls.config_h_in_name = os.environ["TEST_CONFIG_H_IN"]
        cls.golden_config_h_path = get_path("TEST_GOLDEN_CONFIG_H")
        cls.golden_config_h_name = os.environ["TEST_GOLDEN_CONFIG_H"]

        cls.subst_h_in_path = None
        cls.subst_h_in_name = None
        cls.golden_subst_h_path = None
        cls.golden_subst_h_name = None

        if "TEST_SUBST_H_IN" in os.environ:
            cls.subst_h_in_path = get_path("TEST_SUBST_H_IN")
            cls.subst_h_in_name = os.environ["TEST_SUBST_H_IN"]

        if "TEST_GOLDEN_SUBST_H" in os.environ:
            cls.golden_subst_h_path = get_path("TEST_GOLDEN_SUBST_H")
            cls.golden_subst_h_name = os.environ["TEST_GOLDEN_SUBST_H"]

        # Set up work directory
        cls.outputs_dir = Path(
            os.environ.get(
                "TEST_UNDECLARED_OUTPUTS_DIR",
                os.environ.get("TEST_TMPDIR", tempfile.gettempdir()),
            )
        )

        cls.work_dir = cls.outputs_dir / "gnu_configure"
        cls.m4_dir = cls.work_dir / "m4"
        cls.config_h_path = cls.work_dir / "config.h"

        if cls.subst_h_in_path:
            cls.subst_h_path = cls.work_dir / "subst.h"

        # Get m4 files (space-separated list from rlocationpaths)
        m4_files_rloc = os.environ["TEST_M4_FILES"].split()
        cls.m4_files = []
        for m4_rloc in m4_files_rloc:
            if not m4_rloc:
                continue
            m4_path = r.Rlocation(m4_rloc, source_repo)
            if not m4_path:
                raise FileNotFoundError(f"Failed to locate: {m4_rloc}")
            cls.m4_files.append(Path(m4_path))

        # Get auxiliary files (optional, space-separated list from rlocationpaths)
        aux_files_rloc = os.environ.get("TEST_AUX_FILES", "").split()
        cls.aux_files = []
        for aux_rloc in aux_files_rloc:
            if not aux_rloc:
                continue
            aux_path = r.Rlocation(aux_rloc, source_repo)
            if not aux_path:
                raise FileNotFoundError(f"Failed to locate aux file: {aux_rloc}")
            cls.aux_files.append(Path(aux_path))

        # Check for autoreconf (preferred) and autoconf
        cls.autoreconf = shutil.which("autoreconf")
        if not cls.autoreconf:
            cls.autoreconf = shutil.which("autoreconf.exe")

        cls.autoconf = shutil.which("autoconf")
        if not cls.autoconf:
            cls.autoconf = shutil.which("autoconf.exe")
        if not cls.autoconf:
            raise EnvironmentError("autoconf is not installed or not in PATH")

        # Determine current platform
        cls.current_platform = platform.system().lower()

    @classmethod
    def _setup_work_directory(cls) -> None:
        """Set up the work directory with all necessary files."""
        # Create work directory
        cls.work_dir.mkdir(exist_ok=True, parents=True)

        # Copy auxiliary files to work directory root (before autoreconf)
        for aux_file in cls.aux_files:
            shutil.copy2(aux_file, cls.work_dir / aux_file.name)

        # Copy all m4 files to m4 directory
        cls.m4_dir.mkdir(exist_ok=True, parents=True)

        for m4_file in cls.m4_files:
            shutil.copy2(m4_file, cls.m4_dir / m4_file.name)

        # Read and prepare configure.ac
        configure_ac_content = cls.configure_ac_path.read_text(encoding="utf-8")

        # Insert AC_CONFIG_MACRO_DIRS after AC_INIT if not present
        if "AC_CONFIG_MACRO_DIRS" not in configure_ac_content:
            configure_ac_content = configure_ac_content.replace(
                "AC_CONFIG_HEADERS([config.h])",
                "AC_CONFIG_HEADERS([config.h])\nAC_CONFIG_MACRO_DIRS([m4])",
            )
        (cls.work_dir / "configure.ac").write_text(
            configure_ac_content, encoding="utf-8"
        )

        config_h_in_path = cls.work_dir / cls.config_h_in_path.name
        shutil.copy2(cls.config_h_in_path, config_h_in_path)

        if cls.subst_h_in_path:
            subst_h_in_path = cls.work_dir / cls.subst_h_in_path.name
            shutil.copy2(cls.subst_h_in_path, subst_h_in_path)

        # Generate Makefile.in
        makefile_in = _generate_makefile_in()
        (cls.work_dir / "Makefile.in").write_text(makefile_in, encoding="utf-8")

        result = subprocess.run(
            [cls.autoreconf, "-i"],
            cwd=cls.work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        # Copy the config files again to ensure templates were not modified
        shutil.copy2(cls.config_h_in_path, config_h_in_path)
        if cls.subst_h_in_path:
            shutil.copy2(cls.subst_h_in_path, subst_h_in_path)

        log_file = cls.work_dir / "autoreconf.log"
        log_file.write_text(result.stdout or "", encoding="utf-8")

    @classmethod
    def _parse_aclocal_m4_includes(cls) -> set[str]:
        """Parse aclocal.m4 to find which m4 files are actually used."""
        aclocal_m4 = cls.work_dir / "aclocal.m4"
        if not aclocal_m4.exists():
            return set()

        content = aclocal_m4.read_text(encoding="utf-8", errors="replace")
        include_pattern = re.compile(r"m4_include\(\[m4/([^\]]+\.m4)\]\)")
        included_files = set(include_pattern.findall(content))
        return included_files

    @classmethod
    def _cleanup_unused_m4_files(cls) -> None:
        """Delete m4 files that are not referenced in aclocal.m4."""
        included_files = cls._parse_aclocal_m4_includes()

        if not included_files:
            print(
                "No m4 files referenced in aclocal.m4, skipping cleanup",
                file=sys.stderr,
            )
            return

        all_m4_files = list(cls.m4_dir.glob("*.m4"))
        deleted_count = 0
        for m4_file in all_m4_files:
            if m4_file.name not in included_files:
                m4_file.unlink()
                deleted_count += 1

        print(
            f"Cleaned up {deleted_count} unused m4 files "
            f"(kept {len(included_files)} referenced files)",
            file=sys.stderr,
        )

    @classmethod
    def _run_autoconf_and_configure(cls) -> None:
        """Run autoconf and configure to generate config.log."""
        result = subprocess.run(
            [cls.autoconf],
            cwd=cls.work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        log_file = cls.work_dir / "autoconf.log"
        log_file.write_text(result.stdout or "", encoding="utf-8")

        assert result.returncode == 0, f"autoconf failed:\n{result.stdout}"

        configure = cls.work_dir / "configure"
        assert configure.exists(), "configure script was not generated"

        # configure.chmod(0o755)
        cls._cleanup_unused_m4_files()

        # Run configure
        result = subprocess.run(
            [str(configure)],
            cwd=cls.work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

        log_file = cls.work_dir / "configure.log"
        log_file.write_text(result.stdout or "", encoding="utf-8")

        assert result.returncode == 0, f"configure failed:\n{result.stdout}"

    def test_template_files_have_all_variables(self) -> None:
        """Test that golden files contain all variables found in config.log.

        This test parses config.log to extract the list of expected variable names
        (from Output variables and confdefs.h sections), then verifies that the
        golden files contain all these variables. It writes the list to a JSON file.
        """
        if not os.getenv("VERIFY_VARIABLES", None):
            self.skipTest("Verification was not requested")

        config_log = self.work_dir / "config.log"
        assert config_log.exists(), "config.log not found"

        # Parse config.log to get the list of expected variable names
        output_variables, confdef_variables = _parse_expected_variables_from_config_log(
            config_log
        )

        # Combine all variables into a single sorted list
        all_variables = sorted(output_variables | confdef_variables)

        # Write the complete list of parsed variables to JSON file
        json_output = self.outputs_dir / "parsed_variables.json"
        json_output.write_text(json.dumps(all_variables, indent=2), encoding="utf-8")

        config_h_in_content = self.config_h_in_path.read_text(encoding="utf-8")
        define_vars = _parse_undef_placeholders(config_h_in_content)

        # Check if golden files have all expected variables
        missing_in_config = [v for v in all_variables if v not in define_vars]
        missing_in_subst = []

        if self.subst_h_in_path:
            subst_h_in_content = self.subst_h_in_path.read_text(encoding="utf-8")
            subst_vars = _parse_subst_placeholders(subst_h_in_content)
            missing_in_subst = [v for v in all_variables if v not in subst_vars]

        expected = self.outputs_dir / "expected_config.h.in"
        expected.write_text(
            "\n".join(
                ["/* config.h.in */"]
                + ["#undef {}".format(v) for v in all_variables]
                + [""]
            ),
            encoding="utf-8",
        )

        expected = self.outputs_dir / "expected_subst.h.in"
        expected.write_text(
            "\n".join(
                ["/* subst.h.in */"]
                + ['#define SUBST_{0} "@{0}@"'.format(v) for v in all_variables]
                + [""]
            ),
            encoding="utf-8",
        )

        errors = []
        if missing_in_config:
            errors.append(
                f"({os.environ['TEST_TARGET']}) config.log has {len(missing_in_config)} defines not found in `config.h.in`:\n"
                + "\n".join(f"  - {v}" for v in missing_in_config)
            )

        if missing_in_subst:
            errors.append(
                f"({os.environ['TEST_TARGET']}) config.log has {len(missing_in_subst)} substs not found in `subst.h.in`:\n"
                + "\n".join(f"  - {v}" for v in missing_in_subst)
            )

        if errors:
            self.fail("\n\n".join(errors))

    def test_config_h_diff(self) -> None:
        """Test that the generated `config.h` file matches the `golden_config.h` file"""
        # Parse config.log if not already done by test_golden_files_have_all_variables

        # Skip the first line which is always injected by configure.
        # ```
        # /* config.h.  Generated from config.h.in by configure.  */
        # ```
        config_lines = self.config_h_path.read_text(encoding="utf-8").splitlines()[1:]

        diff_output = diff(
            file1_content=self.golden_config_h_path.read_text(
                encoding="utf-8"
            ).splitlines(),
            file2_content=config_lines,
            file1_name=self.golden_config_h_name,
            file2_name=self.config_h_path.name,
        )

        divider = "=" * 70

        if diff_output:
            self.fail(f"Files differ\n{divider}\n```diff\n{diff_output}\n```")

    def test_subst_h_diff(self) -> None:
        """Test that the generated `subst.h` file matches the `golden_subst.h` file"""
        if not self.subst_h_in_path:
            self.skipTest("No subst file provided")

        diff_output = diff(
            file1_content=self.golden_subst_h_path.read_text(
                encoding="utf-8"
            ).splitlines(),
            file2_content=self.subst_h_path.read_text(encoding="utf-8").splitlines(),
            file1_name=self.golden_subst_h_name,
            file2_name=self.subst_h_path.name,
        )

        divider = "=" * 70

        if diff_output:
            self.fail(f"Files differ\n{divider}\n```diff\n{diff_output}\n```")


if __name__ == "__main__":
    unittest.main()
