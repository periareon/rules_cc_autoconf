"""Tests for the passes over a target's checks: merging plural calls."""

from __future__ import annotations

from tools.convert.decisions import Decisions
from tools.convert.ir import Call, Raw, Target
from tools.convert.results import (
    _drop_duplicate_defines,
    _merge_duplicate_defines,
    _merge_list_checks,
    _merge_outer_substs,
    _rename_repeated_probes,
)
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


# Repeated probes of one cache variable.


def test_same_probe_under_other_conditions_folds_into_the_first() -> None:
    """bash's BASH_TYPE_BITS16_T: AC_CHECK_TYPE(bits16_t) once per sizeof branch.  One probe
    stays, its `requires` the disjunction of the branches; the defines after it are untouched."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_TYPE", ["bits16_t"], {"requires": ["ac_cv_sizeof_short==2"]}),
            Call(
                "checks.AC_DEFINE",
                ["bits16_t", "short"],
                {"requires": ["!ac_cv_type_bits16_t", "ac_cv_sizeof_short==2"]},
            ),
            Call(
                "checks.AC_CHECK_TYPE", ["bits16_t"], {"requires": ["!ac_cv_sizeof_short==2", "ac_cv_sizeof_char==2"]}
            ),
            Call(
                "checks.AC_DEFINE", ["bits16_t", "char"], {"requires": ["!ac_cv_type_bits16_t", "ac_cv_sizeof_char==2"]}
            ),
            Call(
                "checks.AC_CHECK_TYPE", ["bits16_t"], {"requires": ["!ac_cv_sizeof_short==2 && !ac_cv_sizeof_char==2"]}
            ),
        ],
    )
    notes = _rename_repeated_probes(target)
    checks = calls(target.checks)
    assert [c.fn for c in checks] == ["checks.AC_CHECK_TYPE", "checks.AC_DEFINE", "checks.AC_DEFINE"]
    either = (
        "(ac_cv_sizeof_short==2) || (!ac_cv_sizeof_short==2 && ac_cv_sizeof_char==2)"
        " || (!ac_cv_sizeof_short==2 && !ac_cv_sizeof_char==2)"
    )
    assert checks[0].kwargs["requires"] == [either]
    assert "name" not in checks[0].kwargs
    assert checks[1].kwargs["requires"] == ["!ac_cv_type_bits16_t", "ac_cv_sizeof_short==2"]
    assert len(notes) == 2 and "folded into the first" in notes[0]


def test_identical_repeat_is_dropped() -> None:
    """The same check with the same condition twice, in a select() arm as well: one stays."""
    target = Target(name="autoconf")
    target.selects = {
        "readline": {
            ":readline_enabled": [
                Call("checks.AC_CHECK_LIB", ["termcap", "tgetent"], {"requires": ["!ac_cv_func_tgetent"]}),
                Call("checks.AC_CHECK_LIB", ["termcap", "tgetent"], {"requires": ["!ac_cv_func_tgetent"]}),
            ]
        }
    }
    _rename_repeated_probes(target)
    arm = calls(target.selects["readline"][":readline_enabled"])
    assert len(arm) == 1 and arm[0].kwargs["requires"] == ["!ac_cv_func_tgetent"]


def test_unconditional_repeat_makes_the_probe_unconditional() -> None:
    """A repeat with no condition means the probe ran on every path: no `requires` at all."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_TYPE", ["bits32_t"], {"requires": ["ac_cv_sizeof_int==4"]}),
            Call("checks.AC_CHECK_TYPE", ["bits32_t"]),
        ],
    )
    _rename_repeated_probes(target)
    checks = calls(target.checks)
    assert len(checks) == 1 and "requires" not in checks[0].kwargs


