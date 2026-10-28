"""`fence report`: where the load-bearing code is, for a tech lead (F6)."""

from __future__ import annotations

import html
from collections import Counter

from fence.context import Context
from fence.scan import scan


def build(ctx: Context, path: str = ".", *, limit: int = 15, max_files: int = 300) -> dict:
    res = scan(ctx, path, max_files=max_files)
    churn: Counter[str] = Counter()
    owners: dict[str, Counter[str]] = {}
    for row in ctx.store.conn.execute(
        "SELECT f.path, c.author_email FROM file_changes f JOIN commits c ON c.sha = f.sha "
        "WHERE c.orphaned = 0 AND c.format_only = 0"
    ):
        churn[row["path"]] += 1
        owners.setdefault(row["path"], Counter())[row["author_email"]] += 1

    def item(f: str, d) -> dict:
        return {"file": f, "lines": [[a.start_line, a.end_line] for a in d.anchors],
                "strength": d.strength.value, "band": d.strength.band, "grade": d.grade,
                "type": d.type, "decision": d.id, "reason": d.reason_text,
                "commits_to_file": churn.get(f, 0)}

    ranked = [item(f, d) for f, d in res.decisions]
    fragile = [x for x in ranked if x["grade"] in ("C", "D")
               and x["strength"] >= ctx.cfg.scoring.verify_min_risk]
    hot = sorted((x for x in ranked if x["strength"] >= ctx.cfg.scoring.bands.medium),
                 key=lambda x: (-x["strength"] * x["commits_to_file"], x["file"]))
    files = sorted({f for f, _ in res.decisions})
    concentration = []
    for f in files:
        c = owners.get(f)
        if not c or sum(c.values()) < 5:
            continue
        top, n = c.most_common(1)[0]
        share = n / sum(c.values())
        if share >= 0.8:
            concentration.append({"file": f, "top_author_share": round(share, 2),
                                  "commits": sum(c.values()), "authors": len(c)})
    return {
        "schema": "fence/v1", "kind": "report", "path": res.path,
        "files_scanned": res.files_scanned, "decisions": len(ranked), "notes": res.notes,
        "load_bearing": ranked[:limit], "fragile_undocumented": fragile[:limit],
        "hot_and_strong": hot[:limit], "ownership_concentration": concentration[:limit],
    }


def to_markdown(r: dict) -> str:
    out = [f"# Fence report: `{r['path']}`", "",
           f"{r['decisions']} decisions in {r['files_scanned']} files.", ""]

    def table(title: str, rows: list[dict], cols: list[str]) -> None:
        out.append(f"## {title}")
        if not rows:
            out.extend(["", "None.", ""])
            return
        out.extend(["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)])
        for x in rows:
            vals = []
            for c in cols:
                v = x.get(c)
                if c == "lines":
                    v = ",".join(f"{a}-{b}" for a, b in v[:3])
                vals.append(str(v).replace("|", "\\|").replace("\n", " ")[:120])
            out.append("| " + " | ".join(vals) + " |")
        out.append("")

    main = ["file", "lines", "strength", "grade", "type", "reason"]
    table("Load-bearing code (do not simply delete)", r["load_bearing"], main)
    table("Fragile and undocumented (strong, but reason only inferred or unknown)",
          r["fragile_undocumented"], main)
    table("Strong and frequently changed", r["hot_and_strong"], [*main[:4], "commits_to_file"])
    table("Ownership concentration (>= 80% of commits by one person)",
          r["ownership_concentration"], ["file", "top_author_share", "commits", "authors"])
    out += [f"Note: {n}" for n in r["notes"]]
    return "\n".join(out)


def to_html(r: dict) -> str:
    md = to_markdown(r)
    body = []
    for line in md.splitlines():
        e = html.escape(line)
        if line.startswith("# "):
            body.append(f"<h1>{e[2:]}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{e[3:]}</h2>")
        elif line.startswith("|---"):
            continue
        elif line.startswith("| "):
            cells = [html.escape(c.strip()) for c in line.strip("|").split(" | ")]
            body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        elif line:
            body.append(f"<p>{e}</p>")
    return ("<!doctype html><meta charset=utf-8><title>Fence report</title>"
            "<style>body{font:14px system-ui;margin:2em;max-width:1100px}"
            "td{border-bottom:1px solid #ddd;padding:4px 8px;vertical-align:top}</style>"
            + "\n".join(body))
