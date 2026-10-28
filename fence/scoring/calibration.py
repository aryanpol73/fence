"""Outcome calibration: Change Risk -> observed regret rate, from backtest data.

The table is fitted offline with isotonic regression (pool-adjacent-violators) on
backtest outcomes and shipped as static data (calibration.json). Nothing is
computed at check time, so results stay deterministic. Bins are merged until each
holds at least MIN_BIN blocks and MIN_POSITIVE regrets (except a trailing all-zero
bin), so no rate rests on a handful of cases. Equal neighbouring rates are merged,
which makes rates strictly increasing with risk.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).with_name("calibration.json")
MIN_BIN = 30
MIN_POSITIVE = 2


@dataclass(frozen=True)
class Bin:
    risk_min: int
    risk_max: int
    n: int
    regretted: int

    @property
    def rate(self) -> float:
        return self.regretted / self.n if self.n else 0.0


def _merge(a: Bin, b: Bin) -> Bin:
    return Bin(a.risk_min, b.risk_max, a.n + b.n, a.regretted + b.regretted)


def _pool(bins: list[Bin]) -> list[Bin]:
    """Pool adjacent violators: merge until rates strictly increase."""
    out: list[Bin] = []
    for b in bins:
        out.append(b)
        while len(out) >= 2 and out[-2].rate >= out[-1].rate:
            hi, lo = out.pop(), out.pop()
            out.append(_merge(lo, hi))
    return out


def fit(points: Iterable[tuple[int, bool]]) -> list[Bin]:
    """Isotonic fit of P(regret) over integer Change Risk, as merged bins."""
    by_risk: dict[int, list[int]] = {}
    for risk, regretted in points:
        c = by_risk.setdefault(int(risk), [0, 0])
        c[0] += 1
        c[1] += int(bool(regretted))
    bins = _pool([Bin(r, r, n, k) for r, (n, k) in sorted(by_risk.items())])
    # Minimum support: grow each bin until it has MIN_BIN blocks and MIN_POSITIVE regrets.
    supported: list[Bin] = []
    cur: Bin | None = None
    for b in bins:
        cur = b if cur is None else _merge(cur, b)
        if cur.n >= MIN_BIN and cur.regretted >= MIN_POSITIVE:
            supported.append(cur)
            cur = None
    if cur is not None:
        if supported:
            supported[-1] = _merge(supported[-1], cur)
        else:
            supported.append(cur)
    out = _pool(supported)
    # Cover 0-100 with no gaps: each bin runs up to the next bin's start.
    for i, b in enumerate(out):
        lo = 0 if i == 0 else b.risk_min
        hi = 100 if i == len(out) - 1 else out[i + 1].risk_min - 1
        out[i] = Bin(lo, hi, b.n, b.regretted)
    return out


def write(bins: list[Bin], path: Path, provenance: dict) -> None:
    data = {"provenance": provenance,
            "bins": [{"risk_min": b.risk_min, "risk_max": b.risk_max, "n": b.n,
                      "regretted": b.regretted, "rate": round(b.rate, 4)} for b in bins]}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@lru_cache(maxsize=4)
def load(path: str = str(TABLE)) -> tuple[tuple[Bin, ...], dict]:
    p = Path(path)
    if not p.is_file():
        return (), {}
    data = json.loads(p.read_text(encoding="utf-8"))
    bins = tuple(Bin(b["risk_min"], b["risk_max"], b["n"], b["regretted"])
                 for b in data.get("bins", []))
    return bins, data.get("provenance", {})


def lookup(risk: int) -> Bin | None:
    """The calibration bin for a Change Risk value, or None if no table is shipped."""
    bins, _ = load()
    for b in bins:
        if b.risk_min <= risk <= b.risk_max:
            return b
    return None


def main(argv: list[str] | None = None) -> None:
    """python -m fence.scoring.calibration OUT.json DUMP.jsonl... (backtest --dump files)."""
    import sys
    from datetime import date

    from fence import __version__

    args = argv if argv is not None else sys.argv[1:]
    out, dumps = Path(args[0]), [Path(a) for a in args[1:]]
    points = []
    for d in dumps:
        for line in d.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            points.append((r["risk"], r["regretted"]))
    bins = fit(points)
    write(bins, out, {"fitted_on": [d.stem for d in dumps], "blocks": len(points),
                      "regretted": sum(k for _, k in points), "date": date.today().isoformat(),
                      "fence_version": __version__, "method": "isotonic (PAV), min support "
                      f"{MIN_BIN} blocks / {MIN_POSITIVE} regrets per bin"})
    for b in bins:
        print(f"risk {b.risk_min:>3}-{b.risk_max:<3} n={b.n:<5} regretted={b.regretted:<3} "
              f"rate={b.rate:.3f}")


if __name__ == "__main__":
    main()
