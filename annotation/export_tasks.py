"""
Export human-verification tasks for the claims made in the JURIX 2026 paper.

Each task is a JSONL file in annotation/tasks/, one item per line, with a
stable item id so labels survive re-export. Samples are seeded, so the
same database always yields the same items.

Usage:
    python annotation/export_tasks.py --db data/canonical_backup_20260913_submitted.db

Tasks
-----
gold           every case of the 61-case citation benchmark: is the expected
               answer the right one for the input?
instructions   every instruction the regex parser extracts from the amendment
               documents: is it correct?
parse_failures every amendment document with no classified instruction: does
               the stored text contain an operative instruction?
links          random sample of citations the pipeline linked to a work: is
               the link correct? (precision estimate)
gazette        random sample of unlinked gazette references whose year and
               number match a work title: is that the cited act?
segmentation   random sample of court decisions: are the considerations
               split correctly?
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from lexsr.consolidation.parser import AmendmentParser  # noqa: E402
from lexsr.evaluation.dataset import KnownAnswerDataset  # noqa: E402
import amendment_diagnostics as ad  # noqa: E402

TASK_DIR = Path(__file__).resolve().parent / "tasks"
SEED = 20261014

GAZ_REF = re.compile(
    r"(?:S\.?\s*B\.?|G\.?\s*B\.?|Staatsblad|Gouvernementsblad)\D{0,6}(\d{4})\D{1,8}(\d{1,4})", re.I)
GAZ_TITLE = re.compile(r"(?:S\.?\s*B\.?|G\.?\s*B\.?)\D{0,6}(\d{4})\D{1,8}(\d{1,4})", re.I)


def context(text: str, needle: str, width: int = 400) -> str:
    """Return text around the first occurrence of needle, marked with [[ ]]."""
    text = text or ""
    i = text.find(needle) if needle else -1
    if i < 0 and needle:
        # same reference with other capitals or line breaks
        m = re.search(r"\s+".join(map(re.escape, needle.split())), text, re.I)
        if m:
            i, needle = m.start(), m.group(0)
    if i < 0:
        return text[: 2 * width]
    return (text[max(0, i - width):i] + "[[" + needle + "]]"
            + text[i + len(needle):i + len(needle) + width])


def preceding(text: str, block: str, width: int = 600) -> str:
    """Return the text just before block in the document (heading context)."""
    i = text.find((block or "")[:80])
    return text[max(0, i - width):i] if i > 0 else ""


def export_gold(conn):
    ds = KnownAnswerDataset.suriname_legal_seed()
    for c in ds.cases():
        yield {
            "id": c.id,
            "title": f"{c.category} / {c.difficulty}",
            "fields": {
                "Input text": c.input_text,
                "Expected answer": "\n".join(c.expected_uris) or "(none: should NOT resolve)",
                "Query date": str(c.query_date or ""),
                "Tags": ", ".join(c.tags),
            },
            "question": "Is the expected answer correct for this input?",
        }


def export_instructions(conn):
    parser = AmendmentParser(use_llm=False)
    ids = sorted({r[0] for r in conn.execute("SELECT amendment_work_id FROM amendment_links")})
    for wid in ids:
        text, title = ad._get_text_and_title(conn, wid)
        if not text.strip():
            continue
        real = [i for i in parser.parse(text, title=title).instructions
                if i.metadata.get("parse_error") is None]
        for n, ins in enumerate(real):
            extracted = {k: v for k, v in {
                "operation": ins.operation.value,
                "target law": ins.target_work_ref,
                "article": ins.target_article,
                "paragraph (lid)": ins.target_paragraph,
                "sub": ins.target_sub,
                "insert after": ins.position_after,
                "new article nr": ins.new_article_number,
                "find": ins.find_text,
                "replace with": ins.replace_text,
                "new text": ins.new_text,
                "confidence": ins.confidence,
            }.items() if v not in (None, "")}
            yield {
                "id": f"{wid}#{n}",
                "title": title,
                "fields": {
                    "Preceding text": preceding(text, ins.source_text),
                    "Source block": ins.source_text,
                    "Extracted instruction": json.dumps(extracted, ensure_ascii=False, indent=1),
                },
                "question": "Does the extracted instruction match the source block?\n"
                            "1 = yes: right operation (replace / insert / delete), right article, right text.\n"
                            "2 = no: wrong article, wrong operation, wrong or garbled text, or nonsense.\n"
                            "3 = not sure (e.g. the OCR is unreadable).",
            }


def export_parse_failures(conn, db_path):
    rows = ad.run_diagnostics(db_path, n=10_000)
    seen = set()
    for r in rows:
        if r.stage == "OK" or r.work_id in seen:
            continue
        seen.add(r.work_id)
        text, title = ad._get_text_and_title(conn, r.work_id)
        yield {
            "id": r.work_id,
            "title": title,
            "fields": {"Stored text (everything the parser saw)": text},
            "question": "The parser found no amendment instruction in this text. Can YOU read one?\n"
                        "An instruction is a sentence that changes a law, like 'Artikel 3 vervalt', "
                        "'... wordt vervangen door ...', '... wordt ingevoegd ...', '... komt te luiden ...'.\n"
                        "1 = I see NO instruction (only memorandum, signatures, a bare list, or "
                        "unreadable OCR): the parser was right to find nothing.\n"
                        "2 = I DO see one: the parser missed it.\n"
                        "3 = I can't tell.\n"
                        "Note what the text is, e.g. 'OCR garbled' or 'only memorandum'.",
        }


POINTER = re.compile(r"^(?:(?:onder|sub)\s+[a-z]{1,2}\b|(?:eerste|tweede|derde|vierde|vijfde|zesde|"
                     r"zevende)\s+lid)\s*,?\s*(?:van\s+(?:de|het)\s+)?", re.I)


def law_key(raw: str, article_prefix) -> str:
    """The law named in a reference, without article, lid, sub or onder pointers:
    'artikel 79 lid 1 onder a van de Personeelswet' -> 'personeelswet'."""
    s = article_prefix.sub("", raw)
    while True:
        t = POINTER.sub("", s)
        if t == s:
            break
        s = t
    s = re.sub(r"(?i)\s+art(?:ikel)?\.?\s*\d.*$", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def shuffled(items) -> list:
    items = list(items)
    random.Random(SEED).shuffle(items)
    return items


def vague(law: str) -> bool:
    """A cited name with no law-type word in it ("strafrecht", "loonbelasting"), which does
    not by itself identify an act; "Personeelswet" or "Kiesregeling" do."""
    return not re.search(r"(?i)wet|besluit|decreet|regeling|reglement|verordening|code", law)


def export_links(conn, n):
    rows = conn.execute("""
        SELECT c.id, c.raw_text, c.citation_type, f.text, w.title, s.title
        FROM citations c
        JOIN works w ON w.id = c.target_work_id
        LEFT JOIN fragments f ON f.id = c.source_fragment_id
        LEFT JOIN works s ON s.id = c.source_work_id
        WHERE c.target_work_id IS NOT NULL
        ORDER BY c.id""").fetchall()
    # one item per (law named, linked act) pair: "artikel 80 Personeelswet" and "Personeelswet"
    # ask the same question, so the article pointer is dropped before grouping. There are few
    # such pairs, so all of them are exported (n caps it); "weight" keeps the share correct
    # over citations right
    from lexsr.core.title_matching import _ARTICLE_PREFIX
    pairs = {}
    for row in rows:
        pairs.setdefault((law_key(row[1], _ARTICLE_PREFIX), row[4]), []).append(row)
    for key in sorted(pairs, key=lambda k: -len(pairs[k]))[:n]:
        cites = pairs[key]
        cid, raw, ctype, ftext, target, source = cites[0]
        examples = sorted({re.sub(r"\s+", " ", c[1]).strip() for c in cites})[:6]
        yield {
            "id": "lawlink-" + hashlib.sha1("|".join(key).encode()).hexdigest()[:12],
            "citation_ids": [c[0] for c in cites],
            "weight": len(cites),
            "title": f"{key[0]}  ->  {target}",
            # kept minimal on purpose: the verdict only needs the law named and the act linked;
            # a vague name ("Strafrecht") also gets two short sentences to judge it by
            "fields": {
                "The text cites": "\n".join(examples[:3]),
                "Linked to": target,
                **({"In the text (2 examples)": "\n\n".join(
                    context(c[3], c[1], width=90) for c in cites[:2])} if vague(key[0]) else {}),
            },
            "question": "Same act?   1 = yes   2 = no   3 = not sure",
        }


def export_links_dna(conn, n):
    """Law-name -> act pairs whose act now has the DNA.sr text (release/merge_dna.py) and
    that the links task has not asked about yet, heaviest first."""
    judged = set()
    prev = TASK_DIR / "links.jsonl"
    if prev.exists():
        judged = {json.loads(l)["id"] for l in open(prev, encoding="utf-8") if l.strip()}
    dna = {wid for (wid,) in conn.execute(
        "SELECT id FROM works WHERE metadata LIKE '%\"text_source\": \"dna\"%'")}
    items = []
    for it in export_links(conn, 100000):
        target = conn.execute("SELECT target_work_id FROM citations WHERE id=?", (it["citation_ids"][0],)).fetchone()[0]
        if it["id"] in judged or target not in dna:
            continue
        # the DNA set also holds parts of codes (one book of the Civil Code), so show the act itself
        m = json.loads(conn.execute("SELECT metadata FROM works WHERE id=?", (target,)).fetchone()[0])
        it["fields"]["Linked act (PDF)"] = f"/raw/dna_pdf/{m['dna_file']}"
        items.append(it)
    return items[:n]


def export_gazette(conn, n):
    titles = conn.execute("SELECT id, title FROM works").fetchall()
    by_num = {}
    for wid, t in titles:
        m = GAZ_TITLE.search(t or "")
        if m:
            by_num.setdefault((m.group(1), int(m.group(2))), []).append((wid, t))
    rows = conn.execute("""
        SELECT c.id, c.raw_text, f.text FROM citations c
        LEFT JOIN fragments f ON f.id = c.source_fragment_id
        WHERE c.target_work_id IS NULL ORDER BY c.id""").fetchall()
    # one item per distinct reference (gazette, year, number): whether the candidate is the
    # cited act does not depend on the sentence, so a reference cited many times is judged
    # once; "weight" (how often it is cited) lets summarize.py weight the share correct
    groups = {}
    for cid, raw, ftext in rows:
        m = GAZ_REF.search(raw or "")
        if m and (m.group(1), int(m.group(2))) in by_num:
            g = "G" if re.match(r"(?i)g|gouv", raw.strip()) else "S"
            groups.setdefault((g, m.group(1), int(m.group(2))), []).append((cid, raw, ftext))
    # references already judged under the earlier one-item-per-citation layout stay in the
    # sample, so no verdict is lost; the rest of the sample is drawn at random
    key_of = {c: k for k, cs in groups.items() for c, _, _ in cs}
    done = {key_of[r["item_id"]] for r in read_labels("gazette") if r["item_id"] in key_of}
    # one fixed random order of all references; the sample is its first n entries plus any
    # already-judged reference further down, so labelling never reshuffles the open items
    order = random.Random(SEED).sample(sorted(groups), len(groups))
    # n is the total: the judged references plus the first open ones in that order
    keys = [k for k in order if k in done]
    keys += [k for k in order if k not in done][:max(0, n - len(keys))]
    for key in keys:
        g, year, num = key
        cites = groups[key]
        cid, raw, ftext = cites[0]
        ts = by_num[(year, num)]
        fields = {
            "Context ([[citation]])": context(ftext, raw),
            "Candidate act(s) in corpus: gazette reference | name": "\n".join(split_ref(t) for _, t in ts),
            "Cited in the corpus": f"{len(cites)} time(s)",
        }
        files = [f for wid, _ in ts for f in source_files(wid)]
        if files:
            # open these to see what each candidate document actually is
            fields["Candidate source file(s)"] = "\n".join(files)
        yield {
            "id": f"gaz-{g}B-{year}-{num}",
            "citation_ids": [c for c, _, _ in cites],
            "weight": len(cites),
            "title": f"{g}.B. {year} no. {num}",
            "fields": fields,
            "question": "Is the candidate the act this gazette reference cites? Judge by the gazette "
                        "reference and the document (open the candidate source file); the name comes "
                        "from a file name and may be wrong. Mind S.B. vs G.B.: different gazettes.",
        }


LABEL_DIR = Path(__file__).resolve().parent / "labels"


def read_labels(task: str) -> list:
    p = LABEL_DIR / f"{task}.jsonl"
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()] if p.exists() else []


def carry_over_labels(task: str, items: list, old_items: dict) -> int:
    """Copy verdicts given under an earlier layout onto the item that now holds the same
    citations, appended as new label lines; the original lines are kept unchanged.
    `old_items` maps earlier item ids to their citation ids (empty for one-citation items)."""
    by_cit = {c: it["id"] for it in items for c in it.get("citation_ids", [])}
    rows = read_labels(task)
    have = {r["item_id"] for r in rows}
    latest = {}
    for r in rows:
        for c in old_items.get(r["item_id"], [r["item_id"]]):
            if c in by_cit:
                latest[by_cit[c]] = r
    new = [{**r, "item_id": iid, "carried_over_from": r["item_id"]}
           for iid, r in latest.items() if iid not in have]
    if new:
        with open(LABEL_DIR / f"{task}.jsonl", "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return len(new)


def carry_over_gazette_labels(items: list) -> int:
    """Copy verdicts given to single citations (old layout) onto the reference item that now
    holds them, appended as new label lines; the original lines are kept unchanged."""
    by_cit = {c: it["id"] for it in items for c in it.get("citation_ids", [])}
    rows = read_labels("gazette")
    have = {r["item_id"] for r in rows}
    latest = {}
    for r in rows:
        if r["item_id"] in by_cit:
            latest[by_cit[r["item_id"]]] = r
    new = [{**r, "item_id": iid, "carried_over_from": r["item_id"]}
           for iid, r in latest.items() if iid not in have]
    if new:
        with open(LABEL_DIR / "gazette.jsonl", "a", encoding="utf-8") as fh:
            for r in new:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    return len(new)


def split_ref(title: str) -> str:
    """'S.B. 2017 no. 22 Besluit X' -> 'S.B. 2017 no. 22   |   Besluit X', so the gazette
    reference (what is judged) stands apart from the name (often wrong)."""
    m = re.match(r"\s*([SG]\.B\. \d{4} no\. \d+[a-z]?)\s*(.*)$", title or "")
    if m:
        return f"{m.group(1)}   |   {m.group(2) or '(no name)'}"
    m = GAZ_TITLE.search(title or "")
    return f"{m.group(0)}   |   {title}" if m else f"(no reference)   |   {title}"


SOURCES_CSV = ROOT / "release" / "work_sources.csv"
_sources: dict = {}


def source_files(wid: str) -> list:
    """Links (served by serve.py from data/raw) to the source file(s) of a work."""
    if not _sources and SOURCES_CSV.exists():
        import csv
        for r in csv.DictReader(open(SOURCES_CSV, encoding="utf-8")):
            _sources.setdefault(r["work_id"], []).append(f"/raw/{r['source']}/{r['file']}")
    return _sources.get(wid or "", [])


def add_source_files(conn, item: dict) -> dict:
    """Add a "Source file" field: links to the original PDF or page of the document the
    item comes from, to check OCR against it."""
    if item["id"].startswith("lawlink-"):
        return item  # links items stay minimal
    iid = item.get("citation_ids", [item["id"]])[0]
    wid = iid.split("#")[0]
    if iid.startswith("c-"):
        row = conn.execute("SELECT source_work_id FROM citations WHERE id=?", (iid,)).fetchone()
        wid = row[0] if row else None
    files = source_files(wid)
    if files:
        item["fields"] = {"Source file": "\n".join(files), **item["fields"]}
    return item


def export_segmentation(conn, n):
    works = conn.execute(
        "SELECT id, title, source_url FROM works WHERE source_type='case_law' ORDER BY id").fetchall()
    for wid, title, url in random.Random(SEED).sample(works, min(n, len(works))):
        frs = conn.execute("""SELECT number, fragment_type, text FROM fragments WHERE work_id=?
                              ORDER BY position""", (wid,)).fetchall()
        label = {"facts": "parties, procedure and facts (before the considerations)",
                 "decision": "decision / dictum and signatures (after the considerations)"}
        shown = "\n\n".join(f"── {label.get(ft) or num or '(unnumbered)'} ──\n{t or ''}" for num, ft, t in frs)
        n = sum(ft == "consideration" for _, ft, _ in frs)
        yield {
            "id": wid,
            "title": f"{title} ({n} considerations)",
            "fields": {"Source page": url or "", "Considerations as segmented": shown},
            "question": "Compare with the source page: are the considerations split at the "
                        "right numbers, with nothing missing?",
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/canonical_backup_20260913_submitted.db")
    ap.add_argument("--links", type=int, default=1000, help="law-name -> act pairs for link precision (all by default)")
    ap.add_argument("--gazette", type=int, default=75, help="number of distinct gazette references")
    ap.add_argument("--segmentation", type=int, default=20)
    ap.add_argument("--links-dna", type=int, default=15, help="new law-name -> act pairs on DNA.sr texts")
    ap.add_argument("--only", nargs="+", help="export only these tasks and leave the other task files as they are")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    TASK_DIR.mkdir(exist_ok=True)
    jobs = {
        "gold": lambda: export_gold(conn),
        # fixed random order, so labelling the first N items is a fair sample of all of them
        "instructions": lambda: shuffled(export_instructions(conn)),
        "parse_failures": lambda: export_parse_failures(conn, args.db),
        "links": lambda: export_links(conn, args.links),
        "links_dna": lambda: export_links_dna(conn, args.links_dna),
        "gazette": lambda: export_gazette(conn, args.gazette),
        "segmentation": lambda: export_segmentation(conn, args.segmentation),
    }
    if args.only:
        jobs = {k: v for k, v in jobs.items() if k in args.only}
    mf = TASK_DIR / "manifest.json"
    meta = json.loads(mf.read_text(encoding="utf-8")) if args.only and mf.exists() else {"tasks": {}}
    meta.update(db=args.db, seed=SEED)
    for name, make in jobs.items():
        items = make()
        items = [add_source_files(conn, it) for it in items]
        if name == "gazette":
            print(f"gazette: {carry_over_gazette_labels(items)} verdict(s) carried over to reference items")
        if name == "links":
            old = {}
            prev = TASK_DIR / "links.jsonl"
            if prev.exists():
                old = {it["id"]: it.get("citation_ids", []) for it in
                       (json.loads(l) for l in open(prev, encoding="utf-8") if l.strip())}
            print(f"links: {carry_over_labels('links', items, old)} verdict(s) carried over to law-name items")
        with open(TASK_DIR / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for it in items:
                fh.write(json.dumps(it, ensure_ascii=False) + "\n")
        meta["tasks"][name] = len(items)
        print(f"{name:15s} {len(items):5d} items")
    (TASK_DIR / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
