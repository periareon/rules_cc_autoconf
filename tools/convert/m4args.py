"""Small helpers for m4 argument text as it appears in marker payloads.

Marker arguments are the macro's arguments after one level of m4 quoting
was removed (what the macro itself sees in `$1`, `$2`, ...).  Action
arguments therefore still contain macro calls such as
`AC_DEFINE([HAVE_X], [1])` and nested quoting such as `[[#include <x.h>]]`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from tools.convert.markers import MARKER_TAG

_NAME_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*")
# `dnl` as a word of a line (not `dnlfoo`).
_DNL_WORD_RE = re.compile(r"(^|\s)dnl(\s|$)")
_WHITESPACE_RE = re.compile(r"\s")


def unquote(text: str) -> str:
    """Strip one level of `[...]` quoting if the whole text is one quoted string."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == "[" and stripped[-1] == "]":
        depth = 0
        for index, char in enumerate(stripped):
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0 and index != len(stripped) - 1:
                    return stripped  # `[a] b [c]`: not a single quoted string
        return stripped[1:-1]
    return stripped


def split_args(text: str) -> list[str]:
    """Split macro argument text on top-level commas, honouring `[]` and `()`.

    Leading whitespace of each argument is dropped as m4 does; a single
    empty argument list yields `[]`.
    """
    args: list[str] = []
    depth_q = 0
    depth_p = 0
    current: list[str] = []
    for char in text:
        if char == "[":
            depth_q += 1
        elif char == "]":
            depth_q = max(0, depth_q - 1)
        elif depth_q == 0 and char == "(":
            depth_p += 1
        elif depth_q == 0 and char == ")":
            depth_p = max(0, depth_p - 1)
        elif depth_q == 0 and depth_p == 0 and char == ",":
            args.append("".join(current).lstrip())
            current = []
            continue
        current.append(char)
    args.append("".join(current).lstrip())
    if len(args) == 1 and args[0].strip() == "":
        return []
    return args


def call_end(text: str, open_index: int) -> int | None:
    """The index of the `)` matching the `(` at `open_index`, `()` inside `[...]` quoting not
    counted (quote depth clamped at 0, as `split_args` does); None when the call is not closed."""
    depth = 0
    depth_q = 0
    for index in range(open_index, len(text)):
        char = text[index]
        if char == "[":
            depth_q += 1
        elif char == "]":
            depth_q = max(0, depth_q - 1)
        elif depth_q == 0 and char == "(":
            depth += 1
        elif depth_q == 0 and char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


@dataclass
class Call:
    """A macro call found in argument text."""

    name: str
    """The macro's name."""
    args: list[str]
    """The arguments with one level of quoting removed, as m4 would pass them."""


def parse_call(text: str) -> Call | None:
    """`NAME(args)` (or bare `NAME`) if `text` is exactly one macro call.

    Trailing whitespace and `dnl` comments are tolerated; anything else
    (two calls, shell around the call) yields None.
    """
    stripped = text.strip()
    match = _NAME_RE.match(stripped)
    if not match:
        return None
    name = match.group(1)
    rest = stripped[match.end() :]
    if rest == "":
        return Call(name, [])
    if not rest.startswith("("):
        return None
    end = call_end(rest, 0)
    if end is None:
        return None
    tail = rest[end + 1 :].strip()
    if tail and not tail.startswith("dnl"):
        return None
    return Call(name, [unquote(a) for a in split_args(rest[1:end])])


def words(text: str) -> list[str]:
    """Whitespace-separated words of a (literal) m4 list argument.

    A backslash-newline continuation inside an unquoted list reaches the
    macro as a `\\` word; it is not a name.  A `dnl` and the rest of its line
    is an m4 comment (flex's `AC_CHECK_FUNCS([dup2 dnl\nmemset dnl ...])`),
    which m4 drops when the list is expanded.
    """
    lines = [line.split("dnl", 1)[0] if _DNL_WORD_RE.search(line) else line for line in text.split("\n")]
    return [word for word in " ".join(lines).split() if word.strip("\\")]


