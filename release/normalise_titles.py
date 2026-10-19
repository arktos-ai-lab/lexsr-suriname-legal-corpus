#!/usr/bin/env python3
"""
normalise_titles.py: write gazette references in legislative titles one way.

    python release/normalise_titles.py --db data/release_build/lexsr_work.db [--apply]

Titles come from source file names and spell the same gazette reference as "S B 2016 no 63",
"SB 2016 No 63", "S. B. 2016 nr. 63" or "S.B. 2016 no. 63", sometimes at the start and
sometimes at the end of the title. They are rewritten to the form the gazettes themselves
use when citing, "S.B. 2016 no. 63" (and "G.B." for the Gouvernementsblad), placed first:

    "Wet Uitvoering Chemisch Wapenverdrag S B 2022 no 147"
        -> "S.B. 2022 no. 147 Wet Uitvoering Chemisch Wapenverdrag"

Titles of consolidated texts ("... bijgewerkt tm S B 2019 no 55") and titles naming more than
one reference keep their word order; only the references in them are rewritten. File-name
residue (".pdf", a list number such as "17.", a "BTW" folder prefix) is dropped. The work's
own reference is stored in works.metadata as "gazette_ref". Work ids, URIs and official_id
are not changed.

Without --apply it prints a sample and the counts. --apply writes release/title_changes.csv
(old and new title per work) and updates works.title, expressions.title and works.metadata.
Running it again changes nothing.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# S B / S.B. / SB / S. B. / S.B / G B ... + year + optional no/nr/No. + number (with letter)
REF_RE = re.compile(
    r"(?<![A-Za-z])(?:BTW)?([SGsg])\s*\.?\s*[Bb]\s*\.?\s*[,;]?\s*(\d{4})\s*[,;]?\s*"
    r"(?:(?:no|nr|n0|No|NO|Nr|N0)\s*\.?\s*)?(\d{1,4}[a-z]?)(?![\d])",
)
CONSOLIDATED = re.compile(r"\b(bijgewerkt|gewijzigd|t/m|tm)\b", re.I)


def fmt(m: re.Match) -> str:
    return f"{m.group(1).upper()}.B. {m.group(2)} no. {m.group(3)}"


# file-name shorthand written out ("Wijz Grondwet" -> "Wijziging Grondwet"); abbreviations a
# law uses as its own name (RiS, MOT, BTW) are left alone
EXPANSIONS = [
    (re.compile(r"\b([Ww])ijz\b\.?"), lambda m: ("W" if m.group(1) == "W" else "w") + "ijziging"),
    (re.compile(r"\b([Ii])nz\b\.?"), lambda m: ("I" if m.group(1) == "I" else "i") + "nzake"),
    (re.compile(r"\b([Bb])ijl\b\.?"), lambda m: ("B" if m.group(1) == "B" else "b") + "ijlage"),
    # "Wet R O" is the Wet Regionale Organen (S.B. 1989 no. 44), not a Rechtsorde law
    (re.compile(r"\b([Ww])et\s+R\.?\s*O\.?(?=\s|$)"), lambda m: m.group(1) + "et Regionale Organen"),
]


# document-type words get one capital ("WET BTW 2022", "wet Casinobelasting" -> "Wet ...");
# abbreviations such as BTW or MOT keep their capitals
DOC_TYPES = re.compile(r"\b(wetboek|wet|staatsbesluit|landsbesluit|besluit|beschikking|resolutie|decreet|"
                       r"reglement|verbeterblad)\b", re.I)


def expand(t: str) -> str:
    for pattern, repl in EXPANSIONS:
        t = pattern.sub(repl, t)
    return DOC_TYPES.sub(lambda m: m.group(1).capitalize(), t)


AMENDING_HEADING = re.compile(
    r"(?i)^(?:wet|decreet|staatsbesluit|landsbesluit|besluit|resolutie|beschikking)\s+van\s+\d{1,2}\s*\w*\s+"
    r"[a-z]+\.?\s+\d{2,4},?\s+(?:houdende|tot)\s+(?:regels\s+ter\s+)?(nadere\s+)?wijziging\s+van\s+"
    r"(?:de|het)\s+(.+)$")


COMPLETE_NAME = re.compile(r"(?i)(\b\d{4}|(?:wet|besluit|regeling|decreet|reglement|wetboek|verordening|"
                           r"code|dagen|ordonnantie))$")


def short_amending(name: str) -> str:
    """'Wet van 11 maart 2022, houdende nadere wijziging van de Rijwet 1971'
    -> 'Nadere wijziging Rijwet 1971' (the short form used for every amending act)."""
    m = AMENDING_HEADING.match(name)
    # only when the amended act's name is complete (ends in a year or a law word); a title cut
    # off mid-name ("... van het Besluit Grondconver") keeps its heading form
    if not m or not COMPLETE_NAME.search(m.group(2).strip(" ,.")):
        return name
    return ("Nadere wijziging " if m.group(1) else "Wijziging ") + m.group(2).strip(" ,.")


def front_wijziging(name: str) -> str:
    """'Wet Huurwaardebelasting 1995 wijziging' -> 'Wijziging Wet Huurwaardebelasting 1995';
    for a bill, 'Ontwerpwet X wijziging' -> 'Ontwerpwet wijziging X'."""
    m = re.match(r"(?i)^(ontwerpwet\s+)?(.+?)\s+wijziging\s*$", name)
    if not m or re.match(r"(?i)\s*wijziging\b", m.group(2)):
        return name
    if m.group(1):
        return f"{m.group(1).strip()} wijziging {m.group(2)}"
    return f"Wijziging {m.group(2)}"


def clean_residue(t: str) -> str:
    t = re.sub(r"\s*\.?\s*pdf\s*$", "", t, flags=re.I)
    t = re.sub(r"\.pd?$", "", t, flags=re.I)  # ".pdf" cut off by the 70-character title limit
    t = re.sub(r"^\s*\d{1,2}\.(?=\S)", "", t)
    return re.sub(r"\s+", " ", t).strip()


def normalise(title: str, shorten: bool = True) -> tuple:
    """(new title, gazette_ref or None). `shorten` is off for titles cut off at 70 characters,
    where shortening an amending-act heading would drop the only words left."""
    sa = short_amending if shorten else (lambda x: x)
    t = expand(clean_residue(title or ""))
    refs = list(REF_RE.finditer(t))
    if not refs:
        return front_wijziging(sa(t)), None
    own = fmt(refs[0])
    if len(refs) > 1 or CONSOLIDATED.search(t):
        new = REF_RE.sub(fmt, t)
        return re.sub(r"\s+", " ", new).strip(), own if refs[0].start() == 0 else None
    m = refs[0]
    name = (t[:m.start()] + " " + t[m.end():]).strip()
    # drop connectives and brackets left dangling by removing the reference
    name = re.sub(r"^[\s,;:.\-()]+|[\s,;:\-(]+$", "", name)
    name = front_wijziging(sa(re.sub(r"\(\s*\)", "", name).strip()))
    if name and name[0].islower():
        name = name[0].upper() + name[1:]
    return (f"{own} {name}".strip() if name else own), own


HEADING_RE = re.compile(
    r"\b((?:WET|Wet|DECREET|Decreet|STAATSBESLUIT|Staatsbesluit|BESLUIT|Besluit|RESOLUTIE|Resolutie|"
    r"BESCHIKKING|Beschikking)\s+van\s+\d{1,2}\s*(?:e|ste|de)?\s+[A-Za-z]+\s+\d{4},?\s+"
    r"(?:houdende|tot|ter|inzake)\b[^()]{5,240}?)\s*(?=\(|MEMORIE|Memorie|\.\s|$)")


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def complete_truncated(original: str, text: str, files: list) -> str:
    """Titles were cut off at 70 characters when made from file names ("... van de Milieu Ra").
    Complete such a title from the act's own heading in its text when the heading starts with
    the truncated words, else from a longer source file name; otherwise keep it."""
    if not (65 <= len(original or "") <= 72):
        return original
    stub = REF_RE.sub(" ", clean_residue(original))
    k = _key(stub)
    if len(k) < 15:
        return original
    for m in HEADING_RE.finditer(text or ""):
        h = re.sub(r"\s+", " ", m.group(1)).strip(" ,;")
        if _key(h).startswith(k) or k.startswith(_key(h)[:len(k)]) and len(_key(h)) > len(k):
            if len(_key(h)) > len(k) + 3:
                ref = REF_RE.search(original)
                return f"{ref.group(0)} {h}" if ref and not REF_RE.search(h) else h
    for f in sorted(files, key=len, reverse=True):
        stem = re.sub(r"\.(pdf|html?)$", "", f, flags=re.I).replace("_", " ")
        if _key(stem).startswith(_key(original)) and len(_key(stem)) > len(_key(original)) + 3:
            return re.sub(r"\s+", " ", stem).strip()
    return original


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "release_build" / "lexsr_work.db")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--csv", type=Path, default=ROOT / "release" / "title_changes.csv")
    a = ap.parse_args()

    c = sqlite3.connect(a.db)
    rows = c.execute("SELECT id, title, metadata FROM works WHERE source_type='legislation'").fetchall()
    # inputs for completing truncated titles: original (file-name) titles, source files, text
    original = {}
    if (ROOT / "data" / "canonical.db").exists():
        original = dict(sqlite3.connect(f"file:{ROOT / 'data' / 'canonical.db'}?mode=ro", uri=True)
                        .execute("SELECT id, title FROM works"))
    files = {}
    if (ROOT / "release" / "work_sources.csv").exists():
        for r in csv.DictReader(open(ROOT / "release" / "work_sources.csv", encoding="utf-8")):
            files.setdefault(r["work_id"], []).append(r["file"])
    changes = []
    for wid, title, meta in rows:
        base = title
        orig = original.get(wid, "")
        cut = 65 <= len(orig) <= 72
        if cut:
            text = " ".join(t or "" for (t,) in c.execute(
                "SELECT text FROM fragments WHERE work_id=? ORDER BY position, rowid", (wid,)))[:20000]
            full = complete_truncated(orig, text, files.get(wid, []))
            if full != orig:
                base, cut = full, False
        new, ref = normalise(base, shorten=not cut)
        md = json.loads(meta) if meta else {}
        if new != title or (ref and md.get("gazette_ref") != ref):
            changes.append((wid, title, new, ref, md))
    print(f"legislative works: {len(rows)}; titles changed: {sum(o != n for _, o, n, _, _ in changes)}; "
          f"with a gazette reference: {sum(1 for w, t, m in rows if normalise(t)[1])}")
    for wid, old, new, ref, _ in random.Random(7).sample(changes, min(25, len(changes))):
        print(f"  {old!r}\n    -> {new!r}")

    if a.apply and changes:
        with open(a.csv, "a" if a.csv.exists() else "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if fh.tell() == 0:
                w.writerow(["work_id", "old_title", "new_title", "gazette_ref"])
            for wid, old, new, ref, _ in changes:
                if old != new:
                    w.writerow([wid, old, new, ref or ""])
        for wid, old, new, ref, md in changes:
            if ref:
                md["gazette_ref"] = ref
            c.execute("UPDATE works SET title=?, metadata=? WHERE id=?",
                      (new, json.dumps(md, ensure_ascii=False), wid))
            c.execute("UPDATE expressions SET title=? WHERE work_id=?", (new, wid))
        c.commit()
        print("applied")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
