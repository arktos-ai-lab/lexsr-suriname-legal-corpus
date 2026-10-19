#!/usr/bin/env python3
"""
map_work_sources.py: tie each legislative work to the source file(s) it was built from.

    python release/map_work_sources.py --db data/release_build/lexsr_work.db

Legislation ingestion did not record the source file per work; the work title was derived
from the file name. This matches each work's original title (before title normalisation),
and the original titles of the duplicates merged into it (release/dedupe_works.csv), against
the file names in manifest/raw_sources.csv, ignoring case, punctuation, a list-number prefix
and the extension. Titles cut off at 70 characters are matched as a prefix of the file name.
Works whose title came from the OCR'd heading are matched by the file SHA-256 that ingestion
stored as the expression checksum.

Writes release/work_sources.csv (work_id, source, file; one row per matching file) and
prints how many works come from each source site. Case law is mapped by its URL.
"""
from __future__ import annotations

import argparse
import collections
import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def key(s: str) -> str:
    s = re.sub(r"\.(pdf|html?|docx?|txt)$", "", s.strip(), flags=re.I)
    s = re.sub(r"^\d{1,2}[_.\s]+", "", s)
    s = re.sub(r"\.?\s*pdf$", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    ap.add_argument("--original", type=Path, default=ROOT / "data" / "canonical.db",
                    help="store with the original (file-name) titles, read only")
    ap.add_argument("--out", type=Path, default=ROOT / "release" / "work_sources.csv")
    a = ap.parse_args()

    files = collections.defaultdict(list)
    caselaw = {}
    for r in csv.DictReader(open(ROOT / "manifest" / "raw_sources.csv", encoding="utf-8")):
        if r["source"] == "rechtspraak":
            caselaw[r["url"].rstrip("/")] = (r["source"], r["file"])
        else:
            files[key(r["file"])].append((r["source"], r["file"]))
    keys = sorted(files)

    orig = sqlite3.connect(f"file:{a.original}?mode=ro", uri=True)
    original_title = dict(orig.execute("SELECT id, title FROM works"))
    # works whose title came from the OCR'd heading rather than the file name: the ingestion
    # recorded the file's SHA-256 as the expression checksum
    by_hash = {r["sha256"]: (r["source"], r["file"])
               for r in csv.DictReader(open(ROOT / "manifest" / "raw_sources.csv", encoding="utf-8"))}
    by_checksum = collections.defaultdict(list)
    for wid, cs in orig.execute("SELECT work_id, checksum FROM expressions WHERE checksum IS NOT NULL"):
        if cs in by_hash:
            by_checksum[wid].append(by_hash[cs])
    merged = collections.defaultdict(list)
    for r in csv.DictReader(open(ROOT / "release" / "dedupe_works.csv", encoding="utf-8")):
        merged[r["kept_id"]].append(r["removed_id"])

    def match(title: str) -> list:
        k = key(title or "")
        if k in files:
            return files[k]
        if len(k) >= 20:
            return [f for fk in keys if fk.startswith(k) for f in files[fk]]
        return []

    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows, by_source, unmatched = [], collections.Counter(), 0
    for wid, stype, url in c.execute("SELECT id, source_type, source_url FROM works"):
        if stype == "case_law":
            hits = [caselaw[url.rstrip("/")]] if url and url.rstrip("/") in caselaw else []
        else:
            hits = []
            for i in [wid] + merged[wid]:
                hits += match(original_title.get(i, "")) or by_checksum.get(i, [])
        hits = sorted(set(hits))
        unmatched += not hits
        for s in {s for s, _ in hits}:
            by_source[s] += 1
        rows += [(wid, s, f) for s, f in hits]

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["work_id", "source", "file"])
        w.writerows(rows)
    print(f"works without a matched file: {unmatched}")
    print("works per source site:", dict(by_source.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
