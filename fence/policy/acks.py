"""The acknowledgement ledger: "I know why this exists; proceed."

A JSONL file committed to the repo, append-only, so acks are shared, reviewed in
PRs and auditable with plain git. An ack applies to one decision AND the exact code
it was given for (a fingerprint). If the code materially changes, or the decision
id no longer matches, the ack silently stops applying: an old "ok" must not cover
new code.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fence.text import clean

MAX_LEDGER_BYTES = 5_000_000
MAX_REASON = 500


class AckError(Exception):
    pass


@dataclass(frozen=True)
class Ack:
    decision_id: str
    path: str
    fingerprint: str
    reason: str
    who: str
    when: str  # ISO 8601 UTC
    expires: str | None = None
    v: int = 1

    def expired(self, now: datetime) -> bool:
        return bool(self.expires) and datetime.fromisoformat(self.expires) <= now


def load(ledger: Path) -> tuple[list[Ack], list[str]]:
    """Return (acks, problems). Malformed lines are reported, never fatal."""
    if not ledger.is_file():
        return [], []
    if ledger.stat().st_size > MAX_LEDGER_BYTES:
        return [], [f"{ledger} is larger than 5 MB; ignoring it"]
    acks: list[Ack] = []
    problems: list[str] = []
    for n, raw in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
            acks.append(Ack(**{k: d[k] for k in Ack.__dataclass_fields__ if k in d}))
        except (ValueError, TypeError, KeyError) as e:
            problems.append(f"{ledger.name}:{n}: unreadable ack ({e.__class__.__name__})")
    return acks, problems


def find_valid(
    acks: list[Ack], decision_id: str, fingerprint: str, now: datetime | None = None
) -> Ack | None:
    now = now or datetime.now(UTC)
    for a in reversed(acks):  # newest wins
        if a.decision_id == decision_id and a.fingerprint == fingerprint and not a.expired(now):
            return a
    return None


def append(
    ledger: Path, *, decision_id: str, path: str, fingerprint: str, reason: str, who: str,
    expiry_days: int | None, now: datetime | None = None,
) -> Ack:
    reason = clean(reason.strip(), max_len=MAX_REASON)
    if len(reason) < 8:
        raise AckError("give a reason of at least a few words (it is what the team will read)")
    now = now or datetime.now(UTC)
    ack = Ack(
        decision_id=decision_id, path=path, fingerprint=fingerprint, reason=reason,
        who=clean(who, max_len=120), when=now.replace(microsecond=0).isoformat(),
        expires=((now + timedelta(days=expiry_days)).replace(microsecond=0).isoformat()
                 if expiry_days else None),
    )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(asdict(ack), sort_keys=True) + "\n")
    return ack
