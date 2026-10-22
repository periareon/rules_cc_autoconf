"""Parse the marker lines `instrument.m4` leaves in an expanded configure.

The expanded script is a sequence of plain shell lines and marker lines.
`parse()` turns it into a tree: every `AC_DEFUN`'d macro that was expanded
becomes a `Node` of kind ``defun`` (BEGIN/END), every instrumented leaf
macro call a `Node` of kind ``call`` (CALL/ENDCALL), and the shell text in
between is kept as `Text` items with their line numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from tools.convert import shparse as sh

MARKER_TAG = "#RCCAC"
MARKER_PREFIX = MARKER_TAG + "\t"
CONTINUATION_PREFIX = MARKER_TAG + "|"

# `_AC_Header_stdio_h`, `_AC_Func_malloc`, `_AC_UNDECLARED_BUILTIN_c`...:
# definitions autoconf's own *_ONCE and helper macros create at expansion
# time.  Their `loc` is the configure.ac line of the top-level call.
INTERNAL_DEFUN_RE = re.compile(r"^_A[CHM]_")
_HEREDOC_RE = re.compile(
    r"cat confdefs\.h - <<_ACEOF >conftest\.\$ac_ext\n/\* end confdefs\.h\.  \*/\n(.*?)\n_ACEOF", re.DOTALL
)


@dataclass
class Text:
    """A run of shell text between two markers."""

    text: str
    """The shell text."""
    line: int
    """1-based line number of the first line in the expanded file."""


@dataclass
class Node:
    """An expanded macro: a DEFUN body (`defun`) or a leaf call (`call`)."""

    kind: str
    """`"defun"` or `"call"`."""
    id: int
    """The marker id, unique over the expansion."""
    name: str
    """The macro's name."""
    loc: str | None = None
    """defun only: `file:line` of the AC_DEFUN."""
    args: list[str] = field(default_factory=list)
    """The call's arguments as the ARG markers recorded them (`args[n - 1]` is `$n`)."""
    prog: str | None = None
    """call only: the expanded *_IFELSE program text."""
    items: list[Node | Text] = field(default_factory=list)
    """The nested nodes and the shell text runs between them, in order."""
    line: int = 0
    """Line number of the BEGIN/CALL marker."""
    end_line: int = 0
    """Line number of the END/ENDCALL marker."""
    parent: Node | None = field(default=None, repr=False, compare=False)
    """The enclosing node, None at the top level."""

    @property
    def children(self) -> list[Node]:
        return [item for item in self.items if isinstance(item, Node)]

    def ancestors(self) -> Iterator[Node]:
        """Enclosing nodes, nearest first."""
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def enclosing(self, *names: str) -> Node | None:
        """The nearest ancestor whose name is one of `names`."""
        for node in self.ancestors():
            if node.name in names:
                return node
        return None

    @property
    def defun(self) -> Node | None:
        """The nearest enclosing DEFUN body."""
        for node in self.ancestors():
            if node.kind == "defun":
                return node
        return None

    @property
    def shell(self) -> str:
        """The residual shell text of this node, children elided."""
        return "".join(item.text for item in self.items if isinstance(item, Text))

    def walk(self) -> Iterator[Node]:
        """This node and every descendant, pre-order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def calls(self, macro: str | None = None) -> list[Node]:
        """Direct and nested `call` nodes, optionally filtered by macro name.

        Nested calls are the implementation of the call they sit in (the
        `AC_REQUIRE`s inside an `AC_CHECK_HEADERS`); use `direct_calls` for
        what this macro's body itself says.
        """
        return [
            node
            for node in self.walk()
            if node is not self and node.kind == "call" and (macro is None or node.name == macro)
        ]

    def direct_calls(self, macro: str | None = None) -> list[Node]:
        """`call` nodes that are immediate children, optionally filtered."""
        return [node for node in self.children if node.kind == "call" and (macro is None or node.name == macro)]

    @property
    def is_internal(self) -> bool:
        return self.kind == "defun" and INTERNAL_DEFUN_RE.match(self.name) is not None

    @property
    def full_text(self) -> str:
        """The shell text of this node and its descendants, markers removed."""
        parts = []
        for item in self.items:
            parts.append(item.text if isinstance(item, Text) else item.full_text)
        return "".join(parts)

    def heredoc_program(self) -> str | None:
        """The conftest program an `AC_*_IFELSE` expansion writes.

        `AC_LANG_CONFTEST` emits `cat confdefs.h - <<_ACEOF >conftest.$ac_ext`
        followed by the program and `_ACEOF`; this is the fully expanded
        program text (macros such as GL_MDA_DEFINES already substituted).
        Macros recorded inside the program (AC_INCLUDES_DEFAULT) contribute
        their text; their markers do not.
        """
        match = _HEREDOC_RE.search(self.full_text)
        return match.group(1) if match else None

    def program(self) -> str | None:
        """The probe's program: the PROG payload if any, else the heredoc."""
        return self.prog if self.prog is not None else self.heredoc_program()

    @property
    def loc_file(self) -> str | None:
        if not self.loc:
            return None
        return normalize_path(self.loc.rsplit(":", 1)[0])

    @property
    def loc_line(self) -> int | None:
        if not self.loc or ":" not in self.loc:
            return None
        try:
            return int(self.loc.rsplit(":", 1)[1])
        except ValueError:
            return None