def unquote_level(text: str) -> str:
    """One level of m4 quoting removed, as m4 does when it expands the text once more: the
    outermost `[...]` pairs go, nested brackets stay (shell globs written `[[0-9]]` in the m4
    become `[0-9]`).  For an option's action arguments, which the expansion recorded one level
    short of what configure runs (gmp's `case $enableval in [yes|no|[02468]]) ;;`)."""
    out: list[str] = []
    depth = 0
    for char in text:
        if char == "[":
            if depth > 0:
                out.append(char)
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
            if depth > 0:
                out.append(char)
        else:
            out.append(char)
    return "".join(out)


def is_literal_list(text: str) -> bool:
    """True when a list argument has no shell or m4 expansion in it."""
    return "$" not in text and "`" not in text and "(" not in text


@dataclass
class LangProgram:
    """The two parts of an `AC_LANG_PROGRAM` argument."""

    prologue: str
    """The text before `main`."""
    body: str
    """The body of `main`."""


def lang_program(text: str) -> LangProgram | None:
    """The two parts of an `AC_LANG_PROGRAM([prologue], [body])` argument."""
    call = parse_call(text)
    if call is None or call.name != "AC_LANG_PROGRAM":
        return None
    # The arguments are usually written `[[...]]`: one level goes when the
    # call is parsed, the other when AC_LANG_PROGRAM itself expands them.
    args = [unquote(a) for a in call.args] + ["", ""]
    return LangProgram(prologue=args[0], body=args[1])


def lang_source(text: str) -> str | None:
    call = parse_call(text)
    if call is None or call.name != "AC_LANG_SOURCE":
        return None
    return unquote(call.args[0]) if call.args else ""


