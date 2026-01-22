#!/usr/bin/env python3.11

import glob
import shutil
import stat
from pathlib import Path

DEST = Path("gnulib/tests/compatibility")

MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH

OK = [
"c-stack",
"func",
"gc",
"gc-arcfour",
"gc-arctwo",
"gc-camellia",
"gc-des",
"gc-hmac-md5",
"gc-hmac-sha1",
"gc-hmac-sha256",
"gc-hmac-sha512",
"gc-md4",
"gc-md5",
"gc-rijndael",
"gc-sha1",
"gc-sha256",
"gc-sha512",
"gc-sm3",
"gettext",
"host-cpu-c-abi",
"iconv_open",
"init-package-version",
"javacomp",
"largefile",
"lib-link",
"libsigsegv",
"libtextstyle",
"libtextstyle-optional",
"libunistring",
"libunistring-base",
"libunistring-optional",
"manywarnings",
"minus-zero",
"nocrash",
"progtest",
"sigsegv",
"std-gnu23",
"stdint",
"terminfo",
]

for entry in sorted(glob.glob("bazel-testlogs/gnulib/tests/compatibility/*")):
    path = Path(entry)
    name = path.name
    if name not in OK:
        continue

    dest_dir = DEST / name
    dest_config = dest_dir / "golden_config.h.in"
    dest_subst = dest_dir / "golden_subst.h.in"

    test_dir = path / f"{name}_test_gnu_autoconf"
    if not test_dir.exists():
        test_dir = path / f"{name}_test_gnu_autoconf_macos"

    if not dest_config.exists():
        dest_config = dest_dir / "golden_config_macos.h.in"

    if not dest_subst.exists():
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
        (dest_dir / "subst.h.in").chmod(MODE)
    if config_in.exists():
        shutil.copy(config_in, dest_dir / "config.h.in")
        (dest_dir / "config.h.in").chmod(MODE)

    print(name)

# for entry in sorted(glob.glob("gnulib/tests/compatibility/*")):
#     if entry.endswith((".bazel", ".bzl")):
#         continue
#     files = sorted([f for f in glob.glob(f"{entry}/golden_config*.h.in") if not f.endswith("_linux.h.in")])
#     if len(files) != 1:
#         print(files)
#     # if not files:
#     #     dest_config = Path(entry) / "golden_config.h.in"
#     #     dest_config.write_text("", encoding="utf-8")
#     #     dest_subst = Path(entry) / "golden_subst.h.in"
#     #     dest_subst.write_text("", encoding="utf-8")
