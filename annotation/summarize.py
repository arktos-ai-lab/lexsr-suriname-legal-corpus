"""
Summarise human verdicts into the figures the paper can report.

    python annotation/summarize.py            # table to stdout
    python annotation/summarize.py --md out.md

For each task: items, labelled, correct / wrong / unsure, and the share
correct among decided items (correct + wrong) with a 95% Wilson interval.
Wrong and unsure items are listed with their notes for follow-up.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent

MEANING = {
    "gold": "benchmark cases with a correct expected answer",
    "instructions": "extracted amendment instructions that are correct",
    "parse_failures": "failing documents with no operative instruction (paper's claim)",
    "links": "citation links that are correct (precision)",
    "gazette": "gazette candidates that are the cited act",
    "segmentation": "decisions segmented correctly",
}


def read_jsonl(p: Path) -> list:
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", help="also write a Markdown report here")
    args = ap.parse_args()

    lines = ["| Task | Items | Labelled | Correct | Wrong | Unsure | Share correct (95% CI) | Annotators |",
             "|---|---:|---:|---:|---:|---:|---|---|"]
    follow = []
    follow_weighted = []
    for tf in sorted((HERE / "tasks").glob("*.jsonl")):
        name = tf.stem
        items = {it["id"]: it for it in read_jsonl(tf)}
        latest = {}
        for row in read_jsonl(HERE / "labels" / f"{name}.jsonl"):
            if row["item_id"] in items:
                latest[row["item_id"]] = row
        v = [r["verdict"] for r in latest.values()]
        c, w, u = v.count("correct"), v.count("wrong"), v.count("unsure")
        lab = c + w + u
        lo, hi = wilson(c, c + w)
        share = f"{c / (c + w):.1%} ({lo:.1%} to {hi:.1%})" if c + w else "n/a"
        who = ", ".join(sorted({r["annotator"] for r in latest.values()}))
        lines.append(f"| {name} | {len(items)} | {lab} | {c} | {w} | {u} | {share} | {who} |")
        # items standing for several citations (gazette: one item per reference) carry a
        # "weight"; the weighted share estimates the share over citations, not references
        if any("weight" in it for it in items.values()):
            wc = sum(items[i].get("weight", 1) for i, r in latest.items() if r["verdict"] == "correct")
            ww = sum(items[i].get("weight", 1) for i, r in latest.items() if r["verdict"] == "wrong")
            if wc + ww:
                follow_weighted.append(f"- {name}: {wc / (wc + ww):.1%} of {wc + ww} citations "
                                       f"(each reference weighted by how often it is cited)")
        for iid, r in latest.items():
            if r["verdict"] in ("wrong", "unsure"):
                follow.append(f"- **{name}** `{iid}` ({r['verdict']}): {items[iid]['title'][:80]}"
                              + (f". Note: {r['note']}" if r["note"] else ""))

    out = "\n".join(lines)
    if follow_weighted:
        out += "\n\nShare correct over citations:\n" + "\n".join(follow_weighted)
    out += "\n\nWhat \"correct\" means per task:\n" + "\n".join(f"- {k}: {m}" for k, m in MEANING.items())
    if follow:
        out += "\n\n## Wrong or unsure\n\n" + "\n".join(follow)
    print(out)
    if args.md:
        Path(args.md).write_text("# LexSR human verification\n\n" + out + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
