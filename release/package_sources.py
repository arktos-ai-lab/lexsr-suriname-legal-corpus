#!/usr/bin/env python3
"""
package_sources.py: put the government source files of the released works in the bundle.

    python release/package_sources.py [--bundle data/release_build/lexsr-corpus-v1.0]

Writes <bundle>/source_files.zip with, for every released work, the file its text was read
from (work_sources.csv in the bundle): the PDFs published by De Nationale Assemblée
(dna.sr) and other government sites, and the decision pages of rechtspraak.sr. Files of the
documents whose text is withheld (release/withhold_text.csv) are left out. Paths inside the
zip are <source>/<file>, matching raw_sources.csv, which holds each file's SHA-256 and URL.

Article 11 of the Surinamese Auteurswet 1913 provides that there is no copyright in laws,
decrees and regulations issued by public authority, nor in judicial and administrative
decisions; the files packaged here are such texts. Run release/build_release.py afterwards
to refresh SHA256SUMS.
"""
from __future__ import annotations

import argparse
import csv
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NOTE = """Source files of the LexSR corpus release

Each file is the government publication a released work was read from: PDFs published by
De Nationale Assemblee (dna.sr) and other Surinamese government sites, and the decision
pages of rechtspraak.sr, the Hof van Justitie's portal. Paths are <source>/<file>, as in
raw_sources.csv, which lists each file's SHA-256 hash and original URL; work_sources.csv
maps works to files. Files of documents whose text is withheld in the release are not
included.

Copyright: Article 11 of the Surinamese Auteurswet 1913 (G.B. 1913 no. 15, as amended)
provides that there is no copyright in laws, decrees and regulations issued by public
authority, nor in judicial and administrative decisions. These files are such texts. They
are copies as published online, not authenticated publications.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, default=ROOT / "data" / "release_build" / "lexsr-corpus-v1.0")
    a = ap.parse_args()

    withheld = {r["work_id"] for r in csv.DictReader(open(ROOT / "release" / "withhold_text.csv", encoding="utf-8"))}
    # a work released because its file is byte-identical on SRiS and a government site may be
    # mapped to the SRiS copy; ship the government copy (same SHA-256) instead
    gov_by_hash, hash_of = {}, {}
    for r in csv.DictReader(open(ROOT / "manifest" / "raw_sources.csv", encoding="utf-8")):
        hash_of[(r["source"], r["file"])] = r["sha256"]
        if r["source"] != "sris":
            gov_by_hash.setdefault(r["sha256"], (r["source"], r["file"]))
    files = set()
    for r in csv.DictReader(open(a.bundle / "work_sources.csv", encoding="utf-8")):
        if r["work_id"] in withheld:
            continue
        f = (r["source"], r["file"])
        if r["source"] == "sris":
            f = gov_by_hash.get(hash_of.get(f))
            if f is None:
                raise SystemExit(f"no government copy for {r['file']} ({r['work_id']})")
        files.add(f)
    files = sorted(files)
    tmp = a.bundle / "source_files.zip.tmp"
    total = 0
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("README.txt", NOTE)
        for source, name in files:
            p = ROOT / "data" / "raw" / source / name
            if not p.exists():
                raise SystemExit(f"missing source file: {p}")
            # PDFs are compressed already; store them as they are
            z.write(p, f"{source}/{name}",
                    compress_type=zipfile.ZIP_STORED if name.lower().endswith(".pdf") else zipfile.ZIP_DEFLATED)
            total += p.stat().st_size
    with open(tmp, "r+b") as fh:
        os.fsync(fh.fileno())
    tmp.replace(a.bundle / "source_files.zip")
    print(f"source_files.zip: {len(files)} files, {total / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