def includes_lines(text: str) -> list[str]:
    """The non-empty lines of an includes argument, stripped: its `#include` directives and
    whatever else the project put there (declarations, conditionals)."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    return [line for line in lines if line]


_CHANGEQUOTE_RE = re.compile(r"^[ \t]*changequote\([^)]*\)dnl[ \t]*\n?", re.MULTILINE)
_DNL_RE = re.compile(r"\bdnl\b[^\n]*")


def strip_m4(text: str) -> str:
    """Drop the m4-level noise raw argument text can carry: `changequote(...)dnl`
    lines (used to put `[...]` globs in shell) and `dnl` comments."""
    return _DNL_RE.sub("", _CHANGEQUOTE_RE.sub("", text))


_M4SH_CALL_RE = re.compile(r"\bAS_(IF|CASE|VAR_SET|VAR_IF)\(")


def m4sh_to_shell(text: str) -> str:
    """`AS_IF` and `AS_CASE` in action text spelled as the shell they expand to, their
    arguments one level unquoted and rewritten in turn: `AS_IF([c1], [a1], [c2], [a2], [else])`
    becomes `if c1; then a1 elif c2; then a2 else else fi`, `AS_CASE([w], [p1], [a1], ...,
    [default])` becomes `case w in p1) a1 ;; ... *) default ;; esac`.  The macro calls inside
    the arms stay as they are (placeholders, or calls `neutralize_macros` deals with), so an
    option's actions written in m4sh (make's `--with-customs`, `AS_IF([test "$enableval" = yes],
    [AC_DEFINE(...)])`) put their calls under the right shell condition."""
    out: list[str] = []
    position = 0
    while True:
        match = _M4SH_CALL_RE.search(text, position)
        if match is None:
            out.append(text[position:])
            return "".join(out)
        end = call_end(text, match.end() - 1)
        if end is None:
            out.append(text[position:])
            return "".join(out)
        args = [m4sh_to_shell(unquote_level(a)) for a in split_args(text[match.end() : end])]
        out.append(text[position : match.start()])
        out.append({"IF": _as_if, "CASE": _as_case, "VAR_SET": _as_var_set, "VAR_IF": _as_var_if}[match.group(1)](args))
        position = end + 1


def _as_if(args: list[str]) -> str:
    parts: list[str] = []
    index = 0
    while index + 1 < len(args):
        parts.append(f"{'if' if not parts else 'elif'} {args[index].strip() or ':'}; then\n{args[index + 1]}\n:\n")
        index += 2
    if not parts:
        return args[0] if args else ""
    if index < len(args):
        parts.append(f"else\n{args[index]}\n:\n")
    return "\n" + "".join(parts) + "fi\n"


def _as_var_set(args: list[str]) -> str:
    """`AS_VAR_SET([name], [value])`: `name=value` (quoted when the value has spaces)."""
    name = args[0].strip() if args else ""
    value = args[1].strip() if len(args) > 1 else ""
    if not name:
        return ""
    return f"{name}={value}" if not _WHITESPACE_RE.search(value) else f'{name}="{value}"'


def _as_var_if(args: list[str]) -> str:
    """`AS_VAR_IF([name], [value], [if-equal], [if-not])`: `if test "x$name" = xvalue; then ... fi`."""
    name = args[0].strip() if args else ""
    value = args[1].strip() if len(args) > 1 else ""
    if not name:
        return ""
    return _as_if([f'test "x${name}" = x{value}', *args[2:4]])


def _as_case(args: list[str]) -> str:
    if not args:
        return ""
    lines = [f"\ncase {args[0].strip()} in"]
    index = 1
    while index + 1 < len(args):
        lines.append(f"{args[index].strip()}) {args[index + 1]}\n:\n;;")
        index += 2
    if index < len(args):
        lines.append(f"*) {args[index]}\n:\n;;")
    lines.append("esac\n")
    return "\n".join(lines)


_MACRO_CALL_RE = re.compile(r"\b(_?(?:AC|AS|AM|AH|AU|gl|gt|m4)_[A-Za-z0-9_]*)\(")


def neutralize_macros(text: str, placeholders: list[tuple[str, str]] | None = None) -> str:
    """Replace macro calls left in shell text (`AC_MSG_ERROR([...])`) by `:`.

    Used on action arguments that are re-parsed as shell: the calls are not
    shell and would derail the parser; their effect is not modeled anyway.
    `placeholders` are (macro name, placeholder) pairs in expansion order, the
    calls the text's node expanded to: a call whose name is the next pair's
    becomes its placeholder instead, so the analysis sees it under the shell's
    condition (gawk's `AC_DEFINE(NO_LINT)` under `test "$enableval" = no`).
    The list is consumed in place, so one list serves consecutive texts.
    """
    pending = placeholders if placeholders is not None else []
    # A macro called bare, without parentheses (make's `CF_NETLIBS` inside --with-customs' action):
    # the next pending placeholder when it is that call's.
    if pending:

        def bare(match: re.Match[str]) -> str:
            if pending and pending[0][0] == match.group(2):
                return str(match.group(1)) + pending.pop(0)[1]
            return str(match.group(0))

        text = _UNEXPANDED_BARE_RE.sub(bare, text)
    # The calls the placeholders stand for may be the project's own macros (mpfr's
    # MPFR_PARSE_DIRECTORY ahead of an AC_DEFINE): matched by name, whatever the prefix.
    call_re = _MACRO_CALL_RE
    if pending:
        names = "|".join(sorted({re.escape(name) for name, _ in pending}, key=len, reverse=True))
        call_re = re.compile(rf"(?:\b(_?(?:AC|AS|AM|AH|AU|gl|gt|m4)_[A-Za-z0-9_]*)|(?<![\w$@])({names}))\(")

    def replace(match: re.Match[str], _end: int) -> str:
        name = match.group(1) or match.group(2)
        at = next((i for i, (pending_name, _) in enumerate(pending) if pending_name == name), None)
        if at is not None:
            return pending.pop(at)[1]
        return f": {name}"  # `:` ignores its arguments; no comment, `;;` may follow

    return _replace_calls(text, call_re, replace)


def _replace_calls(text: str, call_re: re.Pattern[str], replace: Callable[[re.Match[str], int], str | None]) -> str:
    """`text` with every call `call_re` matches (the name and its `(`) replaced, arguments and
    closing `)` included, by `replace(match, end)`, `end` the index of the `)` (of the `(` when
    the call is not closed); a call `replace` declines (None) stays, and the scan resumes after
    the match."""
    out: list[str] = []
    position = 0
    while True:
        match = call_re.search(text, position)
        if not match:
            out.append(text[position:])
            return "".join(out)
        end = call_end(text, match.end() - 1)
        if end is None:
            end = match.end() - 1
        replacement = replace(match, end)
        if replacement is None:
            out.append(text[position : match.end()])
            position = match.end()
            continue
        out.append(text[position : match.start()])
        out.append(replacement)
        position = end + 1


# A macro name is upper case by convention (PKG_CHECK_MODULES, AX_FOO, CF_NETLIBS, MPFR_X): a
# mixed-case word with a parenthesis (`EVP_DecryptInit(`, `OPENSSL_init_ssl(` in a C snippet) is
# not one.
_UNEXPANDED_CALL_RE = re.compile(r"(?<![A-Za-z0-9_$@.\-])([A-Z][A-Z0-9]*_[A-Z0-9_]*)(\()")
_UNEXPANDED_BARE_RE = re.compile(r"^([ \t]*)([A-Z][A-Z0-9]*_[A-Z0-9_]*)[ \t]*$", re.MULTILINE)
# Autoconf's and m4sh's own: never "undefined"; a call of one left in the shell is one the
# expansion did not attribute (a call in a shell comment, an unbracketed call in a case arm).
_BUILTIN_PREFIXES = ("AC_", "AS_", "AM_", "AH_", "AU_", "AT_", "m4_", "_AC_", "_AS_", "_AM_", "_m4_")


def _in_shell_comment(text: str, at: int) -> bool:
    """Whether position `at` of `text` lies in a shell comment: a `#` earlier on its line, at
    the line's start or after whitespace, outside quotes, on a line that is not a marker."""
    start = text.rfind("\n", 0, at) + 1
    line = text[start:at]
    if line.lstrip().startswith(MARKER_TAG):
        return False
    quoted = False
    for index, char in enumerate(line):
        if char in ('"', "'"):
            quoted = not quoted
        elif char == "#" and not quoted and (index == 0 or line[index - 1] in " \t"):
            return True
    return False


def neutralize_unexpanded(text: str) -> tuple[str, list[str], list[str]]:
    """Replace calls of macros that were never defined by `:`.

    A macro no m4 file defines (an autoconf-archive AX_* macro, pkg-config's
    PKG_CHECK_MODULES, a macro directory the project did not declare) stays
    as literal text in configure; as shell it is a syntax error that would
    hide the condition of everything after it.  Returns the shell with each
    such call (and each bare `PA_PROG_CC` line) replaced, the names, and apart
    from them the autoconf builtins met this way (calls the expansion did not
    attribute, which are not missing macros).  A call inside a shell comment
    is left as it is: m4 expanded it, the shell never saw it.
    """
    names: list[str] = []
    builtins: list[str] = []

    def replace(match: re.Match[str], _end: int) -> str | None:
        name = match.group(1)
        if _in_shell_comment(text, match.start()):
            return None
        bucket = builtins if name.startswith(_BUILTIN_PREFIXES) else names
        if name not in bucket:
            bucket.append(name)
        return f": {name}"

    joined = _replace_calls(text, _UNEXPANDED_CALL_RE, replace)

    def bare(match: re.Match[str]) -> str:
        bucket = builtins if match.group(2).startswith(_BUILTIN_PREFIXES) else names
        if match.group(2) not in bucket:
            bucket.append(match.group(2))
        return f"{match.group(1)}: {match.group(2)}"

    return _UNEXPANDED_BARE_RE.sub(bare, joined), names, builtins


def quadrigraphs(text: str) -> str:
    """Resolve the quadrigraphs autoconf authors use inside quoted C code."""
    return (
        text.replace("@<:@", "[")
        .replace("@:>@", "]")
        .replace("@S|@", "$")
        .replace("@%:@", "#")
        .replace("@{:@", "(")
        .replace("@:}@", ")")
        .replace("@&t@", "")
    )
