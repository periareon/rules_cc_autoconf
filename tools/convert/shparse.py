"""A small shell AST over tree-sitter-bash for the residual configure shell.

The converter only needs the control flow between macro calls: `if`/`elif`
with `test` conditions, `case` on a variable, assignments, `&&`/`||` lists
and `for` over literal words.  Everything else is kept as an opaque `Cmd`
(or `Unknown` when it could matter for dataflow, such as `eval`).

Macro calls are not shell: the caller replaces each marker node with a
placeholder command (`__RCCAC_NODE_<id>__`) before parsing, and gets a
`Placeholder` statement back at that position.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import tree_sitter_bash
from tree_sitter import Language, Parser
from tree_sitter import Node as TsNode

PLACEHOLDER_PREFIX = "__RCCAC_NODE_"
_PLACEHOLDER_RE = re.compile(r"^__RCCAC_NODE_(\d+)__$")
_GNULIB_M4CODE_FUNCTION = "func_gl_gnulib_m4code_"  # gnulib-tool --conditional-dependencies
_SIMPLE_VAR_RE = re.compile(r'^"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?$')

_LANGUAGE = Language(tree_sitter_bash.language())
_PARSER = Parser(_LANGUAGE)


def placeholder(node_id: int) -> str:
    return f"{PLACEHOLDER_PREFIX}{node_id}__"


def simple_var(text: str) -> str | None:
    """`x` for `$x`, `"$x"`, `${x}`; None for any other text."""
    match = _SIMPLE_VAR_RE.match(text)
    return match.group(1) if match else None


# -- words -------------------------------------------------------------------


@dataclass(frozen=True)
class Word:
    """A shell word: its source text and the variables it expands."""

    text: str
    """The word's source text, quotes included."""
    vars: tuple[str, ...] = ()
    """The names of the variables the word expands, in order."""
    unknown: bool = False
    """The word holds something the analysis cannot follow: a command substitution, arithmetic,
    a glob..."""

    @property
    def literal(self) -> str | None:
        """The word's value when it expands no variable."""
        if self.vars or self.unknown:
            return None
        return unquote_word(self.text)

    @property
    def single_var(self) -> str | None:
        """`x` for `$x`, `"$x"`, `${x}`."""
        return simple_var(self.text)


