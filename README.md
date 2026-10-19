# LexSR: Surinamese legislation and case law as linked data

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

LexSR turns Suriname's scattered, mostly scanned legal publications into a structured,
versioned corpus. It scrapes government sources and the Hof van Justitie's case-law portal
(*rechtspraak.sr*), runs OCR where needed, consolidates amendment acts
(*wijzigingswetten*) into dated versions of the laws they change, and links court
decisions to the legislation they apply. The result is a knowledge graph of amendment,
citation and case-application edges.

This repository accompanies the paper:

> Ernst van Gassen. *LexSR: Consolidating Legislation and Linking Case Law to Build a Legal
> Knowledge Graph for Suriname, a Low-Resource Jurisdiction.* JURIX 2026 (accepted).

The paper source is in [`article/`](article/).

## Corpus at a glance

Figures for the research corpus, as reported in the paper (the public release holds only
its government-sourced part; see [Data availability](#data-availability)):

| | |
|---|---|
| Legislative works | 1,285 |
| Case-law decisions | 300 (1952 to 2025) |
| Fragments (articles, paragraphs, considerations) | 68,127 |
| Considerations (case law) | 2,684 |
| Citations extracted | 12,343 |
| Citations linked to a work in the corpus | 2,613 (21.2%) |
| Knowledge graph | 1,585 nodes, 2,653 edges |

The corpus is published separately (SQLite and Parquet); see
[Data availability](#data-availability).

## How it works

1. **Ingestion.** Scrapers for eleven government sites (parliament DNA.sr, tax and maritime
   authorities, standards bureau, government portal, six ministries), the private legal
   portal SRiS (Stichting voor de Rechtsorde in Suriname), which supplies most legislative
   texts, plus
   *rechtspraak.sr*, which is enumerated through its WordPress sitemap. Source files are
   kept unchanged on disk.
2. **OCR and structure.** Embedded PDF text first, Tesseract OCR as fallback. Articles,
   paragraphs (*leden*) and sub-items are segmented by the standard Dutch legislative
   headings; court decisions are segmented into numbered considerations.
3. **Data model.** FRBR-style: a `Work` (a law or decision) has dated `Expression`s, and
   each expression is split into addressable `Fragment`s. Identifiers follow an
   ELI-inspired pattern, `/id/law/...` and `/id/caselaw/...`.
4. **Amendment consolidation.** A regex parser (about 20 patterns for repeal, replacement,
   insertion and phrase substitution) extracts instructions from amendment acts; the
   consolidation engine applies them to produce a new expression at the effective date.
   An optional LLM pass, off by default, re-reads low-confidence instructions.
5. **Citation linking.** References such as *artikel 4 lid 2 Arbeidswet 1963* are
   extracted from legislation and case law and resolved to works, falling back to title
   matching because scraped documents do not carry canonical identifiers.
6. **Knowledge graph.** One node per work; `AMENDS`, `CITES` and `APPLIES` edges, exported
   as GraphML.

## Installation

Requires Python 3.10 or later. The core package has no dependencies; features are
installed as extras.

```bash
git clone https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus.git
cd LexSR
pip install -e ".[all]"      # or pick extras: ingestion, scraper, ocr, search, static, eval, graph
```

OCR of scanned PDFs also needs [Tesseract](https://github.com/tesseract-ocr/tesseract)
with the Dutch language pack (`nld`).

## Usage

All commands run through `python -m lexsr`:

```bash
python -m lexsr ingest                 # scrape the live government sources
python -m lexsr ingest-pdfs            # process PDFs already in data/raw/
python -m lexsr ingest-caselaw         # scrape rechtspraak.sr
python -m lexsr consolidate            # parse and apply amendment acts
python -m lexsr link-citations         # (re)link citations for all fragments
python -m lexsr build-graph            # write data/knowledge_graph.graphml
python -m lexsr stats                  # corpus statistics
python -m lexsr benchmark              # citation-resolution benchmark
python -m lexsr index                  # BM25 search index
python -m lexsr search "arbeidswet"    # search the corpus
python -m lexsr generate               # build the static website
```

## Collecting the data yourself

The corpus can be rebuilt from the public sources with one command, which runs the
scrapers, OCR, case-law ingestion, consolidation, citation linking and graph build in the
order used for the paper:

```bash
python scripts/rebuild_corpus.py                 # resumable: ingestion skips stored documents
python scripts/rebuild_corpus.py --from consolidate   # resume from a later step
```

Downloaded files land in `data/raw/<source>/`; the canonical store is `data/canonical.db`.
Government websites change, so a rebuild will not return exactly the paper's documents.
[`manifest/raw_sources.csv`](manifest/raw_sources.csv) lists every source file behind the
paper's corpus with its SHA-256 (and URL for case law); the 300 case-law pages were fetched
again on 2026-10-14 and segment to exactly the considerations stored in the paper's
corpus. Check your download against the manifest with:

```bash
python scripts/build_source_manifest.py --check
```

## Reproducing the paper's evaluations

```bash
python -m lexsr benchmark                                   # 65-case citation benchmark
python scripts/amendment_diagnostics.py --db data/canonical.db --n 100   # amendment parsing coverage
python -m lexsr build-graph                                 # graph statistics
```

## Human verification

[`annotation/`](annotation/) holds a local review tool for checking the benchmark cases,
extracted amendment instructions, citation links and case-law segmentation by hand.
Verdicts are stored in `annotation/labels/`.

## Known limitations

These are discussed in the paper:

- 79% of extracted citations are not linked; most are gazette references
  (*Staatsblad*, *Gouvernementsblad*) that the resolver does not yet match.
- 25 of 36 amendment documents yield at least one classified instruction. Most failures
  trace to operative text missing from the ingested source, not to the parser rules.
- Six candidate source sites were excluded because their sites changed.
- 499 of the 1,285 legislative works come from SRiS, a private foundation: they are copies,
  not authenticated publications. Where parliament (DNA.sr) publishes the same act, its
  PDF is used instead (704 works).
- OCR quality and document classification are not evaluated separately.
- The 65-case citation benchmark was produced together with the resolver patterns and
  measures pattern coverage, not generalisation.

## Data availability

The government-sourced part of the corpus (300 decisions from rechtspraak.sr and 58
legislative works from government websites; SQLite, Parquet, knowledge graph and source
manifest) is archived on Zenodo: https://doi.org/10.5281/zenodo.23467651 (DOI to be filled
in at publication). Works obtained from SRiS, a private foundation whose terms allow
copies for personal use only, are not redistributed;
[`release/release_works.csv`](release/release_works.csv) gives the decision for every
work. [`release/`](release/) holds the build script, data card and upload steps. Works
listed in [`release/withhold_text.csv`](release/withhold_text.csv) are not official texts
and are released without their text.

Surinamese legislation and court decisions are not protected by copyright: Article 11 of
the Auteurswet 1913 (G.B. 1913 no. 15, as amended) provides that there is no copyright in
general regulations issued by public authority, nor in judicial decisions and
administrative decisions.

## Repository layout

```
src/lexsr/       package: core model, ingestion, OCR, consolidation, graph, search, site
scripts/         one-off diagnostics and maintenance scripts
tests/           pytest suite
article/         JURIX 2026 paper (LaTeX source and PDF)
docs/            architecture notes
```

## Development

LexSR was developed by one researcher with AI coding assistants. Run the tests with:

```bash
pip install -e ".[dev]"
pytest
```

## Licence

Code is released under the [MIT License](LICENSE).

## Contact

Ernst van Gassen, Arktos AI Lab ([ORCID 0009-0000-0314-6441](https://orcid.org/0009-0000-0314-6441))
