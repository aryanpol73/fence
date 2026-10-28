"""Contract tests for the GitHub layer, using response shapes recorded from the REST API
(fields trimmed to what Fence reads). No network."""

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from fence.context import Context
from fence.decisions.build import why
from fence.github.client import Client, GithubError, RateLimited
from fence.github.sync import sync
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo

PR = {
    "url": "https://api.github.com/repos/acme/pay/pulls/7", "number": 7, "state": "closed",
    "title": "Wait before retrying charges",
    "body": "Retrying immediately caused duplicate charges during the March outage.\n\n"
            "Closes #3. We must not retry without the idempotency key.",
    "user": {"login": "rahul", "id": 1}, "labels": [{"id": 1, "name": "bug"},
                                                    {"id": 2, "name": "incident"}],
    "merged_at": "2026-04-14T11:00:00Z", "merge_commit_sha": "a" * 40,
    "html_url": "https://github.com/acme/pay/pull/7",
}
REVIEWS = [{"id": 11, "user": {"login": "priya"}, "body": "", "state": "APPROVED",
            "submitted_at": "2026-04-14T10:00:00Z", "html_url": "https://github.com/x"}]
COMMENTS = [{"id": 21, "user": {"login": "sam"}, "path": "pay.py", "line": 2,
             "body": "Careful: this is needed because the gateway times out under load.",
             "created_at": "2026-04-13T10:00:00Z", "html_url": "https://github.com/acme/pay/pull/7#r21"}]
ISSUE = {"number": 3, "title": "Duplicate charges on timeout", "state": "closed",
         "body": "Customers were charged twice.", "labels": [{"name": "bug"}],
         "closed_at": "2026-04-14T11:00:00Z", "html_url": "https://github.com/acme/pay/issues/3"}


class FakeResp(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(json.dumps(data).encode())
        self.status = status
        self.headers = Message()
        for k, v in {"X-RateLimit-Remaining": "4999", "ETag": '"e1"', **(headers or {})}.items():
            self.headers[k] = v


def fake_opener(routes: dict, log: list):
    def opener(req, timeout=None):
        log.append((req.full_url, dict(req.header_items())))
        path = req.full_url.replace("https://api.github.com", "").split("?")[0]
        if path not in routes:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", Message(), None)
        return FakeResp(routes[path])
    return opener


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret123")
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pay.py": "def charge():\n    time.sleep(2)\n"}, "Wait before retrying (#7)")
    fx.git("remote", "add", "origin", "https://github.com/acme/pay.git")
    c = Context.open(fx.root)
    index_repo(c.repo, c.store, c.cfg)
    return c


ROUTES = {"/repos/acme/pay/pulls/7": PR, "/repos/acme/pay/pulls/7/comments": COMMENTS,
          "/repos/acme/pay/pulls/7/reviews": REVIEWS, "/repos/acme/pay/issues/3": ISSUE}


def test_sync_then_why_reaches_grade_a(ctx: Context) -> None:
    log: list = []
    client = Client("https://api.github.com", "ghp_secret123", ctx.store,
                    opener=fake_opener(ROUTES, log))
    rep = sync(ctx, client=client)
    assert (rep.prs, rep.issues, rep.stopped) == (1, 1, None)
    assert all(h.get("Authorization") == "Bearer ghp_secret123" for _, h in log)
    (d,) = why(ctx, "pay.py", 2).decisions
    assert d.grade == "A"
    assert d.stated_reason.startswith("Retrying immediately caused duplicate charges")
    assert d.type == "incident"  # from text + the "incident" label
    assert "priya" in d.authors  # reviewer counts as a person who touched it
    assert any(e.type == "issue" and e.retrieval_status == "ok" for e in d.evidence)
    assert any("must not retry" in c for c in d.constraints)
    # Resumable: nothing left to fetch on a second run.
    assert sync(ctx, client=Client("https://api.github.com", "t", ctx.store,
                                   opener=fake_opener(ROUTES, []))).prs == 0


def test_missing_pr_is_marked_unreachable(ctx: Context) -> None:
    client = Client("https://api.github.com", "t", ctx.store, opener=fake_opener({}, []))
    assert sync(ctx, client=client).unreachable == 1
    (d,) = why(ctx, "pay.py", 2).decisions
    assert any(e.type == "pr" and e.retrieval_status == "unreachable" for e in d.evidence)


def test_etag_304_uses_cache(ctx: Context) -> None:
    calls = []

    def opener(req, timeout=None):
        calls.append(dict(req.header_items()))
        if len(calls) == 1:
            return FakeResp({"ok": 1})
        raise urllib.error.HTTPError(req.full_url, 304, "nm", Message(), None)

    c = Client("https://api.github.com", None, ctx.store, opener=opener)
    assert c.get("/x").data == {"ok": 1}
    assert c.get("/x").data == {"ok": 1}
    assert calls[1].get("If-none-match") == '"e1"'


def test_rate_limit_stops_and_token_is_redacted(ctx: Context) -> None:
    def limited(req, timeout=None):
        h = Message()
        h["X-RateLimit-Remaining"] = "0"
        raise urllib.error.HTTPError(req.full_url, 403, "rl", h, None)

    with pytest.raises(RateLimited):
        Client("https://api.github.com", "t", ctx.store, opener=limited).get("/x")

    def down(req, timeout=None):
        raise urllib.error.URLError("bad ghp_secret123 host")

    with pytest.raises(GithubError) as e:
        Client("https://api.github.com", "ghp_secret123", ctx.store, opener=down).get("/x")
    assert "ghp_secret123" not in str(e.value)


def test_token_never_sent_to_other_hosts(ctx: Context) -> None:
    c = Client("https://api.github.com", "t", ctx.store, opener=fake_opener({}, []))
    with pytest.raises(GithubError, match="another host"):
        c.get("https://evil.example.com/steal")


def test_pagination_follows_link_header(ctx: Context) -> None:
    pages = {1: [1, 2], 2: [3]}

    def opener(req, timeout=None):
        n = 2 if "page=2" in req.full_url else 1
        nxt = {"Link": '<https://api.github.com/items?per_page=100&page=2>; rel="next"'} \
            if n == 1 else {}
        return FakeResp(pages[n], headers=nxt)

    c = Client("https://api.github.com", None, ctx.store, opener=opener)
    assert c.paginate("/items") == [1, 2, 3]
