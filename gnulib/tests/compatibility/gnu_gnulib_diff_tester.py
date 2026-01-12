"""GNU Gnulib M4 Test Runner

Tests that GNU autoconf produces values matching the golden files.

This test:
1. Copies all m4 files to work directory
2. Runs autoconf to generate configure
3. Runs configure to generate config.log
4. Parses config.log for AC_DEFINE values (confdefs.h section)
5. Parses config.log for AC_SUBST values (Output variables section)
6. Compares against golden files
"""

import difflib
import os
import platform
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from python.runfiles import Runfiles

# Variables that are allowed to be missing from config.log without causing test failure.
# These are common autoconf variables that may not be set by all m4 macros.
SKIPPED_OUTPUT_VARIABLES = frozenset([
    # Build system variables
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
    "LIBOBJS",
    "LIBS",
    "LTLIBOBJS",
    "OBJEXT",
    "PATH_SEPARATOR",
    "SHELL",
    # Include handling
    "INCLUDE_NEXT",
    "INCLUDE_NEXT_AS_FIRST_DIRECTIVE",
    "NEXT_AS_FIRST_DIRECTIVE_UNISTD_H",
    "NEXT_UNISTD_H",
    # Pragma handling
    "PRAGMA_COLUMNS",
    "PRAGMA_SYSTEM_HEADER",
])

# Prefixes for variables that should be skipped
SKIPPED_VARIABLE_PREFIXES = (
    "PACKAGE_",           # Package metadata
    "GL_MODULE_INDICATOR_PREFIX",  # Gnulib module indicators
)


def should_skip_variable(name: str) -> bool:
    """Check if a variable should be skipped (not required to be present)."""
    # Skip explicitly listed variables
    if name in SKIPPED_OUTPUT_VARIABLES:
        return True
    
    # Skip variables with certain prefixes
    for prefix in SKIPPED_VARIABLE_PREFIXES:
        if name.startswith(prefix):
            return True
    
    # Skip all lowercase variables (typically internal autoconf vars)
    if name.islower():
        return True
    
    return False


def parse_undef_placeholders(content: str) -> Set[str]:
    """Extract #undef MACRO patterns from config.h.in (AC_DEFINE targets)."""
    undef_pattern = re.compile(r'#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)')
    return set(undef_pattern.findall(content))


def parse_subst_placeholders(content: str) -> Set[str]:
    """Extract @PLACEHOLDER@ patterns from gnulib_*.h.in (AC_SUBST targets)."""
    subst_pattern = re.compile(r'@([A-Za-z_][A-Za-z0-9_]*)@')
    return set(subst_pattern.findall(content))


def parse_ac_config_files_input(configure_ac_content: str) -> Optional[str]:
    """Parse configure.ac for AC_CONFIG_FILES input filename (the .h.in file).
    
    Looks for patterns like:
    - AC_CONFIG_FILES([output:input])
    - AC_CONFIG_FILES([output.h:output.h.in])
    
    Returns the input filename (after the colon), or None if not found.
    """
    # Match AC_CONFIG_FILES([output:input]) where input ends with .h.in
    pattern = re.compile(r'AC_CONFIG_FILES\(\[([^:]+):([^\]]+\.h\.in)\]\)')
    match = pattern.search(configure_ac_content)
    if match:
        return match.group(2)
    return None


def extract_config_log_section(content: str, section_name: str) -> Optional[str]:
    """Extract a named section from config.log.
    
    Sections are delimited by:
    ## ----------- ##
    ## section_name ##
    ## ----------- ##
    
    Returns the content between the section header and the next section, or None.
    """
    # Build pattern to match section header
    # The dashes adjust to the section name length
    pattern = re.compile(
        r'## -+ ##\n## ' + re.escape(section_name) + r' ##\n## -+ ##\n(.*?)(?=\n## -+ ##|\Z)',
        re.DOTALL
    )
    match = pattern.search(content)
    if match:
        return match.group(1).strip()
    return None


def parse_confdefs_section(confdefs_content: str) -> Dict[str, str]:
    """Parse the confdefs.h section from config.log for AC_DEFINE values.
    
    The section contains C preprocessor definitions like:
    #define HAVE_FEATURE 1
    /* #undef MISSING_FEATURE */
    """
    defines = {}
    
    # Match #define MACRO VALUE patterns
    define_pattern = re.compile(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*?)$', re.MULTILINE)
    for match in define_pattern.finditer(confdefs_content):
        name, value = match.groups()
        defines[name] = value.strip() or '1'
    
    # Match /* #undef MACRO */ patterns (undefined)
    undef_pattern = re.compile(r'/\*\s*#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\*/')
    for match in undef_pattern.finditer(confdefs_content):
        name = match.group(1)
        if name not in defines:
            defines[name] = '/* undef */'
    
    return defines


