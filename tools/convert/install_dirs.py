"""Automake's installation directories at configure's defaults, and shell values expanded at them.

A leaf module: `translate.py` and `makefiles.py` both read it, and neither may import the other
for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Automake's installation directories at configure's defaults (`--prefix=/usr/local`).
INSTALL_DIRS = {
    "prefix": "/usr/local",
    "exec_prefix": "$(prefix)",
    "bindir": "$(exec_prefix)/bin",
    "sbindir": "$(exec_prefix)/sbin",
    "libexecdir": "$(exec_prefix)/libexec",
    "libdir": "$(exec_prefix)/lib",
    "datarootdir": "$(prefix)/share",
    "datadir": "$(datarootdir)",
    "sysconfdir": "$(prefix)/etc",
    "sharedstatedir": "$(prefix)/com",
    "localstatedir": "$(prefix)/var",
    "runstatedir": "$(localstatedir)/run",
    "includedir": "$(prefix)/include",
    "oldincludedir": "/usr/include",
    "localedir": "$(datarootdir)/locale",
    "infodir": "$(datarootdir)/info",
    "mandir": "$(datarootdir)/man",
    "docdir": "$(datarootdir)/doc/$(PACKAGE)",
    "pkgdatadir": "$(datadir)/$(PACKAGE)",
    "pkglibdir": "$(libdir)/$(PACKAGE)",
    "pkglibexecdir": "$(libexecdir)/$(PACKAGE)",
    "pkgincludedir": "$(includedir)/$(PACKAGE)",
    "htmldir": "$(docdir)",
    "dvidir": "$(docdir)",
    "pdfdir": "$(docdir)",
    "psdir": "$(docdir)",
    "lispdir": "$(datarootdir)/emacs/site-lisp",
    "PATH_SEPARATOR": ":",
}

_DIR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$\(([A-Za-z_][A-Za-z0-9_]*)\)|\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_install_dirs(text: str, substs: Mapping[str, str] | None = None) -> str:
    """A shell value at configure's defaults: `${runstatedir}/pppd` is `/usr/local/var/run/pppd`,
    `${libdir}/pppd/$VERSION` is `/usr/local/lib/pppd/2.5.2` with `substs` (the literal
    substitutions, VERSION among them).  A reference neither table knows stays as written."""
    known = substs or {}
    for _ in range(8):

        def one(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2) or match.group(3)
            if name in INSTALL_DIRS:
                return INSTALL_DIRS[name]
            if name in known:
                return known[name]
            return match.group(0)

        expanded = _DIR_REF_RE.sub(one, text)
        if expanded == text:
            break
        text = expanded
    return text
