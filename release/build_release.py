#!/usr/bin/env python3
"""
build_release.py: assemble the LexSR corpus release (v1.0) from a canonical store.

    python release/build_release.py --db data/release_build/lexsr_work.db

Writes data/release_build/lexsr-corpus-v1.0/ (or --out):

  lexsr_corpus_v1.0.sqlite      the works from government sources (release/release_works.csv),
                                taken from the canonical store (VACUUM INTO, read only source)
  <table>.parquet               one Parquet file per table, same rows
  knowledge_graph.graphml       the graph over the released works (python -m lexsr build-graph)
  knowledge_graph_stats.json
  corpus_stats.json             figures for the release
  research_corpus_stats.json    figures for the full research corpus, as reported in the paper
  raw_sources.csv               SHA-256 manifest of the government source files
  work_sources.csv              source file(s) of each released work
  README.md                     data card (release/README.md)
  LICENSE.txt                   CC BY 4.0 notice for the compilation
  SHA256SUMS

Each output is written to a temp name, fsynced and renamed, and listed in
_progress.json, so a rerun skips finished files. --force rebuilds everything.
The source database is opened read only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "1.0"
TABLES = ["works", "expressions", "fragments", "citations", "amendment_links",
          "amendments", "version_diffs", "work_metadata", "manifestations"]

# Same patterns as annotation/export_tasks.py, with the gazette (S.B./G.B.) captured.
GAZ_REF = re.compile(
    r"(S\.?\s*B\.?|G\.?\s*B\.?|Staatsblad|Gouvernementsblad)\D{0,6}(\d{4})\D{1,8}(\d{1,4})", re.I)
GAZ_TITLE = re.compile(r"(S\.?\s*B\.?|G\.?\s*B\.?)\D{0,6}(\d{4})\D{1,8}(\d{1,4})", re.I)


def gazette_key(m) -> tuple:
    """(gazette, year, number); S = Staatsblad, G = Gouvernementsblad."""
    return (m.group(1)[0].upper(), m.group(2), int(m.group(3)))


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def fsync_replace(tmp: Path, dst: Path) -> None:
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, dst)


class Progress:
    def __init__(self, out: Path, force: bool):
        self.path = out / "_progress.json"
        self.done = {} if force or not self.path.exists() else json.loads(self.path.read_text())

    def ok(self, name: str, out: Path) -> bool:
        p = out / name
        return name in self.done and p.exists() and self.done[name] == sha256(p)

    def mark(self, name: str, out: Path) -> None:
        self.done[name] = sha256(out / name)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.done, indent=1))
        fsync_replace(tmp, self.path)
        print("built", name, flush=True)


def corpus_stats(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    q = lambda s: c.execute(s).fetchone()[0]  # noqa: E731
    cites = q("SELECT COUNT(*) FROM citations")
    resolved = q("SELECT COUNT(*) FROM citations WHERE target_work_id IS NOT NULL")
    titles = {(t or "").strip().lower() for (t,) in c.execute("SELECT title FROM works")}
    exact = sum(1 for (r,) in c.execute("SELECT raw_text FROM citations")
                if (r or "").strip().lower() in titles)
    by_issue = set()
    for (t,) in c.execute("SELECT title FROM works"):
        m = GAZ_TITLE.search(t or "")
        if m:
            by_issue.add(gazette_key(m))
    by_num = {k[1:] for k in by_issue}
    unresolved = [r for (r,) in c.execute("SELECT raw_text FROM citations WHERE target_work_id IS NULL")]
    gaz = [GAZ_REF.search(r or "") for r in unresolved]
    gaz_refs = sum(1 for m in gaz if m)
    gaz_cands = sum(1 for m in gaz if m and gazette_key(m)[1:] in by_num)
    gaz_same_issue = sum(1 for m in gaz if m and gazette_key(m) in by_issue)
    s = {
        "works": q("SELECT COUNT(*) FROM works"),
        "works_legislation": q("SELECT COUNT(*) FROM works WHERE source_type='legislation'"),
        "works_case_law": q("SELECT COUNT(*) FROM works WHERE source_type='case_law'"),
        "expressions": q("SELECT COUNT(*) FROM expressions"),
        "fragments": q("SELECT COUNT(*) FROM fragments"),
        "considerations": q("SELECT COUNT(*) FROM fragments WHERE fragment_type='consideration'"),
        "citations": cites,
        "citations_resolved": resolved,
        "citations_resolved_pct": round(100 * resolved / cites, 1) if cites else 0,
        "citations_by_type": dict(c.execute(
            "SELECT citation_type, COUNT(*) FROM citations GROUP BY 1").fetchall()),
        "citations_resolved_by_type": dict(c.execute(
            "SELECT citation_type, COUNT(*) FROM citations WHERE target_work_id IS NOT NULL GROUP BY 1").fetchall()),
        "exact_title_baseline": exact,
        "exact_title_baseline_pct": round(100 * exact / cites, 1) if cites else 0,
        "unresolved": len(unresolved),
        "unresolved_gazette_refs": gaz_refs,
        "unresolved_gazette_refs_with_year_number_match": gaz_cands,
        "unresolved_gazette_refs_same_gazette_year_number_in_corpus": gaz_same_issue,
        "amendment_links": q("SELECT COUNT(*) FROM amendment_links"),
        "amendment_links_applied": q("SELECT COALESCE(SUM(resolved),0) FROM amendment_links"),
    }
    c.close()
    return s


def keep_government_sources(db: Path) -> None:
    """Remove every work that release/release_works.csv does not mark for release (works
    obtained from SRiS or from local archives of unknown origin), with its expressions,
    fragments, outgoing citations and metadata. A citation or amendment link from a released
    work to a removed one keeps its raw text; the target id moves to metadata
    ("target_not_released") so no id points outside the release."""
    import csv
    with open(ROOT / "release" / "release_works.csv", encoding="utf-8") as fh:
        keep = {r["work_id"] for r in csv.DictReader(fh) if r["release"] == "1"}
    c = sqlite3.connect(db)
    c.execute("CREATE TEMP TABLE keep (id TEXT PRIMARY KEY)")
    c.executemany("INSERT INTO keep VALUES (?)", [(w,) for w in keep])
    gone = "NOT IN (SELECT id FROM keep)"
    c.execute(f"DELETE FROM version_diffs WHERE from_expression_id IN (SELECT id FROM expressions WHERE work_id {gone})"
              f" OR to_expression_id IN (SELECT id FROM expressions WHERE work_id {gone})")
    c.execute(f"DELETE FROM manifestations WHERE expression_id IN (SELECT id FROM expressions WHERE work_id {gone})")
    for table, col in (("fragments", "work_id"), ("expressions", "work_id"), ("work_metadata", "work_id"),
                       ("citations", "source_work_id"), ("amendment_links", "amendment_work_id"),
                       ("amendments", "amendment_work_id")):
        c.execute(f"DELETE FROM {table} WHERE {col} {gone}")
    rows = c.execute(f"SELECT id, target_work_id, COALESCE(metadata,'{{}}') FROM citations "
                     f"WHERE target_work_id IS NOT NULL AND target_work_id {gone}").fetchall()
    for cid, tgt, meta in rows:
        m = json.loads(meta or "{}")
        m["target_not_released"] = tgt
        c.execute("UPDATE citations SET target_work_id = NULL, target_fragment_id = NULL, metadata = ? WHERE id = ?",
                  (json.dumps(m, ensure_ascii=False), cid))
    for table in ("amendment_links", "amendments"):
        c.execute(f"UPDATE {table} SET base_work_id = NULL WHERE base_work_id IS NOT NULL AND base_work_id {gone}")
    n = c.execute(f"DELETE FROM works WHERE id {gone}").rowcount
    c.commit()
    c.execute("VACUUM")
    c.close()
    print(f"government sources only: removed {n} works, {len(rows)} citation targets moved to metadata")


def withhold_text(db: Path) -> None:
    """Blank the text of works listed in release/withhold_text.csv (material that is not an
    official text under Auteurswet art. 11). Rows, ids, titles and citations stay, so every
    count is unchanged; works.metadata gets "text_withheld" with the reason."""
    import csv
    with open(ROOT / "release" / "withhold_text.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    c = sqlite3.connect(db)
    for r in rows:
        wid = r["work_id"]
        if not c.execute("SELECT 1 FROM works WHERE id = ?", (wid,)).fetchone():
            continue  # not in the release (not from a government source)
        n = c.execute("UPDATE fragments SET text = NULL, title = NULL WHERE work_id = ?", (wid,)).rowcount
        c.execute("UPDATE expressions SET full_text = NULL WHERE work_id = ?", (wid,))
        meta = json.loads(c.execute("SELECT COALESCE(metadata,'{}') FROM works WHERE id = ?",
                                    (wid,)).fetchone()[0] or "{}")
        meta["text_withheld"] = r["reason"]
        c.execute("UPDATE works SET metadata = ? WHERE id = ?", (json.dumps(meta, ensure_ascii=False), wid))
        print(f"withheld text: {wid} ({n} fragments)")
    c.commit()
    c.execute("VACUUM")
    c.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "release_build" / f"lexsr-corpus-v{VERSION}")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    prog = Progress(out, a.force)
    src = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    name = f"lexsr_corpus_v{VERSION}.sqlite"
    if not prog.ok(name, out):
        tmp = out / (name + ".tmp")
        tmp.unlink(missing_ok=True)
        src.execute(f"VACUUM INTO '{tmp.as_posix()}'")
        keep_government_sources(tmp)
        withhold_text(tmp)
        fsync_replace(tmp, out / name)
        prog.mark(name, out)
    src.close()
    rel = sqlite3.connect(f"file:{out / name}?mode=ro", uri=True)

    import pyarrow as pa
    import pyarrow.parquet as pq
    for t in TABLES:
        name = f"{t}.parquet"
        if prog.ok(name, out):
            continue
        cur = rel.execute(f'SELECT * FROM "{t}" ORDER BY rowid')
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        data = {col: [r[i] for r in rows] for i, col in enumerate(cols)}
        table = pa.table({k: pa.array(v, type=pa.string() if all(x is None or isinstance(x, str) for x in v) else None)
                          for k, v in data.items()}) if rows else pa.table({k: pa.array([], pa.string()) for k in cols})
        tmp = out / (name + ".tmp")
        pq.write_table(table, tmp, compression="zstd")
        fsync_replace(tmp, out / name)
        prog.mark(name, out)

    if not (prog.ok("knowledge_graph.graphml", out) and prog.ok("knowledge_graph_stats.json", out)):
        rc = subprocess.call([sys.executable, "-m", "lexsr", "--db", str((out / f"lexsr_corpus_v{VERSION}.sqlite").resolve()),
                              "build-graph", "--output", str(out / "knowledge_graph.graphml"),
                              "--stats-output", str(out / "knowledge_graph_stats.json")],
                             cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
        if rc:
            return rc
        prog.mark("knowledge_graph.graphml", out)
        prog.mark("knowledge_graph_stats.json", out)

    # corpus_stats.json describes the release; research_corpus_stats.json the full research
    # corpus the paper's figures come from (counts only; its SRiS texts are not in the release)
    for name, db in (("corpus_stats.json", out / f"lexsr_corpus_v{VERSION}.sqlite"),
                     ("research_corpus_stats.json", a.db)):
        if not prog.ok(name, out):
            tmp = out / (name + ".tmp")
            tmp.write_text(json.dumps(corpus_stats(db), indent=2))
            fsync_replace(tmp, out / name)
            prog.mark(name, out)

    # provenance of the released works only: their source files, and work -> file
    import csv
    released = {r[0] for r in rel.execute("SELECT id FROM works")}
    # works whose text comes from a DNA.sr PDF (release/merge_dna.py): that PDF is their source
    dna = {}
    for wid, m in rel.execute("SELECT id, metadata FROM works WHERE metadata LIKE '%\"text_source\": \"dna\"%'"):
        m = json.loads(m)
        dna[wid] = m
    downloads = {}
    dl = ROOT / "data" / "raw" / "dna_pdf" / "_downloads.jsonl"
    if dl.exists():
        for line in open(dl, encoding="utf-8"):
            r = json.loads(line)
            downloads[r["file"]] = r
    dna_raw = [{"source": "dna_pdf", "file": m["dna_file"], "sha256": m.get("dna_sha256", ""),
                "url": m.get("dna_url", ""),
                "bytes": str((ROOT / "data" / "raw" / "dna_pdf" / m["dna_file"]).stat().st_size)
                if (ROOT / "data" / "raw" / "dna_pdf" / m["dna_file"]).exists() else ""}
               for m in dna.values() if m.get("dna_file") in downloads]
    dna_work = [{"work_id": wid, "source": "dna_pdf", "file": m["dna_file"]} for wid, m in dna.items()]
    for name, path, keep_row, extra in (
            ("raw_sources.csv", ROOT / "manifest" / "raw_sources.csv", lambda r: r["source"] != "sris", dna_raw),
            ("work_sources.csv", ROOT / "release" / "work_sources.csv",
             lambda r: r["work_id"] in released and r["work_id"] not in dna, dna_work)):
        with open(path, encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            rows = [r for r in rd if keep_row(r)]
            # the manifest may already list the DNA.sr PDFs; add only those it lacks
            listed = {(r.get("source"), r.get("file"), r.get("work_id")) for r in rows}
            rows += sorted((e for e in extra if (e.get("source"), e.get("file"), e.get("work_id")) not in listed),
                           key=lambda r: tuple(r.values()))
            fields = rd.fieldnames
        tmp = out / (name + ".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        if prog.ok(name, out) and prog.done[name] == sha256(tmp):
            tmp.unlink()
            continue
        fsync_replace(tmp, out / name)
        prog.mark(name, out)

    for name, s in {"README.md": ROOT / "release" / "README.md",
                    "LICENSE.txt": ROOT / "release" / "LICENSE-DATA.txt"}.items():
        if prog.ok(name, out) and prog.done[name] == sha256(s):
            continue
        tmp = out / (name + ".tmp")
        shutil.copyfile(s, tmp)
        fsync_replace(tmp, out / name)
        prog.mark(name, out)
    rel.close()
    # the graph step may leave the store in WAL mode; fold it back so only the .sqlite ships
    c = sqlite3.connect(out / f"lexsr_corpus_v{VERSION}.sqlite")
    c.execute("PRAGMA journal_mode=DELETE")
    c.close()
    for side in ("-wal", "-shm"):
        (out / f"lexsr_corpus_v{VERSION}.sqlite{side}").unlink(missing_ok=True)

    files = sorted(p for p in out.rglob("*") if p.is_file() and not p.name.startswith("_")
                   and p.name != "SHA256SUMS" and not p.name.endswith((".tmp", "-wal", "-shm")))
    tmp = out / "SHA256SUMS.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for p in files:
            fh.write(f"{sha256(p)}  {p.relative_to(out).as_posix()}\n")
    fsync_replace(tmp, out / "SHA256SUMS")
    print(json.dumps(json.loads((out / "corpus_stats.json").read_text()), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
