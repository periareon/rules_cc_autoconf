"""Tests for the SPDX identifier read from a license file and the source headers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.convert import licenses

GPL3 = "GNU GENERAL PUBLIC LICENSE\n   Version 3, 29 June 2007\n\n Copyright (C) 2007 Free Software Foundation, Inc.\n"
LGPL21 = "GNU LESSER GENERAL PUBLIC LICENSE\n   Version 2.1, February 1999\n"
OR_LATER = "/* This program is free software: you can redistribute it and/or modify\n   it under the terms of the GNU General Public License as published by\n   the Free Software Foundation, either version 3 of the License, or\n   (at your option) any later version. */\n"
ONLY = "/* Licensed under the terms of version 2.1 of the GNU Lesser General Public License. */\n"


def _tree(root: Path, license_text: str, headers: list[str], name: str = "COPYING") -> None:
    (root / name).write_text(license_text)
    for i, header in enumerate(headers):
        (root / f"src{i}.c").write_text(header + "int x;\n")


def test_gnu_text_with_the_headers_suffix(tmp_path: Path) -> None:
    """The text gives family and version; the headers' "any later version" gives `-or-later`, a
    bare "version N of the License" `-only`."""
    _tree(tmp_path, GPL3, [OR_LATER, OR_LATER])
    spdx, evidence = licenses.detect(tmp_path, "COPYING")
    assert spdx == "GPL-3.0-or-later"
    assert "2 source headers accept any later version" in evidence
    only = tmp_path / "only"
    only.mkdir()
    _tree(only, LGPL21, [ONLY], name="COPYING.LESSER")
    assert licenses.detect(only, "COPYING.LESSER")[0] == "LGPL-2.1-only"


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        pytest.param([], "no source header says", id="no-headers"),
        pytest.param(
            [OR_LATER, "/* under version 3 of the GNU General Public License */\n"], "disagree", id="disagree"
        ),
    ],
)
def test_gnu_text_the_headers_do_not_settle(tmp_path: Path, headers: list[str], reason: str) -> None:
    """A GNU text whose suffix the headers leave open is not guessed: the caller asks."""
    _tree(tmp_path, GPL3, headers)
    spdx, evidence = licenses.detect(tmp_path, "COPYING")
    assert spdx is None
    assert reason in evidence


@pytest.mark.parametrize(
    ("text", "spdx"),
    [
        pytest.param("Permission is hereby granted, free of charge, to any person obtaining a copy\n", "MIT", id="mit"),
        pytest.param(
            "Redistribution and use in source and binary forms, with or without modification, are permitted "
            "provided that the following conditions are met:\n1. ...\n2. ...\n3. Neither the name of the "
            "author nor the names of its contributors may be used\n",
            "BSD-3-Clause",
            id="bsd3",
        ),
        pytest.param(
            "Redistribution and use in source and binary forms, with or without modification, are permitted\n",
            "BSD-2-Clause",
            id="bsd2",
        ),
        pytest.param("Apache License\nVersion 2.0, January 2004\n", "Apache-2.0", id="apache"),
        pytest.param("Mozilla Public License Version 2.0\n", "MPL-2.0", id="mpl"),
        pytest.param(
            "Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee\n",
            "ISC",
            id="isc",
        ),
    ],
)
def test_permissive_texts(tmp_path: Path, text: str, spdx: str) -> None:
    """The permissive licenses are recognized from their text alone."""
    (tmp_path / "LICENSE").write_text(text)
    assert licenses.detect(tmp_path, "LICENSE")[0] == spdx


def test_title_before_preamble_mentions(tmp_path: Path) -> None:
    """The GPL 2 text mentions the Library GPL in its preamble: the family is the title's."""
    gpl2 = (
        "GNU GENERAL PUBLIC LICENSE\n Version 2, June 1991\n\n Preamble\n ... use the GNU Library General "
        "Public License instead of this License.\n"
    )
    _tree(tmp_path, gpl2, [OR_LATER], name="LICENSE.GPL-2")
    assert licenses.detect(tmp_path, "LICENSE.GPL-2")[0] == "GPL-2.0-or-later"


def test_bsd_third_clause_spellings(tmp_path: Path) -> None:
    """libpcap's text spells the non-endorsement clause as "the names of the authors may not be
    used": three clauses, like the standard "neither the name" wording."""
    (tmp_path / "LICENSE").write_text(
        "Redistribution and use in source and binary forms, with or without modification, are permitted "
        "provided that the following conditions are met: 1. ... 2. ... 3. The names of the authors may not "
        "be used to endorse or promote products derived from this software\n"
    )
    assert licenses.detect(tmp_path, "LICENSE")[0] == "BSD-3-Clause"


def test_unknown_text(tmp_path: Path) -> None:
    (tmp_path / "COPYRIGHT").write_text("Copyright 2001 Example Corp. All rights reserved. See the website.\n")
    spdx, evidence = licenses.detect(tmp_path, "COPYRIGHT")
    assert spdx is None
    assert "not a license text the converter recognizes" in evidence
