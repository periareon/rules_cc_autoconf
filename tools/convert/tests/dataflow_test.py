"""Unit tests for shparse + dataflow + lower on the shapes gnulib m4 produces."""

from __future__ import annotations

import textwrap
import time
from collections.abc import Sequence

import pytest

from tools.convert import dataflow as df
from tools.convert import lower
from tools.convert import shparse as sh


def analyze(text: str, bindings: dict[str, df.Value] | None = None) -> df.Analysis:
    return df.Evaluator(bindings).run(sh.parse(textwrap.dedent(text)))


class _Options:
    """The option registry `lower.flag_arms` consults (ProjectState in the converter)."""

    def label(self, name: str, value: str) -> str:
        return f":{name}_enabled" if value == "yes" else f":{name}_{value}"

    def values(self, name: str) -> set[str]:
        return set()

    def default(self, name: str) -> bool:
        return False


# Shell parsing


def test_if_elif_else_with_test() -> None:
    seq = sh.parse('if test "$a" = yes; then\n  X=1\nelif test $b != no; then\n  X=2\nelse\n  X=3\nfi\n')
    (stmt,) = seq.stmts
    assert isinstance(stmt, sh.If)
    assert len(stmt.branches) == 3
    cond0, body0 = stmt.branches[0]
    assert isinstance(cond0, sh.TestEq)
    assert cond0.lhs.single_var == "a"
    assert cond0.rhs.literal == "yes"
    assign0 = body0.stmts[0]
    assert isinstance(assign0, sh.Assign)
    assert assign0.name == "X"
    cond1 = stmt.branches[1][0]
    assert isinstance(cond1, sh.TestEq)
    assert cond1.negate
    assert stmt.branches[2][0] is None


def test_numeric_test_is_equality() -> None:
    """`-eq` / `-ne` on configure's integer results compare like `=` / `!=`."""
    seq = sh.parse('if test $ac_cv_sizeof_void_p -eq 8; then\n  X=1\nfi\nif [ "$n" -ne 0 ]; then\n  Y=1\nfi\n')
    first, second = seq.stmts
    assert isinstance(first, sh.If)
    assert isinstance(second, sh.If)
    cond = first.branches[0][0]
    assert isinstance(cond, sh.TestEq)
    assert (cond.lhs.single_var, cond.rhs.literal, cond.negate) == ("ac_cv_sizeof_void_p", "8", False)
    second_cond = second.branches[0][0]
    assert isinstance(second_cond, sh.TestEq)
    assert second_cond.negate


def test_autoconf_as_if_shape() -> None:
    text = 'if test ${gl_cv_x+y}\nthen :\n  printf %s "(cached) " >&6\nelse case e in #(\n  e)\n    __RCCAC_NODE_7__\n    gl_cv_x=yes ;;\nesac\nfi\n'
    seq = sh.parse(text)
    (stmt,) = seq.stmts
    assert isinstance(stmt, sh.If)
    else_body = stmt.branches[1][1]
    (case,) = else_body.stmts
    assert isinstance(case, sh.Case)
    assert case.subject.literal == "e"
    assert case.items[0][0] == ["e"]
    kinds = [type(s).__name__ for s in case.items[0][1].stmts]
    assert kinds == ["Placeholder", "Assign"]


def test_case_and_list() -> None:
    seq = sh.parse('case "$host_os" in\n  darwin* | mingw*) REPLACE_X=1 ;;\n  *) : ;;\nesac\ntest $y = 1 && Z=2\n')
    case, lst = seq.stmts
    assert isinstance(case, sh.Case)
    assert case.subject.single_var == "host_os"
    assert case.items[0][0] == ["darwin*", "mingw*"]
    assert isinstance(lst, sh.If)


def test_words() -> None:
    seq = sh.parse('A="$B"\nC=$(uname)\nD="guessing yes"\nE=${F:-x}\n')
    a, c, d, e = seq.stmts
    assert isinstance(a, sh.Assign)
    assert isinstance(c, sh.Assign)
    assert isinstance(d, sh.Assign)
    assert isinstance(e, sh.Assign)
    assert a.value.single_var == "B"
    assert c.value.unknown
    assert d.value.literal == "guessing yes"
    assert e.value.unknown