def parse_output_variables_section(output_vars_content: str) -> Dict[str, str]:
    """Parse the Output variables section from config.log for AC_SUBST values.
    
    The section contains variable assignments like:
    VAR='value'
    """
    variables = {}
    
    # Match VAR='value' patterns (value may be empty or contain single quotes escaped as '\'')
    var_pattern = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)='(.*)'$", re.MULTILINE)
    for match in var_pattern.finditer(output_vars_content):
        name, value = match.groups()
        # Unescape single quotes: '\'' -> '
        value = value.replace("'\\''", "'")
        variables[name] = value
    
    return variables


def generate_makefile_in() -> str:
    """Generate a minimal Makefile.in."""
    return '''# Minimal Makefile.in for autoconf testing
all:
\t@echo "Build complete"
'''


def parse_golden_config_h(content: str) -> Dict[str, str]:
    """Parse golden config.h file for #define values."""
    variables = {}
    
    # Match #define MACRO VALUE patterns
    define_pattern = re.compile(r'#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*?)$', re.MULTILINE)
    for match in define_pattern.finditer(content):
        name, value = match.groups()
        value = value.strip()
        if value:
            variables[name] = value
    
    # Match /* #undef MACRO */ patterns
    undef_pattern = re.compile(r'/\*\s*#\s*undef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\*/')
    for match in undef_pattern.finditer(content):
        name = match.group(1)
        if name not in variables:
            variables[name] = '/* undef */'
    
    return variables


def parse_golden_gnulib_h(content: str) -> Dict[str, str]:
    """Parse golden gnulib_*.h file for #define SUBST_* values.
    
    Returns mapping without SUBST_ prefix (e.g., FOO not SUBST_FOO).
    Handles both:
    - #define SUBST_VAR value  -> VAR=value
    - #define SUBST_VAR        -> VAR= (empty value)
    """
    variables = {}
    
    # Match #define SUBST_MACRO [VALUE] patterns (VALUE is optional, rest of line)
    # Use MULTILINE so $ matches end of line, not just end of string
    define_pattern = re.compile(
        r'#\s*define\s+SUBST_([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]+(.*))?$',
        re.MULTILINE
    )
    for match in define_pattern.finditer(content):
        name = match.group(1)
        value = match.group(2) or ''  # None if no value captured
        value = value.strip()
        # Skip unsubstituted @VAR@ placeholders
        if not value.startswith('@'):
            variables[name] = value
    
    return variables


def format_comparison(expected: Dict[str, str], actual: Dict[str, str]) -> Tuple[bool, str]:
    """Compare expected vs actual and return (success, diff)."""
    expected_lines = [f'{k}={expected[k]}' for k in sorted(expected.keys())]
    actual_lines = []
    
    for k in sorted(expected.keys()):
        if k in actual:
            actual_lines.append(f'{k}={actual[k]}')
        else:
            actual_lines.append(f'{k}=<MISSING>')
    
    if expected_lines == actual_lines:
        return True, ""
    
    diff = difflib.unified_diff(
        expected_lines,
        actual_lines,
        fromfile="expected",
        tofile="actual",
        lineterm="",
    )
    return False, '\n'.join(diff)


