"""One sticky PR comment, found by a hidden marker and updated in place.

Fork PRs get a read-only token: posting then fails with 403, and the caller falls
back to the job summary. Fence never asks for pull_request_target.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from fence.render import COMMENT_MARKER

MAX_COMMENT = 60_000


def upsert(api: str, repo: str, pr: int, body: str, token: str, *, opener=None) -> str:
    """Create or update the sticky comment. Returns 'created', 'updated' or 'forbidden'."""
    if not api.startswith("https://"):
        raise ValueError("api base must be https")
    opener = opener or urllib.request.urlopen
    body = body[:MAX_COMMENT]

    def call(method: str, url: str, data: dict | None = None):
        req = urllib.request.Request(  # noqa: S310 - https only, checked above
            url, method=method, data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "User-Agent": "fence", "Content-Type": "application/json"})
        with opener(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    base = f"{api.rstrip('/')}/repos/{repo}/issues"
    try:
        page = 1
        while page <= 10:
            comments = call("GET", f"{base}/{pr}/comments?per_page=100&page={page}") or []
            for c in comments:
                if COMMENT_MARKER in (c.get("body") or ""):
                    call("PATCH", f"{base}/comments/{c['id']}", {"body": body})
                    return "updated"
            if len(comments) < 100:
                break
            page += 1
        call("POST", f"{base}/{pr}/comments", {"body": body})
        return "created"
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):
            return "forbidden"
        raise