# Dataflow


def test_case_on_composite_subject_stays_unknown() -> None:
    # make's jobserver: `case /$a/$b/ in */no/*)`: neither arm is decidable, and the default
    # arm is not the complement of the first (an Unknown is not a proposition).
    analysis = analyze("""\
        case /$make_cv_job_server/$user_job_server/ in
          */no/*) : ;;
          *) __RCCAC_NODE_7__ ;;
        esac
        """)
    guard = analysis.guards[7]
    assert guard is not df.FALSE
    assert df.has_unknown(guard)
    unknown = df.Unknown("x")
    assert df.conj(unknown, df.neg(unknown)) == df.And((unknown, df.Not(unknown)))
    assert df.disj(unknown, df.neg(unknown)) == df.Or((unknown, df.Not(unknown)))


def test_strerror_0() -> None:
    analysis = analyze(
        """\
        REPLACE_STRERROR_0=0
        __RCCAC_NODE_1__
        case "$gl_cv_func_strerror_0_works" in
          *yes) ;;
          *)
            REPLACE_STRERROR_0=1
            __RCCAC_NODE_2__
            ;;
        esac
        """,
        {"gl_cv_func_strerror_0_works": df.Probe("gl_cv_func_strerror_0_works")},
    )
    assert analysis.guards[1] == df.TRUE
    assert analysis.guards[2] == df.Not(df.Truthy("gl_cv_func_strerror_0_works"))
    decisions = analysis.decisions("REPLACE_STRERROR_0")
    assert [(d.cond, d.value) for d in decisions] == [
        (df.TRUE, df.Lit("0")),
        (df.Not(df.Truthy("gl_cv_func_strerror_0_works")), df.Lit("1")),
    ]
    assert lower.requires_list(analysis.guards[2]) == ["!gl_cv_func_strerror_0_works"]


def test_fstat_host_os_and_external_variable() -> None:
    analysis = analyze("""\
        case "$host_os" in
          darwin* | mingw* | windows* | solaris*)
            REPLACE_FSTAT=1
            ;;
        esac
        __RCCAC_NODE_3__
        if test $HAVE_FCHDIR = 0; then
          case "$gl_cv_func_open_directory_works" in
            *yes) ;;
            *)
              REPLACE_FSTAT=1
              ;;
          esac
        fi
        """)
    first, second = analysis.decisions("REPLACE_FSTAT")
    assert first.cond == df.HostOs(("darwin*", "mingw*", "windows*", "solaris*"))
    assert second.cond == df.And(
        (
            df.Eq("HAVE_FCHDIR", "0", by_value=True),  # another module's ALLCAPS variable: its words
            df.Not(df.Truthy("gl_cv_func_open_directory_works")),
        )
    )
    arms = lower.os_arms(first.cond)
    assert set(arms) == {"@platforms//os:windows", "@platforms//os:macos", lower.DEFAULT_ARM}
    assert arms["@platforms//os:windows"] is df.TRUE
    assert arms[lower.DEFAULT_ARM] is df.FALSE
    assert lower.unmatched_patterns(first.cond) == ["solaris*"]
    assert lower.expression(second.cond).expr == "HAVE_FCHDIR==0 && !gl_cv_func_open_directory_works"


def test_posix_memalign_if_else_and_globs() -> None:
    analysis = analyze(
        """\
        if test $ac_cv_func_posix_memalign = yes; then
          __RCCAC_NODE_5__
          case "$gl_cv_func_posix_memalign_works" in
            *yes) ;;
            *) REPLACE_POSIX_MEMALIGN=1 ;;
          esac
        else
          HAVE_POSIX_MEMALIGN=0
          case "$gl_cv_onwards_func_posix_memalign" in
            future*) REPLACE_POSIX_MEMALIGN=1 ;;
          esac
        fi
        """,
        {
            "ac_cv_func_posix_memalign": df.Probe("ac_cv_func_posix_memalign"),
            "gl_cv_func_posix_memalign_works": df.Probe("gl_cv_func_posix_memalign_works"),
        },
    )
    assert analysis.guards[5] == df.Truthy("ac_cv_func_posix_memalign")
    (have,) = analysis.decisions("HAVE_POSIX_MEMALIGN")
    assert have.cond == df.Not(df.Truthy("ac_cv_func_posix_memalign"))
    replace = analysis.decisions("REPLACE_POSIX_MEMALIGN")
    assert len(replace) == 2
    assert lower.expression(replace[0].cond).expr == "ac_cv_func_posix_memalign && !gl_cv_func_posix_memalign_works"
    # `future*` is a glob on an external variable: not expressible.
    assert not lower.expression(replace[1].cond).ok


