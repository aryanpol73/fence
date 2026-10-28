"""The fence check: a diff in, a justified verdict per change block out.

Verdicts:
  KEEP     strong, active decision contradicted: stop and verify (can fail CI)
  VERIFY   a decision is touched and its reason is only partly known: review it
  CLEAR    nothing meaningful affected
  SAFE-TO-REMOVE-CANDIDATE  the reason is known and evidence says it is obsolete

Only KEEP can fail a run (exit 2 or 3), and only for grades at or above
scoring.grade_floor_for_block: "reason unknown" never blocks anyone.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fence.anchors.fingerprint import fingerprint, shingles
from fence.check.classify import Classification, classify
from fence.check.diff import DiffError, DiffSpec, FileDiff, parse_diff, produce_diff
from fence.config import BANDS, Config, PathOverride
from fence.context import Context
from fence.decisions.build import TEST_PATH, Target, decisions_for_lines, format_only_shas
from fence.decisions.model import DecisionRecord, Score
from fence.diffing.ranges import RemovedRange
from fence.git.blame import MAX_LINES
from fence.git.runner import GitError
from fence.globs import match_any
from fence.lang import tokens_for
from fence.policy import acks as ackmod
from fence.scoring.strength import change_risk
from fence.text import clean
from fence.trace import PreimageMismatch, trace

KEEP, VERIFY, CLEAR, SAFE = "KEEP", "VERIFY", "CLEAR", "SAFE-TO-REMOVE-CANDIDATE"
VERDICT_ORDER = (CLEAR, SAFE, VERIFY, KEEP)
GRADE_ORDER = "ABCD"

EXIT_OK, EXIT_ERROR, EXIT_POLICY, EXIT_ACK, EXIT_NO_INDEX = 0, 1, 2, 3, 4


@dataclass
class Finding:
    path: str
    start: int  # base line numbers of the removed block
    end: int
    kind: str
    kind_reason: str
    verdict: str
    risk: Score
    decision: DecisionRecord
    decision_fingerprint: str
    removed_preview: str
    lost_signals: tuple[str, ...] = ()
    ack: ackmod.Ack | None = None

    @property
    def blocking(self) -> bool:
        return self.verdict == KEEP and self.ack is None


@dataclass
class CheckResult:
    base: str
    head: str | None
    findings: list[Finding] = field(default_factory=list)
    verdict: str = CLEAR
    exit_code: int = EXIT_OK
    files_checked: int = 0
    blocks_checked: int = 0
    notes: list[str] = field(default_factory=list)
    bypassed: bool = False
    seconds: float = 0.0


def effective_policy(cfg: Config, path: str) -> tuple[str, str, int, str]:
    """(fail_on_band, require_ack_for, risk_bonus, reason) after path overrides.
    When several overrides match, the strictest value of each wins."""
    fail_on, ack_for, bonus, why = cfg.policy.fail_on_band, cfg.policy.require_ack_for, 0, ""
    for pattern, ov in cfg.policy.path_overrides.items():
        if not match_any(path, [pattern]):
            continue
        ov: PathOverride
        if ov.fail_on_band and BANDS.index(ov.fail_on_band) < BANDS.index(fail_on):
            fail_on = ov.fail_on_band
        if ov.require_ack_for and BANDS.index(ov.require_ack_for) < BANDS.index(ack_for):
            ack_for = ov.require_ack_for
        if ov.risk_bonus > bonus:
            bonus, why = ov.risk_bonus, f"sensitive path ({pattern})"
    return fail_on, ack_for, bonus, why


def check(
    ctx: Context, spec: DiffSpec | None = None, *, diff_text: str | None = None,
    base: str | None = None, deep: bool = True,
) -> CheckResult:
    started = time.monotonic()
    if diff_text is None:
        spec = spec or DiffSpec(base="HEAD")
        base_sha, diff_text = produce_diff(ctx.repo, spec,
                                           rename_threshold=ctx.cfg.index.follow_renames_sensitivity)
        head = spec.head
    else:
        base_sha = ctx.repo.verify_ref(base or "HEAD")
        head = None
    result = CheckResult(base=base_sha, head=head)
    files = parse_diff(diff_text)
    ledger = ctx.repo.root / ctx.cfg.policy.ack_ledger
    acks, problems = ackmod.load(ledger)
    result.notes += problems
    if ctx.store.commit_count() == 0:
        result.notes.append("index is empty: run `fence index` for full evidence")

    diff_added = frozenset().union(*(
        shingles(tokens_for(f.path, "\n".join(f.added_lines))) for f in files if f.added_lines
    )) if files else frozenset()
    tests_removed = any(
        (f.status == "deleted" or f.blocks) and TEST_PATH.search(f.old_path or "")
        for f in files
    )
    now = datetime.now(UTC)
    for fd in files:
        if not fd.blocks or fd.old_path is None or fd.binary:
            continue
        if match_any(fd.old_path, ctx.cfg.policy.ignore_paths):
            continue
        result.files_checked += 1
        result.blocks_checked += len(fd.blocks)
        try:
            result.findings += _check_file(ctx, fd, base_sha, diff_added, tests_removed,
                                           acks, now, result.notes, deep=deep)
        except PreimageMismatch as e:
            raise DiffError(str(e)) from None

    # Within equal risk, blocks that drop a guard/retry/lock come first: they are the
    # lines a reviewer most needs to see.
    result.findings.sort(key=lambda f: (-VERDICT_ORDER.index(f.verdict), -f.risk.value,
                                        -len(f.lost_signals), f.path, f.start))
    if result.findings:
        result.verdict = result.findings[0].verdict
    bypass_var = ctx.cfg.policy.allow_bypass_env
    result.bypassed = bool(bypass_var and os.environ.get(bypass_var))
    result.exit_code = _exit_code(ctx.cfg, result)
    result.seconds = round(time.monotonic() - started, 3)
    return result


def _exit_code(cfg: Config, result: CheckResult) -> int:
    if result.bypassed:
        return EXIT_OK
    code = EXIT_OK
    for f in result.findings:
        if not f.blocking:
            continue
        _, ack_for, _, _ = effective_policy(cfg, f.path)
        if BANDS.index(f.risk.band) >= BANDS.index(ack_for):
            return EXIT_ACK
        code = EXIT_POLICY
    return code


def _check_file(
    ctx: Context, fd: FileDiff, base_sha: str, diff_added: frozenset[int], tests_removed: bool,
    acks: list[ackmod.Ack], now: datetime, notes: list[str], *, deep: bool,
) -> list[Finding]:
    path = fd.old_path or ""
    try:
        text = ctx.repo.read_blob(base_sha, path, max_bytes=ctx.cfg.index.max_file_size)
    except GitError as e:
        notes.append(f"{path}: skipped ({e})")
        return []
    base_lines = text.split("\n")
    blocks = fd.blocks
    total = sum(b.end - b.start + 1 for b in blocks)
    if total > MAX_LINES:
        kept, n = [], 0
        for b in blocks:
            if n + (b.end - b.start + 1) > MAX_LINES:
                break
            kept.append(b)
            n += b.end - b.start + 1
        notes.append(f"{path}: {total} removed lines; only the first {n} were traced")
        blocks = kept
    ranges = [RemovedRange(path, b.start, b.end, tuple(t for _, t in b.removed)) for b in blocks]
    history = ctx.store.path_history(path)
    traced = trace(ctx.repo, base_sha, ranges, extra_ignore=format_only_shas(ctx, history))
    blamed = [bl for t in traced for bl in t.lines]
    target = Target(path, blocks[0].start, blocks[-1].end, None, base_sha)
    decisions = decisions_for_lines(ctx, target, base_lines, blamed, history, deep=deep)
    owner: dict[int, DecisionRecord] = {}
    for d in decisions:
        for a in d.anchors:
            for n in range(a.start_line, a.end_line + 1):
                owner[n] = d

    fail_on, _, bonus, bonus_reason = effective_policy(ctx.cfg, path)
    findings: list[Finding] = []
    for b in blocks:
        cls = classify(path, b, diff_added)
        touched = {owner[n].id: owner[n] for n, _ in b.removed if n in owner}
        best: Finding | None = None
        for d in touched.values():
            risk = change_risk(d.strength, cls.kind, ctx.cfg, tests_removed=tests_removed,
                               path_bonus=bonus, path_bonus_reason=bonus_reason)
            f = Finding(
                path=path, start=b.start, end=b.end, kind=cls.kind, kind_reason=cls.reason,
                verdict=_verdict(ctx.cfg, d, risk, fail_on, cls), risk=risk, decision=d,
                decision_fingerprint=_decision_fingerprint(path, base_lines, d),
                removed_preview=clean("\n".join(t for _, t in b.removed[:6]), max_len=400),
                lost_signals=cls.lost_signals,
            )
            if best is None or (VERDICT_ORDER.index(f.verdict), f.risk.value) > (
                    VERDICT_ORDER.index(best.verdict), best.risk.value):
                best = f
        if best is None or best.verdict == CLEAR:
            continue
        best.ack = ackmod.find_valid(acks, best.decision.id, best.decision_fingerprint, now)
        findings.append(best)
    return findings


def _verdict(cfg: Config, d: DecisionRecord, risk: Score, fail_on: str,
             cls: Classification) -> str:
    if d.status == "stale-suspected" and d.grade in ("A", "B"):
        return SAFE
    can_block = (
        GRADE_ORDER.index(d.grade) <= GRADE_ORDER.index(cfg.scoring.grade_floor_for_block)
        and d.status == "active"
        and risk.confidence >= cfg.scoring.min_confidence_to_warn
    )
    if can_block and BANDS.index(risk.band) >= BANDS.index(fail_on):
        return KEEP
    if risk.value >= cfg.scoring.bands.medium and cls.kind != "move":
        return VERIFY
    return CLEAR


def _decision_fingerprint(path: str, base_lines: list[str], d: DecisionRecord) -> str:
    owned = [base_lines[n - 1] for a in d.anchors for n in range(a.start_line, a.end_line + 1)
             if n - 1 < len(base_lines)]
    return fingerprint(tokens_for(path, "\n".join(owned))).exact


def ledger_path(ctx: Context) -> Path:
    return ctx.repo.root / ctx.cfg.policy.ack_ledger
