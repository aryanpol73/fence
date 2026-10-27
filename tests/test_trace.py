"""Trace logic on the seeded demo history, with removed ranges built by hand.

The parser-driven version of these checks (cleanup.diff -> CLI) lives in
tests/test_integration.py; these pin the git side independently of diff parsing.
"""

from pathlib import Path

import pytest

from fence.diffing.ranges import RemovedRange
from fence.git.runner import Repo
from fence.text import clean
from fence.trace import PreimageMismatch, trace

IMPORT_TIME = RemovedRange("payments/client.py", 3, 3, ("import time",))
SLEEP = RemovedRange("payments/client.py", 22, 22, ("            time.sleep(2)",))
LOG_LINE = RemovedRange(
    "payments/client.py",
    19,
    19,
    ('            log.warning("charge timed out, attempt %d", attempt + 1)',),
)
REFUND = RemovedRange(
    "payments/client.py",
    25,
    26,
    (
        "def refund(gateway, charge_id, amount_cents):",
        "    return gateway.create_refund(charge_id, amount_cents)",
    ),
)


def _repo(d: dict[str, str]) -> Repo:
    return Repo.open(Path(d["repo"]))


def test_removed_lines_trace_to_the_fence_commit(demo: dict[str, str]) -> None:
    results = trace(_repo(demo), "main", [IMPORT_TIME, SLEEP])
    assert [r.introducing[0].sha for r in results] == [demo["fence"], demo["fence"]]
    assert results[0].symbol.qualname == "<module>"
    assert results[1].symbol.qualname == "charge"
    assert results[1].introducing[0].summary.startswith("Wait 2s before retrying")


def test_without_ignore_revs_w_still_sees_through_reindent(demo_no_ignore: dict[str, str]) -> None:
    # The formatter changed only leading whitespace on the sleep line (2 -> 4 spaces
    # per level). `git blame -w` ignores whitespace-only changes, so the fence commit
    # is found even though the formatter commit is not in .git-blame-ignore-revs.
    (result,) = trace(_repo(demo_no_ignore), "main", [SLEEP])
    assert result.introducing[0].sha == demo_no_ignore["fence"]


def test_without_ignore_revs_w_does_not_see_through_quote_changes(
    demo_no_ignore: dict[str, str], demo: dict[str, str]
) -> None:
    # Documented limit: the formatter also changed '...' to "..." on the log line.
    # That is not whitespace, so `-w` credits the formatter. Only listing the
    # formatter commit in .git-blame-ignore-revs (read from base) fixes this.
    (unlisted,) = trace(_repo(demo_no_ignore), "main", [LOG_LINE])
    assert unlisted.introducing[0].sha == demo_no_ignore["format"]
    (listed,) = trace(_repo(demo), "main", [LOG_LINE])
    assert listed.introducing[0].sha == demo["initial"]


def test_harmless_lines_do_not_trace_to_the_fence_commit(demo: dict[str, str]) -> None:
    (result,) = trace(_repo(demo), "main", [REFUND])
    shas = {bl.sha for bl in result.introducing}
    assert shas == {demo["refund"]}
    assert demo["fence"] not in shas
    assert result.symbol.qualname == "refund"


def test_wrong_base_raises_preimage_mismatch(demo: dict[str, str]) -> None:
    # At the initial commit the file is 2-space indented and has no sleep line.
    with pytest.raises(PreimageMismatch, match="different base"):
        trace(_repo(demo), demo["initial"], [SLEEP])


def test_range_past_end_of_base_file_is_mismatch(demo: dict[str, str]) -> None:
    with pytest.raises(PreimageMismatch):
        trace(_repo(demo), "main", [RemovedRange("payments/client.py", 900, 900, ("x",))])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("evil\x1b[2Jtext", "evil�[2Jtext"),
        ("a\x00b\x07c", "abc"),
        ("bidi‮esrever", "bidiesrever"),
        ("zero​width", "zerowidth"),
        ("tab\there", "tab    here"),
        ("line\nbreak", "linebreak"),
    ],
)
def test_clean(raw: str, expected: str) -> None:
    assert clean(raw) == expected


def test_clean_caps_length() -> None:
    assert len(clean("x" * 1000, max_len=10)) == 10