def test_colon_joined_compare_and_cross_guess() -> None:
    analysis = analyze("""\
        if test "$GL_GENERATE_ERRNO_H:$REPLACE_STRERROR_0" = false:0; then
          X=1
        fi
        case "$host_os" in
          *-gnu* | gnu*) gl_cv_w="guessing yes" ;;
          mingw* | windows*) gl_cv_w="guessing yes" ;;
          *) gl_cv_w="$gl_cross_guess_normal" ;;
        esac
        """)
    (x,) = analysis.decisions("X")
    assert lower.expression(x.cond).expr == "GL_GENERATE_ERRNO_H==false && REPLACE_STRERROR_0==0"
    guesses = analysis.decisions("gl_cv_w")
    assert [lower.guess_value(g.value) for g in guesses] == ["yes", "yes", "no"]
    arms = lower.os_arms(guesses[2].cond)
    assert arms[lower.DEFAULT_ARM] is df.TRUE
    assert arms["@platforms//os:linux"] is df.FALSE
    assert arms["@platforms//os:windows"] is df.FALSE


def test_prefixed_compare() -> None:
    analysis = analyze(
        'if test "x$ac_cv_header_x_h" = xyes; then\n  X=1\nfi\n', {"ac_cv_header_x_h": df.Probe("ac_cv_header_x_h")}
    )
    (x,) = analysis.decisions("X")
    assert x.cond == df.Truthy("ac_cv_header_x_h")


def test_configure_option_through_enableval() -> None:
    analysis = analyze("""        if __RCCAC_FLAG_dfltcc__; then
          enableval=yes
          enable_dfltcc=yes
          case $enableval in
            yes|no) gl_dfltcc=$enableval ;;
            *) : # AC_MSG_ERROR ;;
          esac
        else
          enable_dfltcc=no
          gl_dfltcc=no
        fi
        if test "$gl_dfltcc" = yes; then
          __RCCAC_NODE_3__
        fi
        """)
    assert analysis.guards[3] == df.Flag("dfltcc")
    arms = lower.flag_arms(analysis.guards[3], _Options())
    assert arms == {":dfltcc_enabled": df.TRUE, lower.DEFAULT_ARM: df.FALSE}


def test_option_values_and_platform_dimensions() -> None:
    """`--with-ssl=openssl` is an option value; host CPU and `$GCC` are select dimensions."""
    analysis = analyze("""\
        if __RCCAC_FLAG_ssl__; then
          withval=$__rccac_optval_ssl
          with_ssl=$__rccac_optval_ssl
        else
          with_ssl=no
        fi
        if test "$with_ssl" = openssl; then
          __RCCAC_NODE_1__
        fi
        case $host_cpu in
          x86_64 | amd64) __RCCAC_NODE_2__ ;;
        esac
        if test "$GCC" = yes; then
          __RCCAC_NODE_3__
        fi
        case $host in
          x86_64-*-linux*) __RCCAC_NODE_4__ ;;
        esac
        """)
    assert df.FlagValue("ssl", "openssl") in df.flag_atoms(analysis.guards[1])
    arms = lower.flag_arms(analysis.guards[1], _Options())
    assert arms == {":ssl_enabled": df.FALSE, ":ssl_openssl": df.TRUE, lower.DEFAULT_ARM: df.FALSE}
    assert analysis.guards[2] == df.HostCpu(("x86_64", "amd64"))
    assert lower.cpu_arms(analysis.guards[2]) == {"@platforms//cpu:x86_64": df.TRUE, lower.DEFAULT_ARM: df.FALSE}
    assert analysis.guards[3] == df.Gnuc()
    assert lower.compiler_arms(analysis.guards[3]) == {
        "@rules_cc//cc/compiler:msvc-cl": df.FALSE,
        "@rules_cc//cc/compiler:clang-cl": df.FALSE,
        lower.DEFAULT_ARM: df.TRUE,
    }
    assert analysis.guards[4] == df.conj(df.HostCpu(("x86_64",)), df.HostOs(("linux*",)))
    assert lower.platform_atoms(analysis.guards[4])


