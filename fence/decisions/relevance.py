"""Evidence scope: which commit, PR and issue text is about *this* code.

A Decision Record covers one file's lines from one commit, but commit messages, PR
bodies and issues describe the whole change. When the change touched only this
source file, its text is about this code. When it touched many, a sentence counts
as evidence for this code only if it names the code: the file, or a symbol or
distinctive identifier defined in or used by the anchored lines. Everything else
is change-level context: worth showing, but never quoted as this code's reason.

Matching is by explicit mention, not word overlap: generic words such as
"index", "cache" or "file" appear everywhere and would tie any sentence to any
file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from fence.decisions.reasons import sentences
from fence.globs import match_any

# File stems too generic to identify a file when they appear in prose.
GENERIC_STEMS = {"index", "main", "init", "__init__", "utils", "util", "base", "common", "core",
                 "types", "helpers", "lib", "app", "mod", "api", "test", "tests", "setup"}
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_TEST_PREFIX = re.compile(r"^(test_|tests?)|(_test|_spec|\.test|\.spec|tests?)$", re.IGNORECASE)


@dataclass(frozen=True)
class Scope:
    focused: bool  # the change touched no other source file
    source_files: int  # source files the change touched (tests and docs excluded)
    names: frozenset[str]  # lower-case names that identify this code in prose
    other_files: frozenset[str] = frozenset()  # names of the change's other files


def _distinctive(ident: str) -> bool:
    """CamelCase or snake_case identifiers long enough not to be ordinary words."""
    if len(ident) < 6:
        return False
    return "_" in ident.strip("_") or bool(re.search(r"[a-z][A-Z]", ident))


def build_scope(path: str, changed: list[tuple[str, int]], code_lines: list[str],
                symbol_names: list[str], *, test_pattern: re.Pattern[str],
                low_signal: list[str]) -> Scope:
    """`changed` is (path, lines added + deleted) for every file in the change."""
    sources = {p for p, n in changed
               if n > 0 and not test_pattern.search(p) and not match_any(p, low_signal)}
    focused = sources <= {path}
    names = _file_names(path)
    for q in symbol_names:
        for part in q.split("."):
            if len(part) >= 4 and part != "<module>":
                names.add(part.lower())
    for line in code_lines:
        for ident in _IDENT.findall(line):
            if _distinctive(ident):
                names.add(ident.lower())
    others: set[str] = set()
    for p, _ in changed:
        if p != path:
            others |= _file_names(p)
    return Scope(focused, len(sources), frozenset(names), frozenset(others - names))


def _file_names(path: str) -> set[str]:
    pp = PurePosixPath(path)
    out = {pp.name.lower()}
    if pp.stem.lower() not in GENERIC_STEMS and len(pp.stem) >= 4:
        out.add(pp.stem.lower())
    return out


def mentions(sentence: str, names: frozenset[str]) -> bool:
    low = sentence.lower()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", low) for n in names)


def relevant_text(text: str, scope: Scope) -> str:
    """The part of `text` that is evidence for this code: all of it for a focused
    change, otherwise only the sentences that name this code."""
    if scope.focused:
        return text
    return "\n\n".join(s for s in sentences(text) if mentions(s, scope.names))


def change_level_text(text: str, scope: Scope) -> str:
    """Sentences about the change as a whole: those that name no other file in it.
    A sentence naming another file is that file's reason, not context for this one."""
    return "\n\n".join(s for s in sentences(text) if not mentions(s, scope.other_files))


def related_tests(test_paths: list[str], path: str, scope: Scope) -> list[str]:
    """Tests from the same change that plausibly exercise this file: any test for a
    focused change, otherwise tests whose name matches the file's name."""
    if scope.focused:
        return test_paths
    stem = PurePosixPath(path).stem.lower()
    out = []
    for t in test_paths:
        tstem = _TEST_PREFIX.sub("", PurePosixPath(t).stem.lower()).strip("_.-")
        if tstem and (tstem == stem or (len(tstem) >= 4 and tstem in stem)
                      or (len(stem) >= 4 and stem in tstem)):
            out.append(t)
    return out
