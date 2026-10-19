#!/usr/bin/env python3
"""
dna_pdfs.py: download the law PDFs published by De Nationale Assemblée (dna.sr) and ingest
them into a staging store, as a government source for the legislation in LexSR.

    python scripts/dna_pdfs.py download     # find and download the PDFs (about 600)
    python scripts/dna_pdfs.py ingest       # text layer or OCR (Tesseract nld+eng) -> staging store
    python scripts/dna_pdfs.py all          # both, in order

The earlier DNA scraper saved the law pages (data/raw/dna/*.html) but not the PDFs they link
to. `download` takes the PDF links from those pages and from the live section listings under
https://www.dna.sr/wetgeving/surinaamse-wetten/, and saves each PDF to data/raw/dna_pdf/.

Both phases are resumable and lose at most one document on a crash: every finished PDF is
appended, flushed and fsynced, to data/raw/dna_pdf/_downloads.jsonl (url, file, sha256, page
title, page url) or data/raw/dna_pdf/_ingest.jsonl (file, work id, fragments, pages, method),
and a restart skips what is listed there. The staging store is data/dna_staging.db, so the
paper's corpus (data/release_build/lexsr_work.db) is not touched. Requests are spaced by
--delay seconds (default 1.5).

OCR uses a local tessdata folder (data/tessdata) holding nld, eng and osd models, so no
system-wide Tesseract language install is needed.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

BASE = "https://www.dna.sr"
START = BASE + "/wetgeving/surinaamse-wetten/"
OUT = ROOT / "data" / "raw" / "dna_pdf"
DOWNLOADS = OUT / "_downloads.jsonl"
INGESTED = OUT / "_ingest.jsonl"
STAGING = ROOT / "data" / "dna_staging.db"
TESSDATA = ROOT / "data" / "tessdata"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LexSR research corpus (https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus)"


def append(path: Path, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def fetch(url: str, delay: float) -> bytes:
    time.sleep(delay)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def page_title(h: str) -> str:
    m = re.search(r"<h1[^>]*>(.*?)</h1>", h, re.S | re.I) or re.search(r"<title>(.*?)</title>", h, re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else ""


def pdf_links(h: str) -> list:
    return sorted({urllib.parse.urljoin(BASE, l) for l in re.findall(r'href="([^"]+\.pdf)"', h, re.I)})


def discover(delay: float) -> dict:
    """{pdf url: (page title, page url)} from the saved pages and the live listings."""
    found = {}
    for f in sorted((ROOT / "data" / "raw" / "dna").glob("*.html")):
        h = f.read_text(encoding="utf-8", errors="replace")
        for u in pdf_links(h):
            found.setdefault(u, (page_title(h), ""))
    # live: section listings -> law pages -> PDFs (pages already seen locally are re-read
    # only for their PDF links, which is cheap)
    seen, queue = set(), [START]
    while queue:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            h = fetch(url, delay).decode("utf-8", errors="replace")
        except Exception as e:  # a listing that fails is reported, not fatal
            print(f"  listing failed: {url}: {e}", flush=True)
            continue
        for u in pdf_links(h):
            found.setdefault(u, (page_title(h), url))
        for l in re.findall(r'href="(/wetgeving/surinaamse-wetten/[^"#?]+)"', h):
            nxt = urllib.parse.urljoin(BASE, l)
            if nxt not in seen:
                queue.append(nxt)
        if len(seen) % 50 == 0:
            print(f"  pages read: {len(seen)}, PDFs found: {len(found)}", flush=True)
    print(f"discovered {len(found)} PDF links ({len(seen)} live pages read)", flush=True)
    return found


def safe_name(url: str) -> str:
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    key = url.rstrip("/").rsplit("/", 2)[-2]  # dna media folder id keeps names unique
    return re.sub(r"[^A-Za-z0-9._-]+", "_", f"{key}_{name}")[:150]


def download(delay: float) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    done = {r["url"] for r in read_jsonl(DOWNLOADS)}
    found = discover(delay)
    todo = [u for u in sorted(found) if u not in done]
    print(f"to download: {len(todo)} (already done: {len(done)})", flush=True)
    for i, url in enumerate(todo, 1):
        name = safe_name(url)
        try:
            data = fetch(url, delay)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] failed {url}: {e}", flush=True)
            continue
        if not data.startswith(b"%PDF"):
            print(f"  [{i}/{len(todo)}] not a PDF: {url}", flush=True)
            continue
        tmp = OUT / (name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, OUT / name)
        title, page = found[url]
        append(DOWNLOADS, {"url": url, "file": name, "sha256": hashlib.sha256(data).hexdigest(),
                           "bytes": len(data), "page_title": title, "page_url": page})
        if i % 25 == 0:
            print(f"  [{i}/{len(todo)}] downloaded", flush=True)
    print("download finished", flush=True)


PRIORITY = ["wet/decreet", "verdrag", "begroting", "naturalisatie"]


def category(r: dict) -> str:
    """Laws and decrees are ingested first; treaties, budgets and naturalisation acts after."""
    s = f"{r.get('page_url', '')} {r['file']} {r.get('page_title', '')}".lower()
    if "begroting" in s:
        return "begroting"
    if "naturalisatie" in s:
        return "naturalisatie"
    if re.search(r"verdrag|overeenkomst|convention|protocol|agreement|treaty", s):
        return "verdrag"
    return "wet/decreet"


def work_id_for(name: str, sha: str) -> str:
    stem = re.sub(r"\.pdf$", "", name, flags=re.I).split("_", 1)[-1]
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:50]
    return f"dna-{slug}-{sha[:8]}"


def ingest() -> None:
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA)
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    from lexsr.core.models import SourceType
    from lexsr.core.store import CanonicalStore
    from lexsr.ocr.extractor import OCREngine
    from lexsr.ocr.pipeline import OCRPipeline, PipelineConfig
    sys.path.insert(0, str(ROOT / "release"))
    from normalise_titles import normalise

    pipe = OCRPipeline(PipelineConfig(ocr_engine=OCREngine.TESSERACT, tesseract_lang="nld+eng"))
    store = CanonicalStore(str(STAGING))
    done = {r["file"] for r in read_jsonl(INGESTED) if r.get("ok")}  # failed files are retried
    rows = [r for r in read_jsonl(DOWNLOADS) if r["file"] not in done]
    rows.sort(key=lambda r: PRIORITY.index(category(r)))
    counts = {c: sum(category(r) == c for r in rows) for c in PRIORITY}
    print(f"to ingest: {len(rows)} (already done: {len(done)}), in this order: {counts}", flush=True)
    for i, r in enumerate(rows, 1):
        t0 = time.time()
        wid = work_id_for(r["file"], r["sha256"])
        raw_title = r["page_title"] or re.sub(r"[_-]+", " ", r["file"].split("_", 1)[-1][:-4])
        title = normalise(raw_title)[0]
        try:
            res = pipe.process_pdf(str(OUT / r["file"]), work_id=wid, title=title, version_date=date.today(),
                                   metadata={"source": "dna", "source_url": r["url"]})
        except Exception as e:
            append(INGESTED, {"file": r["file"], "work_id": wid, "ok": False, "error": str(e)[:300]})
            print(f"  [{i}/{len(rows)}] error {r['file']}: {str(e)[:120]}", flush=True)
            continue
        if not res.success or not res.fragments:
            append(INGESTED, {"file": r["file"], "work_id": wid, "ok": False,
                              "error": "; ".join(res.errors)[:300] or "no fragments"})
            continue
        work = res.work
        work.title, work.source_url = title, r["url"]
        work.source_type = SourceType.LEGISLATION
        work.metadata = {**(work.metadata or {}), "source": "dna", "source_file": r["file"],
                         "sha256": r["sha256"], "page_title": r["page_title"], "page_url": r["page_url"]}
        store.save_work(work)
        store.save_expression(res.expression)
        for f in res.fragments:
            store.save_fragment(f)
        method = "text" if all(o.is_digital for o in res.ocr_results) else "ocr"
        append(INGESTED, {"file": r["file"], "work_id": wid, "ok": True, "title": title,
                          "fragments": len(res.fragments), "pages": len(res.ocr_results),
                          "method": method, "seconds": round(time.time() - t0, 1)})
        print(f"  [{i}/{len(rows)}] {len(res.fragments):4d} fragments, {len(res.ocr_results)} pages, "
              f"{time.time() - t0:.0f}s  {title[:60]}", flush=True)
    store.close()
    print("ingest finished", flush=True)


REOCR = OUT / "_reocr.jsonl"


def needs_reocr(r: dict) -> bool:
    """OCR'd before page rotation and two-page-spread splitting existed: rotated or landscape sheets."""
    from pypdf import PdfReader
    if not r.get("ok") or r.get("method") != "ocr":
        return False
    try:
        pg = PdfReader(str(OUT / r["file"])).pages[0]
    except Exception:
        return False
    w, h = float(pg.mediabox.width), float(pg.mediabox.height)
    rot = int(pg.get("/Rotate", 0) or 0)
    return bool(rot) or w > 1.2 * h


