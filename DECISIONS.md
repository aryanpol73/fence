# Decisions

Append-only. One line per change to a default from the brief or CLAUDE.md, with the reason.

- 2026-10-28: Exit code 2 now means "policy threshold exceeded", as the brief says, not "Fence broke". Errors are 1, ack required is 3, index unavailable is 4. CI still fails open on 1 and 4.
- 2026-10-28: The MCP server is hand-written JSON-RPC over stdio, not the MCP SDK. The SDK's dependency tree is out of proportion for three tools.
- 2026-10-28: The ack ledger lives at `.fence/acks.jsonl`, committed. JSONL appends merge cleanly in git, while TOML rewrites cause conflicts.
- 2026-10-28: Grade D findings never block (`grade_floor_for_block = "C"`). This reconciles "grounded or silent" with the brief's grade-D warnings.
