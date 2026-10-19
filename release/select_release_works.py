#!/usr/bin/env python3
"""
select_release_works.py: decide which works the public release may contain.

    python release/select_release_works.py --db data/release_build/lexsr_work.db

Only text published by a Surinamese government body is redistributed:

  - case law from rechtspraak.sr, the Hof van Justitie's own decision portal: all decisions;
  - legislation whose text was taken over from the PDFs published by De Nationale Assemblée
    (release/merge_dna.py);
  - legislation whose stored text comes from a file downloaded from a government site
    (parliament DNA.sr, tax and maritime authorities, standards bureau, government portal,
    ministries). A work qualifies when its own source file name occurs only on government
    sites, or when the SRiS and government files under that name are byte-identical.

Excluded: works obtained from SRiS (sris.sr), a private foundation whose terms allow copies
for personal use only; works whose file name occurs on both SRiS and a government site with
different files (the stored text matches the SRiS copy); and works from local archives of
unknown origin. Their file names and SHA-256 hashes stay listed in raw_sources.csv.

Writes release/release_works.csv (work_id, source_type, title, release, reason).
"""
from __future__ import annotations

import argparse
import collections
import csv
import sqlite3
from pathlib import Path

from map_work_sources import key

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    ap.add_argument("--original", type=Path, default=ROOT / "data" / "canonical.db")
    ap.add_argument("--out", type=Path, default=ROOT / "release" / "release_works.csv")
    a = ap.parse_args()

    files = collections.defaultdict(list)
    for r in csv.DictReader(open(ROOT / "manifest" / "raw_sources.csv", encoding="utf-8")):
        if r["source"] != "rechtspraak":
            files[key(r["file"])].append((r["source"], r["sha256"]))
    keys = sorted(files)

    def sources(title: str) -> list:
        k = key(title or "")
        if k in files:
            return files[k]
        return [f for fk in keys if len(k) >= 20 and fk.startswith(k) for f in files[fk]]

    oc = sqlite3.connect(f"file:{a.original}?mode=ro", uri=True)
    orig = dict(oc.execute("SELECT id, title FROM works"))
    # fallback for titles taken from the OCR'd heading: the file SHA-256 stored as checksum
    by_hash = {r["sha256"]: (r["source"], r["sha256"])
               for r in csv.DictReader(open(ROOT / "manifest" / "raw_sources.csv", encoding="utf-8"))}
    by_checksum = collections.defaultdict(list)
    for wid, cs in oc.execute("SELECT work_id, checksum FROM expressions WHERE checksum IS NOT NULL"):
        if cs in by_hash:
            by_checksum[wid].append(by_hash[cs])
    c = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    rows, count = [], collections.Counter()
    for wid, stype, title, wmeta in c.execute("SELECT id, source_type, title, metadata FROM works ORDER BY id"):
        if stype == "case_law":
            ok, why = True, "rechtspraak.sr (Hof van Justitie)"
        elif '"text_source": "dna"' in (wmeta or ""):
            ok, why = True, "government site: dna.sr (PDF published by De Nationale Assemblee)"
        else:
            fs = sources(orig.get(wid, "")) or by_checksum.get(wid, [])
            sites = {s for s, _ in fs}
            gov = sorted(sites - {"sris"})
            if not fs:
                ok, why = False, "source unknown (local archive)"
            elif "sris" not in sites:
                ok, why = True, "government site: " + ", ".join(gov)
            elif gov and len({h for _, h in fs}) == 1:
                ok, why = True, "government site (identical file also on SRiS): " + ", ".join(gov)
            elif gov:
                ok, why = False, "text taken from the SRiS copy"
            else:
                ok, why = False, "SRiS only"
        count[(stype, ok)] += 1
        rows.append((wid, stype, title, int(ok), why))

    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["work_id", "source_type", "title", "release", "reason"])
        w.writerows(rows)
    for (stype, ok), n in sorted(count.items()):
        print(f"{stype:12s} {'released' if ok else 'not released':13s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