def _ocr_one(path: str, wid: str, title: str, url: str):
    os.environ["TESSDATA_PREFIX"] = str(TESSDATA)
    import logging
    logging.disable(logging.WARNING)
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    from lexsr.ocr.extractor import OCREngine
    from lexsr.ocr.pipeline import OCRPipeline, PipelineConfig
    pipe = OCRPipeline(PipelineConfig(ocr_engine=OCREngine.TESSERACT, tesseract_lang="nld+eng"))
    return pipe.process_pdf(path, work_id=wid, title=title, version_date=date.today(),
                            metadata={"source": "dna", "source_url": url})


def reocr(workers: int) -> None:
    """Re-OCR rotated and two-page-spread scans in parallel, replacing their fragments in the staging store."""
    import sqlite3
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from lexsr.core.models import SourceType
    from lexsr.core.store import CanonicalStore

    latest = {}
    for r in read_jsonl(INGESTED):
        latest[r["file"]] = r
    done = {r["file"] for r in read_jsonl(REOCR)}
    meta = {r["file"]: r for r in read_jsonl(DOWNLOADS)}
    todo = [r for r in latest.values() if r["file"] not in done and needs_reocr(r)]
    print(f"to re-OCR: {len(todo)} (already done: {len(done)})", flush=True)
    store = CanonicalStore(str(STAGING))
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(_ocr_one, str(OUT / r["file"]), r["work_id"], r["title"], meta[r["file"]]["url"]): r
                for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r, m = futs[fut], meta[futs[fut]["file"]]
            try:
                res = fut.result()
            except Exception as e:
                append(REOCR, {"file": r["file"], "ok": False, "error": str(e)[:300]})
                continue
            if not res.fragments:
                append(REOCR, {"file": r["file"], "ok": False, "error": "no fragments", "kept_old": True})
                continue
            work = res.work
            work.title, work.source_url = r["title"], m["url"]
            work.source_type = SourceType.LEGISLATION
            work.metadata = {**(work.metadata or {}), "source": "dna", "source_file": r["file"],
                             "sha256": m["sha256"], "page_title": m["page_title"], "page_url": m["page_url"]}
            con = sqlite3.connect(str(STAGING))
            con.execute("DELETE FROM fragments WHERE work_id = ?", (r["work_id"],))
            con.commit()
            con.close()
            store.save_work(work)
            store.save_expression(res.expression)
            for f in res.fragments:
                store.save_fragment(f)
            append(REOCR, {"file": r["file"], "work_id": r["work_id"], "ok": True,
                           "fragments_before": r["fragments"], "fragments": len(res.fragments)})
            print(f"  [{i}/{len(todo)}] {r['fragments']:4d} -> {len(res.fragments):4d} fragments  {r['title'][:60]}",
                  flush=True)
    store.close()
    print("re-OCR finished", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=["download", "ingest", "all", "reocr"])
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--workers", type=int, default=6, help="parallel OCR processes (reocr)")
    a = ap.parse_args()
    if a.phase in ("download", "all"):
        download(a.delay)
    if a.phase in ("ingest", "all"):
        ingest()
    if a.phase == "reocr":
        reocr(a.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
