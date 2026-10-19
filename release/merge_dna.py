#!/usr/bin/env python3
"""
merge_dna.py: bring the law PDFs published by De Nationale Assemblée (dna.sr) into the store.

    python release/merge_dna.py --db data/release_build/lexsr_work.db [--staging data/dna_staging.db] [--apply]

scripts/dna_pdfs.py ingests the DNA PDFs into a staging store. For each DNA work this script
finds the gazette issue it publishes (S.B./G.B. year and number, from the file name, the DNA
page title or the printed heading) and compares it with the legislative works in --db that
name the same issue:

  - same issue and the same document (similarity >= 0.60 on the first 5,000 characters of
    normalised text, or word-set overlap >= 0.30, which also holds when one copy reads a
    two-page spread straight across; reported as overlap + 0.30): the work
    keeps its id, URI and title, and its text is replaced by the DNA text, so the government
    copy is the one stored and released;
  - otherwise the DNA work is added as a new work.

A work that already has consolidated versions (amendments applied by the engine) keeps its
text, since the versions are built on it. A second DNA file for an issue already taken over (similarity >= 0.90 with the text now
stored) is skipped as a repeat. Every decision is written to release/dna_merge.csv. Works
taken over or added carry {"text_source": "dna", "dna_file", "dna_url", "dna_sha256"} in
their metadata, which release/select_release_works.py reads.

Idempotent: DNA files already recorded in a work's metadata are skipped. Without --apply it
only prints the plan. Before the first --apply it copies --db to <db>.pre_dna.
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import json
import re
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NAME_RE = re.compile(r"\b(s|g)\s*\.?\s*b\.?\s+(\d{4})\s+(?:no\.?\s*)?(\d{1,4}[a-z]?)\b", re.I)
HEAD_RE = re.compile(r"\b(\d{4})\s+(STAATSBLAD|GOUVERNEMENTSBLAD)\b.{0,80}?\bN[o0]\.?\s*(\d{1,4}[a-z]?)\b",
                     re.I | re.S)


def issue_from_name(s: str):
    s = re.sub(r"[_\-]+", " ", s or "")
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    m = NAME_RE.search(s)
    return (m.group(1).upper(), m.group(2), m.group(3).lower()) if m else None


def issue_from_text(t: str):
    m = HEAD_RE.search((t or "")[:600])
    if not m:
        return None
    return ("S" if m.group(2).upper().startswith("S") else "G", m.group(1), m.group(3).lower())


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (t or "").lower())).strip()


def text_of(c, wid: str) -> str:
    return " ".join(t or "" for (t,) in c.execute(
        "SELECT text FROM fragments WHERE work_id=? ORDER BY position, rowid", (wid,)))


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm(a)[:5000], norm(b)[:5000], autojunk=False).ratio()


def overlap(a: str, b: str) -> float:
    """Word-set overlap (Jaccard), insensitive to line order: a two-page spread read straight
    across both pages holds the same words as the same pages read one at a time."""
    x, y = set(norm(a)[:8000].split()), set(norm(b)[:8000].split())
    return len(x & y) / max(1, len(x | y))


def meta(raw) -> dict:
    try:
        return json.loads(raw or "{}") or {}
    except ValueError:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    ap.add_argument("--staging", type=Path, default=ROOT / "data" / "dna_staging.db")
    ap.add_argument("--csv", type=Path, default=ROOT / "release" / "dna_merge.csv")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    s = sqlite3.connect(f"file:{a.staging}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    c = sqlite3.connect(a.db)
    c.row_factory = sqlite3.Row

    # legislative works in the store, by the gazette issue their title or metadata names
    by_issue = collections.defaultdict(list)
    merged_files = set()
    for r in c.execute("SELECT id, title, metadata FROM works WHERE source_type='legislation'"):
        m = meta(r["metadata"])
        if m.get("dna_file"):
            merged_files.add(m["dna_file"])
        key = issue_from_name(m.get("gazette_ref") or "") or issue_from_name(r["title"])
        if key:
            by_issue[key].append(r["id"])

    # works with consolidated versions (amendments applied by the engine) keep their text:
    # replacing it would drop the versions built on it
    consolidated = {wid for (wid,) in c.execute(
        "SELECT work_id FROM expressions GROUP BY work_id HAVING COUNT(*) > 1")}
    plan, taken = [], {}
    for w in s.execute("SELECT * FROM works ORDER BY id"):
        m = meta(w["metadata"])
        f = m.get("source_file", "")
        if f in merged_files:
            continue
        dtext = text_of(s, w["id"])
        key = (issue_from_name(f.split("_", 1)[-1]) or issue_from_name(m.get("page_title", ""))
               or issue_from_text(dtext))
        best, score = None, 0.0
        for wid in by_issue.get(key, []) if key else []:
            if wid in taken:
                continue
            ctext = text_of(c, wid)
            sc = max(sim(dtext, ctext), overlap(dtext, ctext) + 0.30)
            if sc > score:
                best, score = wid, sc
        if best and score >= 0.60 and best in consolidated:
            plan.append(("skip_consolidated", w, best, score, key))
            taken[best] = w["id"]
            continue
        if best and score >= 0.60:
            plan.append(("replace", w, best, score, key))
            taken[best] = w["id"]
            continue
        repeat = next((wid for wid, dna in taken.items() if key and wid in by_issue.get(key, [])
                       and sim(dtext, text_of(s, dna)) >= 0.90), None)
        if repeat:
            plan.append(("skip_repeat", w, repeat, 1.0, key))
        else:
            plan.append(("add", w, best, score, key))

    count = collections.Counter(p[0] for p in plan)
    print(f"DNA works: {len(plan)} to merge ({len(merged_files)} already merged): "
          f"{count['replace']} replace the text of an existing work, {count['add']} added as new works, "
          f"{count['skip_repeat']} repeats skipped, {count['skip_consolidated']} kept for an existing work with consolidated versions")
    adds = [p for p in plan if p[0] == "add"]
    print(f"  added: {sum(p[4] is None for p in adds)} without a gazette issue, "
          f"{sum(p[4] is not None and p[2] is None for p in adds)} with an issue not in the store, "
          f"{sum(p[2] is not None for p in adds)} with a same-issue work below 0.60 "
          f"(similarity {sorted(round(p[3], 2) for p in adds if p[2])})")
    if not a.apply or not plan:
        return 0

    backup = a.db.with_name(a.db.name + ".pre_dna")
    if not backup.exists():
        c.close()
        shutil.copyfile(a.db, backup)
        c = sqlite3.connect(a.db)
        c.row_factory = sqlite3.Row

    fcols = [r[1] for r in c.execute("PRAGMA table_info(fragments)")]
    wcols = [r[1] for r in c.execute("PRAGMA table_info(works)")]
    ecols = [r[1] for r in c.execute("PRAGMA table_info(expressions)")]
    new_file = not a.csv.exists()
    with open(a.csv, "a", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        if new_file:
            out.writerow(["dna_file", "dna_work_id", "action", "work_id", "similarity", "gazette_issue", "title"])
        for action, w, target, score, key in plan:
            m = meta(w["metadata"])
            tag = {"text_source": "dna", "dna_file": m.get("source_file"), "dna_url": w["source_url"],
                   "dna_sha256": m.get("sha256"), "dna_page_url": m.get("page_url")}
            issue = f"{key[0]}.B. {key[1]} no. {key[2]}" if key else ""
            if action.startswith("skip"):
                out.writerow([m.get("source_file"), w["id"], action, target, "", issue, w["title"]])
                continue
            frags = [dict(r) for r in s.execute("SELECT * FROM fragments WHERE work_id=? ORDER BY rowid", (w["id"],))]
            dexp = s.execute("SELECT * FROM expressions WHERE work_id=?", (w["id"],)).fetchone()
            if action == "replace":
                exp = c.execute("SELECT id, uri FROM expressions WHERE work_id=? ORDER BY version_date DESC",
                                (target,)).fetchone()
                c.execute("DELETE FROM citations WHERE source_work_id=?", (target,))
                c.execute("DELETE FROM fragments WHERE work_id=?", (target,))
                wid, eid, euri = target, exp["id"], exp["uri"]
                old = meta(c.execute("SELECT metadata FROM works WHERE id=?", (target,)).fetchone()[0])
                c.execute("UPDATE works SET metadata=?, source_url=? WHERE id=?",
                          (json.dumps({**old, **tag}, ensure_ascii=False), w["source_url"], target))
            else:
                row = dict(w)
                # DNA page titles often drop "wijz." that the file name keeps ("Wetboek van
                # Strafvordering" for S.B. 2008 no. 20 wijz. Wetboek van Strafvordering); an
                # amending act titled like the act itself would take that act's citations
                title = row["title"] or ""
                if re.search(r"(?:^|[_\-\s])(?:wijz|wijziging|nadere-wijziging)", m.get("source_file", ""), re.I) \
                        and not re.search(r"wijz", title, re.I):
                    title = "Wijziging " + title
                if issue and not issue_from_name(title):
                    title = f"{issue} {title}"
                row["title"] = title
                row["metadata"] = json.dumps({**m, **tag, **({"gazette_ref": issue} if issue else {})},
                                             ensure_ascii=False)
                c.execute(f"INSERT OR REPLACE INTO works ({','.join(wcols)}) VALUES ({','.join('?' * len(wcols))})",
                          [row.get(k) for k in wcols])
                erow = dict(dexp)
                erow["title"] = title
                c.execute(f"INSERT OR REPLACE INTO expressions ({','.join(ecols)}) VALUES ({','.join('?' * len(ecols))})",
                          [erow.get(k) for k in ecols])
                wid, eid, euri = w["id"], dexp["id"], dexp["uri"]
            for pos, fr in enumerate(frags):
                fr["id"] = eid + fr["id"][len(dexp["id"]):]
                fr["uri"] = euri + fr["uri"][len(dexp["uri"]):]
                if fr.get("parent_fragment_id", "") and fr["parent_fragment_id"].startswith(dexp["uri"]):
                    fr["parent_fragment_id"] = euri + fr["parent_fragment_id"][len(dexp["uri"]):]
                fr.update(work_id=wid, expression_id=eid, expression_uri=euri, position=pos)
                c.execute(f"INSERT OR REPLACE INTO fragments ({','.join(fcols)}) VALUES ({','.join('?' * len(fcols))})",
                          [fr.get(k) for k in fcols])
            out.writerow([m.get("source_file"), w["id"], action, wid, f"{score:.2f}", issue, w["title"]])
            c.commit()
    print("works now:", c.execute("SELECT COUNT(*) FROM works").fetchone()[0],
          "fragments:", c.execute("SELECT COUNT(*) FROM fragments").fetchone()[0])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