def test_unknowns_are_reported_not_guessed() -> None:
    analysis = analyze("""\
        eval "foo=\\$bar"
        if some_command; then
          __RCCAC_NODE_9__
        fi
        """)
    assert analysis.unknowns == ['eval "foo=\\$bar"']
    assert isinstance(analysis.guards[9], df.Unknown)
    assert lower.requires_list(analysis.guards[9]) is None


def test_backticks_pair_within_a_line() -> None:
    # A GNU-style `word' in a comment must not pair with the next real
    # command substitution: SHLIB_MAJOR is a substitution, not `expr`.
    text = textwrap.dedent("""\
        # don't use `foo' here
        SHLIB_MAJOR=`expr "$LIBVERSION" : '\\([0-9]\\)\\..*'`
        """)
    analysis = df.Evaluator().run(sh.parse(text))
    (decision,) = analysis.decisions("SHLIB_MAJOR")
    assert isinstance(decision.value, df.UnknownValue)


def test_substitute_empty_and_mentions() -> None:
    analysis = df.Evaluator().run(sh.parse('test -n "$ARFLAGS" || ARFLAGS="cr"\n'))
    (decision,) = analysis.decisions("ARFLAGS")
    assert df.mentions(decision.cond, "ARFLAGS")
    assert not df.mentions(decision.cond, "CFLAGS")
    assert df.substitute_empty(decision.cond, "ARFLAGS") is df.TRUE
    assert df.substitute_empty(df.Eq("X", "yes"), "X") is df.FALSE
    assert df.substitute_empty(df.Eq("X", ""), "X") is df.TRUE
    assert df.substitute_empty(df.Truthy("Y"), "X") == df.Truthy("Y")


def test_backticks_across_lines_but_not_in_comments() -> None:
    text = "# don't use `foo' here\nX=`cat <<EOF\nabc\nEOF`\nY=1\n"
    analysis = df.Evaluator().run(sh.parse(text))
    (x,) = analysis.decisions("X")
    assert isinstance(x.value, df.UnknownValue)
    (y,) = analysis.decisions("Y")
    assert y.value == df.Lit("1")


def test_rewrite_backticks() -> None:
    assert sh.rewrite_backticks("x=`a b`\n") == "x=$(a b)\n"
    assert sh.rewrite_backticks("# like `this'\nx=`a`\n") == "# like `this'\nx=$(a)\n"
    assert sh.rewrite_backticks("q='s/[\"`$]/x/'\nx=`a`\n") == "q='s/[\"`$]/x/'\nx=$(a)\n"
    assert sh.rewrite_backticks("x=`cat <<EOF\nabc\nEOF`\n") == "x=$(cat <<EOF\nabc\nEOF)\n"
    assert sh.rewrite_backticks('x="`a`" # c `d\'\n') == 'x="$(a)" # c `d\'\n'
    assert sh.rewrite_backticks("x=`a\n") == "x=`a\n"
    assert sh.rewrite_backticks("x=`( print -n ) 2>/dev/null`\n") == "x=$( ( print -n ) 2>/dev/null)\n"


@pytest.mark.parametrize("guard", ["test ${gl_cv_x+y}", "${gl_cv_x+:} false", 'test "${gl_cv_x+set}" = set'])
def test_cache_hit_guards_never_hold(guard: str) -> None:
    """AC_CACHE_VAL's `if test ${var+y}` (and the older spellings) wrap the
    commands; a fresh configure has no cache, so the commands always run."""
    text = f"if {guard}\nthen :\nelse\n  gl_cv_x=yes\nfi\n"
    analysis = df.Evaluator().run(sh.parse(text))
    (decision,) = analysis.decisions("gl_cv_x")
    assert decision.cond is df.TRUE, guard


