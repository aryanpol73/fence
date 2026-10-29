"""Regression tests: change-level text must not become a line-level reason.

Root cause these pin down: one multi-file commit's reason sentence was quoted as
the reason for every file it touched, chosen by generic word overlap. Every test
file in the commit was cited as evidence, and the decision type came from
sentences about other files.
"""

from pathlib import Path

import pytest

from fence.context import Context
from fence.decisions.build import why
from fence.decisions.reasons import stated_reason
from fence.index.indexer import index_repo
from tests.gitfix import FixtureRepo

STORE = "class Store:\n    def save_record(self, rec):\n        self.rows.append(rec)\n"
CONFIG = "class Settings:\n    cache_dir = '~/.cache'\n    max_items = 10\n"
GLOBS = "def match_glob(path, pattern):\n    return path == pattern\n"
MESSAGE = (
    "Add storage, settings and globs\n\n"
    "Store keeps one file per repo in the cache dir because duplicate records corrupted "
    "the index during an outage.\n\n"
    "Settings reject unknown keys so a typo cannot silently weaken policy."
)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Context:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pkg/store.py": STORE, "pkg/settings.py": CONFIG, "pkg/globs.py": GLOBS,
               "tests/test_store.py": "def test_save():\n    pass\n",
               "tests/test_settings.py": "def test_keys():\n    pass\n",
               "tests/helpers.py": "x = 1\n"}, MESSAGE)
    c = Context.open(fx.root)
    index_repo(c.repo, c.store, c.cfg)
    return c


def _one(ctx: Context, path: str):
    (d,) = why(ctx, path).decisions
    return d


def test_each_file_gets_the_sentence_that_names_it(ctx: Context) -> None:
    store, settings = _one(ctx, "pkg/store.py"), _one(ctx, "pkg/settings.py")
    assert store.stated_reason.startswith("Store keeps one file per repo")
    assert settings.stated_reason.startswith("Settings reject unknown keys")
    assert store.reason_scope == settings.reason_scope == "code"
    # Generic words ("cache", "dir") shared with settings.py no longer pull in the
    # storage sentence.
    assert "cache dir" not in settings.stated_reason


def test_reasons_naming_other_files_are_not_borrowed(ctx: Context) -> None:
    globs = _one(ctx, "pkg/globs.py")
    assert globs.stated_reason is None and globs.change_reason is None
    assert globs.grade == "D" and globs.reason_text.startswith("Reason unknown")


def test_change_level_reason_is_labelled_inferred(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "def alpha():\n    return 1\n", "b.py": "def beta():\n    return 2\n"},
              "Split helpers\n\nThe old module was split because imports were circular.")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "a.py").decisions
    assert d.stated_reason is None and d.reason_scope == "change"
    assert d.change_reason == "The old module was split because imports were circular."
    assert d.grade == "C" and d.reason_text.startswith("Inferred: added in a change to 2")


def test_only_related_tests_are_evidence(ctx: Context) -> None:
    tests = {e.ref.split("@")[0] for e in _one(ctx, "pkg/settings.py").evidence
             if e.type == "test"}
    assert tests == {"tests/test_settings.py"}
    assert not [e for e in _one(ctx, "pkg/globs.py").evidence if e.type == "test"]


def test_type_comes_only_from_text_about_this_code(ctx: Context) -> None:
    # "duplicate records corrupted the index during an outage" names Store only.
    assert _one(ctx, "pkg/store.py").type in ("incident", "data-integrity")
    assert _one(ctx, "pkg/settings.py").type not in ("incident", "data-integrity")
    assert not {f.id for f in _one(ctx, "pkg/globs.py").strength.factors} & {
        "type.incident", "type.data-integrity", "test"}


def test_focused_change_applies_whole_message(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "def f():\n    return 1\n", "tests/test_other.py": "x = 1\n"},
              "Return one\n\nCallers break otherwise.")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "a.py").decisions
    assert d.grade == "B" and d.reason_scope == "code"
    assert [e for e in d.evidence if e.type == "test"]  # the only source file: tests apply


def test_span_label_contains_the_whole_span(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "class A:\n    x = 1\n\n\nclass B:\n    y = 2\n"}, "add")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "a.py", 2, 6).decisions
    assert d.anchors[0].symbol == "<module>"  # spans A and B, so not labelled "A"
    (d,) = why(ctx, "a.py", 6).decisions
    assert d.anchors[0].symbol == "B"


