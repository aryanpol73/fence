"""Minimal GitHub REST client: ETag revalidation, rate-limit respect, pagination.

Stdlib only. The token is sent only to the configured https base URL and is
never logged: every error message passes through `_redact`.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from fence.index.store import Store

MAX_BODY = 2_000_000
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class GithubError(Exception):
    pass


class RateLimited(GithubError):
    pass


class Unreachable(GithubError):
    """A network failure (DNS, connect, reset, timeout), not an HTTP answer."""


@dataclass
class Response:
    status: int
    data: object
    next_url: str | None


class Client:
    def __init__(self, base_url: str, token: str | None, store: Store, *, min_remaining: int = 50,
                 policy: str = "stop", max_requests: int = 2000, timeout: float = 20.0,
                 opener=None, retry_delays: tuple[float, ...] = (2.0, 5.0)) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.store = store
        self.min_remaining = min_remaining
        self.policy = policy
        self.max_requests = max_requests
        self.timeout = timeout
        self.requests = 0
        self.remaining: int | None = None
        self._open = opener or urllib.request.urlopen
        # A sync makes hundreds of sequential requests; one dropped connection should not
        # end it. HTTP errors are answers, so only network failures are retried.
        self.retry_delays = retry_delays

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def get(self, path_or_url: str) -> Response:
        url = path_or_url if path_or_url.startswith(self.base) else self.base + path_or_url
        if not url.startswith(self.base + "/"):
            raise GithubError("refusing to send the token to another host")
        if self.requests >= self.max_requests:
            raise RateLimited(f"request budget of {self.max_requests} per run used up")
        if self.remaining is not None and self.remaining <= self.min_remaining:
            raise RateLimited(f"GitHub rate limit nearly exhausted ({self.remaining} left)")
        cached = self.store.conn.execute(
            "SELECT etag, body FROM http_cache WHERE url = ?", (url,)).fetchone()
        req = urllib.request.Request(url, headers={  # noqa: S310 - base is validated https
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "fence",
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            **({"If-None-Match": cached["etag"]} if cached and cached["etag"] else {}),
        })
        self.requests += 1
        for delay in self.retry_delays:
            try:
                return self._send(req, url, cached)
            except Unreachable:
                time.sleep(delay)
        return self._send(req, url, cached)

    def _send(self, req: urllib.request.Request, url: str, cached) -> Response:
        try:
            with self._open(req, timeout=self.timeout) as resp:  # noqa: S310 - https only
                self._note_limits(resp.headers)
                body = resp.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise GithubError(f"response too large: {url}")
                etag = resp.headers.get("ETag")
                text = body.decode("utf-8", "replace")
                with self.store.transaction():
                    self.store.conn.execute(
                        "INSERT OR REPLACE INTO http_cache VALUES(?,?,?,?)",
                        (url, etag, text, int(time.time())))
                return Response(resp.status, json.loads(text), _next(resp.headers))
        except urllib.error.HTTPError as e:
            self._note_limits(e.headers)
            if e.code == 304 and cached:
                return Response(200, json.loads(cached["body"]), _next(e.headers))
            if e.code in (403, 429) and (e.headers.get("X-RateLimit-Remaining") == "0"
                                         or e.code == 429):
                if self.policy == "wait":
                    reset = int(e.headers.get("X-RateLimit-Reset") or 0)
                    delay = max(1, min(900, reset - int(time.time())))
                    time.sleep(delay)
                    return self.get(url)
                raise RateLimited("GitHub rate limit reached; re-run later to resume") from None
            return Response(e.code, None, None)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Unreachable(self._redact(f"GitHub unreachable: {e}")) from None
        except ValueError:
            raise GithubError(f"invalid JSON from {url}") from None

    def paginate(self, path: str, limit: int = 300) -> list:
        out: list = []
        url: str | None = path + ("&" if "?" in path else "?") + "per_page=100"
        while url and len(out) < limit:
            r = self.get(url)
            if r.status != 200 or not isinstance(r.data, list):
                break
            out += r.data
            url = r.next_url
        return out[:limit]

    def _note_limits(self, headers) -> None:
        rem = headers.get("X-RateLimit-Remaining") if headers else None
        if rem and rem.isdigit():
            self.remaining = int(rem)


def _next(headers) -> str | None:
    m = _LINK_NEXT.search(headers.get("Link", "") if headers else "")
    return m.group(1) if m else None
