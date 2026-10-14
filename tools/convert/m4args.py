"""Small helpers for m4 argument text as it appears in marker payloads.

Marker arguments are the macro's arguments after one level of m4 quoting
was removed (what the macro itself sees in `$1`, `$2`, ...).  Action
arguments therefore still contain macro calls such as
`AC_DEFINE([HAVE_X], [1])` and nested quoting such as `[[#include <x.h>]]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NAME_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*")


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


@dataclass
class Call:
    """A macro call found in argument text."""

    name: str
    args: list[str]  # with one level of quoting removed, as m4 would pass them
    raw_args: list[str]  # as written


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
        return Call(name, [], [])
    if not rest.startswith("("):
        return None
    depth = 0
    depth_q = 0
    for index, char in enumerate(rest):
        if char == "[":
            depth_q += 1
        elif char == "]":
            depth_q = max(0, depth_q - 1)
        elif depth_q == 0 and char == "(":
            depth += 1
        elif depth_q == 0 and char == ")":
            depth -= 1
            if depth == 0:
                tail = rest[index + 1 :].strip()
                if tail and not tail.startswith("dnl"):
                    return None
                raw = split_args(rest[1:index])
                return Call(name, [unquote(a) for a in raw], raw)
    return None


def words(text: str) -> list[str]:
    """Whitespace-separated words of a (literal) m4 list argument.

    A backslash-newline continuation inside an unquoted list reaches the
    macro as a `\\` word; it is not a name.
    """
    return [word for word in text.split() if word.strip("\\")]


def is_literal_list(text: str) -> bool:
    """True when a list argument has no shell or m4 expansion in it."""
    return "$" not in text and "`" not in text and "(" not in text


@dataclass
class LangProgram:
    prologue: str
    body: str


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
    """`#include` directives (and other preprocessor lines) of an includes argument."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    return [line for line in lines if line]


_CHANGEQUOTE_RE = re.compile(r"^[ \t]*changequote\([^)]*\)dnl[ \t]*\n?", re.M)
_DNL_RE = re.compile(r"\bdnl\b[^\n]*")


def strip_m4(text: str) -> str:
    """Drop the m4-level noise raw argument text can carry: `changequote(...)dnl`
    lines (used to put `[...]` globs in shell) and `dnl` comments."""
    return _DNL_RE.sub("", _CHANGEQUOTE_RE.sub("", text))


_MACRO_CALL_RE = re.compile(r"\b(_?(?:AC|AS|AM|AH|AU|gl|gt|m4)_[A-Za-z0-9_]*)\(")


def neutralize_macros(text: str) -> str:
    """Replace macro calls left in shell text (`AC_MSG_ERROR([...])`) by `:`.

    Used on action arguments that are re-parsed as shell: the calls are not
    shell and would derail the parser; their effect is not modeled anyway.
    """
    out = []
    position = 0
    while True:
        match = _MACRO_CALL_RE.search(text, position)
        if not match:
            out.append(text[position:])
            break
        out.append(text[position:match.start()])
        depth = 0
        depth_q = 0
        end = match.end() - 1
        for index in range(match.end() - 1, len(text)):
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
                    end = index
                    break
        out.append(f": {match.group(1)}")  # `:` ignores its arguments; no comment, `;;` may follow
        position = end + 1
    return "".join(out)


_UNEXPANDED_CALL_RE = re.compile(r"(?<![A-Za-z0-9_$@.\-])([A-Z][A-Z0-9]*_[A-Za-z0-9_]*)(\()")
_UNEXPANDED_BARE_RE = re.compile(r"^([ \t]*)([A-Z][A-Z0-9]*_[A-Z0-9_]*)[ \t]*$", re.M)


def neutralize_unexpanded(text: str) -> tuple[str, list[str]]:
    """Replace calls of macros that were never defined by `:`.

    A macro no m4 file defines (an autoconf-archive AX_* macro, pkg-config's
    PKG_CHECK_MODULES, a macro directory the project did not declare) stays
    as literal text in configure; as shell it is a syntax error that would
    hide the condition of everything after it.  Returns the shell with each
    such call (and each bare `PA_PROG_CC` line) replaced, and the names.
    """
    names: list[str] = []
    out: list[str] = []
    position = 0
    while True:
        match = _UNEXPANDED_CALL_RE.search(text, position)
        if not match:
            out.append(text[position:])
            break
        name = match.group(1)
        out.append(text[position:match.start()])
        depth = 0
        depth_q = 0
        end = match.end() - 1
        for index in range(match.end() - 1, len(text)):
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
                    end = index
                    break
        if name not in names:
            names.append(name)
        out.append(f": {name}")
        position = end + 1
    joined = "".join(out)

    def bare(match: re.Match) -> str:
        if match.group(2) not in names:
            names.append(match.group(2))
        return f"{match.group(1)}: {match.group(2)}"

    return _UNEXPANDED_BARE_RE.sub(bare, joined), names


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
