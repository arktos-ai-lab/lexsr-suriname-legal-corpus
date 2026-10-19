#!/usr/bin/env python3
"""
dedupe_works.py: merge legislative works that were ingested twice.

    python release/dedupe_works.py --db data/release_build/lexsr_work.db [--apply]

The same gazette PDF often exists under two file names ("S B 2016 no 63 ..." and
"S.B. 2016 no 63 ..."), which produced two works with the same text. Works are grouped
by title with case and everything but letters and digits ignored. A group is merged only
when its texts are identical, the shorter text appears whole inside the longer one (a
partial scan), or the texts are two OCR passes over the same pages (similarity >= 0.90). Groups under generic OCR headers ("2023 STAATSBLAD VAN
DE") hold different acts and fail that test, so they stay apart. Case law is not touched:
decisions sharing a roll number or the title "onbekend" are different decisions.

The kept work is the one whose text comes from a government copy, then the longest text, then the dotted title ("S.B."), then the
earliest created_at. Amendment rows pointing at a removed work are repointed to the kept
one (exact repeats dropped); everything else of the removed work is deleted. Citations
are not repointed: run `python -m lexsr link-citations` afterwards on a cleared citations
table, as release/prepare_release_db.py does.

Without --apply it only prints the plan. Before the first --apply it copies the database
to <db>.pre_dedupe; once no groups remain, a re-run does nothing. The groups and
decisions are written to release/dedupe_works.csv.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def norm_title(t: str) -> str:
    """Title with case, punctuation, a list-number prefix ("17.") and ".pdf" ignored."""
    t = re.sub(r"^\s*\d{1,2}\.(?=\S)|\.?\s*pdf\s*$", "", (t or "").lower())
    return re.sub(r"[^a-z0-9]", "", t)


def work_text(c, wid: str) -> str:
    rows = c.execute("SELECT text FROM fragments WHERE work_id=? ORDER BY position", (wid,))
    return re.sub(r"\s+", " ", " ".join(t or "" for (t,) in rows)).strip()


def same_act(short: str, long_: str) -> bool:
    """True if `short` occurs (up to 2% slack) as one run inside `long_`, or the two
    texts are two OCR passes over the same pages (similarity >= 0.90 on the first 15,000
    characters). Same-act pairs score 0.90 to 1.00; groups under generic headers score
    0.10 to 0.20."""
    if not short:
        return True
    probe = short[:20000]
    sm = difflib.SequenceMatcher(None, probe, long_, autojunk=False)
    if sm.find_longest_match(0, len(probe), 0, len(long_)).size >= 0.98 * len(probe):
        return True
    return difflib.SequenceMatcher(None, short[:15000], long_[:15000], autojunk=False).ratio() >= 0.90


def government(c, wid: str) -> bool:
    """Text taken from a government copy (release/merge_dna.py)."""
    row = c.execute("SELECT metadata FROM works WHERE id=?", (wid,)).fetchone()
    return '"text_source": "dna"' in ((row and row[0]) or "")


def plan(c) -> list:
    groups = defaultdict(list)
    for wid, title, created in c.execute(
            "SELECT id, title, created_at FROM works WHERE source_type='legislation'"):
        groups[norm_title(title)].append((wid, title, created or ""))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        texts = {wid: work_text(c, wid) for wid, _, _ in members}
        ranked = sorted(members, key=lambda m: (not government(c, m[0]), -len(texts[m[0]]), "." not in m[1], m[2]))
        keep = ranked[0]
        merge = all(texts[m[0]] == texts[keep[0]] or same_act(texts[m[0]], texts[keep[0]])
                    for m in ranked[1:])
        out.append({"keep": keep, "drop": ranked[1:] if merge else [], "members": ranked,
                    "identical": len({texts[m[0]] for m in members}) == 1, "merge": merge,
                    "reason": "same title"})
    return out


GAZ_RE = re.compile(r"\b(S\.?\s*B\.?|G\.?\s*B\.?)\s*\d{4}\b", re.I)


def title_score(title: str, others: list) -> tuple:
    """Rank titles of one act: names a gazette issue, reads as words (not an OCR'd page
    header such as "2023 STAATSBLAI) VA}[ DE"), not cut off where another member goes
    on, no ".pdf" or list-number residue, then longest."""
    t = (title or "").strip()
    letters = sum(ch.isalpha() for ch in t) / max(len(t), 1)
    header = bool(re.match(r"^\W*\d{4}\s+STAATSBLA|^\W*GOUVERNEMENTSBLAD|^\W*STAATSBLAD\W*$", t, re.I))
    cut = any(norm_title(o) != norm_title(t) and norm_title(o).startswith(norm_title(t)) for o in others)
    residue = bool(re.search(r"\.?\s*pdf$", t, re.I) or re.match(r"^\d{1,2}\.", t))
    return (bool(GAZ_RE.search(t)), letters >= 0.6 and not header, not cut, not residue, len(t))


def plan_identical_text(c, skip: set) -> list:
    """Groups of legislative works (not in `skip`) whose text is identical, at least 300
    characters, under titles that differ beyond punctuation."""
    groups = defaultdict(list)
    for wid, title, created in c.execute(
            "SELECT id, title, created_at FROM works WHERE source_type='legislation'"):
        if wid in skip:
            continue
        text = work_text(c, wid)
        if len(text) >= 300:
            groups[text].append((wid, title, created or ""))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        titles = [m[1] for m in members]
        ranked = sorted(members, key=lambda m: (government(c, m[0]), title_score(m[1], titles), "." in m[1]),
                        reverse=True)
        out.append({"keep": ranked[0], "drop": ranked[1:], "members": ranked, "identical": True,
                    "merge": True, "reason": "identical text"})
    return out


ISSUE_RE = re.compile(r"(S\.?\s*B\.?|G\.?\s*B\.?)\D{0,6}(\d{4})\D{1,8}(\d{1,4})\b", re.I)


def plan_same_issue(c, skip: set) -> list:
    """Pairs of legislative works (not in `skip`) whose titles name the same gazette issue
    and whose texts are two OCR passes over the same pages (similarity >= 0.90 on the
    first 6,000 characters). Below that, same-issue pairs are different documents: a
    consolidated text and its amendment, Bijlage A and B, "no. 13" and "no. 13a"."""
    issues = defaultdict(list)
    for wid, title, created in c.execute(
            "SELECT id, title, created_at FROM works WHERE source_type='legislation'"):
        m = ISSUE_RE.search(title or "")
        if m and wid not in skip:
            issues[(m.group(1).upper()[0], m.group(2), m.group(3))].append((wid, title, created or ""))
    out = []
    for members in issues.values():
        if len(members) < 2:
            continue
        texts = {wid: work_text(c, wid) for wid, _, _ in members}
        ranked = sorted(members, key=lambda m: (not government(c, m[0]), -len(texts[m[0]])))
        keep, drop = ranked[0], []
        for m in ranked[1:]:
            if difflib.SequenceMatcher(None, texts[m[0]][:6000], texts[keep[0]][:6000],
                                       autojunk=False).ratio() >= 0.90:
                drop.append(m)
        if drop:
            titles = [m[1] for m in [keep] + drop]
            best = max(titles, key=lambda t: title_score(t, titles))
            out.append({"keep": (keep[0], best, keep[2]), "drop": drop, "members": [keep] + drop,
                        "identical": False, "merge": True, "reason": "same issue, OCR variant"})
    return out


def clean_title(title: str) -> str:
    return re.sub(r"\s*\.?\s*pdf$", "", re.sub(r"^\d{1,2}\.(?=\S)", "", title.strip()), flags=re.I).strip()


def delete_work(c, wid: str) -> None:
    eids = [e for (e,) in c.execute("SELECT id FROM expressions WHERE work_id=?", (wid,))]
    for e in eids:
        c.execute("DELETE FROM manifestations WHERE expression_id=?", (e,))
        c.execute("DELETE FROM version_diffs WHERE from_expression_id=? OR to_expression_id=?", (e, e))
    c.execute("DELETE FROM fragments WHERE work_id=?", (wid,))
    c.execute("DELETE FROM citations WHERE source_work_id=? OR target_work_id=?", (wid, wid))
    c.execute("DELETE FROM expressions WHERE work_id=?", (wid,))
    c.execute("DELETE FROM work_metadata WHERE work_id=?", (wid,))
    c.execute("DELETE FROM works WHERE id=?", (wid,))


def repoint(c, table: str, old: str, new: str) -> None:
    for col in ("amendment_work_id", "base_work_id"):
        c.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
    # drop self-links and exact repeats created by the repointing (rows touching `new` only)
    c.execute(f"DELETE FROM {table} WHERE amendment_work_id = base_work_id AND base_work_id=?", (new,))
    c.execute(f"""DELETE FROM {table} WHERE (amendment_work_id=? OR base_work_id=?)
                  AND base_work_id IS NOT NULL AND rowid NOT IN (
                    SELECT MIN(rowid) FROM {table} WHERE amendment_work_id=? OR base_work_id=?
                    GROUP BY amendment_work_id, base_work_id)""", (new, new, new, new))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--csv", type=Path, default=ROOT / "release" / "dedupe_works.csv")
    a = ap.parse_args()

    c = sqlite3.connect(a.db)
    groups = plan(c)
    merged = [g for g in groups if g["merge"]]
    print(f"duplicate title groups: {len(groups)}; merged: {len(merged)} "
          f"({sum(g['identical'] for g in merged)} identical text, "
          f"{len(merged) - sum(g['identical'] for g in merged)} partial scan or OCR variant); "
          f"works removed: {sum(len(g['drop']) for g in merged)}; "
          f"kept apart: {len(groups) - len(merged)}")
    for g in groups:
        if not g["merge"]:
            print("  kept apart:", [m[1] for m in g["members"]])
    second = plan_identical_text(c, {d[0] for g in merged for d in g["drop"]})
    print(f"identical text under different titles: {len(second)} groups; "
          f"works removed: {sum(len(g['drop']) for g in second)}")
    merged += second
    third = plan_same_issue(c, {d[0] for g in merged for d in g["drop"]})
    print(f"same gazette issue, OCR variants: {len(third)} groups; "
          f"works removed: {sum(len(g['drop']) for g in third)}")
    merged += third

    if a.apply and merged:
        backup = a.db.with_name(a.db.name + ".pre_dedupe")
        if not backup.exists():
            c.close()
            shutil.copyfile(a.db, backup)
            c = sqlite3.connect(a.db)
        # appended, so a second run (after the first pass was applied) keeps earlier rows
        new_file = not a.csv.exists()
        with open(a.csv, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["kept_id", "kept_title", "removed_id", "removed_title", "identical_text", "reason"])
            for g in merged:
                for d in g["drop"]:
                    w.writerow([g["keep"][0], g["keep"][1], d[0], d[1], g["identical"], g["reason"]])
        for g in merged:
            for d in g["drop"]:
                repoint(c, "amendments", d[0], g["keep"][0])
                repoint(c, "amendment_links", d[0], g["keep"][0])
                delete_work(c, d[0])
            title = clean_title(g["keep"][1])
            if title != c.execute("SELECT title FROM works WHERE id=?", (g["keep"][0],)).fetchone()[0]:
                c.execute("UPDATE works SET title=? WHERE id=?", (title, g["keep"][0]))
                c.execute("UPDATE expressions SET title=? WHERE work_id=?", (title, g["keep"][0]))
        c.commit()
        print("works now:", c.execute("SELECT COUNT(*) FROM works").fetchone()[0])
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
