"""The tree-sitter-bash wheel loads and parses on this platform."""

from __future__ import annotations

import unittest

import tree_sitter_bash
from tree_sitter import Language, Parser


class TreeSitterSmokeTest(unittest.TestCase):

    def test_parse_if(self) -> None:
        parser = Parser(Language(tree_sitter_bash.language()))
        tree = parser.parse(b'if test "$ac_cv_header_x_h" = yes; then\n  REPLACE_X=1\nfi\n')
        root = tree.root_node
        self.assertEqual(root.type, "program")
        self.assertFalse(root.has_error)
        kinds = [child.type for child in root.children]
        self.assertEqual(kinds, ["if_statement"])
        if_statement = root.children[0]
        # `test` is an ordinary command; `[ ... ]` would be a test_command.
        self.assertEqual(if_statement.child_by_field_name("condition").type, "command")


if __name__ == "__main__":
    unittest.main()
