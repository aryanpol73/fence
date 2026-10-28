"""What kind of change is a block? delete > loosen > alter > move (by risk).

- move:   the removed code reappears elsewhere in the diff with its guards intact.
- loosen: a guard, lock, retry, timeout, assertion or error handler is removed or
          weakened (its code signal disappears, or a number on such a line changes),
          including a move that silently drops one.
- delete: nothing meaningful replaces the removed lines.
- alter:  anything else (expressions or constants changed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fence.anchors.fingerprint import containment, jaccard, shingles
from fence.check.diff import Block
from fence.lang import tokens_for
from fence.signals.code import match_code

GUARD_SIGNALS = {
    "code.guard", "code.lock", "code.retry_timeout", "code.exception_handling",
    "code.assertion", "code.feature_flag",
}
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
MOVE_CONTAINMENT = 0.8
DELETE_SIMILARITY = 0.3


@dataclass(frozen=True)
class Classification:
    kind: str  # delete | loosen | alter | move
    reason: str
    lost_signals: tuple[str, ...] = ()


def classify(path: str, block: Block, diff_added: frozenset[int]) -> Classification:
    """`diff_added` is the shingle set of every added line in the whole diff."""
    removed = [t for _, t in block.removed]
    added = [t for _, t in block.added]
    r_tokens = tokens_for(path, "\n".join(removed))
    a_tokens = tokens_for(path, "\n".join(added))
    if not r_tokens:
        return Classification("alter", "only blank lines or comments removed")
    r_sigs = {s for s, _ in match_code(removed)} & GUARD_SIGNALS
    a_sigs = {s for s, _ in match_code(added)} & GUARD_SIGNALS

    r_sh = shingles(r_tokens)
    if len(r_tokens) >= 4 and containment(r_sh, diff_added) >= MOVE_CONTAINMENT and \
            jaccard(r_sh, shingles(a_tokens)) < MOVE_CONTAINMENT:
        moved_sigs = r_sigs - a_sigs
        dest_ok = all(_signal_survives(s, diff_added, path, removed) for s in moved_sigs)
        if dest_ok:
            return Classification("move", "the removed code reappears elsewhere in this change")

    lost = tuple(sorted(r_sigs - a_sigs))
    if not a_tokens:
        return Classification("delete", "lines removed with no replacement", lost)
    if jaccard(r_sh, shingles(a_tokens)) < DELETE_SIMILARITY and len(r_tokens) >= 8:
        return Classification("delete", "replaced by unrelated code", lost)
    if lost:
        names = ", ".join(s.split(".", 1)[1].replace("_", " ") for s in lost)
        return Classification("loosen", f"removes or weakens: {names}", lost)
    if _guard_number_changed(removed, added):
        return Classification("loosen", "a retry, timeout, lock or guard value changed")
    return Classification("alter", "code changed in place")


def _signal_survives(sid: str, diff_added: frozenset[int], path: str, removed: list[str]) -> bool:
    # The guard line itself must be among the moved shingles.
    guard_lines = [t for t in removed if sid in {s for s, _ in match_code([t])}]
    if not guard_lines:
        return True
    sh = shingles(tokens_for(path, "\n".join(guard_lines)))
    return containment(sh, diff_added) >= MOVE_CONTAINMENT


def _guard_number_changed(removed: list[str], added: list[str]) -> bool:
    guarded = [t for t in removed if {s for s, _ in match_code([t])} & GUARD_SIGNALS]
    if not guarded:
        return False
    before = [_NUMBER.findall(t) for t in guarded]
    after_all = [_NUMBER.findall(t) for t in added]
    return any(nums and nums not in after_all for nums in before)
