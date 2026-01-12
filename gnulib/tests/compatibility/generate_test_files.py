#!/usr/bin/env python3
"""Generate proper test files by parsing gnulib m4 files.

This script:
1. Reads ~/Code/gnulib/m4/{name}.m4 for each test module
2. Extracts all AC_DEFUN macro names (excluding *_DEFAULTS)
3. Extracts all AC_DEFINE and AC_SUBST calls
4. Generates:
   - configure.ac that invokes the macros
   - config.h.in with #undef for each AC_DEFINE
   - gnulib_*.h.in with @VAR@ for each AC_SUBST
"""

import re
import sys
from pathlib import Path


GNULIB_M4_DIR = Path.home() / 'Code' / 'gnulib' / 'm4'


def extract_defuns(m4_content: str) -> list:
    """Extract AC_DEFUN and AC_DEFUN_ONCE macro names."""
    defuns = []
    pattern = r'AC_DEFUN(?:_ONCE)?\s*\(\s*\[([^\]]+)\]'
    for match in re.finditer(pattern, m4_content):
        name = match.group(1)
        if not name.endswith('_DEFAULTS'):
            defuns.append(name)
    return defuns


def extract_defines(m4_content: str) -> list:
    """Extract AC_DEFINE variable names."""
    defines = []
    # Match AC_DEFINE([NAME], or AC_DEFINE_UNQUOTED([NAME],
    pattern = r'AC_DEFINE(?:_UNQUOTED)?\s*\(\s*\[([^\]]+)\]'
    for match in re.finditer(pattern, m4_content):
        name = match.group(1)
        # Skip template variables like ${GLTYPE}
        if '$' in name or '{' in name:
            continue
        if name not in defines:
            defines.append(name)
    return defines


def extract_substs(m4_content: str) -> list:
    """Extract AC_SUBST variable names."""
    substs = []
    # Match AC_SUBST([NAME]) or AC_SUBST([NAME], [value])
    pattern = r'AC_SUBST\s*\(\s*\[([^\]]+)\]'
    for match in re.finditer(pattern, m4_content):
        name = match.group(1)
        # Skip template variables like ${GLTYPE}
        if '$' in name or '{' in name:
            continue
        if name not in substs:
            substs.append(name)
    return substs


def generate_configure_ac(module_name: str, defuns: list, gnulib_h_name: str) -> str:
    """Generate configure.ac file."""
    lines = [
        f'AC_INIT([test_{module_name}], [1.0])',
        'AC_CONFIG_HEADERS([config.h])',
        '',
        f'# Invoke macros from {module_name}.m4',
    ]
    
    for defun in defuns:
        lines.append(defun)
    
    lines.extend([
        '',
        f'AC_CONFIG_FILES([{gnulib_h_name}:{gnulib_h_name}.in])',
        'AC_OUTPUT',
    ])
    
    return '\n'.join(lines)


def generate_config_h_in(defines: list) -> str:
    """Generate config.h.in file."""
    lines = ['/* config.h.in - AC_DEFINE placeholders */']
    for define in sorted(set(defines)):
        lines.append(f'#undef {define}')
    return '\n'.join(lines) + '\n'


def generate_gnulib_h_in(module_name: str, substs: list) -> str:
    """Generate gnulib_*.h.in file."""
    if not substs:
        return f'/* gnulib_{module_name}.h.in - No AC_SUBST placeholders */\n'
    
    lines = [f'/* gnulib_{module_name}.h.in - AC_SUBST placeholders */']
    for subst in sorted(set(substs)):
        lines.append(f'#define SUBST_{subst} @{subst}@')
    return '\n'.join(lines) + '\n'


def process_module(module_dir: Path) -> tuple:
    """Process a single module.
    
    Returns: (success, message)
    """
    module_name = module_dir.name
    
    # Find the corresponding m4 file
    m4_file = GNULIB_M4_DIR / f'{module_name}.m4'
    
    if not m4_file.exists():
        # Try alternate naming
        for alt_name in [module_name.replace('_', '-'), module_name.replace('-', '_')]:
            m4_file = GNULIB_M4_DIR / f'{alt_name}.m4'
            if m4_file.exists():
                break
        else:
            return (False, f"No m4 file found")
    
    try:
        m4_content = m4_file.read_text()
    except Exception as e:
        return (False, f"Failed to read m4: {e}")
    
    # Extract information
    defuns = extract_defuns(m4_content)
    defines = extract_defines(m4_content)
    substs = extract_substs(m4_content)
    
    if not defuns:
        return (False, f"No AC_DEFUN found")
    
    # Determine gnulib header name
    gnulib_h_name = f'gnulib_{module_name}.h'
    
    # Generate files
    configure_ac = generate_configure_ac(module_name, defuns, gnulib_h_name)
    config_h_in = generate_config_h_in(defines) if defines else '/* config.h.in - No AC_DEFINE placeholders */\n'
    gnulib_h_in = generate_gnulib_h_in(module_name, substs)
    
    # Write files
    (module_dir / 'configure.ac').write_text(configure_ac)
    (module_dir / 'config.h.in').write_text(config_h_in)
    (module_dir / f'{gnulib_h_name}.in').write_text(gnulib_h_in)
    
    return (True, f"{len(defuns)} defuns, {len(defines)} defines, {len(substs)} substs")


def main():
    workspace = Path(__file__).parent.parent.parent.parent
    variables_dir = workspace / 'gnulib' / 'tests' / 'variables'
    
    if not GNULIB_M4_DIR.exists():
        print(f"Error: {GNULIB_M4_DIR} does not exist")
        sys.exit(1)
    
    if len(sys.argv) > 1:
        modules = sys.argv[1:]
    else:
        skip = {'BUILD.bazel', '__pycache__'}
        skip.update(f.name for f in variables_dir.glob('*.py'))
        skip.update(f.name for f in variables_dir.glob('*.bzl'))
        modules = [d.name for d in variables_dir.iterdir() 
                   if d.is_dir() and d.name not in skip]
    
    success = 0
    failed = 0
    
    for module in sorted(modules):
        module_dir = variables_dir / module
        if not module_dir.exists():
            print(f"SKIP: {module} (not found)")
            continue
        
        ok, msg = process_module(module_dir)
        if ok:
            print(f"OK: {module} - {msg}")
            success += 1
        else:
            print(f"FAIL: {module} - {msg}")
            failed += 1
    
    print(f"\nSuccess: {success}, Failed: {failed}")


if __name__ == '__main__':
    main()
