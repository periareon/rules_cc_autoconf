"""Unit tests for shparse + dataflow + lower on the shapes gnulib m4 produces."""

from __future__ import annotations

import textwrap
import unittest

from tools.convert import dataflow as df
from tools.convert import lower
from tools.convert import shparse as sh


def analyze(text: str, bindings: dict | None = None) -> df.Analysis:
    return df.Evaluator(bindings).run(sh.parse(textwrap.dedent(text)))


class _Options:
    """The option registry `lower.flag_arms` consults (ProjectState in the converter)."""

    def label(self, name: str, value: str) -> str:
        return f":{name}_enabled" if value == "yes" else f":{name}_{value}"

    def values(self, name: str) -> set[str]:
        return set()


class ShParseTest(unittest.TestCase):

    def test_if_elif_else_with_test(self) -> None:
        seq = sh.parse('if test "$a" = yes; then\n  X=1\nelif test $b != no; then\n  X=2\nelse\n  X=3\nfi\n')
        (stmt,) = seq.stmts
        self.assertIsInstance(stmt, sh.If)
        self.assertEqual(len(stmt.branches), 3)
        cond0, body0 = stmt.branches[0]
        self.assertIsInstance(cond0, sh.TestEq)
        self.assertEqual(cond0.lhs.single_var, "a")
        self.assertEqual(cond0.rhs.literal, "yes")
        self.assertEqual(body0.stmts[0].name, "X")
        self.assertTrue(stmt.branches[1][0].negate)
        self.assertIsNone(stmt.branches[2][0])

    def test_numeric_test_is_equality(self) -> None:
        """`-eq` / `-ne` on configure's integer results compare like `=` / `!=`."""
        seq = sh.parse('if test $ac_cv_sizeof_void_p -eq 8; then\n  X=1\nfi\nif [ "$n" -ne 0 ]; then\n  Y=1\nfi\n')
        first, second = seq.stmts
        cond = first.branches[0][0]
        self.assertIsInstance(cond, sh.TestEq)
        self.assertEqual((cond.lhs.single_var, cond.rhs.literal, cond.negate), ("ac_cv_sizeof_void_p", "8", False))
        self.assertTrue(second.branches[0][0].negate)

    def test_autoconf_as_if_shape(self) -> None:
        text = 'if test ${gl_cv_x+y}\nthen :\n  printf %s "(cached) " >&6\nelse case e in #(\n  e)\n    __RCCAC_NODE_7__\n    gl_cv_x=yes ;;\nesac\nfi\n'
        seq = sh.parse(text)
        (stmt,) = seq.stmts
        self.assertIsInstance(stmt, sh.If)
        else_body = stmt.branches[1][1]
        (case,) = else_body.stmts
        self.assertIsInstance(case, sh.Case)
        self.assertEqual(case.subject.literal, "e")
        self.assertEqual(case.items[0][0], ["e"])
        kinds = [type(s).__name__ for s in case.items[0][1].stmts]
        self.assertEqual(kinds, ["Placeholder", "Assign"])

    def test_case_and_list(self) -> None:
        seq = sh.parse('case "$host_os" in\n  darwin* | mingw*) REPLACE_X=1 ;;\n  *) : ;;\nesac\ntest $y = 1 && Z=2\n')
        case, lst = seq.stmts
        self.assertEqual(case.subject.single_var, "host_os")
        self.assertEqual(case.items[0][0], ["darwin*", "mingw*"])
        self.assertIsInstance(lst, sh.If)

    def test_words(self) -> None:
        seq = sh.parse('A="$B"\nC=$(uname)\nD="guessing yes"\nE=${F:-x}\n')
        a, c, d, e = seq.stmts
        self.assertEqual(a.value.single_var, "B")
        self.assertTrue(c.value.unknown)
        self.assertEqual(d.value.literal, "guessing yes")
        self.assertTrue(e.value.unknown)


