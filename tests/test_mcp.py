"""MCP protocol tests over a real stdio-style stream, against the demo repo."""

import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fence.mcp.server import Server, serve


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FENCE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def _rpc(repo: str, *msgs: dict) -> list[dict]:
    out = io.StringIO()
    serve(repo, io.StringIO("".join(json.dumps(m) + "\n" for m in msgs) + "not json\n"), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def _call(repo: str, name: str, args: dict, mid: int = 1) -> dict:
    (resp, _) = _rpc(repo, {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                             "params": {"name": name, "arguments": args}})
    return resp["result"]


def test_initialize_list_and_errors(demo: dict) -> None:
    # The notification gets no response, so four messages give four responses
    # (the fourth is the parse error for the trailing garbage line).
    init, listed, unknown, parse_err = _rpc(
        demo["repo"],
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nope"},
    )
    assert init["result"]["serverInfo"]["name"] == "fence"
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"why", "check", "list_fences"}  # write tools off by default
    for t in listed["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object"
    assert unknown["error"]["code"] == -32601
    assert parse_err["error"]["code"] == -32700


def test_why_is_compact_and_flags_load_bearing_code(demo: dict) -> None:
    res = _call(demo["repo"], "why", {"target": "payments/client.py:22"})
    text = res["content"][0]["text"]
    assert not res["isError"]
    assert text.startswith("FENCE: this code is load-bearing")
    body = json.loads(text.split("\n", 1)[1])
    assert body["decisions"][0]["grade"] == "B"
    assert "duplicate charges" in body["decisions"][0]["reason"]
    assert len(text) / 4 < 1500  # token budget


def test_check_guardrail_says_stop(demo: dict) -> None:
    res = _call(demo["repo"], "check", {"base": "main", "head": "cleanup-payments"})
    assert res["content"][0]["text"].startswith("STOP: ask the human before editing")
    ok = _call(demo["repo"], "check", {"base": "main", "head": "harmless-rename"})
    assert not ok["content"][0]["text"].startswith("STOP")


def test_tool_errors_are_results_not_crashes(demo: dict) -> None:
    res = _call(demo["repo"], "why", {"target": "missing.py:1"})
    assert res["isError"] and "fence error" in res["content"][0]["text"]
    res = _call(demo["repo"], "acknowledge", {"decision_id": "x", "at": "a:1", "reason": "r"})
    assert res["isError"]  # not exposed by default


def test_list_fences(demo: dict) -> None:
    res = _call(demo["repo"], "list_fences", {"path": "payments", "limit": 3})
    body = json.loads(res["content"][0]["text"])
    assert body["fences"] and body["fences"][0]["file"] == "payments/client.py"


def test_acknowledge_when_enabled(demo: dict, tmp_path: Path) -> None:
    repo = tmp_path / "copy"
    shutil.copytree(demo["repo"], repo)
    (repo / "fence.toml").write_text("[mcp]\nwrite_tools_enabled = true\n")
    s = Server(str(repo))
    why = json.loads(s.call("why", {"target": "payments/client.py:22"})["content"][0]["text"]
                     .split("\n", 1)[1])
    res = s.call("acknowledge", {"decision_id": why["decisions"][0]["id"],
                                 "at": "payments/client.py:22",
                                 "reason": "Human said the gateway dedupes now"})
    assert not res["isError"], res
    assert (repo / ".fence" / "acks.jsonl").is_file()


def test_real_stdio_process(demo: dict) -> None:
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    proc = subprocess.run([sys.executable, "-m", "fence.cli", "mcp", "-C", demo["repo"]],
                          input="".join(json.dumps(m) + "\n" for m in msgs),
                          capture_output=True, text=True, timeout=120, check=False)
    lines = [json.loads(x) for x in proc.stdout.splitlines()]
    assert [x["id"] for x in lines] == [1, 2], proc.stderr