def unquote_word(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text.replace("\\", "")


# -- statements and expressions ----------------------------------------------


@dataclass
class Seq:
    """A sequence of statements: a script, a body, a branch."""

    stmts: list[Stmt] = field(default_factory=list)
    """The statements, in order."""
    errors: list[str] = field(default_factory=list)
    """Top level only: the syntax errors, as `line N: snippet`."""


@dataclass
class Placeholder:
    """A macro call's stand-in command (`__RCCAC_NODE_<id>__`)."""

    node_id: int
    """The marker node's id."""


@dataclass
class Assign:
    """`name=value`."""

    name: str
    """The variable assigned."""
    value: Word
    """The word assigned to it."""


@dataclass
class Cmd:
    """An opaque command (printf, rm, :, ...)."""

    text: str
    """The command's source text."""


@dataclass
class Unknown:
    """A construct the dataflow cannot follow (eval, pipelines into vars...)."""

    text: str
    """The construct's source text."""


@dataclass
class If:
    """`if` / `elif` / `else`, and `a && b` / `a || b` lists as one-branch ifs."""

    branches: list[tuple[Expr | None, Seq]]
    """`(condition, body)` per branch; `(None, body)` is the else branch."""


@dataclass
class Case:
    """`case subject in ... esac`."""

    subject: Word
    """The word the arms' patterns are matched against."""
    items: list[tuple[list[str], Seq]]
    """`(patterns, body)` per arm."""


@dataclass
class For:
    """`for var in words; do body; done`."""

    var: str
    """The loop variable."""
    words: list[Word]
    """The words iterated over."""
    body: Seq
    """The loop body."""


Stmt = Seq | Placeholder | Assign | Cmd | Unknown | If | Case | For


@dataclass
class TestEq:
    """`test lhs = rhs` (also `==`, `!=`, `-eq`, `-ne`)."""

    lhs: Word
    """The left operand."""
    rhs: Word
    """The right operand."""
    negate: bool = False
    """`!=` (or `-ne`) rather than `=`."""


@dataclass
class TestUnary:
    """`test -op arg`."""

    op: str
    """The operator: `-z`, `-n`, `-f`, `-d`, `-x`..."""
    arg: Word
    """The operand."""


@dataclass
class Not:
    """`! expr`."""

    expr: Expr
    """The negated condition."""


@dataclass
class And:
    """`a && b` (or `test a -a b`) as a condition."""

    exprs: list[Expr]
    """The conjuncts."""


@dataclass
class Or:
    """`a || b` (or `test a -o b`) as a condition."""

    exprs: list[Expr]
    """The disjuncts."""


@dataclass
class CmdExpr:
    """A command used as a condition (`if ac_fn_c_try_compile "$LINENO"`)."""

    text: str
    """The command's source text."""
    placeholder: int | None = None
    """The marker node's id when the command is a macro call's placeholder."""


@dataclass
class UnknownExpr:
    """A condition the parser did not follow."""

    text: str
    """The condition's source text."""


Expr = TestEq | TestUnary | Not | And | Or | CmdExpr | UnknownExpr


# -- parsing ------------------------------------------------------------------


def rewrite_backticks(text: str) -> str:
    """Turn backtick command substitutions into `$(...)`.

    A scanner rather than a regex: a backtick inside single quotes (libtool's
    `sed_quote_subst='s/\\(["`$\\\\]\\)/.../'`) or in a comment (the GNU
    `like this' idiom) is not a substitution and must not pair with the next
    real one; substitutions themselves may span lines.
    """
    out: list[str] = []
    in_single = in_double = False
    open_at: int | None = None  # index in `out` of the `$(` of an open substitution
    at_line_start = True
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\n":
            at_line_start = True
            out.append(char)
            i += 1
            continue
        if char == "\\" and not in_single and i + 1 < len(text):
            out.append(text[i : i + 2])
            i += 2
            at_line_start = False
            continue
        if char == "'" and not in_double and open_at is None:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double and (at_line_start or text[i - 1] in " \t;"):
            end = text.find("\n", i)
            end = len(text) if end == -1 else end
            out.append(text[i:end])
            i = end
            continue
        elif char == "`" and not in_single:
            if open_at is None:
                open_at = len(out)
                # `( ... )` would become `$((`, an arithmetic expansion.
                out.append("$( " if text[i + 1 : i + 2] == "(" else "$(")
            else:
                out.append(")")
                open_at = None
            i += 1
            at_line_start = False
            continue
        out.append(char)
        at_line_start = at_line_start and char in " \t"
        i += 1
    if open_at is not None:
        out[open_at] = "`"  # unterminated: leave the stray backtick as it was
    return "".join(out)


def parse(text: str) -> Seq:
    """Parse shell text into a `Seq` (syntax errors are listed in `errors`)."""
    # tree-sitter-bash trips over backtick substitutions holding quoted
    # backslashes (configure's `printf '%s\n' ... | sed` idiom); the modern
    # form parses the same for our purposes.
    text = rewrite_backticks(text)
    source = text.encode("utf-8")
    tree = _PARSER.parse(source)
    converter = _Converter(source)
    seq = converter.seq(tree.root_node.children)
    if tree.root_node.has_error:
        seq.errors = converter.errors(tree.root_node)
    return seq


class _Converter:
    def __init__(self, source: bytes) -> None:
        self.source = source

    def text(self, node: TsNode) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def errors(self, root: TsNode, limit: int = 8) -> list[str]:
        """`line N: snippet` for the first ERROR / MISSING nodes."""
        out: list[str] = []

        def visit(node: TsNode) -> None:
            if len(out) >= limit:
                return
            if node.type == "ERROR" or node.is_missing:
                snippet = " ".join(self.text(node).split())[:80] or node.type
                out.append(f"line {node.start_point[0] + 1}: {snippet}")
                return
            if node.has_error:
                for child in node.children:
                    visit(child)

        visit(root)
        return out

    # statements

    def seq(self, nodes: Iterable[TsNode]) -> Seq:
        out = Seq()
        for node in nodes:
            stmt = self.stmt(node)
            if stmt is not None:
                out.stmts.append(stmt)
        return out

    def stmt(self, node: TsNode) -> Stmt | None:
        """A tree-sitter statement node as a Stmt: assignments, commands (macro placeholders
        included), if / case / for, compound statements; anything else is Unknown with its text,
        which the analysis reports.
        """
        kind = node.type
        if kind in ("comment", ";", "\n", ";;", "&", "&&", "||", "|"):
            return None
        if kind in ("compound_statement", "do_group", "subshell"):
            return self.seq(node.named_children)
        if kind == "variable_assignment":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is None:
                raise ValueError(f"variable_assignment without a name: {self.text(node)!r}")
            return Assign(self.text(name), self.word(value) if value is not None else Word(""))
        if kind == "command":
            return self.command(node)
        if kind in ("test_command", "negated_command", "pipeline"):
            return Cmd(self.text(node))
        if kind == "if_statement":
            return self.if_statement(node)
        if kind == "case_statement":
            return self.case_statement(node)
        if kind == "for_statement":
            var = node.child_by_field_name("variable")
            words = [self.word(c) for c in node.children_by_field_name("value")]
            body = node.child_by_field_name("body")
            return For(self.text(var) if var else "", words, self.seq(body.named_children) if body else Seq())
        if kind == "list":
            return self.list_statement(node)
        if kind == "redirected_statement":
            body = node.child_by_field_name("body")
            inner = self.stmt(body) if body is not None else None
            if isinstance(inner, (Placeholder, Assign)):
                return inner
            return Cmd(self.text(node))
        if kind == "function_definition":
            # gnulib-tool's conditional-dependencies shape: gl_INIT defines
            # `func_gl_gnulib_m4code_<module> () { if ! $gl_gnulib_enabled_<module>; then
            # <the module's m4 code> gl_gnulib_enabled_<module>=true; fi }` and calls each once a
            # module is enabled.  A static build enables every module, so the body counts as run
            # where it is defined; the `! $gl_gnulib_enabled_...` test reads the `=false` above it.
            # Other functions stay opaque: when they run is not known here.
            name = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name is not None and body is not None and self.text(name).startswith(_GNULIB_M4CODE_FUNCTION):
                return self.stmt(body)
            return Unknown(self.text(node))
        if kind in ("while_statement", "c_style_for_statement", "declaration_command", "unset_command"):
            return Unknown(self.text(node))
        if kind == "ERROR":
            return Unknown(self.text(node))
        return Cmd(self.text(node))

    def command(self, node: TsNode) -> Placeholder | Unknown | Cmd:
        name_node = node.child_by_field_name("name")
        name = self.text(name_node) if name_node is not None else ""
        match = _PLACEHOLDER_RE.match(name)
        if match:
            return Placeholder(int(match.group(1)))
        if name == "eval":
            return Unknown(self.text(node))
        return Cmd(self.text(node))

    def list_statement(self, node: TsNode) -> If | Unknown:
        """`a && b` / `a || b` as statements: conditional execution of b."""
        children = [c for c in node.children if c.type not in ("\n", ";")]
        if len(children) != 3:
            return Unknown(self.text(node))
        left, op, right = children
        cond = self.expr(left)
        body = self.stmt(right)
        body_seq = body if isinstance(body, Seq) else Seq([body] if body is not None else [])
        if op.type == "&&":
            return If([(cond, body_seq)])
        if op.type == "||":
            return If([(Not(cond), body_seq)])
        return Unknown(self.text(node))

    def if_statement(self, node: TsNode) -> If:
        branches: list[tuple[Expr | None, Seq]] = []
        head = [c for c in node.children if c.type not in ("elif_clause", "else_clause")]
        branches.append(self.conditional_branch(head, "if"))
        for child in node.children:
            if child.type == "elif_clause":
                branches.append(self.conditional_branch(list(child.children), "elif"))
            elif child.type == "else_clause":
                else_body = [c for c in child.children if c.type not in ("else", ";", "\n", "comment")]
                branches.append((None, self.seq(else_body)))
        return If(branches)

    def conditional_branch(self, children: Iterable[TsNode], keyword: str) -> tuple[Expr, Seq]:
        """`if`/`elif` CONDITIONS `then` BODY: split on the `then` token.

        (tree-sitter-bash tags the `if` conditions with a field but not the
        `elif` ones, so both are found positionally.)
        """
        conditions: list[TsNode] = []
        body: list[TsNode] = []
        seen_then = False
        for child in children:
            if child.type == keyword:
                continue
            if child.type == "then":
                seen_then = True
                continue
            if child.type in ("fi", ";", "\n", "comment"):
                continue
            (body if seen_then else conditions).append(child)
        return self.conditions(conditions), self.seq(body)

    def conditions(self, nodes: Iterable[TsNode]) -> Expr:
        exprs = [self.expr(n) for n in nodes if n.type not in (";", "\n", "comment")]
        if not exprs:
            return UnknownExpr("")
        # `if a; b; then`: only the last command's status counts.
        return exprs[-1]

    def case_statement(self, node: TsNode) -> Case:
        """A `case` node: the subject word and, per arm, its unquoted patterns and body."""
        subject = self.word(node.child_by_field_name("value"))
        items: list[tuple[list[str], Seq]] = []
        for child in node.children:
            if child.type != "case_item":
                continue
            values = child.children_by_field_name("value")
            patterns = [unquote_word(self.text(p)) for p in values]
            body = [
                c
                for c in child.children
                if c.type not in (")", "(", ";;", ";&", ";;&", "\n", "comment", "|") and c not in values
            ]
            items.append((patterns, self.seq(body)))
        return Case(subject, items)

    # expressions

    def expr(self, node: TsNode) -> Expr:
        """A tree-sitter condition node as an Expr: `test` / `[ ]` commands, `!`, `&&` / `||` lists,
        binary and unary test expressions; a plain command is a CmdExpr (a macro placeholder when
        it is one).
        """
        kind = node.type
        if kind == "command":
            name_node = node.child_by_field_name("name")
            name = self.text(name_node) if name_node is not None else ""
            args = [c for c in node.children if c != name_node and c.type not in ("variable_assignment",)]
            if name == "test":
                return self.test_args([self.word(a) for a in args])
            if name == "!":
                return Not(self.expr_from_words(args))
            match = _PLACEHOLDER_RE.match(name)
            return CmdExpr(self.text(node), int(match.group(1)) if match else None)
        if kind == "test_command":
            inner = [c for c in node.children if c.type not in ("[", "]", "[[", "]]")]
            if len(inner) == 1 and inner[0].type in ("binary_expression", "unary_expression", "negated"):
                return self.test_expression(inner[0])
            return self.test_args([self.word(c) for c in inner])
        if kind == "negated_command":
            inner = [c for c in node.children if c.type != "!"]
            return Not(self.expr(inner[0])) if len(inner) == 1 else UnknownExpr(self.text(node))
        if kind == "list":
            children = [c for c in node.children if c.type not in ("\n", ";")]
            if len(children) == 3:
                left, op, right = children
                if op.type == "&&":
                    return And([self.expr(left), self.expr(right)])
                if op.type == "||":
                    return Or([self.expr(left), self.expr(right)])
            return UnknownExpr(self.text(node))
        if kind in ("compound_statement", "subshell"):
            inner = [c for c in node.named_children]
            return self.expr(inner[-1]) if inner else UnknownExpr(self.text(node))
        if kind == "pipeline":
            return CmdExpr(self.text(node))
        return UnknownExpr(self.text(node))

    def expr_from_words(self, nodes: Sequence[TsNode]) -> Expr:
        if not nodes:
            return UnknownExpr("")
        if self.text(nodes[0]) == "test":
            return self.test_args([self.word(a) for a in nodes[1:]])
        return CmdExpr(" ".join(self.text(n) for n in nodes))

    def test_expression(self, node: TsNode) -> Expr:
        """A `[ ... ]` binary or unary expression node: equality comparisons (also `-eq` / `-ne`)
        and unary tests; other operators are Unknown.
        """
        if node.type == "binary_expression":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            op = node.child_by_field_name("operator")
            op_text = self.text(op) if op else ""
            if op_text in ("=", "==", "!=", "-eq", "-ne"):
                return TestEq(self.word(left), self.word(right), negate=op_text in ("!=", "-ne"))
            return UnknownExpr(self.text(node))
        if node.type == "unary_expression":
            op = node.child_by_field_name("operator")
            children = [c for c in node.children if c != op]
            return TestUnary(self.text(op) if op else "", self.word(children[0]) if children else Word(""))
        return UnknownExpr(self.text(node))

    def test_args(self, words: list[Word]) -> Expr:
        """The words of a `test` command: `A = B` style comparisons (also `-eq` / `-ne`), unary
        tests, `!`, and `-a` / `-o` conjunctions; a lone word tests for non-emptiness.
        """
        texts = [w.text for w in words]
        if len(words) == 3 and texts[1] in ("=", "==", "!=", "-eq", "-ne"):
            # `-eq` on configure's integer results (`$ac_cv_sizeof_void_p -eq 8`)
            # compares the same literal text as `=`.
            return TestEq(words[0], words[2], negate=texts[1] in ("!=", "-ne"))
        if len(words) == 2 and texts[0].startswith("-"):
            return TestUnary(texts[0], words[1])
        if len(words) >= 2 and texts[0] == "!":
            return Not(self.test_args(words[1:]))
        for op, cls in (("-o", Or), ("-a", And)):
            if op in texts:
                index = texts.index(op)
                return cls([self.test_args(words[:index]), self.test_args(words[index + 1 :])])
        if len(words) == 1:
            return TestUnary("-n", words[0])
        return UnknownExpr("test " + " ".join(texts))

    # words

    def word(self, node: TsNode | None) -> Word:
        """A word node as a Word: its text, the variables it expands, and whether it holds anything
        the analysis cannot follow (command substitution, `${x:-y}` style expansions,
        arithmetic).
        """
        if node is None:
            return Word("")
        text = self.text(node)
        variables: list[str] = []
        unknown = False

        def visit(n: TsNode) -> None:
            nonlocal unknown
            if n.type in ("simple_expansion", "expansion"):
                names = [c for c in n.children if c.type in ("variable_name", "special_variable_name")]
                if names:
                    variables.append(self.text(names[0]))
                if n.type == "expansion" and any(c.type not in ("${", "}", "variable_name") for c in n.children):
                    unknown = True  # ${x+y}, ${x:-y} ...
                return
            if n.type in ("command_substitution", "arithmetic_expansion", "process_substitution"):
                unknown = True
                return
            for c in n.children:
                visit(c)

        visit(node)
        if node.type in ("word", "concatenation") and any(ch in text for ch in "*?["):
            unknown = unknown or not variables  # a glob
        return Word(text, tuple(variables), unknown)
