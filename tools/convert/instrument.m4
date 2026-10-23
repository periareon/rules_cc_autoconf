# rules_cc_autoconf converter: autom4te instrumentation prelude.
#
# Loaded after the autoconf library and before the project's own m4 files
# (`autom4te --language=Autoconf-without-aclocal-m4 instrument.m4 aclocal.m4
# configure.ac`).  It leaves the generated configure script semantically
# intact and interleaves shell-comment marker lines that record:
#
#   #RCCAC<TAB>BEGIN<TAB>id=N<TAB>name=MACRO<TAB>loc=FILE:LINE
#   #RCCAC<TAB>ARG<TAB>id=N<TAB>n=K<TAB>TEXT         (one per argument)
#   ...body of an AC_DEFUN'd macro, expanded...
#   #RCCAC<TAB>END<TAB>id=N<TAB>name=MACRO
#
#   #RCCAC<TAB>CALL<TAB>id=N<TAB>macro=MACRO
#   #RCCAC<TAB>ARG<TAB>id=N<TAB>n=K<TAB>TEXT
#   #RCCAC<TAB>PROG<TAB>id=N<TAB>TEXT               (*_IFELSE: expanded program)
#   ...the macro's own expansion...
#   #RCCAC<TAB>ENDCALL<TAB>id=N<TAB>macro=MACRO
#
# Multi-line TEXT continues on lines starting with `#RCCAC|`.  The markers
# are written with the `@%:@` quadrigraph so autom4te's forbidden-token scan
# (which strips `#...` comments first) never sees their contents, and the
# text is escaped with m4_escape so brackets never unbalance.
#
# The AC_DEFUN / AC_DEFUN_ONCE definitions below mirror m4sugar's m4_defun
# and m4_defun_once from autoconf 2.73 exactly, with the body wrapped in
# BEGIN/END markers *inside* the _m4_defun_pro/_m4_defun_epi pair, so that
# AC_REQUIRE'd bodies are still hoisted ahead of their requirer and the
# markers travel with the body.  The converter asserts the autoconf version.

m4_define([_rccac_id], [0])
m4_define([_rccac_next_id],
[m4_define([_rccac_id], m4_incr(_rccac_id))_rccac_id])

# File-existence checks (AC_LIBSOURCES, gl_LIBSOURCES) run `test -r` through
# m4_syscmd and AC_FATAL on failure.  Sources are not staged for the
# expansion (and the command would go through cmd.exe on Windows), so the
# builtin is disabled: m4_sysval keeps its last value, 0.
m4_define([m4_syscmd], [])

# _rccac_payload(TEXT)
# --------------------
# TEXT with [ ] # $ turned into quadrigraphs, every newline followed by a
# continuation prefix, so the whole thing lives inside shell comments, and
# the empty quadrigraph after (nearly) every word character.  The quoting
# m4_escape leaves protects the text on an ordinary rescan, but the bodies
# of AS_IF / AS_CASE / AC_CHECK_DECL actions go through m4_expand, which
# rescans the expansion under other quote characters: there `m4_shiftn(...)`
# in a recorded argument would run.  Split into one-letter tokens nothing
# does.  `S` and `t` are left alone so the quadrigraphs @S|@ and @&t@ stay
# whole; a run of them plus one letter is never a macro name.
m4_define([_rccac_payload],
[m4_bpatsubst(m4_bpatsubst([[]m4_escape([$1])[]], [
], [
@%:@RCCAC|]), [[A-RU-Za-su-z0-9_]], [\&@&t@])])

# _rccac_emit(KIND, FIELDS)
# -------------------------
m4_define([_rccac_emit],
[[
@%:@RCCAC	$1	]_rccac_payload([$2])[
]])