# Inherited assignments


def test_decided_reads_inlined_macro_assignments() -> None:
    """gmp's `want_cxx=no`, then GMP_PROG_CXX_WORKS (expanded in place) assigning `want_cxx=yes`
    under --enable-cxx: `test $want_cxx = yes` is the macro's condition, not "never"."""
    analysis = analyze("want_cxx=no\n")
    evaluator = analysis.evaluator
    assert evaluator is not None
    # translate's `_settle`: the inlined macro's conditional assignment voids the literal
    analysis.inherited["want_cxx"] = [df.Assignment(df.Truthy("enable_cxx"), df.Lit("yes"), "want_cxx=yes")]
    evaluator.state["want_cxx"] = None
    evaluator.branch_state["want_cxx"] = (df.Truthy("enable_cxx"), df.Lit("yes"))
    assert evaluator.compare("want_cxx", df.Lit("yes")) == df.Truthy("enable_cxx")
    assert evaluator.compare("want_cxx", df.Lit("no")) == df.neg(df.Truthy("enable_cxx"))


# Minimize


def test_ten_atoms_is_fast_and_equivalent() -> None:
    """libssh2's path conditions reach ten atoms: minimizing one took over twenty seconds per
    polarity with the pairwise combine (fifty million pairs over eight rounds); the bucketed
    one takes a fraction of a second for both.  The budget sits between the two with room for
    a loaded CI runner, which has taken close to three seconds."""
    atoms: list[df.Cond] = [df.Eq(f"v{i}", "yes") for i in range(10)]
    cond = df.disj(*[df.conj(atoms[i], df.neg(atoms[(i + 1) % 10]), atoms[(i + 3) % 10]) for i in range(10)])
    start = time.monotonic()
    positive = lower.minimize(cond, atoms, True)
    negative = lower.minimize(cond, atoms, False)
    assert time.monotonic() - start < 20.0
    assert positive is not None
    assert negative is not None
    for bits in range(1 << 10):
        values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
        expected = df.evaluate(cond, values)
        term = tuple(values[a] for a in atoms)

        def covered(implicants: Sequence[tuple[bool | None, ...]], term: tuple[bool, ...] = term) -> bool:
            return any(all(p is None or p == v for p, v in zip(imp, term)) for imp in implicants)

        assert covered(positive) == expected, bits
        assert covered(negative) == (not expected), bits


def test_minimize_merges_adjacent_terms() -> None:
    a, b = df.Eq("a", "yes"), df.Eq("b", "yes")
    assert lower.minimize(df.disj(df.conj(a, b), df.conj(a, df.neg(b))), [a, b], True) == [(True, None)]
    assert lower.minimize(df.disj(a, df.neg(a)), [a], True) is None
    assert lower.minimize(df.conj(a, df.neg(a)), [a], True) == []


# gl_CONDITIONAL shapes: the shell gnulib-comp.m4 puts in gl_CONDITIONAL's second argument


def _condition(shell: str) -> df.Cond:
    seq = sh.parse(f"if {shell}; then :; fi\n")
    assert len(seq.stmts) == 1
    (stmt,) = seq.stmts
    assert isinstance(stmt, sh.If)
    expr, _ = stmt.branches[0]
    assert expr is not None, shell
    return df.Evaluator().expr(expr)


def test_negated_brace_groups() -> None:
    # tree-sitter-bash reads `! {` as a command named `{`; the translator
    # rewrites the groups as subshells before parsing (see _test_condition).
    cond = _condition(
        "! ( test $HAVE_ISWCNTRL = 0 || test $REPLACE_ISWCNTRL = 1; ) "
        "&& ( test $HAVE_ISWBLANK = 0 || test $REPLACE_ISWBLANK = 1; )"
    )
    # !(A || B) && (C || D), in the normal form the lowering produces.
    assert lower.expression(cond).expr == (
        "!(HAVE_ISWCNTRL==0 || REPLACE_ISWCNTRL!=0 || (!HAVE_ISWBLANK==0 && REPLACE_ISWBLANK==0))"
    ), repr(cond)


