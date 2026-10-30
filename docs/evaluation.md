# Evaluation and known limitations

What has been measured about Fence, how, and what the numbers do and do not support. The
README summarises this page; the numbers live here.

## Attribution

Labelled scenarios in `tests/test_attribution.py`: Fence attributes 6 of 6 correctly,
against 3 of 6 for plain `git blame`. Plain blame misses the reformat, the re-indent and
the cross-file move.

## Explanation scoping

`why` scopes commit, PR and issue text to the code it is about:

- If this file is the change's only source file, the whole message applies.
- Otherwise, only sentences that name the file, a symbol in the span, or a distinctive
  identifier from the lines count as this code's reason.
- A reason the change gives for itself, naming no other file, is shown as
  `Inferred: added in a change to N source files whose message says "..."`. It is graded C,
  with `reason_scope: "change"` in JSON.
- Test files count as evidence only if they match the file by name, or if the change was
  focused.

Example from this repository: commit be874db added 8 source files at once.

| Target | Before | After |
|---|---|---|
| `fence why fence/config.py` | B, data-integrity: "The index is one SQLite file per repo…", plus `tests/gitfix.py` and `tests/test_globs.py` as evidence | B: "fence.toml now has every section … so a typo cannot silently weaken policy." Evidence: `tests/test_config.py` only |
| `fence why fence/index/indexer.py` | B, data-integrity: the same SQLite sentence | C: "Inferred: added in a change to 8 source files whose message says 'The index is one SQLite file…'", labelled as not naming this code |
| `fence why fence/config.py:85-200` | span 94-209 labelled `ScoringConfig`, with a data-integrity constraint | span labelled `<module>` (it crosses classes), with no borrowed constraint |

## Removal-Regret Backtest (pooled)

`uv run python evals/run_suite.py` backtests every repo pinned in `evals/repos.toml`:

- 500 first-parent commits per repo, with a regret window of 300 commits.
- Local-git mode, with no token.
- A block is regretted if it is reverted, or its code is re-added to the same file.

`uv run python evals/analyze.py` pools the results into `evals/results/pooled.md`, with
Wilson 95% intervals for proportions, a fixed-seed bootstrap over repos for lift, and a
leave-one-repo-out calibration check.

The results cover 11 repos (Go, Python, JavaScript, Java), measured at 4e27101, **before**
the explanation-scoping change. attrs failed to clone (network reset) and is missing.

| Metric | Value (95% CI) |
|---|---|
| Removal blocks / commits | 5,674 / 5,500 |
| Regretted (base rate) | 178 (3.1% [2.7%, 3.6%]) |
| Precision of warnings | 3.5% [2.7%, 4.7%] (47/1,334) |
| Lift | 1.12x, repo-bootstrap CI [0.47, 1.75] |
| Recall | 26.4% [20.5%, 33.3%] |
| Commits warned | 6.8% [6.2%, 7.5%] |

| Risk band | Regret rate (95% CI) |
|---|---|
| low (<25) | 4.2% [3.4%, 5.2%] |
| medium (25-59) | 2.6% [2.0%, 3.2%] |
| high (60-84) | 2.2% [1.3%, 3.6%] |
| critical (≥85) | 3.8% [1.5%, 9.3%] |

What this supports, and what it does not:

- **Lift not established.** The interval includes 1, and the evaluation predates the
  explanation-scoping fix. The data shows neither that Change Risk predicts regretted
  removals nor that it cannot.
  - Regret rates do not rise with the risk band.
  - Lift is above 1 on 4 of 11 repos.
  - The leave-one-repo-out calibration is non-decreasing on only 2 of 10 held-out repos.
  - The regret-rate calibration table was therefore withdrawn, and findings show no outcome
    probability.
- **Supported:** warnings are rare enough to read. 6.8% of commits are warned; the earlier
  three-repo runs warned on up to 37%.
- **How to read the scores:** they are experimental. Each is an explained sum of evidence
  factors (grade, type, history shape, code signals), not a probability. `check` text, JSON
  (`score_note`) and MCP responses say so. Read the evidence, not the number.
