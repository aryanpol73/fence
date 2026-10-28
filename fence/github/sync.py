"""Fetch PRs, linked issues and review comments for indexed commits.

Resumable: every item is committed as soon as it is fetched, and already-stored
PRs are skipped, so an interrupted or rate-limited run continues next time.
Text is stored verbatim and stays untrusted.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from fence.context import Context
from fence.github.client import Client, GithubError, RateLimited
from fence.signals.catalog import extract_text


@dataclass
class SyncReport:
    prs: int = 0
    issues: int = 0
    review_comments: int = 0
    unreachable: int = 0
    requests: int = 0
    stopped: str | None = None
    notes: list[str] = field(default_factory=list)


def sync(ctx: Context, *, client: Client | None = None, max_prs: int = 500) -> SyncReport:
    rep = SyncReport()
    cfg = ctx.cfg.github
    if cfg.enabled == "off" or not ctx.slug:
        rep.notes.append("GitHub sync skipped (disabled or no GitHub remote)")
        return rep
    client = client or Client(cfg.api_base_url, ctx.github_token, ctx.store,
                              min_remaining=cfg.min_remaining, policy=cfg.rate_limit_policy,
                              max_requests=cfg.max_requests_per_run)
    owner, name = ctx.slug
    base = f"/repos/{owner}/{name}"
    min_time = int(time.time()) - cfg.max_age_days * 86400 if cfg.max_age_days else 0
    todo = [r["pr_number"] for r in ctx.store.conn.execute(
        "SELECT DISTINCT c.pr_number FROM commits c LEFT JOIN prs p ON p.number = c.pr_number "
        "WHERE c.pr_number IS NOT NULL AND p.number IS NULL AND c.orphaned = 0 "
        "AND c.committer_time >= ? ORDER BY c.committer_time DESC LIMIT ?",
        (min_time, max_prs))]
    try:
        for n in todo if cfg.sync_prs else []:
            _sync_pr(ctx, client, base, n, rep)
    except RateLimited as e:
        rep.stopped = str(e)
    except GithubError as e:
        rep.stopped = str(e)
    rep.requests = client.requests
    return rep


def _sync_pr(ctx: Context, client: Client, base: str, n: int, rep: SyncReport) -> None:
    r = client.get(f"{base}/pulls/{n}")
    if r.status != 200 or not isinstance(r.data, dict):
        with ctx.store.transaction():
            ctx.store.conn.execute(
                "INSERT OR REPLACE INTO prs(number, state, fetched_at) VALUES(?, 'unreachable', ?)",
                (n, int(time.time())))
        rep.unreachable += 1
        return
    d = r.data
    labels = [lb.get("name", "") for lb in d.get("labels") or [] if isinstance(lb, dict)]
    reviewers: set[str] = set()
    comments = []
    if ctx.cfg.github.sync_review_comments:
        for c in client.paginate(f"{base}/pulls/{n}/comments", 200):
            comments.append(c)
        for rv in client.paginate(f"{base}/pulls/{n}/reviews", 100):
            if isinstance(rv, dict) and rv.get("user"):
                reviewers.add(rv["user"].get("login", ""))
                if rv.get("body"):
                    comments.append({**rv, "path": None, "line": None})
    with ctx.store.transaction():
        ctx.store.conn.execute(
            "INSERT OR REPLACE INTO prs VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (n, d.get("title"), d.get("body") or "", json.dumps(labels), d.get("state"),
             d.get("merged_at"), d.get("merge_commit_sha"), d.get("html_url"),
             (d.get("user") or {}).get("login"), json.dumps(sorted(reviewers)),
             int(time.time())))
        for c in comments:
            if not isinstance(c, dict) or "id" not in c:
                continue
            ctx.store.conn.execute(
                "INSERT OR REPLACE INTO review_comments VALUES(?,?,?,?,?,?,?,?)",
                (c["id"], n, c.get("path"), c.get("line"), c.get("body") or "",
                 (c.get("user") or {}).get("login"), c.get("html_url"),
                 c.get("created_at") or c.get("submitted_at")))
    rep.prs += 1
    rep.review_comments += len(comments)
    if ctx.cfg.github.sync_issues:
        refs = {d for s, d in extract_text(d.get("body") or "")
                if s in ("text.closes_issue", "text.issue_ref") and d.startswith("#")}
        for ref in sorted(refs)[:10]:
            _sync_issue(ctx, client, base, int(ref[1:]), n, rep)


def _sync_issue(ctx: Context, client: Client, base: str, n: int, pr: int,
                rep: SyncReport) -> None:
    if ctx.store.conn.execute("SELECT 1 FROM issues WHERE number=?", (n,)).fetchone():
        return
    r = client.get(f"{base}/issues/{n}")
    with ctx.store.transaction():
        if r.status != 200 or not isinstance(r.data, dict):
            ctx.store.conn.execute(
                "INSERT OR REPLACE INTO issues(number, fetched_at, retrieval_status) "
                "VALUES(?, ?, 'unreachable')", (n, int(time.time())))
            rep.unreachable += 1
            return
        d = r.data
        labels = [lb.get("name", "") for lb in d.get("labels") or [] if isinstance(lb, dict)]
        ctx.store.conn.execute(
            "INSERT OR REPLACE INTO issues VALUES(?,?,?,?,?,?,?,?,'ok')",
            (n, d.get("title"), d.get("body") or "", json.dumps(labels), d.get("state"),
             d.get("closed_at"), d.get("html_url"), int(time.time())))
        ctx.store.conn.execute("INSERT OR IGNORE INTO pr_issue_links VALUES(?,?,?)",
                               (pr, n, "references"))
    rep.issues += 1
