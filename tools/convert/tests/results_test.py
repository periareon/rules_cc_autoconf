"""Tests for the passes over a target's checks: merging plural calls."""

from __future__ import annotations

from tools.convert.ir import Call, Raw, Target
from tools.convert.results import _merge_list_checks, _merge_outer_substs
from tools.convert.tests.helpers import calls

# Merging plural list checks.


def test_same_keywords_merge_into_the_first() -> None:
    """Plural calls with the same keywords join the first; a different keyword set, another
    macro, or a select() arm is its own group; names are not repeated."""
    target = Target(
        name="autoconf",
        checks=[
            Call("macros.AC_CHECK_FUNCS", [["a", "b"]], returns_list=True, comments=["one"]),
            Call("checks.AC_DEFINE", ["X", 1]),
            Call("macros.AC_CHECK_FUNCS", [["b", "c"]], returns_list=True, comments=["two"]),
            Call("macros.AC_CHECK_FUNCS", [["d"]], {"compile_defines": Raw("SYSTEM_EXTENSIONS")}, returns_list=True),
            Call("macros.AC_CHECK_HEADERS", [["h.h"]], returns_list=True),
            Call("macros.AC_CHECK_FUNCS", [["e"]], {"compile_defines": Raw("SYSTEM_EXTENSIONS")}, returns_list=True),
            Call("macros.AC_CHECK_FUNCS", [["f"]], {"requires": ["ac_cv_x"]}, returns_list=True),
        ],
    )
    target.selects = {
        "os": {
            "@platforms//os:linux": [
                Call("macros.AC_CHECK_FUNCS", [["g"]], returns_list=True),
                Call("macros.AC_CHECK_FUNCS", [["h"]], returns_list=True),
            ]
        }
    }
    assert _merge_list_checks([target]) == 3
    checks = calls(target.checks)
    assert [(c.fn, c.args[0]) for c in checks] == [
        ("macros.AC_CHECK_FUNCS", ["a", "b", "c"]),
        ("checks.AC_DEFINE", "X"),
        ("macros.AC_CHECK_FUNCS", ["d", "e"]),
        ("macros.AC_CHECK_HEADERS", ["h.h"]),
        ("macros.AC_CHECK_FUNCS", ["f"]),
    ]
    assert checks[0].comments == ["one", "two"]
    assert calls(target.selects["os"]["@platforms//os:linux"])[0].args[0] == ["g", "h"]


# Merging outer AC_SUBST constants into the probe's own if_false.


def test_outer_constant_equal_to_if_false_is_dropped() -> None:
    """The `MPC_HAVE_COMPLEX_H=0` of the branch without complex.h is the probe's own if_false:
    the probe requires the header, so where the header is missing its condition is false."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_HEADER", ["complex.h"]),
            Call(
                "checks.AC_TRY_LINK",
                [],
                {
                    "name": "autoconf_probe_mpc_54",
                    "code": "int main(void) { return 0; }",
                    "requires": ["ac_cv_header_complex_h"],
                },
            ),
            Call(
                "checks.AC_SUBST",
                ["MPC_HAVE_COMPLEX_H"],
                {"condition": "autoconf_probe_mpc_54", "if_true": 1, "if_false": 0},
            ),
            Call("checks.AC_SUBST", ["MPC_HAVE_COMPLEX_H", 0], {"requires": ["!ac_cv_header_complex_h"]}),
            Call("checks.AC_SUBST", ["OTHER", 0], {"requires": ["!ac_cv_header_complex_h"]}),
        ],
    )
    notes = _merge_outer_substs(target)
    checks = calls(target.checks)
    kinds = [(c.fn, c.args[0] if c.args else c.kwargs.get("name")) for c in checks]
    assert kinds == [
        ("checks.AC_CHECK_HEADER", "complex.h"),
        ("checks.AC_TRY_LINK", "autoconf_probe_mpc_54"),
        ("checks.AC_SUBST", "MPC_HAVE_COMPLEX_H"),
        ("checks.AC_SUBST", "OTHER"),
    ]
    assert "condition" in checks[2].kwargs
    assert len(notes) == 1