- **Not measured:** the pooled numbers after the scoping change. A single re-run on the
  smallest repo (requests: 114 blocks, 4 regretted) moved verdicts from KEEP 10 / VERIFY 8 /
  CLEAR 96 to KEEP 9 / VERIFY 6 / CLEAR 99, and grades from C 38 / D 72 to C 28 / D 82. The
  regrets caught are the same (1 of 4). One repo shows no trend. Details:
  `evals/results/requests-before-after/`.

Per-repo figures are in `evals/results/pooled.md`. A warm `fence why` on requests takes
about 0.4s, and a 10-file `check` about 2s.

## Live GitHub (grade A)

The opt-in test (`FENCE_LIVE_GITHUB=1 GITHUB_TOKEN=… uv run pytest tests/test_github_live.py`)
ran on psf/requests (2026-10-30, Windows). It fetched 40 PRs, 74 linked issues and 29 review
comments through 194 API requests, and produced grade-A decisions that quote PR, issue and
review text verbatim, with links.

Grade A means the reason was quoted from PR or issue text, not that the quote is correct. In
that run, 19 of 1,364 decisions in the test's 24-file sample reached grade A. A hand check
found about 7 of the 19 were not real reasons for the code: 6 quoted upstream release notes
from Dependabot PR bodies, and 1 was a review comment ("…but otherwise this looks good.").
Since 0.1.1, text written by `[bot]` accounts (commit bodies, PR bodies, review comments) is
kept as evidence but never quoted as a stated reason, and "but/and otherwise" no longer
counts as causal. On a second live run (same repo and sample), grade A fell from 19 to 13.
None of the 13 comes from bot text, and all 7 flagged quotes are gone. The remaining 13 are
not all hand-verified as correct.

## Reason accuracy (in progress)

A human-labelled benchmark (`evals/reasons/`) measures whether `why` recovers the real
reason. Its pilot is not yet labelled, so **no reason-accuracy figure is claimed yet**.

## Known limitations

- **Risk scores are experimental.** Lift is not established (see above). Treat verdicts as
  "evidence exists here", not as a probability.
- **Scoping is lexical.** A sentence that describes the code without naming the file, a
  symbol or a distinctive identifier is treated as change-level (graded C). One that names
  an identifier the code merely uses can still count as specific. Another file's name that
  is also a common word (`text.py`, `fence.toml`) keeps a sentence containing that word out
  of the change-level context.
- **Reasons need recognisable phrasing.** A reason is recognised by:
  - a causal word ("because", "since", "so that");
  - a result clause (", so …");
  - a hazard ("… would be executed / lost / corrupted");
  - a sentence starting "So", "Hence" or "That is why", which is quoted with the sentence
    before it.

  A reason phrased any other way is missed, and the code is graded C or D. On 33,022 commit
  messages from the eval repos, these forms added 106 stated reasons to 1,053. A hand check
  of 50 of the additions found no false positives; the full set was not labelled.
- **Bare file stems.** A file's bare stem in prose ("uses text labels" for `text.py`) counts
  as naming that file in its own sentence, and can lend it a constraint or reason that is
  not about it. The sentences after it are not attached unless the file is named as code
  (`text.py`, `` `text` ``, `fence.text`).
- **Backtest labels are a proxy.** Regret labels (reverted, or the same code re-added in the
  same file) are a noisy lower bound for "risky removal".
- **GitHub data.** Without `GITHUB_TOKEN`, grade A is unreachable (local-git mode), and
  `fence doctor` says so. Rebase-merged PRs map to their PR only through the GitHub API.
  Bots that post under a human-looking account (no `[bot]` suffix) are not recognised.
- **Blob-less clones index slowly.** With `git clone --filter=blob:none`, indexing needs
  every changed file's contents, so git downloads them one commit at a time, and a network
  drop fails the run (`git log failed (128): fatal: could not fetch … from promisor
  remote`). Use a full clone or `--filter=blob:limit=1m`.
- **Languages.** Symbol anchors cover Python, JavaScript/TypeScript, Go and Java. Other
  languages get file and line anchors, with a notice.
- **Platforms.** Windows, macOS and Linux are supported and tested in CI. Symlink tests are
  skipped on Windows without Developer Mode.
- **Not built:** the LLM synthesis pass, a web view, GitLab/Bitbucket support, and full
  regression echo. See STATUS.md.