def test_purpose_clause_is_a_stated_reason() -> None:
    assert stated_reason("Reject unknown keys so a typo cannot weaken policy.")
    assert stated_reason("Reject unknown keys so it is strict.") is None


def test_symbol_names_match_with_their_exact_case(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    # A class `Repo` is not named by the prose word "repo" (fence/git/runner.py was
    # quoted the index's SQLite sentence this way).
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "class Repo:\n    pass\n", "b.py": "class Disk:\n    pass\n"},
              "Add pieces\n\nThe cache is one file per repo because it holds private content.")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "a.py").decisions
    assert d.stated_reason is None and d.reason_scope == "change"
    fx.commit({"a.py": "class Repo:\n    x = 1\n", "b.py": "class Disk:\n    x = 1\n"},
              "Tidy\n\nRepo stays a plain class because subclasses broke pickling.")
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    d = next(x for x in why(ctx, "a.py").decisions if x.stated_reason)
    assert d.stated_reason.startswith("Repo stays a plain class") and d.grade == "B"


@pytest.mark.parametrize("text", [
    # Each was missed before (fence/scoring/strength.py, fence/text.py, fence/trace.py).
    '"now" is the revision\'s commit time, so the same repo state always scores the same.',
    "Commit messages are attacker-controlled, and an escape sequence would be executed by "
    "the reader's terminal.",
    "Isatty is true for the NUL device. Therefore use GetConsoleMode instead.",
])
def test_reasons_without_causal_words(text: str) -> None:
    assert stated_reason(text)


def test_consequence_sentence_is_quoted_with_its_premise() -> None:
    text = ("A wrong base makes blame trace other code. So every range is compared first."
            "\n\nUnrelated paragraph.")
    assert stated_reason(text) == ("A wrong base makes blame trace other code. "
                                   "So every range is compared first.")
    # Never joined across paragraphs: those sentences are not adjacent in the source.
    assert stated_reason("A wrong base makes blame trace other code.\n\nSo it is.") is None


@pytest.mark.parametrize("text", [
    "So far only Python is supported.",
    "Update docs, tests and so on.",
    "This could be faster.",
    "It would be nice to have colours.",
    "Rewrite the parser so it is strict.",
])
def test_descriptions_are_not_reasons(text: str) -> None:
    assert stated_reason(text) is None


def test_a_sentence_continues_the_one_naming_the_code(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"pkg/text.py": "def clean(s):\n    return s\n", "pkg/other.py": "Y = 1\n"},
              "Add pieces\n\n- pkg/text.py strips escapes. Commit text is hostile, and an "
              "escape would be executed by the terminal.\n"
              "- Human output uses text labels. JSON is stable, so tools can parse it.")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "pkg/text.py").decisions
    assert d.grade == "B" and d.stated_reason.startswith("Commit text is hostile")
    # The bare stem in "uses text labels" names its own sentence only, so the next
    # sentence ("JSON is stable, so ...") is not borrowed as this code's reason.
    fx.commit({"pkg/text.py": "def clean(s):\n    return s.strip()\n", "pkg/other.py": "Y = 2\n"},
              "Tidy\n\nHuman output uses text labels. JSON is stable, so tools can parse it.")
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    d = next(x for x in why(ctx, "pkg/text.py").decisions if x.introduced_sha == fx.head())
    assert d.stated_reason is None and d.reason_scope == "change"


def test_known_limit_other_files_common_word_stem_blocks_change_context(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Known limit (README): another file's bare stem that is also an ordinary word
    # ("text" for text.py) still marks a sentence as that file's, so it is not offered as
    # change-level context here. The safe direction: "reason unknown", never a borrowed
    # reason. If this starts failing, the limit was lifted; update README and STATUS.
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    fx = FixtureRepo(tmp_path / "r")
    fx.commit({"a.py": "def alpha():\n    return 1\n", "text.py": "def beta():\n    return 2\n"},
              "Split\n\nPlain text output was dropped because terminals garbled it.")
    ctx = Context.open(fx.root)
    index_repo(ctx.repo, ctx.store, ctx.cfg)
    (d,) = why(ctx, "a.py").decisions
    assert d.grade == "D" and d.change_reason is None