@dataclass
class Tree:
    """The parsed expansion."""

    items: list[Node | Text]
    """The top-level nodes and the shell text runs between them, in order."""
    warnings: list[str] = field(default_factory=list)
    """Malformed nesting met while parsing."""

    @property
    def nodes(self) -> list[Node]:
        return [item for item in self.items if isinstance(item, Node)]

    def walk(self) -> Iterator[Node]:
        for node in self.nodes:
            yield from node.walk()

    def defuns(self, *, include_internal: bool = False) -> list[Node]:
        """Every expanded DEFUN body, in output order."""
        return [node for node in self.walk() if node.kind == "defun" and (include_internal or not node.is_internal)]

    def find(self, name: str) -> list[Node]:
        return [node for node in self.walk() if node.name == name]


def normalize_path(path: str) -> str:
    """Forward slashes (native perl on Windows writes `m4\\foo.m4`)."""
    return path.replace("\\", "/")


@dataclass
class _Marker:
    """One marker line, continuation lines folded in."""

    kind: str
    """BEGIN, END, CALL, ENDCALL, ARG or PROG."""
    fields: dict[str, str]
    """The key=value fields."""
    text: str
    """Free text after the key=value fields (ARG, PROG)."""
    line: int
    """Line number of the marker in the expanded file."""


def _parse_marker(line: str, line_no: int) -> _Marker:
    """One instrumentation marker line: its kind and key=value fields. ARG and PROG markers end with
    free text (which may contain tabs and `=`), taken positionally after the fixed fields.
    """
    parts = line[len(MARKER_PREFIX) :].split("\t")
    kind = parts[0]
    fields: dict[str, str] = {}
    free = ""
    positional = {"ARG": 2, "PROG": 1}.get(kind)
    if positional is None:
        for part in parts[1:]:
            key, _, value = part.partition("=")
            fields[key] = value
    else:
        for part in parts[1 : 1 + positional]:
            key, _, value = part.partition("=")
            fields[key] = value
        free = "\t".join(parts[1 + positional :])
    return _Marker(kind, fields, free, line_no)


def _tokens(text: str) -> Iterator[_Marker | Text]:
    """Markers (with continuation lines folded in) and shell text runs."""
    pending: _Marker | None = None
    run: list[str] = []
    run_start = 0
    for index, line in enumerate(text.split("\n"), start=1):
        if line.startswith(CONTINUATION_PREFIX) and pending is not None:
            pending.text += "\n" + line[len(CONTINUATION_PREFIX) :]
            continue
        if line.startswith(MARKER_PREFIX):
            if pending is not None:
                yield pending
                pending = None
            if run:
                yield Text("\n".join(run) + "\n", run_start)
                run = []
            pending = _parse_marker(line, index)
            continue
        if pending is not None:
            yield pending
            pending = None
        if not run:
            run_start = index
        run.append(line)
    if pending is not None:
        yield pending
    if run:
        yield Text("\n".join(run), run_start)