# _rccac_args(ID, N, ARGS...)
# ---------------------------
# One ARG record per remaining argument.  A single trailing empty argument
# (the `MACRO` vs `MACRO()` ambiguity) is not recorded.
m4_define([_rccac_arg], [_rccac_emit([ARG], [id=$1	n=$2	$3])])
m4_define([_rccac_args],
[m4_if([$#], [2], [],
       [m4_if([$#$3], [3], [],
              [_rccac_arg([$1], [$2], [$3])_rccac_args([$1], m4_incr([$2]), m4_shift3($@))])])])

# _rccac_name(NAME)
# -----------------
# NAME with the empty quadrigraph after every character.  Marker text is
# rescanned when a wrapped macro is used unquoted as another macro's
# argument (`AS_IF([...], AC_CACHE_CHECK(...))`), and the name is rescanned
# as it is emitted here; a bare `AC_CACHE_CHECK` would expand again (a
# nameless call, or infinite recursion for a DEFUN emitting its own BEGIN
# marker).  No piece of `A@&t@C@&t@_...` is a macro token, and autom4te
# turns the quadrigraphs back into nothing when it writes the output.
m4_define([_rccac_name], [m4_bpatsubst([$1], [.], [\&@&t@])])

# _rccac_begin(NAME, ARGS...) / _rccac_end(NAME)
# ----------------------------------------------
# Pure-m4 macros (see AC_DEFUN below) get no markers: the emitting macro is
# chosen by name and applied to the arguments outside the m4_ifdef, so the
# arguments are not rescanned an extra time on the way.
m4_define([_rccac_noop], [])
m4_define([_rccac_begin],
[m4_pushdef([_rccac_cur], _rccac_next_id)]dnl
[m4_ifdef([_rccac_pure($1)], [_rccac_noop], [_rccac_emit])([BEGIN], [id=]_rccac_cur[	name=]_rccac_name([$1])[	loc=]m4_ifdef([m4_location($1)],
  [m4_defn([m4_location($1)])], [-]))]dnl
[m4_ifdef([_rccac_pure($1)], [_rccac_noop], [_rccac_args])(_rccac_cur, [1], m4_shift($@))])
m4_define([_rccac_end],
[m4_ifdef([_rccac_pure($1)], [_rccac_noop], [_rccac_emit])([END], [id=]_rccac_cur[	name=]_rccac_name([$1]))m4_popdef([_rccac_cur])])

# _rccac_call(NAME, ARGS...) / _rccac_endcall(NAME)
# -------------------------------------------------
m4_define([_rccac_call],
[m4_pushdef([_rccac_cur], _rccac_next_id)]dnl
[_rccac_emit([CALL], [id=]_rccac_cur[	macro=]_rccac_name([$1]))]dnl
[_rccac_args(_rccac_cur, [1], m4_shift($@))])
m4_define([_rccac_endcall],
[_rccac_emit([ENDCALL], [id=]_rccac_cur[	macro=]_rccac_name([$1]))m4_popdef([_rccac_cur])])

# _rccac_prog(SOURCE)
# -------------------
# The expanded program text of an AC_*_IFELSE first argument (what
# AC_LANG_CONFTEST writes to conftest.$ac_ext).
m4_define([_rccac_prog],
[_rccac_emit([PROG], [id=]_rccac_cur[	]m4_expand([$1]))])

# _rccac_wrap(NAME)
# -----------------
# Redefine NAME (if defined) as CALL marker + original definition + ENDCALL
# marker.  The original text is spliced in, not delegated to a copy, so `$0`
# still names NAME (AC_DEFINE calls `_$0`).
m4_define([_rccac_wrap],
[m4_ifdef([$1], [m4_bmatch(m4_index(m4_dquote(m4_defn([$1])), [_m4_defun_once(]), [^[01]$],
  [m4_define([_rccac_once($1)])],
  [m4_define([$1],
    [_rccac_call([$1], $][@)]m4_defn([$1])[_rccac_endcall([$1])])])])])

# _m4_defun_once -- m4sugar 2.73, with the CALL/ENDCALL markers inside the body
# of a wrapped once-only library macro (`_rccac_once(NAME)`, set by _rccac_wrap:
# AC_CANONICAL_HOST, AC_USE_SYSTEM_EXTENSIONS).  Called inside another macro,
# m4sugar hoists such a body before the caller's output, as AC_REQUIRE does,
# out of the range a wrapper around the call would mark; markers in the body
# travel with it.
m4_define([_m4_defun_once],
[m4_pushdef([$1])$3[m4_ifdef([_rccac_once($1)], [_rccac_call([$1])])$2[]m4_ifdef([_rccac_once($1)],
  [_rccac_endcall([$1])])m4_provide([$1])]$4])

# _rccac_wrap_prog(NAME): like _rccac_wrap, plus a PROG record of `$1`.
# (`m4_dquote($[1])` yields a quoted `$1` in the new definition; a literal
# `[$][1]` would stay a nested quote and never be substituted.)
m4_define([_rccac_wrap_prog],
[m4_ifdef([$1], [m4_define([$1],
  [_rccac_call([$1], $][@)_rccac_prog(]m4_dquote($[1])[)]m4_defn([$1])[_rccac_endcall([$1])])])])

# _rccac_wrap_after(NAME): markers emitted after the expansion, for macros
# that switch diversions themselves (AC_INIT runs in the KILL diversion until
# it opens BODY, which would swallow a leading marker).
m4_define([_rccac_wrap_after],
[m4_ifdef([$1], [m4_define([$1],
  m4_defn([$1])[_rccac_call([$1], $][@)_rccac_endcall([$1])])])])

# _rccac_pure_m4(EXPANSION)
# -------------------------
# True for a body that is a value for other macros: a one-line quoted
# constant (`[[_pa_lang_seen_set]]`, the name of an m4 set), or m4 computing
# one (`m4_normalize(m4_expand([$1]))`, a `m4_case` over languages): it opens
# with an m4_* call, calls no autoconf/automake/gnulib macro, and has no
# line that is anything but m4 (every line starts with `m4_`, a quote or a
# closing paren; the character class is spelled with ranges because `[`
# and `]` cannot appear in a quoted m4 regex).  Markers inside such a value
# would corrupt it (`AC_LANG(PA_FLAGS_LANGLIST(...))`), so these macros get
# none.  The body is only ever looked at with m4_bregexp: transforming it
# with another macro would rescan, and so run, it.  `^` matches at every
# line start in m4's regex syntax, so "starts with" is "matches at index 0".
m4_define([_rccac_pure_m4],
[m4_if(m4_index([$1], m4_newline), [-1],
       [m4_if(m4_bregexp([$1], [^[	 ]*[Z-^][^(]*$]), [0], [1], [_rccac_pure_m4_call([$1])])],
       [_rccac_pure_m4_call([$1])])])
m4_define([_rccac_pure_m4_call],
[m4_if(m4_bregexp([$1], [^[	 
]*_?m4_[a-z_0-9]+(]), [0],
  [m4_bmatch([$1], [\<\(A[CSMHU]\|AX\|PKG\|gl\|gt\|LT\)_[A-Za-z0-9_]*(], [0],
     [^[	 ]*[!-'*-Z`-ln-~]], [0], [1])], [0])])

# m4_fatal: the expansion is analysis data, never a configure script that
# runs, so a script's own fatal checks (autoconf version pins such as
# postgres's `m4_if(m4_defn([m4_PACKAGE_VERSION]), [2.69], [], [m4_fatal(...)])`
# and binutils' _GCC_AUTOCONF_VERSION_CHECK) become warnings the report lists.
m4_define([m4_fatal], [m4_warn([syntax], [m4_fatal ignored by the converter: $1])])

# AC_DEFUN(NAME, EXPANSION) -- m4sugar 2.73 m4_defun with BEGIN/END markers.
# A pure-m4 body is remembered as `_rccac_pure(NAME)`; its markers are
# skipped at call time (see _rccac_begin / _rccac_end).
m4_define([AC_DEFUN],
[m4_define([m4_location($1)], m4_location)]dnl
[m4_if(_rccac_pure_m4([$2]), [1], [m4_define([_rccac_pure($1)])])]dnl
[m4_define([$1],
  [_m4_defun_pro(]m4_dquote($[0])[)_rccac_begin(]m4_dquote($[0])[, ]$[@][)$2[]_rccac_end(]m4_dquote($[0])[)_m4_defun_epi(]m4_dquote($[0])[)])])

# AC_DEFUN_ONCE(NAME, EXPANSION) -- m4sugar 2.73 m4_defun_once with markers.
# (Invocation arguments are not recorded; once-only macros take none.)
m4_define([AC_DEFUN_ONCE],
[m4_define([m4_location($1)], m4_location)]dnl
[m4_define([$1], [_m4_defun_once([$1], [_rccac_begin([$1])$2[]_rccac_end([$1])], m4_if(_m4_divert_dump, [],
  [[_m4_defun_pro([$1])m4_unquote(], [)_m4_defun_epi([$1])]],
m4_ifdef([_m4_diverting([$1])], [-]), [-], [[m4_unquote(], [)]],
  [[_m4_require_call([$1],], [, _m4_divert_dump)]]))])])

# Leaf macros recorded as CALL ranges.
m4_map_args([_rccac_wrap],
  [AC_REQUIRE],
  [AC_DEFINE], [AC_DEFINE_UNQUOTED], [AC_SUBST],
  [AC_CACHE_CHECK], [AC_CACHE_VAL],
  [AC_CHECK_HEADER], [AC_CHECK_HEADERS], [AC_CHECK_HEADERS_ONCE],
  [AC_CHECK_FUNC], [AC_CHECK_FUNCS], [AC_CHECK_FUNCS_ONCE], [AC_REPLACE_FUNCS],
  [AC_CHECK_DECL], [AC_CHECK_DECLS], [AC_CHECK_DECLS_ONCE],
  [AC_CHECK_TYPE], [AC_CHECK_TYPES], [AC_CHECK_MEMBER], [AC_CHECK_MEMBERS],
  [AC_CHECK_SIZEOF], [AC_CHECK_ALIGNOF], [AC_COMPUTE_INT],
  [AC_CHECK_LIB], [AC_SEARCH_LIBS], [AC_EGREP_CPP], [AC_EGREP_HEADER],
  [AC_LIBOBJ], [AC_MSG_ERROR], [AC_MSG_WARN], [AC_MSG_FAILURE],
  [AC_ARG_ENABLE], [AC_ARG_WITH], [AC_ARG_VAR],
  [AC_LANG], [AC_LANG_PUSH], [AC_LANG_POP],
  [AC_CONFIG_HEADERS], [AC_CONFIG_FILES], [AC_CONFIG_COMMANDS],
  [AC_CONFIG_MACRO_DIRS], [AC_CONFIG_MACRO_DIR], [AC_CONFIG_AUX_DIR],
  [AC_CONFIG_SRCDIR], [AC_CONFIG_SUBDIRS], [AC_OUTPUT], [AC_PREREQ],
  [AC_CHECK_PROG], [AC_CHECK_PROGS], [AC_CHECK_TOOL], [AC_CHECK_TOOLS], [AC_PATH_PROG], [AC_PATH_PROGS],
  [AC_PATH_TOOL], [AC_PATH_PROGS_FEATURE_CHECK])
# The *_IFELSE program text is not captured with m4_expand (programs may
# use macros such as GL_MDA_DEFINES that divert, which m4_expand forbids);
# the converter reads it from the conftest heredoc in the expansion instead.
m4_map_args([_rccac_wrap],
  [AC_COMPILE_IFELSE], [AC_LINK_IFELSE], [AC_PREPROC_IFELSE], [AC_RUN_IFELSE])
m4_map_args([_rccac_wrap_after], [AC_INIT])

dnl @RCCAC_EXTRA_WRAPS@
