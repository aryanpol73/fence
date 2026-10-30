# Status

What is done, partial, or not done, each with the reason. Kept accurate to the code.

## Done

- **F1 indexing:** incremental SQLite index, crash-safe batches, resume, rewrite and orphan
  detection, shallow-clone and empty-repo reporting, format-only commit detection,
  `.git-blame-ignore-revs` read from the base revision, PR mapping from merge and squash
  subjects, and GitHub enrichment with ETag, rate-limit, pagination and request-budget
  handling. All covered by fixture tests.
- **Rename and move tracking:** git renames, `blame -w -C`, format-only skipping, plus
  Fence's own cross-file move follower. Attribution benchmark: 6/6 versus 3/6 for plain blame.
- **Languages:** Python (stdlib), JS, TS/TSX, Go and Java (tree-sitter). Other languages get
  file and line anchors.
- **F2 deterministic extraction:** signal catalog (text, label and code signals, each
  unit-tested), strict A-D grading, verbatim stated reasons, `Inferred:` marking, and
  injection-sentence filtering.
- **F3 why:** file, line, range or symbol targets; commit/PR/issue text scoped to the code it names (`reason_scope`); strongest-first or newest-first; reverts,
  regression chain, orphaned evidence; human and JSON (`fence/v1`) output.
- **F4 check:** working tree, staged, range, PR or diff file; delete/loosen/alter/move
  classification; explainable risk; policy file with path overrides; ack ledger with
  fingerprint invalidation and expiry; bypass env var; stable exit codes.
- **F5 interfaces:**
  - CLI: index, why, check, ack, explain, scan, report, doctor, config, backtest, hook, mcp,
    comment.
  - MCP server with guardrail mode.
  - Git hooks.
  - GitHub Action with a fork-safe sticky comment.
- **F6:** `scan` and `report` (markdown, JSON, HTML).
- **F7 / D3 obsolescence:** dependency removed or upgraded, feature flag gone, private symbol
  unreferenced, each yielding SAFE-TO-REMOVE-CANDIDATE.
- **D1 backtest:** no future leakage (`Context.as_of`); reports precision, recall, noise,
  latency, lead time and a calibration table.
- **D2 Ghost Fences:** tombstones for deleted guards, linked when the code comes back.
- **D5:** agent guardrail mode and the drop-in instruction snippet (README).
- **D6:** team ack ledger.
- **D7:** honest uncertainty: grades, "reason unknown", and confidence shown with every score.
- **D8 orphaned evidence:** rewritten commits and PRs or issues that cannot be fetched are
  marked as such.

## Release-readiness audit (2026-10-29)

Each item from the completion brief, after this session's work. "Verified" means a test
in the normal suite exercises it, or it was run by hand and the output checked.

| Item | Status |
|---|---|
| 1. Core `why`/`check`, text/markdown/JSON, CLI/MCP parity | Implemented + verified. `why --format markdown` added; parity tests in `tests/test_edge_cases.py` |
| 2. Local git mandatory; GitHub optional, offline mode, caching, rate limits | Implemented + verified against recorded responses. Live sync verified once (`tests/test_github_live.py`, psf/requests, 2026-10-30): 40 PRs, 74 issues, 29 review comments, 194 API requests, no rate-limit stop. Rate-limit and ETag behaviour live, and repos other than requests, not exercised |
| 3. Python + JS/TS/Go/Java via `LanguagePlugin`, text fallback | Implemented + verified (`tests/test_lang.py`; `why README.md` works) |
| 4. MCP stdio, git hooks, pre-commit framework hook, GitHub Action, `fence.toml` | Implemented + verified, except the Action and the pre-commit framework hook, which were not run in a real PR or `pre-commit` install |
| 5. Reasons without causal words | Implemented + verified: result clause, hazard, consequence sentence; +106/1,053 on 33k messages, 50 hand-checked |
| 6. Common-word names | Partly fixed: a bare stem no longer extends to later sentences. Remaining limit pinned by `test_known_limit_other_files_common_word_stem_blocks_change_context` |
| 7. Grade A live validation | Passed once (122s): grade A reached from live PR, issue and review text, quoted verbatim with URLs. This proves the pipeline, not quote quality. A hand check of the run's 19 grade-A decisions found about 7 were not reasons for the code: 6 Dependabot release-note quotes, and 1 "…otherwise this looks good" review comment. Fixed since: `[bot]`-authored commit bodies, PR bodies and review comments are never stated reasons, and "but/and otherwise" is not causal. Second live run (110s), same sample: grade A 19 -> 13, with no A/B quote from bot text and all 7 flagged quotes gone |
| 8. Eval results in repo | Done: `evals/results/requests-before-after/` with reproduce commands |
| 9. Packaging and docs | Implemented. Distribution `gitfence` (commands `fence` and `gitfence`). Clean-venv `pip install` of the wheel verified on Windows; CI's package job passed on Windows, macOS and Linux at 3e01144. Released: gitfence 0.1.0 on PyPI via Trusted Publishing (tag v0.1.0 = a6db4e4), and a fresh `pip install gitfence==0.1.0` runs `fence` and `gitfence`. CI's macOS package job on a6db4e4 was cancelled in the queue, not failed. It passed on re-run. Released: gitfence 0.1.1 on 2026-10-30 (tag v0.1.1 = 503d723, publish run 41035837541). All 9 CI checks passed on that commit, and a fresh `pip install gitfence==0.1.1` from PyPI runs `fence` and `gitfence` 0.1.1 |