def parse(text: str) -> Tree:
    """Build the node tree of an expanded, instrumented configure script."""
    root: list[Node | Text] = []
    stack: list[Node] = []
    by_id: dict[int, Node] = {}
    warnings: list[str] = []

    def current_items() -> list[Node | Text]:
        return stack[-1].items if stack else root

    for token in _tokens(text):
        if isinstance(token, Text):
            current_items().append(token)
            continue
        marker = token
        kind = marker.kind
        if kind in ("BEGIN", "CALL"):
            node = Node(
                kind="defun" if kind == "BEGIN" else "call",
                id=int(marker.fields["id"]),
                name=marker.fields.get("name") or marker.fields.get("macro", ""),
                loc=marker.fields.get("loc"),
                line=marker.line,
            )
            if node.loc == "-":
                node.loc = None
            node.parent = stack[-1] if stack else None
            current_items().append(node)
            stack.append(node)
            by_id[node.id] = node
        elif kind in ("END", "ENDCALL"):
            node_id = int(marker.fields["id"])
            if stack and stack[-1].id == node_id:
                stack.pop().end_line = marker.line
            elif node_id in by_id and by_id[node_id] in stack:
                # Should not happen (m4 output is well nested); recover.
                warnings.append(
                    f"line {marker.line}: {kind} id={node_id} closes a range that is not "
                    f"innermost (open: {[n.id for n in stack]})"
                )
                while stack and stack[-1].id != node_id:
                    stack.pop().end_line = marker.line
                if stack:
                    stack.pop().end_line = marker.line
            else:
                warnings.append(f"line {marker.line}: {kind} id={node_id} without a matching open")
        elif kind == "ARG":
            target = by_id.get(int(marker.fields["id"]))
            if target is None:
                warnings.append(f"line {marker.line}: ARG for unknown id {marker.fields['id']}")
                continue
            index = int(marker.fields["n"])
            while len(target.args) < index:
                target.args.append("")
            # The arguments the translator re-parses as shell (an option's actions, AM_COND_IF's
            # arms) keep the calls inside them as placeholders, so the shell's conditions reach
            # them; every other argument is read as m4 text (a check's action `AC_DEFINE(...)`),
            # with the expansion's marker lines dropped.
            target.args[index - 1] = (
                fold_embedded_calls(marker.text)
                if target.name in SHELL_ARGUMENT_MACROS
                else strip_embedded_markers(marker.text)
            )
        elif kind == "PROG":
            target = by_id.get(int(marker.fields["id"]))
            if target is None:
                warnings.append(f"line {marker.line}: PROG for unknown id {marker.fields['id']}")
                continue
            target.prog = strip_embedded_markers(marker.text)
        else:
            warnings.append(f"line {marker.line}: unknown marker kind {kind!r}")

    for node in stack:
        warnings.append(f"line {node.line}: {node.kind} {node.name} (id={node.id}) never closed")
    return Tree(root, warnings)


# Macros whose action arguments the translator turns back into shell (`_flag_shell`,
# `_cond_if_shell`): their embedded calls fold to placeholders.
SHELL_ARGUMENT_MACROS = ("AC_ARG_ENABLE", "AC_ARG_WITH", "AM_COND_IF")


def fold_embedded_calls(text: str) -> str:
    """An argument m4 expanded before it was recorded (an option's action-if-given, where
    autoconf expands `$3` itself) carries the CALL/ENDCALL blocks of the calls inside it.  Each
    outermost block becomes the call's placeholder command (`__RCCAC_NODE_<id>__`), so shell
    re-parsed from the argument sees the call under the argument's conditions (gawk's
    `AC_DEFINE(NO_LINT)` under `test "$enableval" = no`); the other marker lines are dropped.
    """
    if MARKER_TAG not in text:
        return text
    out: list[str] = []
    depth = 0
    for line in text.split("\n"):
        if line.startswith(MARKER_PREFIX):
            marker = _parse_marker(line, 0)
            if marker.kind == "CALL":
                if depth == 0:
                    out.append(sh.placeholder(int(marker.fields["id"])))
                depth += 1
            elif marker.kind == "ENDCALL":
                depth = max(0, depth - 1)
            continue
        if depth > 0 or line.startswith(CONTINUATION_PREFIX):
            continue
        out.append(line)
    return "\n".join(out)


def strip_embedded_markers(text: str) -> str:
    """Drop marker lines that macro expansion injected into a payload.

    `m4_expand` of a program that mentions an instrumented macro (an
    `AC_REQUIRE` inside `AC_INCLUDES_DEFAULT`, say) leaves CALL/ENDCALL lines
    in the captured text; they are not part of the program.
    """
    if MARKER_TAG not in text:
        return text
    lines = [line for line in text.split("\n") if not (line.startswith((MARKER_PREFIX, CONTINUATION_PREFIX)))]
    return "\n".join(lines)
