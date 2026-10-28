"""Find the function or class that encloses a line of Python source.

Kept as the stable Day 1 entry point; the work is done by the Python language
plugin. For any language, use `fence.lang.enclosing_symbol(path, source, line)`.
"""

from __future__ import annotations

from fence.lang import MODULE, Symbol
from fence.lang import enclosing_symbol as _enclosing

__all__ = ["MODULE", "Symbol", "enclosing_symbol"]


def enclosing_symbol(source: str, line: int) -> Symbol | None:
    """Return the innermost function/class containing `line` (1-based).

    Returns None if the source does not parse or the line is past the end of the file.
    Lines outside any def/class return the `<module>` symbol.
    """
    return _enclosing("x.py", source, line)
