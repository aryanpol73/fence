# Decisions

Append-only. One line per change to a default from the brief or CLAUDE.md, with the reason.

- 2026-10-28: Exit code 2 now means "policy threshold exceeded", as the brief says, not "Fence broke". Errors are 1, ack required is 3, index unavailable is 4. CI still fails open on 1 and 4.
- 2026-10-28: The MCP server is hand-written JSON-RPC over stdio, not the MCP SDK. The SDK's dependency tree is out of proportion for three tools.
- 2026-10-28: The ack ledger lives at `.fence/acks.jsonl`, committed. JSONL appends merge cleanly in git, while TOML rewrites cause conflicts.
- 2026-10-28: Grade D findings never block (`grade_floor_for_block = "C"`). This reconciles "grounded or silent" with the brief's grade-D warnings.
- 2026-10-28: Python is parsed with the stdlib `ast`/`tokenize`, not tree-sitter. It is the interpreter's exact grammar and needs no dependency. tree-sitter covers JS, TS/TSX, Go and Java.
- 2026-10-28: Format-only detection verifies only candidates: commits whose message reads like formatting, or whose files kept their line counts. Verifying all commits would cost two blob reads per file over the whole history. Misses can still be listed in .git-blame-ignore-revs.
- 2026-10-28: Noise tuning from the first real backtest (cobra + requests, 600 commits: 74% of removal commits warned, precision 1.9% against a 1.6% base rate). The code-signal cap drops from 20 to 12, the `alter` multiplier from 0.6 to 0.5, and a test added alongside no longer lifts grade D to C. VERIFY now needs risk >= 50 (`scoring.verify_min_risk`) for inferred grades C/D, and stays at the medium band (25) for stated grades A/B.
- 2026-10-29: False-warning breakdown, 500-commit backtest, tuning set cobra + requests (286 warned blocks, 13 regretted; express held out):
  - (1) docs/config paths: 41 warned, 0 regretted. Completion-script paths: 106 warned, 1 regretted. Half of all warnings carry almost no signal.
  - (2) Inferred decision types carry no signal: type "bugfix" 72 warned, 0 regretted; grade C 72 warned, 1 regretted.
  - (3) Grade D does relatively well (122 warned, 9 regretted). Its risk comes from history-shape factors (survival, people, re-fixes), not from inferred text.
  - (4) Losing a guard signal barely helps (5.8% vs 4.0% regretted), too weak to require for VERIFY.
  - (5) Nearly all warnings are deletes (275 of 286). Evidence confidence is not monotonic in regret rate, so it cannot serve as outcome confidence.
- 2026-10-29: Precision and noise changes, tuned on cobra + requests only:
  - Change Risk on `scoring.low_signal_paths` (docs, config, completion scripts) is scaled by 0.5.
  - Decision types inferred from keywords (grade C/D) count 0.5x.
  - VERIFY needs risk >= 50 for every grade, except a stated (A/B) decision of a type in `scoring.high_stakes_types`, which keeps the medium band.
  - Results at 500 commits: cobra 2.85x lift / 43% recall / 6.2% of commits warned; requests 1.6x / 25% / 1.6%. Held-out express: 0.96x / 20% / 7.4% (before: 1.4x / 40% / 9.8%).
  - Kept because noise fell on all three repos, held-out included. The precision change on express rests on 2 regrets. Targets missed and not tuned further against express.
- 2026-10-29: Outcome calibration ships as a static isotonic table, fitted on cobra + requests (834 blocks, 27 regrets; at least 30 blocks and 2 regrets per bin). It is monotonic on the fitting set and flat on express. This is reported, not hidden: the table's rates describe the fitting repos only.
- 2026-10-29: Evidence scoping. Commit, PR and issue text counts as a specific Decision Record's reason only when the change touched no other source file, or when the sentence names the file, a span symbol or a distinctive identifier. Otherwise it is change-level context, graded C at best. Tests count only when name-related. Reason: be874db's single SQLite-index sentence was quoted as the grade-B reason for both config.py and indexer.py, chosen by word overlap, with unrelated tests cited.
- 2026-10-29: Withdrew the shipped regret-rate calibration table, with thresholds and weights unchanged. Pooled backtest (11 repos, 178 regrets): lift 1.12x [0.47, 1.75]; regret rate not monotonic in risk band; leave-one-repo-out calibration non-decreasing on 2/10 repos. Refitting on the pooled data would encode no signal, and threshold tuning cannot create one.
- 2026-10-29: Reason detection accepts three forms without a causal word: a result clause (", so …", not "so far/on/forth"), a hazard ("would/could/might [be] executed/lost/corrupted/…"), and a sentence opening with "So/Hence/Therefore/Thus/That is why", quoted together with the previous sentence of the same paragraph. Reason: 3 of 14 audited files lost real reasons. Measured offline on 33,022 eval commit messages: +106 stated reasons, 0 lost, 50 of the 106 hand-checked as genuine. A broader "If …, … would" form was rejected as too likely to match documentation-style sentences.
- 2026-10-29: Scoping continuation. In a multi-file change, a sentence that directly follows one naming this code, in the same paragraph and naming no other file, counts for this code. Reason: commit bullets usually name the file, then explain it ("fence/text.py strips escapes. Commit messages are attacker-controlled, …"). A bare file stem (an ordinary word like "text") names its own sentence only and starts no continuation. Exclusion by other files' bare stems is unchanged (safe direction: D, not a borrowed reason), pinned as a known limit by a test.
- 2026-10-29: Kept `evals/` as the eval directory (the brief said `eval/results/`). A second, parallel directory would split the harness from its results.
- 2026-10-29: `why --json` stays as an alias of `--format json`, so existing scripts and the Action keep working.
- 2026-10-29: No PyPI publish and no GitHub Release. The local `v0.1.0` tag marks the build that installs from a clean venv and passes the fast suite. Publishing waits for the owner's approval.
