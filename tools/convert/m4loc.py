"""Locate macro calls in m4 source files.

autom4te reports only where an `AC_DEFUN` was *defined*; the calls inside a
body are attributed to the top-level configure.ac line.  To comment the
emitted Starlark with `m4/foo.m4:NN` the converter searches the DEFUN's
source span for the call text instead.  Matches are heuristic (the first
occurrence after the previous match) and are marked approximate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Span:
    start: int  # 1-based, inclusive
    end: int  # 1-based, inclusive


class M4Source:
    """One m4 file's text with call-location helpers."""

    def __init__(self, text: str) -> None:
        self.lines = text.replace("\r\n", "\n").split("\n")

    def defun_span(self, start_line: int) -> Span:
        """The lines of the `AC_DEFUN(...)` call that starts on `start_line`."""
        depth_paren = 0
        depth_quote = 0
        started = False
        for number in range(start_line, len(self.lines) + 1):
            for char in self.lines[number - 1]:
                if char == "[":
                    depth_quote += 1
                elif char == "]":
                    depth_quote = max(0, depth_quote - 1)
                elif depth_quote == 0 and char == "(":
                    depth_paren += 1
                    started = True
                elif depth_quote == 0 and char == ")":
                    depth_paren -= 1
                    if started and depth_paren == 0:
                        return Span(start_line, number)
        return Span(start_line, len(self.lines))

    def text(self, span: Span) -> str:
        return "\n".join(self.lines[span.start - 1 : span.end])


class CallLocator:
    """Find successive macro calls inside one DEFUN span, in order."""

    def __init__(self, source: M4Source, span: Span) -> None:
        self.source = source
        self.span = span
        self.cursor = span.start

    def find(self, macro: str, first_arg: str | None = None) -> int | None:
        """The line of the next `macro(` (whose first argument starts like
        `first_arg`, if given) at or after the cursor."""
        if not macro:
            return None
        pattern = re.compile(r"\b" + re.escape(macro) + r"\b")
        probe = (first_arg or "").strip().split("\n")[0].strip()[:24]
        for number in range(self.cursor, self.span.end + 1):
            line = self.source.lines[number - 1]
            if not pattern.search(line):
                continue
            if probe:
                window = "\n".join(self.source.lines[number - 1 : min(number + 2, self.span.end)])
                after = window.split(macro, 1)[1] if macro in window else ""
                if probe not in after:
                    continue
            self.cursor = number
            return number
        return None
