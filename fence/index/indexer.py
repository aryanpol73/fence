"""Incremental history indexing.

Each run indexes only commits not yet stored. If a stored ref tip is no longer an
ancestor of the current tip (force-push, rebase), the reachable set is recomputed
and commits that fell out of history are marked orphaned, not deleted: decisions
that cite them must say their evidence is unreachable.

Refs are saved only after every batch has landed, so an interrupted run simply
resumes: the next run walks the same range and skips stored commits.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fence.config import Config
from fence.git.history import LogEntry, iter_log
from fence.git.runner import GitError, Repo
from fence.globs import included
from fence.index.store import CommitRow, FileChange, Store
from fence.signals.catalog import extract_text

_MERGE_PR = re.compile(r"^Merge pull request #(\d+) from ")
_SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
_REVERT = re.compile(r"This reverts commit ([0-9a-f]{40})")

FormatDetector = Callable[[Repo, list[LogEntry]], set[str]]  # -> format-only SHAs
Progress = Callable[[int], None]


@dataclass
class IndexReport:
    indexed: int = 0
    skipped_existing: int = 0
    orphaned: int = 0
    rewrite_detected: bool = False
    shallow: bool = False
    empty: bool = False
    total: int = 0
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


def pr_from_subject(subject: str) -> tuple[int | None, str | None]:
    if m := _MERGE_PR.match(subject):
        return int(m.group(1)), "merge-subject"
    if m := _SQUASH_PR.search(subject):
        return int(m.group(1)), "squash-subject"
    return None, None


def reverted_sha(body: str) -> str | None:
    m = _REVERT.search(body)
    return m.group(1) if m else None


def resolve_tips(repo: Repo, refs: list[str]) -> dict[str, str]:
    tips: dict[str, str] = {}
    for ref in refs:
        try:
            tips[ref] = repo.verify_ref(ref)
        except GitError:
            continue  # a missing ref (e.g. empty repo) is reported by the caller
    return tips


def is_ancestor(repo: Repo, old: str, new: str) -> bool:
    try:
        repo.run(["merge-base", "--is-ancestor", old, new])
        return True
    except GitError:
        return False


def commit_exists(repo: Repo, sha: str) -> bool:
    try:
        repo.run(["cat-file", "-e", f"{sha}^{{commit}}"])
        return True
    except GitError:
        return False


def index_repo(
    repo: Repo,
    store: Store,
    cfg: Config,
    *,
    detect_format: FormatDetector | None = None,
    progress: Progress | None = None,
) -> IndexReport:
    started = time.monotonic()
    report = IndexReport()
    report.shallow = repo.run(["rev-parse", "--is-shallow-repository"]).strip() == "true"
    if report.shallow:
        report.notes.append("shallow clone: history before the shallow boundary is not visible")

    tips = resolve_tips(repo, cfg.index.refs)
    if not tips:
        report.empty = True
        report.notes.append("no commits found for " + ", ".join(cfg.index.refs))
        return report

    old_refs = store.refs()
    exclude_old: list[str] = []
    for name, new in tips.items():
        old = old_refs.get(name)
        if not old or old == new:
            if old:
                exclude_old.append(old)
            continue
        if commit_exists(repo, old) and is_ancestor(repo, old, new):
            exclude_old.append(old)
        else:
            report.rewrite_detected = True

    if report.rewrite_detected:
        reachable = repo.run(["rev-list", *tips.values()]).split()
        with store.transaction():
            report.orphaned = store.mark_orphans(reachable)
        report.notes.append(
            f"history was rewritten (force-push or rebase); {report.orphaned} commits orphaned"
        )

    extra: list[str] = []
    if cfg.index.history_depth:
        extra.append(f"--max-count={cfg.index.history_depth}")
    if cfg.index.since_date:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cfg.index.since_date):
            raise GitError(f"index.since_date must be YYYY-MM-DD, got {cfg.index.since_date!r}")
        extra.append(f"--since={cfg.index.since_date}")
    revs = [*dict.fromkeys(tips.values())]
    if exclude_old:
        revs += ["--not", *dict.fromkeys(exclude_old)]

    batch: list[LogEntry] = []
    for entry in iter_log(
        repo, revs, extra=extra, rename_threshold=cfg.index.follow_renames_sensitivity,
        timeout=cfg.performance.log_timeout_seconds,
    ):
        if store.has_commit(entry.sha):
            report.skipped_existing += 1
            continue
        batch.append(entry)
        if len(batch) >= cfg.index.batch_size:
            _write_batch(repo, store, cfg, batch, detect_format)
            report.indexed += len(batch)
            batch = []
            if progress:
                progress(report.indexed)
    if batch:
        _write_batch(repo, store, cfg, batch, detect_format)
        report.indexed += len(batch)
        if progress:
            progress(report.indexed)

    with store.transaction():
        for name, sha in tips.items():
            store.set_ref(name, sha)
        store.set_meta("last_indexed_at", str(int(time.time())))
        store.set_meta("shallow", "1" if report.shallow else "0")
        if report.indexed or report.rewrite_detected:
            store.clear_query_cache()
    report.total = store.commit_count()
    report.seconds = round(time.monotonic() - started, 3)
    return report


def _write_batch(
    repo: Repo, store: Store, cfg: Config, batch: list[LogEntry],
    detect_format: FormatDetector | None,
) -> None:
    rows: list[CommitRow] = []
    changes: list[FileChange] = []
    signals: list[tuple[str, str, str]] = []
    reverts: list[tuple[str, str]] = []
    fmt_shas = detect_format(repo, batch) if detect_format else set()
    for e in batch:
        pr, pr_source = pr_from_subject(e.subject)
        kept = [
            c for c in e.changes
            if included(c.path, cfg.index.include, cfg.index.exclude)
            or (c.old_path and included(c.old_path, cfg.index.include, cfg.index.exclude))
        ]
        format_only = bool(kept) and e.sha in fmt_shas
        rows.append(
            CommitRow(
                sha=e.sha, parents=e.parents, author_name=e.author_name,
                author_email=e.author_email, author_time=e.author_time,
                committer_time=e.committer_time, subject=e.subject, body=e.body,
                pr_number=pr, pr_source=pr_source, files_changed=len(e.changes),
                format_only=format_only, bulk=len(e.changes) >= cfg.index.bulk_commit_files,
            )
        )
        changes.extend(
            FileChange(e.sha, c.path, c.old_path, c.status, c.similarity, c.added, c.deleted)
            for c in kept
        )
        skip = frozenset({f"#{pr}"}) if pr else frozenset()
        signals.extend((e.sha, sid, detail) for sid, detail in extract_text(
            f"{e.subject}\n\n{e.body}", skip_refs=skip
        ))
        if target := reverted_sha(e.body):
            reverts.append((e.sha, target))
    with store.transaction():
        store.put_commits(rows, changes)
        store.put_signals(signals)
        for sha, target in reverts:
            store.put_revert(sha, target)