class GnuGnulibTest(unittest.TestCase):
    """Test case for gnulib m4 file comparison."""

    @classmethod
    def setUpClass(cls):
        """Set up test class with parsed arguments."""
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
        cls.gnulib_h_in_path = get_path("TEST_GNULIB_H_IN")
        cls.golden_config_h_path = get_path("TEST_GOLDEN_CONFIG_H")
        cls.golden_gnulib_h_path = get_path("TEST_GOLDEN_GNULIB_H")

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

        # Check for autoconf
        cls.autoconf = shutil.which("autoconf")
        if not cls.autoconf:
            cls.autoconf = shutil.which("autoconf.exe")
        if not cls.autoconf:
            raise EnvironmentError("autoconf is not installed or not in PATH")

        # Determine current platform
        cls.current_platform = platform.system().lower()

    def setUp(self) -> None:
        # Prefer TEST_UNDECLARED_OUTPUTS_DIR for Bazel test artifacts (persisted after test),
        # fall back to TEST_TMPDIR for scratch space
        outputs_dir = os.environ.get("TEST_UNDECLARED_OUTPUTS_DIR")
        if outputs_dir:
            self.work_dir = Path(outputs_dir)
        else:
            self.work_dir = Path(os.environ.get("TEST_TMPDIR", "/tmp"))

        # Create subdirectory for m4 files
        self.m4_dir = self.work_dir / "m4"
        self.m4_dir.mkdir(exist_ok=True)

    def _setup_work_directory(self) -> None:
        """Set up the work directory with all necessary files."""
        # Copy all m4 files to m4 directory (already created in setUp)
        for m4_file in self.m4_files:
            shutil.copy2(m4_file, self.m4_dir / m4_file.name)

        # Read and prepare configure.ac
        configure_ac_content = self.configure_ac_path.read_text(encoding='utf-8')
        
        # Parse configure.ac to find the expected input filename for AC_CONFIG_FILES
        expected_h_in_name = parse_ac_config_files_input(configure_ac_content)
        
        # Insert AC_CONFIG_MACRO_DIRS after AC_INIT if not present
        if 'AC_CONFIG_MACRO_DIRS' not in configure_ac_content:
            configure_ac_content = configure_ac_content.replace(
                'AC_CONFIG_HEADERS([config.h])',
                'AC_CONFIG_HEADERS([config.h])\nAC_CONFIG_MACRO_DIRS([m4])'
            )
        (self.work_dir / "configure.ac").write_text(configure_ac_content, encoding='utf-8')

        # Copy config.h.in
        shutil.copy2(self.config_h_in_path, self.work_dir / "config.h.in")

        # Copy gnulib_*.h.in with the name expected by configure.ac
        # If configure.ac expects e.g. gnulib_access.h.in, copy our subst.h.in to that name
        if expected_h_in_name:
            dest_name = expected_h_in_name
        else:
            dest_name = self.gnulib_h_in_path.name
        shutil.copy2(self.gnulib_h_in_path, self.work_dir / dest_name)

        # Generate Makefile.in
        makefile_in = generate_makefile_in()
        (self.work_dir / "Makefile.in").write_text(makefile_in, encoding='utf-8')

    def _parse_aclocal_m4_includes(self) -> Set[str]:
        """Parse aclocal.m4 to find which m4 files are actually used.
        
        Returns set of m4 filenames (just the basename, e.g., 'access.m4').
        """
        aclocal_m4 = self.work_dir / "aclocal.m4"
        if not aclocal_m4.exists():
            return set()
        
        content = aclocal_m4.read_text(encoding='utf-8', errors='replace')
        
        # Match m4_include([m4/filename.m4]) patterns
        include_pattern = re.compile(r'm4_include\(\[m4/([^\]]+\.m4)\]\)')
        included_files = set(include_pattern.findall(content))
        
        return included_files

    def _cleanup_unused_m4_files(self) -> None:
        """Delete m4 files that are not referenced in aclocal.m4."""
        included_files = self._parse_aclocal_m4_includes()
        
        if not included_files:
            print("No m4 files referenced in aclocal.m4, skipping cleanup", file=sys.stderr)
            return
        
        # Get all m4 files in the m4 directory
        all_m4_files = list(self.m4_dir.glob("*.m4"))
        
        deleted_count = 0
        for m4_file in all_m4_files:
            if m4_file.name not in included_files:
                m4_file.unlink()
                deleted_count += 1
        
        print(f"Cleaned up {deleted_count} unused m4 files "
              f"(kept {len(included_files)} referenced files)", file=sys.stderr)

    def _run_autoconf(self) -> bool:
        """Run autoreconf to generate configure script with auxiliary files."""
        # First, try autoreconf -i which installs missing auxiliary files
        autoreconf = shutil.which("autoreconf")
        if autoreconf:
            result = subprocess.run(
                [autoreconf, "-i", "-f"],
                cwd=self.work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
            )
        else:
            # Fall back to plain autoconf
            result = subprocess.run(
                [self.autoconf],
                cwd=self.work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
            )

        log_file = self.work_dir / "autoconf.log"
        log_file.write_text(result.stdout or "", encoding='utf-8')

        if result.returncode != 0:
            print(f"autoconf failed:\n{result.stdout}", file=sys.stderr)
            return False

        configure = self.work_dir / "configure"
        if not configure.exists():
            print("configure script was not generated", file=sys.stderr)
            return False

        configure.chmod(0o755)
        
        # Clean up unused m4 files (those not referenced in aclocal.m4)
        self._cleanup_unused_m4_files()
        
        return True

    def _run_configure(self) -> bool:
        """Run the configure script."""
        configure = self.work_dir / "configure"
        result = subprocess.run(
            [str(configure)],
            cwd=self.work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
        )

        log_file = self.work_dir / "configure.log"
        log_file.write_text(result.stdout or "", encoding='utf-8')

        if result.returncode != 0:
            print(f"configure failed:\n{result.stdout}", file=sys.stderr)
            return False

        return True

    def _parse_config_log(
        self, define_vars: Set[str], subst_vars: Set[str]
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Parse config.log for AC_DEFINE and AC_SUBST values.
        
        Args:
            define_vars: Set of variable names expected from config.h.in
            subst_vars: Set of variable names expected from gnulib_*.h.in
        
        Returns:
            Tuple of (defines_dict, substs_dict)
        """
        config_log = self.work_dir / "config.log"
        if not config_log.exists():
            return {}, {}
        
        content = config_log.read_text(encoding='utf-8', errors='replace')
        
        # Parse confdefs.h section for AC_DEFINE values
        confdefs_section = extract_config_log_section(content, "confdefs.h.")
        defines = {}
        if confdefs_section:
            defines = parse_confdefs_section(confdefs_section)
        
        # Variables in config.h.in that aren't in confdefs.h are undefined
        # (they appear as /* #undef VAR */ in the generated config.h)
        for var in define_vars:
            if var not in defines:
                defines[var] = '/* undef */'
        
        # Parse Output variables section for AC_SUBST values
        output_vars_section = extract_config_log_section(content, "Output variables.")
        substs = {}
        if output_vars_section:
            substs = parse_output_variables_section(output_vars_section)
        
        return defines, substs

    def test_autoconf_matches_golden(self):
        """Test that GNU autoconf produces values matching golden files."""
        # Set up work directory
        self._setup_work_directory()

        print(f"Work directory: {self.work_dir}", file=sys.stderr)
        print(f"configure.ac: {self.configure_ac_path.name}", file=sys.stderr)
        print(f"M4 files: {[m4.name for m4 in self.m4_files]}", file=sys.stderr)

        # Run autoconf
        self.assertTrue(self._run_autoconf(), "autoconf failed - check autoconf.log")

        # Run configure
        self.assertTrue(self._run_configure(), "configure failed - check configure.log")

        # Parse templates for expected placeholders
        config_h_in_content = self.config_h_in_path.read_text(encoding='utf-8')
        gnulib_h_in_content = self.gnulib_h_in_path.read_text(encoding='utf-8')
        
        define_vars = parse_undef_placeholders(config_h_in_content)
        subst_vars = parse_subst_placeholders(gnulib_h_in_content)
        
        print(f"config.h.in: {len(define_vars)} #undef placeholders", file=sys.stderr)
        print(f"gnulib_*.h.in: {len(subst_vars)} @SUBST@ placeholders", file=sys.stderr)

        # Parse config.log for actual values
        all_defines, all_substs = self._parse_config_log(define_vars, subst_vars)
        
        # Filter to only relevant variables
        actual_defines = {k: v for k, v in all_defines.items() if k in define_vars}
        actual_substs = {k: v for k, v in all_substs.items() if k in subst_vars}

        print(f"Extracted {len(actual_defines)} defines, {len(actual_substs)} substs", file=sys.stderr)
        print(f"Total in config.log: {len(all_defines)} defines, {len(all_substs)} substs", file=sys.stderr)

        # Check for missing values (excluding skipped variables)
        missing_defines = [
            v for v in define_vars 
            if v not in actual_defines and not should_skip_variable(v)
        ]
        missing_substs = [
            v for v in subst_vars 
            if v not in actual_substs and not should_skip_variable(v)
        ]

        if missing_defines:
            self.fail(
                f"config.h.in has {len(missing_defines)} placeholders not found in config.log:\n"
                + '\n'.join(f"  - {p}" for p in sorted(missing_defines))
            )

        if missing_substs:
            self.fail(
                f"gnulib_*.h.in has {len(missing_substs)} placeholders not found in config.log:\n"
                + '\n'.join(f"  - {p}" for p in sorted(missing_substs))
            )

        # Parse golden files
        golden_config_content = self.golden_config_h_path.read_text(encoding='utf-8')
        golden_gnulib_content = self.golden_gnulib_h_path.read_text(encoding='utf-8')
        
        expected_defines = parse_golden_config_h(golden_config_content)
        expected_substs = parse_golden_gnulib_h(golden_gnulib_content)

        # Compare config.h
        success, diff = format_comparison(expected_defines, actual_defines)
        if not success:
            self.fail(
                f"config.h differs from golden ({self.current_platform})\n"
                f"{'=' * 70}\n```diff\n{diff}\n```"
            )

        # Compare gnulib_*.h (subst values)
        success, diff = format_comparison(expected_substs, actual_substs)
        if not success:
            self.fail(
                f"gnulib_*.h substitutions differ from golden ({self.current_platform})\n"
                f"{'=' * 70}\n```diff\n{diff}\n```"
            )

        print("SUCCESS: All values match golden files!", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