def test_other_includes_still_rename_the_repeat() -> None:
    """libpcap's shape is unchanged: a repeat that differs beyond `requires` becomes
    `<cache>_2`, run when the first failed, and later readers follow the new name."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_DECL", ["ether_hostton"], {"includes": ["#include <a.h>"]}),
            Call("checks.AC_CHECK_DECL", ["ether_hostton"], {"includes": ["#include <b.h>"]}),
            Call("checks.AC_DEFINE", ["HAVE_ETHER_HOSTTON", 1], {"requires": ["ac_cv_have_decl_ether_hostton"]}),
        ],
    )
    _rename_repeated_probes(target)
    checks = calls(target.checks)
    assert len(checks) == 3
    assert checks[1].kwargs["name"] == "ac_cv_have_decl_ether_hostton_2"
    assert checks[1].kwargs["requires"] == ["!ac_cv_have_decl_ether_hostton"]
    assert checks[2].kwargs["requires"] == ["ac_cv_have_decl_ether_hostton_2"]


def test_other_value_on_another_path_keeps_the_first_define_and_asks() -> None:
    """The typedef defines of BASH_TYPE_BITS64_T: `char *` where a pointer is 8 bytes, `double`
    elsewhere.  One producer per define: the first stays, the other is left out, and the value
    the other path's platforms get is a `define:` decision; an unconditional define of another
    name is untouched."""
    target = Target(
        name="autoconf",
        checks=[
            Call(
                "checks.AC_DEFINE",
                ["bits64_t", "char *"],
                {"requires": ["!ac_cv_type_bits64_t", "ac_cv_sizeof_char_p==8"]},
            ),
            Call("checks.AC_DEFINE", ["HAVE_X", 1]),
            Call(
                "checks.AC_DEFINE",
                ["bits64_t", "double"],
                {"requires": ["!ac_cv_type_bits64_t", "!ac_cv_sizeof_char_p==8"]},
            ),
        ],
    )
    asked = Decisions()
    notes = _merge_duplicate_defines(target, asked)
    checks = calls(target.checks)
    assert [(c.fn, c.args) for c in checks] == [
        ("checks.AC_DEFINE", ["bits64_t", "char *"]),
        ("checks.AC_DEFINE", ["HAVE_X", 1]),
    ]
    assert checks[0].kwargs["requires"] == ["!ac_cv_type_bits64_t", "ac_cv_sizeof_char_p==8"]
    note = (
        "configure.ac: bits64_t is defined as double on another path (!ac_cv_type_bits64_t && !ac_cv_sizeof_char_p==8); "
        "left out, the first path's define (char * under !ac_cv_type_bits64_t && ac_cv_sizeof_char_p==8) stays: "
        "the rules take one producer per define, so `define:bits64_t` says what the other paths' platforms get"
    )
    assert notes == [note]
    assert list(asked.pending) == ["define:bits64_t"]
    assert asked.pending["define:bits64_t"].context == {
        "paths": [
            "char * under !ac_cv_type_bits64_t && ac_cv_sizeof_char_p==8",
            "double under !ac_cv_type_bits64_t && !ac_cv_sizeof_char_p==8",
        ]
    }


def test_other_value_already_decided_is_not_asked_again() -> None:
    """A `define:` decision in the file answers the request: nothing pending, the first stays."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_DEFINE", ["bits64_t", "char *"], {"requires": ["ac_cv_sizeof_char_p==8"]}),
            Call("checks.AC_DEFINE", ["bits64_t", "double"], {"requires": ["!ac_cv_sizeof_char_p==8"]}),
        ],
    )
    decided = Decisions.from_text('DECISIONS = {"define:bits64_t": "double"}')
    _merge_duplicate_defines(target, decided)
    assert [c.args for c in calls(target.checks)] == [["bits64_t", "char *"]]
    assert not decided.pending and decided.used == {"define:bits64_t"}


