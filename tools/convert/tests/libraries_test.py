"""The registry library table and its name lookup."""

from __future__ import annotations

import unittest

from tools.convert import libraries
from tools.convert.ir import Target
from tools.convert.project import _configure_cc_deps, _registry_libraries_in_configure
from tools.convert.translate import ProjectState


class LibrariesTest(unittest.TestCase):

    def test_link_names(self) -> None:
        self.assertEqual(libraries.known("z").label, "@zlib//:z")
        self.assertEqual(libraries.known("-lz").label, "@zlib//:z")
        self.assertEqual(libraries.known("gmp").module, "gmp")
        self.assertIsNone(libraries.known("m"))  # the host's, a link option
        self.assertIsNone(libraries.known("nosuchlibrary"))

    def test_alternate_spellings(self) -> None:
        """`libz`, the module or pkg-config name and a versioned pkg-config name find the entry."""
        self.assertEqual(libraries.known("libz").label, "@zlib//:z")
        self.assertEqual(libraries.known("zlib").label, "@zlib//:z")
        self.assertEqual(libraries.known("libcurl").module, "curl")
        self.assertEqual(libraries.known("libpng16").label, "@libpng//:png")
        self.assertEqual(libraries.known("libssl").label, "@openssl//:ssl")
        # openssl backs ssl and crypto: the module name alone names neither.
        self.assertIsNone(libraries.known("openssl"))
        # ncurses backs several names and is one of them.
        self.assertEqual(libraries.known("ncurses").label, libraries.known("tinfo").label)

    def test_configure_links_registry_library(self) -> None:
        """`LIBS="-lgmp $LIBS"` in configure's own shell: the module is recorded and the root
        targets get it as cc_deps, so the probes see its headers and archive."""
        state = ProjectState()
        notes = _registry_libraries_in_configure(
            {"configure.ac": 'dnl -lfoo in a comment is not a link\nLIBS="-lgmp $LIBS"\n',
             "acinclude.m4": "#include <zlib.h>\n", "gnulib-comp.m4": 'LIBS="-lpng $LIBS"'},
            {"configure.ac", "acinclude.m4"}, state)
        self.assertEqual(state.configure_libs, {"gmp": "@gmp//:gmp", "z": "@zlib//:z"})
        self.assertEqual(set(state.external_modules), {"gmp", "zlib"})
        self.assertEqual(len(notes), 2)
        main, segment = Target(name="autoconf"), Target(name="autoconf_1")
        _configure_cc_deps([segment, main], state)
        self.assertEqual(main.attrs["cc_deps"], ["@gmp//:gmp", "@zlib//:z"])
        self.assertEqual(segment.attrs["cc_deps"], ["@gmp//:gmp", "@zlib//:z"])

    def test_table_entries_are_labels(self) -> None:
        for name, entry in libraries.table().items():
            self.assertTrue(entry.label.startswith(f"@{entry.module}//:"), (name, entry))


if __name__ == "__main__":
    unittest.main()
