# LexSR corpus v1.0: how to publish

Nothing has been uploaded, pushed or committed. Everything below waits for your go.
Commands are for Git Bash from the LexSR repository root
(`E:\GitHub\Arktos-AI-lab-project-library\90-published-and-review\90-30-under-review\20-19-lexsr-suriname-legal-fabric`).

## What is where

| Item | Path |
|---|---|
| Release bundle (17 files, 96 MB) | `data/release_build/lexsr-corpus-v1.0/` (gitignored) |
| Build input (paper store, re-segmented, citations re-extracted) | `data/release_build/lexsr_work.db` |
| Data card (copied into the bundle as `README.md`) | `release/README.md` |
| Licence text (bundle `LICENSE.txt`) | `release/LICENSE-DATA.txt` |
| Works released without text | `release/withhold_text.csv` (17 rows) |
| Zenodo metadata | `release/zenodo_metadata.json` |
| Scripts | `release/prepare_release_db.py`, `release/build_release.py`, `release/zenodo_upload.py` |

Source store, read only and untouched: `data/canonical.db`.

## Before publishing

1. **Withheld texts.** Check `release/withhold_text.csv`. The 17 entries were found by file
   name (theses, CROSQ standards drafts, SSB technical specifications, Caribbean MOU codes,
   the development plan). Other non-official material may remain under OCR-garbled titles;
   adding a row and rebuilding withholds it without changing any count in the paper.
2. **Licence.** Compilation CC BY 4.0, texts free under Auteurswet art. 11. Change
   `release/LICENSE-DATA.txt`, `release/README.md` and `"license"` in
   `release/zenodo_metadata.json` together if you prefer CC0.
3. **Verify the bundle:**
   `(cd data/release_build/lexsr-corpus-v1.0 && sha256sum -c SHA256SUMS)`
4. **Zenodo token** with scopes `deposit:write` and `deposit:actions`.

## Publish: Zenodo (gives the DOI)

Step 1 creates a private draft, uploads the bundle and reserves the DOI:

```bash
export ZENODO_TOKEN=<your token>
python release/zenodo_upload.py --bundle data/release_build/lexsr-corpus-v1.0 --metadata release/zenodo_metadata.json
```

It prints the draft URL and `reserved DOI: 10.5281/zenodo.NNNNNNN`. Add `--sandbox` to
rehearse on sandbox.zenodo.org first.

Step 2 puts the record number everywhere the placeholder stands, rebuilds the card in the
bundle and refreshes the draft (only changed files are uploaded again):

```bash
N=NNNNNNN   # the number from step 1
sed -i "s/zenodo\.XXXXXXX/zenodo.$N/g" article/lexsr_jurix2026.tex article/jurix2026_paper_draft.md release/README.md README.md
python release/build_release.py --db data/release_build/lexsr_work.db
python release/zenodo_upload.py --bundle data/release_build/lexsr-corpus-v1.0 --metadata release/zenodo_metadata.json --publish
```

`--publish` is permanent: files cannot be changed afterwards, only superseded by a new
version. Leave it off to check the draft in the browser and press Publish there.

## Optional: GitHub release with the same files

```bash
cd data/release_build/lexsr-corpus-v1.0
gh release create corpus-v1.0 $(cut -d' ' -f3 SHA256SUMS) SHA256SUMS \
  --repo arktos-ai-lab/lexsr-suriname-legal-corpus --title "LexSR corpus v1.0" --notes-file README.md --draft
```

Remove `--draft` (or publish it on GitHub) once checked. Commit and push the repository
changes first, so the release tag points at the code that built it.

## Camera-ready submission (JURIX 2026, paper #258)

1. Compile: `cd article && pdflatex lexsr_jurix2026 && pdflatex lexsr_jurix2026`.
   Expect 11 pages: body ends on page 10, references on page 11, no overfull boxes.
   Confirm that no `XXXXXXX` remains: `grep -n XXXXXXX article/lexsr_jurix2026.tex`.
2. The camera-ready deadline is listed as 15 or 18 October 2026 (AoE) on
   irit.fr/jurix2026/important-dates; check the EasyChair notification for which applies,
   and for the IOS Press licence/copyright form.
3. Upload `article/lexsr_jurix2026.pdf` (and the LaTeX sources if asked) in EasyChair. The
   abstract changed; the current text is in `article/SUBMIT_lexsr_jurix2026.txt`.
4. After the proceedings appear, add the paper DOI to Zenodo as a new related identifier
   (`isSupplementTo`), which can be edited on a published record without a new version.

## Rebuilding

```bash
python release/prepare_release_db.py --src data/canonical.db --out data/release_build/lexsr_work.db
python release/build_release.py --db data/release_build/lexsr_work.db
```

`prepare_release_db.py` merges legislative works obtained under several file names, takes in
the DNA.sr PDFs from `data/dna_staging.db` (built by `scripts/dna_pdfs.py`; the government
copy replaces the text of the same act, see `release/dna_merge.csv`), merges again (532
works removed in all, listed in `release/dedupe_works.csv`), re-segments the 300 stored decision pages (2,684
considerations) and re-extracts all citations in one pass (12,343 rows).
`build_release.py` then keeps only the works marked for release in
`release/release_works.csv` (government sources; rebuild it with
`python release/map_work_sources.py && python release/select_release_works.py`). Both steps are
idempotent. `build_release.py` records each finished output in `_progress.json` and skips
it on a rerun; `--force` rebuilds all. Every figure in the paper's corpus table is in
`corpus_stats.json`.