def test_brace_group_alone() -> None:
    cond = _condition("{ test $HAVE_X = 0 || test $REPLACE_X = 1; }")
    assert lower.expression(cond).expr == "HAVE_X==0 || REPLACE_X!=0"  # REPLACE_*: zero or not


# Symbolic values: copies of a probe's result and the m4sh spellings of tests


def test_prefixed_test_with_quotes_after_the_prefix() -> None:
    """nasm's PA macros: `test x"$var" != xno` (the quote after the x) is the same test as `test "x$var"`."""
    analysis = analyze(
        """
        if test x"$gl_cv_foo" != xno; then
          X=1
        fi
    """,
        {"gl_cv_foo": df.Probe("gl_cv_foo")},
    )
    (assignment,) = analysis.decisions("X")
    assert assignment.cond == df.Truthy("gl_cv_foo")


def test_copy_of_a_probe_result_compares_as_the_probe() -> None:
    """nasm's PA_FIND_FUNC: `found="$pa_cv_func_x"`, then `test x"$found" != xyes` is the probe's failure,
    also when the copy is one of several conditional assignments (`decided`)."""
    analysis = analyze(
        """
        found=no
        if test "$a" = yes; then
          found="$pa_cv_func_x"
        fi
        if test x"$found" != xyes; then
          X=1
        fi
    """,
        {"a": df.Ref("a")},
    )
    (assignment,) = analysis.decisions("X")
    assert assignment.cond == df.neg(df.conj(df.Truthy("a"), df.Truthy("pa_cv_func_x")))


def test_pinned_variable_keeps_its_binding() -> None:
    """A compiler-option test's cache variable reads what the translator bound it to (no), whatever the
    shell around the probe assigns (`pa_cv_CFLAGS__Wall=yes` before the probe that may reset it)."""
    evaluator = df.Evaluator({"pa_cv_CFLAGS__Wall": df.Lit("no")}, pinned={"pa_cv_CFLAGS__Wall"})
    analysis = evaluator.run(
        sh.parse(
            textwrap.dedent("""
        pa_cv_CFLAGS__Wall=yes
        if test "x$pa_cv_CFLAGS__Wall" = xyes; then
          X=1
        fi
    """)
        )
    )
    (assignment,) = analysis.decisions("X")
    assert assignment.cond is df.FALSE


def test_gnu_compiler_variables() -> None:
    """`ac_compiler_gnu` (AC_PROG_CC's own name for `$GCC`) is the compiler atom."""
    analysis = analyze("""
        if test x$ac_compiler_gnu = xyes; then
          X=1
        fi
    """)
    (assignment,) = analysis.decisions("X")
    assert assignment.cond == df.Gnuc()


# gnulib conditional dependencies


def test_m4code_function_body_is_read_where_defined() -> None:
    """bison's gl_INIT (gnulib-tool --conditional-dependencies): the module's m4 code sits in a
    shell function under `if ! $gl_gnulib_enabled_<hash>`; a static build enables every module,
    so the AC_LIBOBJ placeholder inside is guarded by the inner test alone."""
    analysis = analyze("""
        gl_gnulib_enabled_332607f759618fb73dfc3076748afea7=false
        func_gl_gnulib_m4code_332607f759618fb73dfc3076748afea7 ()
        {
          if ! $gl_gnulib_enabled_332607f759618fb73dfc3076748afea7; then
            if test $HAVE_POSIX_SPAWN = 0 || test $REPLACE_POSIX_SPAWN = 1; then
              __RCCAC_NODE_7__
            fi
            gl_gnulib_enabled_332607f759618fb73dfc3076748afea7=true
          fi
        }
        func_other ()
        {
          __RCCAC_NODE_8__
        }
    """)
    assert analysis.guards[7] == df.disj(
        df.Eq("HAVE_POSIX_SPAWN", "0", by_value=True), df.Eq("REPLACE_POSIX_SPAWN", "1", by_value=True)
    )
    assert 8 not in analysis.guards  # an ordinary function: when it runs is not known