## Partial

- **Risk scoring: experimental, lift not established.** The pooled backtest covers 11
  repos, 5,674 removal blocks and 178 regrets. It predates the explanation-scoping fix:
  - Lift is 1.12x, repo-bootstrap 95% CI [0.47, 1.75]. The interval includes 1, so neither
    a useful nor a useless score is shown. Lift is above 1 on 4 of 11 repos.
  - Regret rate by band: low 4.2%, medium 2.6%, high 2.2%, critical 3.8%. It does not rise
    with risk.
  - Thresholds and weights were left unchanged: tuning cannot add signal that the score
    lacks.
  - Scores are explained evidence summaries, not probabilities, and every `check`, JSON
    and MCP response labels them experimental.
- **Calibration: withdrawn.** The regret-rate table fitted on cobra + requests did not
  transfer. A table refitted leave-one-repo-out was non-decreasing on only 2 of 10 held-out
  repos. No table ships, so findings carry no outcome probability. The fitting code
  (`fence/scoring/calibration.py`) and the backtest's per-bin report remain.
- **Pooled numbers after evidence scoping: not measured.** Scoping lowers Strength for code
  that came from multi-file commits. The suite (`evals/run_suite.py`, resumable) has not been
  re-run since. attrs is missing from the pooled set (its clone failed).
- **Evidence scoping is lexical.** Commit, PR and issue sentences count for code only if
  they name the file, a symbol in the span or a distinctive identifier. Sentences that
  describe the code in other words are treated as change-level (grade C). The sentence
  right after a naming sentence, in the same paragraph, counts too. A 14-target audit of
  this repo found 4 over-cautious cases. All 4 (obsolete.py, text.py, trace.py,
  strength.py) are now grade B with the correct quote.
- **`check` latency** is 0.7-0.9s median and 2.0-2.8s p90 per commit on Windows (500-commit
  backtests). It can go over the 2-3s target on larger diffs, because line-history walks
  run once per decision. Hook mode skips them.
- **Blast radius** (caller counts) is used only for obsolescence, not as a Strength factor.
  Counting callers cheaply and correctly across languages needs a references index.
- **D4 regression echo.** Re-fixes of the same lines raise Strength (the regression-chain
  factor), but similar fixes across different lines are not detected.
- **"Same PR touches the evidence-linked tests"** is not weighted. Removed tests are.
- **Rebase-merged PRs** map to their PR only through the GitHub API.

## Not done

- **LLM synthesis pass (P2).** Optional by design. The `[llm]` config exists but nothing reads
  it, so enabling it does nothing. Left out to protect core quality within the time budget.
- **Web view (P2).** Lowest priority in the brief.
- **GitLab/Bitbucket.** The extension point is the `github/` package shape, but no adapter
  exists.
- **Dogfood findings on large (100k+) histories.** The index streams with bounded memory, but
  it has been run only on repos of up to about 6.5k commits.
