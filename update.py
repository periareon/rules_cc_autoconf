#!/usr/bin/env python3.11

import glob
import shutil
import stat
from pathlib import Path

DEST = Path("gnulib/tests/compatibility")

MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH

for entry in sorted(glob.glob("bazel-testlogs/gnulib/tests/compatibility/*")):
    path = Path(entry)
    name = path.name

    dest_dir = DEST / name
    dest_config = dest_dir / "golden_config.h.in"
    dest_subst = dest_dir / "golden_subst.h.in"

    test_dir = path / f"{name}_test_gnu_autoconf"
    if not test_dir.exists():
        test_dir = path / f"{name}_test_gnu_autoconf_macos"
        dest_config = dest_dir / "golden_config_macos.h.in"
        dest_subst = dest_dir / "golden_subst_macos.h.in"

    outputs_dir = test_dir / "test.outputs"
    new_config = outputs_dir / "gnu_configure/config.h"
    new_subst = outputs_dir / "gnu_configure/subst.h"

    dest_config.chmod(MODE)
    dest_subst.chmod(MODE)

    try:
        content = new_config.read_text(encoding="utf-8")
        clean = "\n".join(content.splitlines()[1:]).strip() + "\n"
        dest_config.write_text(clean, encoding="utf-8")

        shutil.copy(new_subst, dest_subst)
    except Exception as exc:
        print(exc)

    dest_config.chmod(MODE)
    dest_subst.chmod(MODE)

    subst_in = outputs_dir / "expected_subst.h.in"
    config_in = outputs_dir / "expected_config.h.in"
    if subst_in.exists():
        shutil.copy(subst_in, dest_dir / "subst.h.in")
    if config_in.exists():
        shutil.copy(config_in, dest_dir / "config.h.in")

    print(name)
