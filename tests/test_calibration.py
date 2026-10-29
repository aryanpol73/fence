import json
from pathlib import Path

from fence.scoring import calibration
from fence.scoring.calibration import MIN_BIN, MIN_POSITIVE, fit, load


def _points(spec: list[tuple[int, int, int]]) -> list[tuple[int, bool]]:
    pts = []
    for risk, n, k in spec:
        pts += [(risk, True)] * k + [(risk, False)] * (n - k)
    return pts


def test_fit_is_strictly_monotonic_and_covers_range() -> None:
    # A non-monotonic input (risk 40 regrets more than risk 60) must be pooled.
    bins = fit(_points([(0, 200, 2), (20, 100, 2), (40, 60, 6), (60, 50, 2), (80, 40, 8)]))
    rates = [b.rate for b in bins]
    assert all(a < b for a, b in zip(rates, rates[1:], strict=False))
    assert bins[0].risk_min == 0 and bins[-1].risk_max == 100
    assert all(b.risk_max + 1 == c.risk_min for b, c in zip(bins, bins[1:], strict=False))
    assert sum(b.n for b in bins) == 450


def test_fit_enforces_minimum_support() -> None:
    bins = fit(_points([(10, 5, 1), (50, 5, 1), (90, 400, 20)]))
    assert all(b.n >= MIN_BIN and b.regretted >= MIN_POSITIVE for b in bins)


def test_fit_with_no_regrets_is_one_flat_bin() -> None:
    (b,) = fit(_points([(10, 100, 0), (70, 50, 0)]))
    assert (b.risk_min, b.risk_max, b.rate) == (0, 100, 0.0)


def test_no_table_ships_so_findings_carry_no_probability() -> None:
    # The pooled backtest did not support a regret-rate table (DECISIONS.md, 2026-10-29).
    assert load() == ((), {})
    assert calibration.lookup(0) is None and calibration.lookup(100) is None


def test_main_writes_table(tmp_path: Path) -> None:
    dump = tmp_path / "x.jsonl"
    dump.write_text("".join(json.dumps({"risk": r, "regretted": k}) + "\n"
                            for r, k in _points([(10, 60, 2), (80, 40, 10)])))
    out = tmp_path / "cal.json"
    calibration.main([str(out), str(dump)])
    data = json.loads(out.read_text())
    assert data["provenance"]["blocks"] == 100 and len(data["bins"]) == 2
