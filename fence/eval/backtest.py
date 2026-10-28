"""Removal-Regret Backtest (D1): replay history and score Fence against what happened.

For each first-parent commit C that removes code, a removal block is **regretted**
when, within the next `window` commits, C is reverted or the removed code (by
fingerprint) is added back. Fence then checks C's diff with only the history
visible at C's parent (Context.as_of), so no future knowledge leaks into the
verdict. A block is **warned** when Fence's verdict is KEEP or VERIFY.

Regret labels are a lower bound: a warned removal that nobody restored may still
have been risky. So precision here is conservative, and the report says so.
"""

from __future__ import annotations

import secrets
import statistics
import time
from dataclasses import dataclass, field

from fence.anchors.fingerprint import containment, shingles
from fence.check.diff import DiffError, FileDiff, parse_diff
from fence.check.engine import KEEP, VERIFY, check
from fence.context import Context
from fence.decisions.build import TEST_PATH
from fence.git.runner import GitError
from fence.lang import plugin_for, tokens_for

MIN_TOKENS = 8
RESTORE_CONTAINMENT = 0.8
MAX_FILES_PER_COMMIT = 50
MAX_DIFF_BYTES = 1_000_000


@dataclass
class _Commit:
    sha: str
    parent: str | None
    time: int
    diff: str
    files: list[FileDiff] = field(default_factory=list)
    added: dict[str, frozenset[int]] = field(default_factory=dict)  # per path

    @property
    def all_added(self) -> frozenset[int]:
        return frozenset().union(*self.added.values()) if self.added else frozenset()


@dataclass
class BlockOutcome:
    sha: str
    path: str
    start: int
    regretted: bool
    regret_kind: str | None  # "reverted" | "restored"
    lead_days: float | None
    verdict: str  # KEEP | VERIFY | CLEAR
    risk: int
    confidence: float | None
    reason: str | None
    kind: str | None = None
    grade: str | None = None
    dtype: str | None = None
    strength: int = 0
    lost: int = 0


