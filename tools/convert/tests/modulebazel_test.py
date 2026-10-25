"""Tests for MODULE.bazel generation: what an existing file's module() entry contributes."""

from __future__ import annotations

from pathlib import Path

from tools.convert import modulebazel


def test_module_entry_is_kept_and_the_rest_reported(tmp_path: Path) -> None:
    path = tmp_path / "MODULE.bazel"
    path.write_text('''"""A hand-written module."""

module(
    name = "foo",
    version = "1.2.bcr.3",
    compatibility_level = 1,
    bazel_compatibility = [">=7.0.0"],
)

bazel_dep(name = "rules_cc", version = "0.1.0")
bazel_dep(name = "zlib", version = "1.3.1")

single_version_override(
    module_name = "zlib",
    version = "1.3.1",
)

register_toolchains("//:all")
''')
    attrs, others = modulebazel.existing_module(path)
    assert attrs == {"compatibility_level": "1", "bazel_compatibility": '[">=7.0.0"]'}
    assert others == ["single_version_override( ...", 'register_toolchains("//:all")']
    text = modulebazel._module_bazel("foo", "1.2.bcr.3", "0.28.0", None, False, module_attrs=attrs)
    assert (
        'module(\n    name = "foo",\n    version = "1.2.bcr.3",\n    bazel_compatibility = [">=7.0.0"],\n'
        "    compatibility_level = 1,\n)"
    ) in text
    assert modulebazel.existing_module(tmp_path / "missing") == ({}, [])


def test_bazel_floor_follows_the_rules() -> None:
    """A kept `>=` below the rules' floor is raised, none is added, a higher one and the other
    constraints stay; the change is reported, an unchanged value is not."""
    raised, note = modulebazel.with_bazel_floor({"bazel_compatibility": '[">=7.2.1"]'}, "7.4.0")
    assert raised == {"bazel_compatibility": '[">=7.4.0"]'}
    assert note == '`bazel_compatibility = [">=7.4.0"]` (was `[">=7.2.1"]`): rules_cc_autoconf needs Bazel 7.4.0'
    added, note = modulebazel.with_bazel_floor({"compatibility_level": "1"}, "7.4.0")
    assert added == {"compatibility_level": "1", "bazel_compatibility": '[">=7.4.0"]'}
    assert note is not None and "was not declared" in note
    kept, note = modulebazel.with_bazel_floor({"bazel_compatibility": '[">=8.0.0", "<10"]'}, "7.4.0")
    assert (kept, note) == ({"bazel_compatibility": '[">=8.0.0", "<10"]'}, None)
    merged, note = modulebazel.with_bazel_floor({"bazel_compatibility": '["<10", ">=7.2.1"]'}, "7.4.0")
    assert (merged["bazel_compatibility"], note is not None) == ('[">=7.4.0", "<10"]', True)
    assert modulebazel.with_bazel_floor({"bazel_compatibility": '[">=7.2.1"]'}, None) == (
        {"bazel_compatibility": '[">=7.2.1"]'},
        None,
    )
    text = modulebazel._module_bazel("foo", "1.2.bcr.3", "0.34.0", None, False, module_attrs=raised)
    assert '    bazel_compatibility = [">=7.4.0"],\n' in text