def test_explicit_define_of_a_probes_own_define_is_dropped() -> None:
    """BASH_CHECK_LIB_SOCKET: the library check defines HAVE_LIBSOCKET itself, so the script's
    `AC_DEFINE(HAVE_LIBSOCKET)` under the probe's success would be a second producer."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_LIB", ["socket", "getpeername"], {"requires": ["!ac_cv_func_getpeername"]}),
            Call("checks.AC_DEFINE", ["HAVE_LIBSOCKET"], {"requires": ["ac_cv_lib_socket_getpeername"]}),
            Call("checks.AC_DEFINE", ["HAVE_SOCKETS", 1]),
        ],
    )
    notes = _merge_duplicate_defines(target)
    checks = calls(target.checks)
    assert [(c.fn, c.args[0]) for c in checks] == [
        ("checks.AC_CHECK_LIB", "socket"),
        ("checks.AC_DEFINE", "HAVE_SOCKETS"),
    ]
    note = (
        "configure.ac: AC_DEFINE(HAVE_LIBSOCKET) left out: AC_CHECK_LIB:socket defines it when it succeeds "
        "(configure defines it once either way; the rules take one producer per define)"
    )
    assert notes == [note]


def test_explicit_define_saying_more_than_the_probe_stays() -> None:
    """An unconditional define, or one with another value, is not the probe's statement again:
    both stay (the rules report the second producer, which needs a decision)."""
    target = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_LIB", ["socket", "getpeername"]),
            Call("checks.AC_DEFINE", ["HAVE_LIBSOCKET"]),
            Call("checks.AC_CHECK_LIB", ["nsl", "gethostbyname"]),
            Call("checks.AC_DEFINE", ["HAVE_LIBNSL", 2], {"requires": ["ac_cv_lib_nsl_gethostbyname"]}),
        ],
    )
    notes = _merge_duplicate_defines(target)
    assert [c.args[0] for c in calls(target.checks)] == ["socket", "HAVE_LIBSOCKET", "nsl", "HAVE_LIBNSL"]
    assert notes == []


def test_list_entry_dropped_for_a_conditional_define_widens_it() -> None:
    """BASH_FUNC_LSTAT: `AC_CHECK_FUNCS([lstat])` plus `AC_DEFINE(HAVE_LSTAT)` on the fallback
    path alone.  The list entry goes (one producer), and the define now holds where lstat
    exists as well as where the fallback link test did."""
    main = Target(
        name="autoconf",
        checks=[
            Call("macros.AC_CHECK_FUNCS", [["dup2", "lstat"]], returns_list=True),
            Call("checks.AC_DEFINE", ["HAVE_LSTAT"], {"requires": ["!ac_cv_func_lstat", "bash_cv_func_lstat"]}),
        ],
    )
    _drop_duplicate_defines(main, [])
    checks = calls(main.checks)
    assert [(c.fn, c.args[0]) for c in checks] == [
        ("macros.AC_CHECK_FUNCS", ["dup2"]),
        ("checks.AC_CHECK_FUNC", "lstat"),
        ("checks.AC_DEFINE", "HAVE_LSTAT"),
    ]
    assert checks[2].kwargs["requires"] == ["(!ac_cv_func_lstat && bash_cv_func_lstat) || (ac_cv_func_lstat)"]


def test_list_entry_dropped_under_a_condition_carries_it_to_the_cache_check() -> None:
    """`AC_CHECK_FUNCS([lstat])` under an `if`: the define-less check that keeps its cache
    variable runs under the same condition, as the list did."""
    main = Target(
        name="autoconf",
        checks=[
            Call("macros.AC_CHECK_FUNCS", [["lstat"]], {"requires": ["ac_cv_header_sys_stat_h"]}, returns_list=True),
            Call("checks.AC_DEFINE", ["HAVE_LSTAT"]),
        ],
    )
    _drop_duplicate_defines(main, [])
    checks = calls(main.checks)
    assert [(c.fn, c.args[0]) for c in checks] == [
        ("checks.AC_CHECK_FUNC", "lstat"),
        ("checks.AC_DEFINE", "HAVE_LSTAT"),
    ]
    assert checks[0].kwargs == {"requires": ["ac_cv_header_sys_stat_h"]}
    assert "requires" not in checks[1].kwargs  # unconditional already: nothing to widen
