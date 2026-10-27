"""Find the function or class that encloses a line of Python source.

The enclosing symbol is part of the cache key and of the human output, so it has
to be stable and boring: `Class.method`, `outer.inner`, or `<module>`.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

MODULE = "<module>"

_Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class Symbol:
    qualname: str
    kind: Literal["function", "class", "module"]
    start_line: int  # first decorator line, if any
    end_line: int


def enclosing_symbol(source: str, line: int) -> Symbol | None:
    """Return the innermost function/class containing `line` (1-based).

    Returns None if the source does not parse or the line is past the end of the file.
    Lines outside any def/class return the `<module>` symbol.
    """
    if line < 1:
        raise ValueError(f"line must be >= 1, got {line}")
    total_lines = len(source.splitlines())
    if line > total_lines:
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    best: Symbol | None = None
    for node, names in _walk_scopes(tree, ()):
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        end = node.end_lineno or node.lineno
        if start <= line <= end:
            # _walk_scopes yields parents before children, so the last match is innermost.
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            best = Symbol(".".join(names), kind, start, end)
    return best or Symbol(MODULE, "module", 1, total_lines)


def _walk_scopes(node: ast.AST, prefix: tuple[str, ...]):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _Scope):
            names = (*prefix, child.name)
            yield child, names
            yield from _walk_scopes(child, names)
        else:
            # Scopes can sit inside if/try/with blocks; keep looking with the same prefix.
            yield from _walk_scopes(child, prefix)
