"""The tree-sitter-bash wheel loads and parses on this platform."""

from __future__ import annotations

import tree_sitter_bash
from tree_sitter import Language, Parser


def test_parse_if() -> None:
    parser = Parser(Language(tree_sitter_bash.language()))
    tree = parser.parse(b'if test "$ac_cv_header_x_h" = yes; then\n  REPLACE_X=1\nfi\n')
    root = tree.root_node
    assert root.type == "program"
    assert not root.has_error
    kinds = [child.type for child in root.children]
    assert kinds == ["if_statement"]
    if_statement = root.children[0]
    # `test` is an ordinary command; `[ ... ]` would be a test_command.
    condition = if_statement.child_by_field_name("condition")
    assert condition is not None
    assert condition.type == "command"
