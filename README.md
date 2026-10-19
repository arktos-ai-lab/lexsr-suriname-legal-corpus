# LexSR: Surinamese legislation and case law as linked data

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Data DOI](https://img.shields.io/badge/data-10.5281%2Fzenodo.23467651-blue.svg)](https://doi.org/10.5281/zenodo.23467651)

LexSR turns Suriname's scattered, mostly scanned legal publications into a structured,
versioned corpus. It scrapes government sources and the Hof van Justitie's case-law portal
(*rechtspraak.sr*), runs OCR where needed, consolidates amendment acts
(*wijzigingswetten*) into dated versions of the laws they change, and links court
decisions to the legislation they apply. The result is a knowledge graph of amendment,
citation and case-application edges.

This repository holds the code, evaluation scripts and source manifest for the paper:

> Ernst van Gassen. *LexSR: Consolidating Legislation and Linking Case Law to Build a Legal
> Knowledge Graph for Suriname, a Low-Resource Jurisdiction.* JURIX 2026.

The corpus itself is on Zenodo: https://doi.org/10.5281/zenodo.23467651

**Contents:** [Corpus at a glance](#corpus-at-a-glance) ·
[Installation](#installation) ·
[1. Use the released corpus](#1-use-the-released-corpus) ·
[2. Use the library in Python](#2-use-the-library-in-python) ·
[3. Command-line interface](#3-command-line-interface) ·
[4. Build the corpus yourself](#4-build-the-corpus-yourself) ·
[5. Reproduce the paper's evaluations](#5-reproduce-the-papers-evaluations) ·
[How it works](#how-it-works) · [Data availability](#data-availability)

## Corpus at a glance

| | Public release (Zenodo) | Research corpus (paper) |
|---|---:|---:|
| Legislative works | 751 | 1,285 |
| Case-law decisions (1952 to 2025) | 300 | 300 |
| Fragments (articles, paragraphs, considerations) | 54,490 | 68,127 |
| Citations extracted | 7,160 | 12,343 |
| Citations linked to a work in the corpus | 606 (8.5%) | 2,613 (21.2%) |
| Knowledge graph nodes / edges | 1,051 / 615 | 1,585 / 2,653 |

The release holds only government-sourced texts; see [Data availability](#data-availability).

## Installation

Requires Python 3.10 or later. The core package (data model, store, citation resolver,
amendment parser) has no dependencies; other features are installed as extras.

```bash
git clone https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus.git
cd lexsr-suriname-legal-corpus
pip install -e .                 # core only: enough for sections 1 to 3 below
pip install -e ".[all]"          # everything
```

| Extra | Needed for |
|---|---|
| `ingestion`, `scraper` | scraping the government sites and rechtspraak.sr |
| `ocr` | PDF text extraction and OCR. Also install [Tesseract](https://github.com/tesseract-ocr/tesseract) with the Dutch (`nld`) and English language packs, and [Poppler](https://poppler.freedesktop.org/) for `pdf2image` |
| `graph` | `build-graph` and reading the GraphML (networkx) |
| `search` | dense vector search (`index-vectors`); BM25 search needs no extra |
| `static` | the static website (`generate`) |
| `eval`, `dev` | evaluation helpers; tests (`pytest`) |

## 1. Use the released corpus

Download the record from Zenodo and unpack it anywhere. You do not need this repository to
read it: the corpus is a plain SQLite database, the same tables as Parquet, and a GraphML
graph.

| File | Content |
|---|---|
| `lexsr_corpus_v1.0.sqlite` | all tables in one database |
| `<table>.parquet` | one Parquet file per table |
| `knowledge_graph.graphml` | one node per work; `AMENDS`, `CITES` and `APPLIES` edges |
| `raw_sources.csv`, `work_sources.csv` | SHA-256 of every source file, and which file(s) each work came from |
| `source_files.zip` | the government PDFs and decision pages themselves (1.2 GB) |

The main tables:

| Table | One row per | Key columns |
|---|---|---|
| `works` | law or court decision | `id`, `title`, `source_type` (`legislation` / `case_law`), `document_type`, `status` |
| `expressions` | dated version of a work | `work_id`, `version_date`, `full_text` |
| `fragments` | article, paragraph (*lid*) or consideration | `work_id`, `expression_id`, `fragment_type`, `number`, `position`, `text`, `uri` |
| `citations` | extracted reference | `source_work_id`, `source_fragment_id`, `target_work_id` (empty if unlinked), `raw_text`, `confidence` |
| `amendment_links` | amendment act to the law it changes | `amendment_work_id`, `base_work_id`, `effective_date`, `staatsblad_ref` |

Legislative titles start with the gazette reference, for example
`S.B. 2002 no. 113 Wijziging Wet Huurwaardebelasting 1995`.

**SQLite.** Read the articles of an act in order:

```python
import sqlite3

db = sqlite3.connect("lexsr_corpus_v1.0.sqlite")
rows = db.execute("""
    SELECT f.fragment_type, f.number, f.text
    FROM works w JOIN fragments f ON f.work_id = w.id
    WHERE w.title LIKE 'S.B. 2002 no. 113%'
    ORDER BY f.position
""").fetchall()
```

Which laws does the case law cite most?

```python
db.execute("""
    SELECT t.title, COUNT(*) AS n
    FROM citations c
    JOIN works s ON s.id = c.source_work_id AND s.source_type = 'case_law'
    JOIN works t ON t.id = c.target_work_id
    GROUP BY t.title ORDER BY n DESC LIMIT 10
""").fetchall()
```

**Parquet (pandas).**

```python
import pandas as pd

works = pd.read_parquet("works.parquet")
fragments = pd.read_parquet("fragments.parquet")
laws = works[works.source_type == "legislation"]
```

**Knowledge graph (networkx).** Nodes carry `title`, `node_type` and `document_type`; edges
carry `edge_type` and, for citations, `raw_text` and `confidence`.

```python
import networkx as nx

g = nx.read_graphml("knowledge_graph.graphml")
amends = [(u, v) for u, v, d in g.edges(data=True) if d["edge_type"] == "AMENDS"]
```

## 2. Use the library in Python

**Resolve legal references in free text.** The resolver finds references to articles,
laws, gazette issues (*Staatsblad*, *Gouvernementsblad*) and ECLIs, and turns them into
identifiers. Without a store it parses only; pass a `CanonicalStore` to resolve law names
against the works in a corpus.

```python
from lexsr.core.resolver import CitationResolver

resolver = CitationResolver()
for ref in resolver.resolve_text(
    "Op grond van artikel 4 lid 2 van de Arbeidswet 1963 en S.B. 2002 no. 113 ..."
):
    print(ref.raw_text, "->", ref.uri)
# artikel 4 lid 2 van de Arbeidswet 1963 -> /id/law/arbeidswet-1963/<date>/article/4/paragraph/2
# S.B. 2002 no. 113 -> /id/gazette/sb-2002-113
```

**Extract amendment instructions.** The rule-based parser reads a *wijzigingswet* and
returns typed instructions (replace, insert, repeal, phrase substitution) with a
confidence. `use_llm=False` keeps it fully offline; with `use_llm=True` an
OpenAI-compatible endpoint re-reads low-confidence instructions.

```python
from lexsr.consolidation.parser import AmendmentParser

text = """Artikel I
In de Wet Huurwaardebelasting 1995 (S.B. 1995 no.108) worden de volgende wijzigingen aangebracht:
A. Artikel 5 lid 2 wordt vervangen door: De belasting wordt ten minste vastgesteld op Sf. 20.000,-.
B. Artikel 7 lid 1 komt te luiden als volgt: ...
"""
parsed = AmendmentParser(use_llm=False).parse(text)
for instruction in parsed.instructions:
    print(instruction)
# <AmendmentInstruction replace art.5 lid 2 of 'Wet Huurwaardebelasting 1995' conf=0.50>
# <AmendmentInstruction replace art.7 lid 1 of 'Wet Huurwaardebelasting 1995' conf=0.85>
```

**Work with a corpus database.** `CanonicalStore` reads and writes the SQLite schema above,
including the released file. [`examples/01_quickstart.py`](examples/01_quickstart.py) builds a
small store from scratch (works, expressions, fragments) and shows the data model.

```python
from lexsr.core.store import CanonicalStore

store = CanonicalStore("lexsr_corpus_v1.0.sqlite")
```

## 3. Command-line interface

All commands run through `python -m lexsr` (or `lexsr` after installation). `--db` selects
the corpus database (default `data/canonical.db`), so every read-only command also works on
the released file:

```bash
python -m lexsr --db lexsr_corpus_v1.0.sqlite stats
python -m lexsr --db lexsr_corpus_v1.0.sqlite --search-db search.db index
python -m lexsr --db lexsr_corpus_v1.0.sqlite --search-db search.db search huurwaardebelasting --no-hybrid
python -m lexsr --db lexsr_corpus_v1.0.sqlite build-graph --output graph.graphml --stats-output graph.json
```

| Command | What it does |
|---|---|
| `stats` | counts of works, expressions, fragments, citations and amendment links |
| `ingest` | scrape the live government sources into `data/raw/` and the store |
| `ingest-pdfs` | process PDFs already in `data/raw/` |
| `ingest-caselaw` | scrape rechtspraak.sr (`--limit`, `--rate-limit`, `--force`) |
| `consolidate` | parse amendment acts and apply them (`--min-confidence`, `--use-llm`, `--show-errors`) |
| `link-citations` | (re)extract and link citations for all fragments |
| `build-graph` | write the knowledge graph as GraphML plus a statistics JSON |
| `index` / `search` | BM25 index and search (`--top-k`; `--no-hybrid` without a vector index) |
| `index-vectors` | dense vector index (`search` extra) |
| `benchmark` | the 65-case citation-resolution benchmark |
| `generate` / `serve` | build and serve the static website |

`python -m lexsr <command> --help` lists every option.

## 4. Build the corpus yourself

The corpus can be rebuilt from the public sources with one command, which runs the
scrapers, OCR, case-law ingestion, consolidation, citation linking and graph build in the
order used for the paper:

```bash
python scripts/rebuild_corpus.py                      # resumable: ingestion skips stored documents
python scripts/rebuild_corpus.py --from consolidate   # resume from a later step
```

Downloaded files land in `data/raw/<source>/`; the canonical store is `data/canonical.db`.
Government websites change, so a rebuild will not return exactly the paper's documents.
[`manifest/raw_sources.csv`](manifest/raw_sources.csv) lists every source file behind the
paper's corpus with its SHA-256 (and URL for case law). Check your download against it:

```bash
python scripts/build_source_manifest.py --check
```

The parliament's gazette PDFs (DNA.sr) are fetched, OCR'd and staged by
[`scripts/dna_pdfs.py`](scripts/dna_pdfs.py), which checkpoints after every file and can be
stopped and restarted at any point. [`release/`](release/) turns the research store into
the public release: `prepare_release_db.py` (deduplication, titles, merging the DNA.sr
texts, re-segmentation, citation linking), `select_release_works.py` (which works may be
redistributed) and `build_release.py` (SQLite, Parquet, graph, checksums).
[`release/README.md`](release/README.md) is the data card.

## 5. Reproduce the paper's evaluations

```bash
python -m lexsr benchmark                                                # 65-case citation benchmark
python scripts/amendment_diagnostics.py --db data/canonical.db --n 100   # amendment parsing coverage
python -m lexsr build-graph                                              # graph statistics
```

[`annotation/`](annotation/) is the local review tool used for the hand checks in the paper
(benchmark cases, gazette references, citation links, amendment instructions, case-law
segmentation). `export_tasks.py` writes the review items, `serve.py` opens them in the
browser on port 8765, and `summarize.py` reports the verdicts stored in
`annotation/labels/`. See [`annotation/README.md`](annotation/README.md).

## How it works

1. **Ingestion.** Scrapers for eleven government sites (parliament DNA.sr, tax and maritime
   authorities, standards bureau, government portal, six ministries), the private legal
   portal SRiS (Stichting voor de Rechtsorde in Suriname), and *rechtspraak.sr*, which is
   enumerated through its WordPress sitemap. Source files are kept unchanged on disk.
2. **OCR and structure.** Embedded PDF text first, Tesseract OCR as fallback, with page
   rotation detection and splitting of two-page A5 scans. Articles, paragraphs (*leden*)
   and sub-items are segmented by the standard Dutch legislative headings; court decisions
   are segmented into numbered considerations.
3. **Data model.** FRBR-style: a `Work` (a law or decision) has dated `Expression`s, and
   each expression is split into addressable `Fragment`s. Identifiers follow an
   ELI-inspired pattern, `/id/law/...`, `/id/caselaw/...` and `/id/gazette/sb-YYYY-N`.
4. **Amendment consolidation.** A regex parser (about 20 patterns for repeal, replacement,
   insertion and phrase substitution) extracts instructions from amendment acts; the
   consolidation engine applies them to produce a new expression at the effective date.
   An optional LLM pass, off by default, re-reads low-confidence instructions.
5. **Citation linking.** References such as *artikel 4 lid 2 Arbeidswet 1963* are
   extracted from legislation and case law and resolved to works, falling back to title
   matching because scraped documents do not carry canonical identifiers.
6. **Knowledge graph.** One node per work; `AMENDS`, `CITES` and `APPLIES` edges, exported
   as GraphML.

## Known limitations

These are discussed in the paper:

- 79% of extracted citations are not linked; most are gazette references
  (*Staatsblad*, *Gouvernementsblad*) that the resolver does not yet match to works.
- 25 of 36 amendment documents yield at least one classified instruction. Most failures
  trace to operative text missing from the ingested source, not to the parser rules.
- 499 of the 1,285 legislative works in the research corpus come from SRiS, a private
  foundation: they are copies, not authenticated publications. Where parliament (DNA.sr)
  publishes the same act, its PDF is used instead (704 works).
- Titles are derived from source file names and normalised; they are not always the
  official title.
- OCR quality and document classification are not evaluated separately.
- The 65-case citation benchmark was produced together with the resolver patterns and
  measures pattern coverage, not generalisation.

## Data availability

The government-sourced part of the corpus (751 legislative works from government websites,
mostly the parliament's DNA.sr gazette PDFs, and 300 decisions from rechtspraak.sr) is
archived on Zenodo under CC BY 4.0: https://doi.org/10.5281/zenodo.23467651. It includes
the source files themselves. Works obtained from SRiS, a private foundation whose terms
allow copies for personal use only, are not redistributed;
[`release/release_works.csv`](release/release_works.csv) gives the decision for every work.
Works listed in [`release/withhold_text.csv`](release/withhold_text.csv) are not official
texts and are released without their text.

Surinamese legislation and court decisions are not protected by copyright: Article 11 of
the Auteurswet 1913 (G.B. 1913 no. 15, as amended) provides that there is no copyright in
general regulations issued by public authority, nor in judicial decisions and
administrative decisions.

## Repository layout

```
src/lexsr/       package: core model, ingestion, OCR, consolidation, graph, search, site
scripts/         rebuild, DNA.sr ingestion, diagnostics and maintenance scripts
release/         public release build and data card
manifest/        SHA-256 manifest of every source file
annotation/      hand-verification tool and labels
examples/        quickstart
tests/           pytest suite
docs/            architecture notes
```

## Development

LexSR was developed by one researcher with AI coding assistants. Run the tests with:

```bash
pip install -e ".[dev]"
pytest
```

## Citation

If you use the corpus or the code, please cite the paper and the dataset:

```bibtex
@inproceedings{vangassen2026lexsr,
  author    = {van Gassen, Ernst},
  title     = {{LexSR}: Consolidating Legislation and Linking Case Law to Build a Legal
               Knowledge Graph for {Suriname}, a Low-Resource Jurisdiction},
  booktitle = {Legal Knowledge and Information Systems (JURIX 2026)},
  year      = {2026}
}

@dataset{vangassen2026lexsrdata,
  author    = {van Gassen, Ernst},
  title     = {{LexSR} corpus v1.0: Surinamese legislation and case law},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.23467651}
}
```

## Licence

Code is released under the [MIT License](LICENSE). The corpus on Zenodo is CC BY 4.0.

## Contact

Ernst van Gassen, Arktos AI Lab ([ORCID 0009-0000-0314-6441](https://orcid.org/0009-0000-0314-6441))