class DataflowTest(unittest.TestCase):

    def test_strerror_0(self) -> None:
        analysis = analyze("""\
            REPLACE_STRERROR_0=0
            __RCCAC_NODE_1__
            case "$gl_cv_func_strerror_0_works" in
              *yes) ;;
              *)
                REPLACE_STRERROR_0=1
                __RCCAC_NODE_2__
                ;;
            esac
            """, {"gl_cv_func_strerror_0_works": df.Probe("gl_cv_func_strerror_0_works")})
        self.assertEqual(analysis.guards[1], df.TRUE)
        self.assertEqual(analysis.guards[2], df.Not(df.Truthy("gl_cv_func_strerror_0_works")))
        decisions = analysis.decisions("REPLACE_STRERROR_0")
        self.assertEqual([(d.cond, d.value) for d in decisions], [
            (df.TRUE, df.Lit("0")),
            (df.Not(df.Truthy("gl_cv_func_strerror_0_works")), df.Lit("1")),
        ])
        self.assertEqual(lower.requires_list(analysis.guards[2]), ["!gl_cv_func_strerror_0_works"])

    def test_fstat_host_os_and_external_variable(self) -> None:
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
        self.assertEqual(first.cond, df.HostOs(("darwin*", "mingw*", "windows*", "solaris*")))
        self.assertEqual(second.cond, df.And((
            df.Eq("HAVE_FCHDIR", "0"),
            df.Not(df.Truthy("gl_cv_func_open_directory_works")),
        )))
        arms = lower.os_arms(first.cond)
        self.assertEqual(set(arms), {"@platforms//os:windows", "@platforms//os:macos", lower.DEFAULT_ARM})
        self.assertIs(arms["@platforms//os:windows"], df.TRUE)
        self.assertIs(arms[lower.DEFAULT_ARM], df.FALSE)
        self.assertEqual(lower.unmatched_patterns(first.cond), ["solaris*"])
        self.assertEqual(lower.expression(second.cond).expr,
                         "HAVE_FCHDIR==0 && !gl_cv_func_open_directory_works")

    def test_posix_memalign_if_else_and_globs(self) -> None:
        analysis = analyze("""\
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
            """, {"ac_cv_func_posix_memalign": df.Probe("ac_cv_func_posix_memalign"),
                  "gl_cv_func_posix_memalign_works": df.Probe("gl_cv_func_posix_memalign_works")})
        self.assertEqual(analysis.guards[5], df.Truthy("ac_cv_func_posix_memalign"))
        (have,) = analysis.decisions("HAVE_POSIX_MEMALIGN")
        self.assertEqual(have.cond, df.Not(df.Truthy("ac_cv_func_posix_memalign")))
        replace = analysis.decisions("REPLACE_POSIX_MEMALIGN")
        self.assertEqual(len(replace), 2)
        self.assertEqual(lower.expression(replace[0].cond).expr,
                         "ac_cv_func_posix_memalign && !gl_cv_func_posix_memalign_works")
        # `future*` is a glob on an external variable: not expressible.
        self.assertFalse(lower.expression(replace[1].cond).ok)

    def test_colon_joined_compare_and_cross_guess(self) -> None:
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
        self.assertEqual(lower.expression(x.cond).expr, "GL_GENERATE_ERRNO_H==false && REPLACE_STRERROR_0==0")
        guesses = analysis.decisions("gl_cv_w")
        self.assertEqual([lower.guess_value(g.value) for g in guesses], ["yes", "yes", "no"])
        arms = lower.os_arms(guesses[2].cond)
        self.assertIs(arms[lower.DEFAULT_ARM], df.TRUE)
        self.assertIs(arms["@platforms//os:linux"], df.FALSE)
        self.assertIs(arms["@platforms//os:windows"], df.FALSE)

    def test_prefixed_compare(self) -> None:
        analysis = analyze('if test "x$ac_cv_header_x_h" = xyes; then\n  X=1\nfi\n',
                           {"ac_cv_header_x_h": df.Probe("ac_cv_header_x_h")})
        (x,) = analysis.decisions("X")
        self.assertEqual(x.cond, df.Truthy("ac_cv_header_x_h"))

    def test_configure_option_through_enableval(self) -> None:
        analysis = analyze("""            if __RCCAC_FLAG_dfltcc__; then
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
        self.assertEqual(analysis.guards[3], df.Flag("dfltcc"))
        arms = lower.flag_arms(analysis.guards[3], _Options())
        self.assertEqual(arms, {":dfltcc_enabled": df.TRUE, lower.DEFAULT_ARM: df.FALSE})

    def test_option_values_and_platform_dimensions(self) -> None:
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
        self.assertIn(df.FlagValue("ssl", "openssl"), df.flag_atoms(analysis.guards[1]))
        arms = lower.flag_arms(analysis.guards[1], _Options())
        self.assertEqual(arms, {":ssl_enabled": df.FALSE, ":ssl_openssl": df.TRUE, lower.DEFAULT_ARM: df.FALSE})
        self.assertEqual(analysis.guards[2], df.HostCpu(("x86_64", "amd64")))
        self.assertEqual(lower.cpu_arms(analysis.guards[2]), {"@platforms//cpu:x86_64": df.TRUE, lower.DEFAULT_ARM: df.FALSE})
        self.assertEqual(analysis.guards[3], df.Gnuc())
        self.assertEqual(lower.compiler_arms(analysis.guards[3]), {
            "@rules_cc//cc/compiler:msvc-cl": df.FALSE, "@rules_cc//cc/compiler:clang-cl": df.FALSE,
            lower.DEFAULT_ARM: df.TRUE})
        self.assertEqual(analysis.guards[4], df.conj(df.HostCpu(("x86_64",)), df.HostOs(("linux*",))))
        self.assertTrue(lower.platform_atoms(analysis.guards[4]))

    def test_unknowns_are_reported_not_guessed(self) -> None:
        analysis = analyze("""\
            eval "foo=\\$bar"
            if some_command; then
              __RCCAC_NODE_9__
            fi
            """)
        self.assertEqual(analysis.unknowns, ['eval "foo=\\$bar"'])
        self.assertIsInstance(analysis.guards[9], df.Unknown)
        self.assertIsNone(lower.requires_list(analysis.guards[9]))


    def test_backticks_pair_within_a_line(self) -> None:
        # A GNU-style `word' in a comment must not pair with the next real
        # command substitution: SHLIB_MAJOR is a substitution, not `expr`.
        text = textwrap.dedent("""\
            # don't use `foo' here
            SHLIB_MAJOR=`expr "$LIBVERSION" : '\\([0-9]\\)\\..*'`
            """)
        analysis = df.Evaluator().run(sh.parse(text))
        (decision,) = analysis.decisions("SHLIB_MAJOR")
        self.assertIsInstance(decision.value, df.UnknownValue)

    def test_substitute_empty_and_mentions(self) -> None:
        analysis = df.Evaluator().run(sh.parse('test -n "$ARFLAGS" || ARFLAGS="cr"\n'))
        (decision,) = analysis.decisions("ARFLAGS")
        self.assertTrue(df.mentions(decision.cond, "ARFLAGS"))
        self.assertFalse(df.mentions(decision.cond, "CFLAGS"))
        self.assertIs(df.substitute_empty(decision.cond, "ARFLAGS"), df.TRUE)
        self.assertIs(df.substitute_empty(df.Eq("X", "yes"), "X"), df.FALSE)
        self.assertIs(df.substitute_empty(df.Eq("X", ""), "X"), df.TRUE)
        self.assertEqual(df.substitute_empty(df.Truthy("Y"), "X"), df.Truthy("Y"))

    def test_backticks_across_lines_but_not_in_comments(self) -> None:
        text = "# don't use `foo' here\nX=`cat <<EOF\nabc\nEOF`\nY=1\n"
        analysis = df.Evaluator().run(sh.parse(text))
        (x,) = analysis.decisions("X")
        self.assertIsInstance(x.value, df.UnknownValue)
        (y,) = analysis.decisions("Y")
        self.assertEqual(y.value, df.Lit("1"))

    def test_rewrite_backticks(self) -> None:
        self.assertEqual(sh.rewrite_backticks("x=`a b`\n"), "x=$(a b)\n")
        self.assertEqual(sh.rewrite_backticks("# like `this'\nx=`a`\n"), "# like `this'\nx=$(a)\n")
        self.assertEqual(sh.rewrite_backticks("q='s/[\"`$]/x/'\nx=`a`\n"), "q='s/[\"`$]/x/'\nx=$(a)\n")
        self.assertEqual(sh.rewrite_backticks("x=`cat <<EOF\nabc\nEOF`\n"), "x=$(cat <<EOF\nabc\nEOF)\n")
        self.assertEqual(sh.rewrite_backticks("x=\"`a`\" # c `d'\n"), "x=\"$(a)\" # c `d'\n")
        self.assertEqual(sh.rewrite_backticks("x=`a\n"), "x=`a\n")
        self.assertEqual(sh.rewrite_backticks("x=`( print -n ) 2>/dev/null`\n"), "x=$( ( print -n ) 2>/dev/null)\n")

    def test_cache_hit_guards_never_hold(self) -> None:
        """AC_CACHE_VAL's `if test ${var+y}` (and the older spellings) wrap the
        commands; a fresh configure has no cache, so the commands always run."""
        for guard in ('test ${gl_cv_x+y}', '${gl_cv_x+:} false', 'test "${gl_cv_x+set}" = set'):
            text = f"if {guard}\nthen :\nelse\n  gl_cv_x=yes\nfi\n"
            analysis = df.Evaluator().run(sh.parse(text))
            (decision,) = analysis.decisions("gl_cv_x")
            self.assertIs(decision.cond, df.TRUE, guard)

if __name__ == "__main__":
    unittest.main()


class MinimizeTest(unittest.TestCase):

    def test_ten_atoms_is_fast_and_equivalent(self) -> None:
        """libssh2's path conditions reach ten atoms: minimizing one took seconds with the
        pairwise combine (half a million pairs a round); the bucketed one is instant."""
        import time
        atoms = [df.Eq(f"v{i}", "yes") for i in range(10)]
        cond = df.disj(*[df.conj(atoms[i], df.neg(atoms[(i + 1) % 10]), atoms[(i + 3) % 10]) for i in range(10)])
        start = time.monotonic()
        positive = lower.minimize(cond, atoms, True)
        negative = lower.minimize(cond, atoms, False)
        self.assertLess(time.monotonic() - start, 2.0)
        for bits in range(1 << 10):
            values = {atom: bool(bits >> i & 1) for i, atom in enumerate(atoms)}
            expected = lower._evaluate(cond, values)
            term = tuple(values[a] for a in atoms)
            covered = lambda implicants: any(all(p is None or p == v for p, v in zip(imp, term)) for imp in implicants)
            self.assertEqual(covered(positive), expected, bits)
            self.assertEqual(covered(negative), not expected, bits)

    def test_minimize_merges_adjacent_terms(self) -> None:
        a, b = df.Eq("a", "yes"), df.Eq("b", "yes")
        self.assertEqual(lower.minimize(df.disj(df.conj(a, b), df.conj(a, df.neg(b))), [a, b], True), [(True, None)])
        self.assertIsNone(lower.minimize(df.disj(a, df.neg(a)), [a], True))
        self.assertEqual(lower.minimize(df.conj(a, df.neg(a)), [a], True), [])
