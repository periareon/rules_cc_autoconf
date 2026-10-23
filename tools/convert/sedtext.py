"""A header recipe's sed program as `autoconf_hdr` substitutions.

gnulib assembles `assert.h` from two files: `assert.in.h` through the usual placeholder sed,
then `verify.h` through a sed that cuts out its `verify` / `assume` half and renames what is
left (`_GL_VERIFY` to `_GL_STATIC_ASSERT`), which is how a one-argument `_Static_assert`
reaches compilers predating C23, gcc < 9 among them.  An `autoconf_hdr` has exact-text
`substitutions`, applied one after another in key order, and no sed.  This module runs the sed
program over the file at conversion time and derives a substitution set that reproduces its
output, a deleted range fenced off with `#if 0` / `#endif` rather than removed, and checks
that it does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EXPRESSION_RE = re.compile(r"-e\s+'([^']*)'")
_SUBST_RE = re.compile(r"^s\|(.*)\|(.*)\|(g?)$")
_RANGE_DELETE_RE = re.compile(r"^/([^/]*)/,/([^/]*)/d$")
_COMMENT_LINE_RE = re.compile(r"^\s*/\*.*\*/\s*$")
_ROUNDS = 4


@dataclass(frozen=True)
class Subst:
    """`s|PATTERN|REPLACEMENT|g`."""

    pattern: re.Pattern[str]
    """The basic regular expression, as Python's."""
    replacement: str
    """The replacement, `\\1` as `\\g<1>`."""
    everywhere: bool
    """The `g` flag."""


@dataclass(frozen=True)
class RangeDelete:
    """`/START/,/END/d`: the lines from one matching START through the next matching END."""

    start: re.Pattern[str]
    """Where a deleted range opens."""
    end: re.Pattern[str]
    """Where it closes."""


Command = Subst | RangeDelete


def parse(program: str) -> list[Command] | None:
    """The `-e '...'` expressions of a sed invocation, in order; None when one is not a
    substitution or a range deletion."""
    commands: list[Command] = []
    for expression in _EXPRESSION_RE.findall(program):
        subst = _SUBST_RE.match(expression)
        delete = _RANGE_DELETE_RE.match(expression)
        if subst:
            commands.append(Subst(_basic_regex(subst.group(1)), _replacement(subst.group(2)), subst.group(3) == "g"))
        elif delete:
            commands.append(RangeDelete(_basic_regex(delete.group(1)), _basic_regex(delete.group(2))))
        else:
            return None
    return commands


def _basic_regex(pattern: str) -> re.Pattern[str]:
    """A POSIX basic regular expression as Python's: `\\(` `\\)` group and `\\{` `\\}` repeat,
    while bare `(` `)` `{` `}` `+` `?` `|` are literal."""
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            n = pattern[i + 1]
            out.append(n if n in "(){}" else "\\" + n)
            i += 2
            continue
        out.append("\\" + c if c in "(){}+?|" else c)
        i += 1
    return re.compile("".join(out))


def _replacement(text: str) -> str:
    """sed's `\\1` and `&` as Python's `\\g<1>` and `\\g<0>`."""
    return re.sub(r"\\(\d)", r"\\g<\1>", text.replace("\\&", "\0")).replace("&", r"\g<0>").replace("\0", "&")


def run(commands: list[Command], text: str, seen: dict[str, str] | None = None) -> list[str | None]:
    """The program's output, one entry per input line, None where a line is deleted.  `seen`
    collects each text a substitution matched and what it became."""
    out: list[str | None] = []
    deleting = False
    for line in text.split("\n"):
        if deleting:
            out.append(None)
            if any(isinstance(c, RangeDelete) and c.end.search(line) for c in commands):
                deleting = False
            continue
        kept: str | None = line
        for command in commands:
            if kept is None:
                break
            if isinstance(command, RangeDelete):
                if command.start.search(kept):
                    # sed looks for the end pattern from the next line on.
                    kept, deleting = None, True
                continue

            def record(match: re.Match[str], replacement: str = command.replacement) -> str:
                expanded = match.expand(replacement)
                if seen is not None:
                    seen.setdefault(match.group(0), expanded)
                return expanded

            kept = command.pattern.sub(record, kept, count=0 if command.everywhere else 1)
        out.append(kept)
    return out


def is_comment_line(line: str) -> bool:
    """A line that is one `/* ... */` comment."""
    return _COMMENT_LINE_RE.match(line) is not None


def _fence(line: str, opening: bool) -> str:
    keyword = "#if 0" if opening else "#endif"
    body = line.strip()
    return f"{keyword} {body}" if is_comment_line(body) else f"{keyword} /* {body} */"


def _apply(substitutions: dict[str, str], text: str) -> str:
    """As the resolver applies them: in key order, each over the whole text."""
    for key in sorted(substitutions):
        text = text.replace(key, substitutions[key])
    return text


def substitutions(commands: list[Command], text: str) -> dict[str, str] | None:
    """Exact-text substitutions whose application in key order yields `run`'s output over
    `text`, with each deleted range left in place between `#if 0` and `#endif` lines.  The
    substitutions matched during the run come first; where applying them in key order gives a
    different line (a rename whose result a later rename matches again), that whole line is a
    key of its own.  None when no such set is found."""
    lines = text.split("\n")
    seen: dict[str, str] = {}
    expected = run(commands, text, seen)
    if any("\n" in value for value in seen.values()):
        return None
    pairs = {key: value for key, value in seen.items() if key != value}
    fences: list[tuple[int, int]] = []
    at = 0
    while at < len(lines):
        if expected[at] is None:
            end = at
            while end + 1 < len(lines) and expected[end + 1] is None:
                end += 1
            fences.append((at, end))
            pairs[lines[at]] = _fence(lines[at], True)
            pairs[lines[end]] = _fence(lines[end], False)
            at = end + 1
        else:
            at += 1
    if any(start == end for start, end in fences):
        return None  # a one-line range cannot be both ends of a fence
    for _ in range(_ROUNDS):
        got = _apply(pairs, text).split("\n")
        if len(got) != len(lines):
            return None
        wrong = [
            at
            for at, (want, have, line) in enumerate(zip(expected, got, lines, strict=True))
            if want is not None and want != have and line
        ]
        bad_fences = [
            (start, end)
            for start, end in fences
            if got[start] != _fence(lines[start], True) or got[end] != _fence(lines[end], False)
        ]
        if not wrong and not bad_fences:
            return dict(sorted(pairs.items()))
        if bad_fences:
            return None
        for at in wrong:
            want = expected[at]
            assert want is not None
            pairs[lines[at]] = want
    return None
