"""Signals read from code text and from history shape (as opposed to messages).

Code signals look at the lines a decision covers; structural signals look at how
those lines lived (survival, flip-flops, re-fixes). Both feed Fence Strength.
"""

from __future__ import annotations

import re

from fence.signals.catalog import Signal, _rx

CODE_SIGNALS: tuple[Signal, ...] = (
    Signal("code.warning_comment", "code", 14, "a comment warns against changing it",
           "deliberate-design",
           _rx(r"(?:#|//|/\*|\*|--)\s*(?:warning|important|careful|do not|don'?t|never|must|"
               r"note)\b[^\n]{0,160}")),
    Signal("code.hack_comment", "code", 8, "a TODO/HACK/XXX/FIXME comment", "compatibility",
           re.compile(r"(?:#|//|/\*|\*|--)\s*(?:TODO|HACK|XXX|FIXME|WORKAROUND)\b[^\n]{0,160}")),
    Signal("code.guard", "code", 6, "a guard clause or validation check", None,
           re.compile(r"^\s*(?:if\b.*(?:\bis None\b|\bnull\b|\bnil\b|==\s*None|!=\s*None|"
                      r"\bnot\b|!|<|>|len\(|\.length|\bundefined\b)|"
                      r"if\s+err\s*!=\s*nil|assert\b|require\(|precondition|"
                      r"raise\b|throw\b|return\s+err\b)")),
    Signal("code.exception_handling", "code", 6, "error handling", None,
           re.compile(r"^\s*(?:except\b|catch\s*\(|catch\b|finally\b|rescue\b|recover\(\))")),
    Signal("code.lock", "code", 10, "a lock, mutex or atomic operation", "concurrency",
           _rx(r"\b(lock\(|unlock\(|\.lock\b|mutex|rwlock|semaphore|synchronized|atomic\w*|"
               r"threading\.\w*lock|with\s+[\w.]*lock\b|sync\.(Mutex|RWMutex|Once)|"
               r"compareandset|\bcas\()")),
    Signal("code.retry_timeout", "code", 8, "a retry, backoff, sleep or timeout", None,
           _rx(r"\b(retr(y|ies|ying)|backoff|back_off|sleep\(|time\.sleep|settimeout|"
               r"timeout|deadline|max_attempts|attempts?)\b")),
    Signal("code.feature_flag", "code", 6, "behind a feature flag", None,
           _rx(r"\b(feature[_ ]?flag|is_enabled\(|isenabled\(|flags?\.\w+|"
               r"launchdarkly|unleash)\b")),
    Signal("code.assertion", "code", 4, "an assertion or invariant", None,
           _rx(r"^\s*(assert\b|assert\w*\(|invariant\(|check\w*\()")),
)

STRUCTURAL_SIGNALS: tuple[Signal, ...] = (
    Signal("struct.survivor", "structural", 1, "survived later edits nearby (per year, capped)"),
    Signal("struct.test_added", "structural", 10, "a test was added in the same change"),
    Signal("struct.revert_involved", "structural", 14, "involved in a revert"),
    Signal("struct.regression_chain", "structural", 8, "the same lines were re-fixed (per fix)"),
    Signal("struct.flip_flop", "structural", 12, "removed and re-added before"),
    Signal("struct.multi_author", "structural", 3, "touched or reviewed by several people (each)"),
    Signal("struct.linked_pr", "structural", 6, "landed through a pull request"),
    Signal("struct.constant_in_fix", "structural", 8, "a constant changed in a fix commit"),
    Signal("struct.format_only", "negative", -20, "only formatting touched these lines"),
    Signal("struct.bulk_change", "negative", -10, "landed in a very large bulk commit"),
    Signal("struct.vendored", "negative", -30, "vendored or generated code"),
)


def match_code(lines: list[str]) -> list[tuple[str, str]]:
    """(signal_id, verbatim line text) for code signals in `lines`."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in lines:
        for s in CODE_SIGNALS:
            assert s.pattern is not None  # noqa: S101
            m = s.pattern.search(line)
            if m:
                key = (s.id, line.strip()[:160])
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found
