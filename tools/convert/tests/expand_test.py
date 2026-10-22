"""Unit tests for the staging step of the expansion."""

from __future__ import annotations

from pathlib import Path

from tools.convert import expand


def test_macro_dirs_outside_the_project(tmp_path: Path) -> None:
    """texlive's `libs/mpfi/configure.ac` says AC_CONFIG_MACRO_DIRS([../../m4]) and
    m4_include([../../m4/kpse-common.m4]): both are staged inside the work directory."""
    (tmp_path / "m4").mkdir()
    (tmp_path / "m4/kpse-common.m4").write_text("AC_DEFUN([KPSE_COMMON], [])\n")
    project = tmp_path / "libs/mpfi"
    project.mkdir(parents=True)
    (project / "configure.ac").write_text(
        "AC_INIT([mpfi], [1])\nAC_CONFIG_MACRO_DIRS([../../m4])\n"
        "m4_include([../../m4/kpse-common.m4])\nm4_include([../../version.ac])\nKPSE_COMMON\nAC_OUTPUT\n"
    )
    (tmp_path / "version.ac").write_text("m4_define([tex_live_version], [2026])\n")
    work = tmp_path / "work"
    work.mkdir()
    staged, dirs = expand.stage(project, work)
    text = staged.read_text()
    assert dirs[0] == "_outside_m4"
    assert (work / "_outside_m4/kpse-common.m4").is_file()
    assert "AC_CONFIG_MACRO_DIRS([_outside_m4])" in text
    assert "m4_include([_outside_m4/kpse-common.m4])" in text
    assert "m4_include([_outside_version_ac])" in text
    assert (work / "_outside_version_ac").is_file()
    assert not (tmp_path / "m4/work").exists()  # nothing written outside the work dir


def test_hand_written_aclocal_includes_are_staged(tmp_path: Path) -> None:
    """postgres: aclocal.m4 is hand-written and m4_includes `config/*.m4`; tcl's includes
    `../unix/tcl.m4`.  Both land in the work directory and the text says the staged names."""
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "configure.ac").write_text("AC_INIT([pg], [1])\nPGAC_X\nAC_OUTPUT\n")
    (project / "aclocal.m4").write_text("m4_include([config/general.m4])\nm4_include([../shared/tcl.m4])\n")
    (project / "config/general.m4").write_text("AC_DEFUN([PGAC_X], [])\n")
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/tcl.m4").write_text("AC_DEFUN([TCL_X], [])\n")
    work = tmp_path / "work"
    work.mkdir()
    expand.stage(project, work)
    assert (work / "config/general.m4").is_file()
    assert (work / "_outside_shared_tcl_m4").is_file()
    assert "m4_include([_outside_shared_tcl_m4])" in (work / "aclocal.m4").read_text()
    assert "m4_include([config/general.m4])" in (work / "aclocal.m4").read_text()


def test_plain_macro_dir(tmp_path: Path) -> None:
    (tmp_path / "m4").mkdir()
    (tmp_path / "m4/x.m4").write_text("")
    (tmp_path / "configure.ac").write_text(
        "AC_INIT([x], [1])\nAC_CONFIG_MACRO_DIRS([m4])\nm4_include([version])\nm4_include([ac/extra.ac])\n"
    )
    (tmp_path / "version").write_text("1.0\n")
    (tmp_path / "ac").mkdir()
    (tmp_path / "ac/extra.ac").write_text("m4_define([api], m4_include([ac/level.txt]))\n")
    (tmp_path / "ac/level.txt").write_text("3\n")
    work = tmp_path / "work"
    work.mkdir()
    _staged, dirs = expand.stage(tmp_path, work)
    assert dirs[0] == "m4"
    assert (work / "version").is_file()
    assert (work / "ac/level.txt").is_file()  # included by an included file


def test_stage_relaxes_the_gcc_exact_version_check(tmp_path: Path) -> None:
    """newlib and the rest of the GCC toolchain pin one autoconf version in config/override.m4
    and make AC_INIT fail under any other: the staged copy has the check emptied and the
    staging notes say so."""
    src = tmp_path / "src"
    (src / "config").mkdir(parents=True)
    (src / "configure.ac").write_text("AC_INIT([x], [1])\nAC_CONFIG_MACRO_DIRS([config])\nAC_OUTPUT\n")
    (src / "config/override.m4").write_text(
        "m4_ifndef([_GCC_AUTOCONF_VERSION],\n  [m4_define([_GCC_AUTOCONF_VERSION], [2.69])])\n"
        "AC_DEFUN([_GCC_AUTOCONF_VERSION_CHECK],\n[m4_if(m4_defn([_GCC_AUTOCONF_VERSION]),\n"
        "  m4_defn([m4_PACKAGE_VERSION]), [],\n"
        "  [m4_fatal([Please use exactly Autoconf ]_GCC_AUTOCONF_VERSION[ instead of ]"
        "m4_defn([m4_PACKAGE_VERSION])[.])])\n])\nm4_define([AC_INIT], m4_defn([AC_INIT])[\n"
        "_GCC_AUTOCONF_VERSION_CHECK\n])\n"
    )
    work = tmp_path / "work"
    work.mkdir()
    expand.stage(src, work)
    staged = (work / "config/override.m4").read_text()
    assert "AC_DEFUN([_GCC_AUTOCONF_VERSION_CHECK], [])" in staged
    assert "Please use exactly" not in staged
    notes = (work / "staging_notes.txt").read_text()
    assert "config/override.m4: requires autoconf 2.69 exactly" in notes