@dataclass
class BacktestReport:
    repo: str
    commits_evaluated: int = 0
    commits_with_removals: int = 0
    blocks: int = 0
    regretted: int = 0
    warned: int = 0
    keep: int = 0
    tp_warned: int = 0
    tp_keep: int = 0
    commits_warned: int = 0
    latencies: list[float] = field(default_factory=list)
    leads: list[float] = field(default_factory=list)
    outcomes: list[BlockOutcome] = field(default_factory=list)
    errors: int = 0
    seconds: float = 0.0

    @staticmethod
    def _ratio(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    def summary(self) -> dict:
        return {
            "repo": self.repo,
            "commits_evaluated": self.commits_evaluated,
            "removal_blocks": self.blocks,
            "regretted_blocks": self.regretted,
            "warned_blocks": self.warned,
            "keep_blocks": self.keep,
            "precision_warn": self._ratio(self.tp_warned, self.warned),
            "recall_warn": self._ratio(self.tp_warned, self.regretted),
            "precision_keep": self._ratio(self.tp_keep, self.keep),
            "recall_keep": self._ratio(self.tp_keep, self.regretted),
            "warned_commits_per_1k": (round(1000 * self.commits_warned / self.commits_evaluated, 1)
                                      if self.commits_evaluated else None),
            "warning_blocks_per_1k_commits": (round(1000 * self.warned / self.commits_evaluated, 1)
                                              if self.commits_evaluated else None),
            "median_check_seconds": (round(statistics.median(self.latencies), 3)
                                     if self.latencies else None),
            "p90_check_seconds": (round(sorted(self.latencies)[int(0.9 * (len(self.latencies)
                                                                          - 1))], 3)
                                  if self.latencies else None),
            "median_lead_days": round(statistics.median(self.leads), 1) if self.leads else None,
            "calibration": self.calibration(),
            "errors": self.errors,
            "seconds": round(self.seconds, 1),
        }

    def calibration(self) -> list[dict]:
        buckets = [(0.0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
        out = []
        for lo, hi in buckets:
            xs = [o for o in self.outcomes if o.verdict in (KEEP, VERIFY)
                  and o.confidence is not None and lo <= o.confidence < hi]
            out.append({"confidence": f"{lo:.1f}-{min(hi, 1.0):.1f}", "warned": len(xs),
                        "regretted": sum(o.regretted for o in xs),
                        "observed_precision": self._ratio(sum(o.regretted for o in xs), len(xs))})
        return out


def _stream_commits(ctx: Context, rev: str, limit: int) -> list[_Commit]:
    nonce = secrets.token_hex(6)
    marker = f"\x1e{nonce}"
    args = ["log", "--first-parent", "--diff-merges=first-parent", "-p", "--unified=0",
            "--no-color", "--no-ext-diff", "--no-textconv", "-M50%", "--src-prefix=a/",
            "--dst-prefix=b/", f"--max-count={limit}", f"--format=%x1e{nonce}%H %P%n%ct",
            ctx.repo.verify_ref(rev), "--"]
    text = "".join(ctx.repo.stream(args, timeout=ctx.cfg.performance.log_timeout_seconds))
    commits = []
    for rec in text.split(marker):
        if not rec.strip():
            continue
        head, _, rest = rec.partition("\n")
        ts, _, diff = rest.partition("\n")
        parts = head.split()
        if not parts or not ts.strip().isdigit():
            continue
        commits.append(_Commit(parts[0], parts[1] if len(parts) > 1 else None, int(ts),
                               diff if len(diff) <= MAX_DIFF_BYTES else ""))
    commits.reverse()  # oldest first
    for c in commits:
        try:
            c.files = parse_diff(c.diff) if c.diff else []
        except DiffError:
            c.files = []
        for f in c.files:
            if f.added_lines:
                c.added[f.path] = shingles(tokens_for(f.path, "\n".join(f.added_lines)))
    return commits


def backtest(ctx: Context, *, rev: str = "HEAD", max_commits: int = 300, window: int = 200,
             deep: bool = True, progress=None) -> BacktestReport:
    started = time.monotonic()
    rep = BacktestReport(repo=str(ctx.repo.root))
    commits = _stream_commits(ctx, rev, max_commits + window)
    # The oldest `max_commits` of the range, so each has up to `window` later commits in
    # which a regret can show up. The newest ones have shorter windows (right-censored).
    evaluate = commits[:max_commits]
    index_of = {c.sha: i for i, c in enumerate(commits)}
    for n, c in enumerate(evaluate):
        if progress and n % 25 == 0:
            progress(n, len(evaluate))
        rep.commits_evaluated += 1
        if not c.parent or not c.files or len(c.files) > MAX_FILES_PER_COMMIT:
            continue
        row = ctx.store.commit(c.sha)
        if row and (row.format_only or row.bulk):
            continue
        blocks = _removal_blocks(c)
        if not blocks:
            continue
        rep.commits_with_removals += 1
        i = index_of[c.sha]
        later = commits[i + 1 : i + 1 + window]
        reverted_by = set(ctx.store.reverts_of(c.sha))
        labels = {}
        for path, b, sh in blocks:
            kind, lead = None, None
            for lc in later:
                if lc.sha in reverted_by:
                    kind, lead = "reverted", (lc.time - c.time) / 86400
                    break
                # Same file only: boilerplate re-added elsewhere is not a restoration.
                if containment(sh, lc.added.get(path, frozenset())) >= RESTORE_CONTAINMENT:
                    kind, lead = "restored", (lc.time - c.time) / 86400
                    break
            labels[(path, b.start)] = (kind, lead)

        ctx.as_of = ctx.store.commit(c.parent).committer_time if ctx.store.commit(c.parent) \
            else c.time - 1
        t0 = time.monotonic()
        try:
            res = check(ctx, diff_text=c.diff, base=c.parent, deep=deep, include_clear=True)
        except (DiffError, GitError):
            rep.errors += 1
            continue
        finally:
            ctx.as_of = None
        rep.latencies.append(time.monotonic() - t0)
        by_block = {(f.path, f.start): f for f in res.findings}
        warned_here = False
        for (path, start), (kind, lead) in labels.items():
            f = by_block.get((path, start))
            verdict = f.verdict if f and f.verdict in (KEEP, VERIFY) else "CLEAR"
            o = BlockOutcome(c.sha, path, start, kind is not None, kind, lead, verdict,
                             f.risk.value if f else 0, f.risk.confidence if f else None,
                             f.decision.reason_text if f else None,
                             f.kind if f else None, f.decision.grade if f else None,
                             f.decision.type if f else None,
                             f.decision.strength.value if f else 0,
                             len(f.lost_signals) if f else 0)
            rep.outcomes.append(o)
            rep.blocks += 1
            rep.regretted += o.regretted
            if verdict in (KEEP, VERIFY):
                warned_here = True
                rep.warned += 1
                rep.tp_warned += o.regretted
                if o.regretted and lead is not None:
                    rep.leads.append(lead)
            if verdict == KEEP:
                rep.keep += 1
                rep.tp_keep += o.regretted
        rep.commits_warned += warned_here
    rep.seconds = time.monotonic() - started
    return rep


def _removal_blocks(c: _Commit):
    out = []
    same_commit_added = c.all_added
    for f in c.files:
        # Source code only: tests, docs and data are not fences.
        if (f.old_path is None or f.binary or plugin_for(f.old_path) is None
                or TEST_PATH.search(f.old_path)):
            continue
        for b in f.blocks:
            toks = tokens_for(f.old_path, "\n".join(t for _, t in b.removed))
            if len(toks) < MIN_TOKENS:
                continue
            sh = shingles(toks)
            if containment(sh, same_commit_added) >= RESTORE_CONTAINMENT:
                continue  # moved or rewritten in place, not removed
            out.append((f.old_path, b, sh))
    return out
