#!/usr/bin/env python3
"""
prepare_release_db.py: derive the v1.0 release store from the paper's canonical store.

    python release/prepare_release_db.py --src data/canonical.db --out data/release_build/lexsr_work.db

Steps (CPU only, a few minutes; every step is idempotent, so an interrupted run can be
started again with the same arguments):

  1. copy --src to --out (SQLite backup API; --src is opened read only). Skipped if --out exists.
  2. merge legislative works ingested twice (release/dedupe_works.py --apply), then write
     gazette references in titles as "S.B. 2016 no. 63" (release/normalise_titles.py --apply);
     take in the DNA.sr PDFs from data/dna_staging.db (release/merge_dna.py --apply: the
     government copy replaces the text of the same act, other DNA works are added), and run
     both steps again.
  3. re-segment the 300 decisions from data/raw/rechtspraak/*.html with the current
     case-law pipeline, saving only fragments that are new or whose text differs and
     removing those it no longer produces.
  4. clear the citations table and extract all citations again with
     `python -m lexsr link-citations` (one pass over every expression).

Then run release/build_release.py on --out.
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def resegment(db: Path) -> None:
    from lexsr.core.store import CanonicalStore
    from lexsr.ingestion.caselaw_pipeline import CaseLawPipeline
    from lexsr.ingestion.scrapers.rechtspraak import RechtspraakScraper

    conn = sqlite3.connect(db)
    urls = {u.rstrip("/").rsplit("/", 1)[-1] + ".html": (wid, u) for wid, u in
            conn.execute("SELECT id, source_url FROM works WHERE source_type='case_law'")}
    store = CanonicalStore(str(db))
    scraper = RechtspraakScraper(rate_limit=0)
    pipe = CaseLawPipeline()
    saved = removed = 0
    for f in sorted((ROOT / "data" / "raw" / "rechtspraak").glob("*.html")):
        wid, url = urls[f.name]
        content = f.read_bytes()
        scraper._fetch_url = lambda _u, c=content: c  # parse the stored page, no network
        res = pipe.process_document(scraper.scrape_decision(url))
        if not res.success or res.work.id != wid:
            raise SystemExit(f"{f.name}: {res.errors or res.work.id}")
        old = {i: (t, p, ft) for i, t, p, ft in conn.execute(
            "SELECT id, text, position, fragment_type FROM fragments WHERE work_id=?", (wid,))}
        for fr in res.fragments:
            if old.get(fr.id) != (fr.text, fr.position, fr.fragment_type.value):
                store.save_fragment(fr)
                # the store's upsert keeps an existing row's position and type
                conn.execute("UPDATE fragments SET position=?, fragment_type=?, number=? WHERE id=?",
                             (fr.position, fr.fragment_type.value, fr.number, fr.id))
                conn.commit()
                saved += 1
        # fragments the current segmentation no longer produces (e.g. a whole-decision
        # fragment that is now split into considerations)
        stale = set(old) - {fr.id for fr in res.fragments}
        if stale:
            conn.executemany("DELETE FROM fragments WHERE id=?", [(i,) for i in stale])
            conn.commit()
            removed += len(stale)
    store.close()
    conn.close()
    print(f"re-segmentation: {saved} fragments added or updated, {removed} removed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=ROOT / "data" / "canonical.db")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    if not a.out.exists():
        src = sqlite3.connect(f"file:{a.src}?mode=ro", uri=True)
        tmp = a.out.with_suffix(".tmp")
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        src.close()
        tmp.replace(a.out)
    # dedupe and clean titles, take in the DNA.sr PDFs (matched on the cleaned gazette
    # references), then dedupe and clean again for the works the DNA set added
    for step in ("dedupe_works.py", "normalise_titles.py", "merge_dna.py", "dedupe_works.py", "normalise_titles.py"):
        rc = subprocess.call([sys.executable, str(ROOT / "release" / step), "--db", str(a.out), "--apply"])
        if rc:
            return rc
    resegment(a.out)
    conn = sqlite3.connect(a.out)
    conn.execute("DELETE FROM citations")
    conn.commit()
    conn.close()
    return subprocess.call([sys.executable, "-m", "lexsr", "--db", str(a.out), "link-citations"],
                           cwd=ROOT, env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})


if __name__ == "__main__":
    sys.exit(main())
