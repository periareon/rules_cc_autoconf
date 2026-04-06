"""Utilities for extracting variable names from condition expressions.

Shared by autoconf_library.bzl and autoconf_srcs.bzl so that the Starlark-side
dependency graph wiring matches what the C++ condition evaluator expects.
"""

_COMPARISON_OPS = ["<=", ">=", "!=", "==", "<", ">", "="]

def _strip_leading_negations(token):
    """Strip all leading '!' characters from a token."""
    for _ in range(len(token)):
        if token.startswith("!"):
            token = token[1:]
        else:
            break
    return token

def extract_condition_vars(expr):
    """Extract all variable names from a condition expression.

    Handles compound expressions with ``||``, ``&&``, ``!``, ``()``, and
    comparison operators (``==``, ``!=``, ``<``, ``>``, ``<=``, ``>=``).

    Args:
        expr: A condition expression string, e.g. ``"HAVE_X==0 || REPLACE_X"``.

    Returns:
        A list of unique variable name strings, in the order they first appear.
    """

    # Tokenize: replace operators and parens with spaces, then split.
    s = expr
    for op in ["||", "&&", "(", ")"]:
        s = s.replace(op, " ")

    # Starlark's split() requires an explicit separator.
    # Collapse consecutive whitespace and split on single space.
    tokens = []
    for part in s.split(" "):
        if part:
            tokens.append(part)

    seen = {}
    result = []
    for token in tokens:
        # Strip leading negation(s)
        token = _strip_leading_negations(token)
        if not token:
            continue

        # Split off comparison operator and RHS
        name = token
        for op in _COMPARISON_OPS:
            idx = token.find(op)
            if idx >= 0:
                name = token[:idx]
                break

        if not name:
            continue

        # Filter out pure numeric values that could appear as standalone tokens
        if name[0].isdigit():
            continue

        if name not in seen:
            seen[name] = True
            result.append(name)

    return result
