"""One positive and one negative example per signal. A new signal without cases fails
test_every_signal_has_cases."""

import pytest

from fence.signals.catalog import TEXT_SIGNALS, extract_text
from fence.signals.code import CODE_SIGNALS, match_code

TEXT_CASES: dict[str, tuple[str, str, str]] = {
    # id: (text that fires, expected verbatim detail, text that must not fire)
    "text.incident": ("Hotpatch after the March outage", "outage", "Add checkout page"),
    "text.security": ("Escape names to block XSS", "XSS", "Add avatar upload"),
    "text.data_integrity": ("Avoid duplicate charges", "duplicate", "Add charge endpoint"),
    "text.concurrency": ("Fix race condition in cache", "race condition", "Add cache"),
    "text.hotfix": ("hotfix: null user", "hotfix", "fix: null user"),
    "text.regression": ("Fix regression from #3", "regression", "Add regressor model"),
    "text.do_not_remove": ("The check is needed because x", "needed because", "Remove check"),
    "text.fix": ("Fix crash on save", "Fix", "Add save button"),
    "text.leak": ("Plug memory leak in pool", "memory leak", "Add pool"),
    "text.workaround": ("Workaround for Safari bug", "Workaround", "Add Safari support"),
    "text.compat": ("Backport to 1.x", "Backport", "Port to Rust"),
    "text.dependency_pin": ("Pin to 2.3 until upstream fix", "Pin to", "Add pin entry form"),
    "text.performance": ("Reduce latency of search", "latency", "Add search"),
    "text.rollback": ("Roll back the cache change", "Roll back", "Add rolling log"),
    "text.closes_issue": ("Closes #42", "#42", "See #42"),
    "text.issue_ref": ("See #42 for context", "#42", "Use C# here"),
    "text.ticket": ("PAY-1234 retry", "PAY-1234", "pay-1234 is lower case"),
    "text.incident_url": ("See https://status.acme.io/incidents/9", "https://status.acme.io/incidents/9",
                          "See https://acme.io/docs"),
    "text.formatting": ("Reformat with black", "Reformat", "Format the date as ISO"),
    "text.refactor": ("Refactor the parser", "Refactor", "Add parser"),
    "text.automated": ("Bump lodash from 4.17.20 to 4.17.21", "Bump lodash from", "Add lodash"),
}

CODE_CASES: dict[str, tuple[str, str]] = {
    "code.warning_comment": ("    # WARNING: do not reorder, see #12", "    x = warn()"),
    "code.hack_comment": ("// HACK: Safari needs this", "// handles Safari"),
    "code.guard": ("    if user is None:", "    user = load()"),
    "code.exception_handling": ("    except TimeoutError:", "    raise_count = 0"),
    "code.lock": ("    with self._lock:", "    clock = now()"),
    "code.retry_timeout": ("    time.sleep(2)", "    sleepy = True"),
    "code.feature_flag": ("if flags.new_checkout:", "if checkout:"),
    "code.assertion": ("    assert total >= 0", "    total = 0"),
}


def test_every_signal_has_cases() -> None:
    assert {s.id for s in TEXT_SIGNALS} == set(TEXT_CASES)
    assert {s.id for s in CODE_SIGNALS} == set(CODE_CASES)


@pytest.mark.parametrize("sid", sorted(TEXT_CASES))
def test_text_signal(sid: str) -> None:
    fires, detail, quiet = TEXT_CASES[sid]
    assert (sid, detail) in extract_text(fires)
    assert sid not in {s for s, _ in extract_text(quiet)}


@pytest.mark.parametrize("sid", sorted(CODE_CASES))
def test_code_signal(sid: str) -> None:
    fires, quiet = CODE_CASES[sid]
    assert sid in {s for s, _ in match_code([fires])}
    assert sid not in {s for s, _ in match_code([quiet])}


def test_closing_ref_is_not_double_counted() -> None:
    found = extract_text("Fixes #9")
    assert ("text.closes_issue", "#9") in found
    assert ("text.issue_ref", "#9") not in found


def test_skip_refs_hides_the_pr_number() -> None:
    assert ("text.issue_ref", "#5") not in extract_text("Add x (#5)", skip_refs=frozenset({"#5"}))


def test_extraction_is_deterministic() -> None:
    text = "Fix race in cache after outage, closes #3 (see PAY-9)"
    assert extract_text(text) == extract_text(text)


def test_details_are_verbatim_and_capped() -> None:
    long = "See https://status.example.com/" + "x" * 500
    ((_, detail),) = [f for f in extract_text(long) if f[0] == "text.incident_url"]
    assert detail.startswith("https://status.example.com/") and len(detail) <= 120
