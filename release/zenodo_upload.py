#!/usr/bin/env python3
"""
zenodo_upload.py: create (or resume) a Zenodo DRAFT deposit and upload the bundle files.

    set ZENODO_TOKEN=...                      (scopes: deposit:write, deposit:actions)
    python release/zenodo_upload.py --bundle <dir> --metadata release/zenodo_metadata.json
    python release/zenodo_upload.py ... --sandbox     # try on sandbox.zenodo.org first

The deposit is left as a draft. Review it at the printed URL and press Publish there, or
rerun with --publish. Publishing on Zenodo is permanent: files cannot be changed afterwards.
Resumable: the draft id is kept in <bundle>/.zenodo_draft[_sandbox].json, and files already
in the draft with a matching MD5 are skipped.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import requests


def md5(p: Path) -> str:
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--metadata", type=Path, required=True)
    ap.add_argument("--sandbox", action="store_true")
    ap.add_argument("--publish", action="store_true")
    a = ap.parse_args()

    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        sys.exit("Set ZENODO_TOKEN first.")
    api = "https://sandbox.zenodo.org/api" if a.sandbox else "https://zenodo.org/api"
    H = {"Authorization": f"Bearer {token}"}
    state = a.bundle / (".zenodo_draft_sandbox.json" if a.sandbox else ".zenodo_draft.json")

    if state.exists():
        dep_id = json.loads(state.read_text())["id"]
        r = requests.get(f"{api}/deposit/depositions/{dep_id}", headers=H, timeout=60)
    else:
        r = requests.post(f"{api}/deposit/depositions", headers=H, json={}, timeout=60)
    r.raise_for_status()
    dep = r.json()
    state.write_text(json.dumps({"id": dep["id"]}))
    bucket = dep["links"]["bucket"]
    have = {f["filename"]: f["checksum"].split(":")[-1] for f in dep.get("files", [])}

    files = sorted(p for p in a.bundle.iterdir() if p.is_file() and not p.name.startswith((".", "_"))
                   and not p.name.endswith(".tmp"))
    for p in files:
        if have.get(p.name) == md5(p):
            print("skip (already uploaded):", p.name)
            continue
        print(f"upload {p.name} ({p.stat().st_size / 1e6:.1f} MB)", flush=True)
        with open(p, "rb") as fh:
            r = requests.put(f"{bucket}/{p.name}", data=fh, headers=H, timeout=None)
        r.raise_for_status()

    meta = json.loads(a.metadata.read_text(encoding="utf-8"))
    r = requests.put(f"{api}/deposit/depositions/{dep['id']}", headers=H, json=meta, timeout=60)
    r.raise_for_status()
    print("draft ready:", r.json()["links"]["html"])
    doi = (r.json().get("metadata", {}).get("prereserve_doi") or {}).get("doi")
    if doi:
        print("reserved DOI:", doi)

    if a.publish:
        r = requests.post(f"{api}/deposit/depositions/{dep['id']}/actions/publish", headers=H, timeout=120)
        r.raise_for_status()
        print("published, DOI:", r.json().get("doi"))


if __name__ == "__main__":
    main()
