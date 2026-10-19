# LexSR corpus v1.0: Surinamese legislation and case law

A structured corpus of Surinamese court decisions and legislation, modelled on FRBR, with
citation and amendment links and a knowledge graph. It is the government-sourced part of the
corpus described in:

> Ernst van Gassen. *LexSR: Consolidating Legislation and Linking Case Law to Build a Legal
> Knowledge Graph for Suriname, a Low-Resource Jurisdiction.* JURIX 2026, Frontiers in
> Artificial Intelligence and Applications, IOS Press.

Code: https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus (MIT). Corpus DOI: 10.5281/zenodo.23467651.

## What is in this release, and what is not

Only text published by a Surinamese government body is included: the 300 court decisions
from *rechtspraak.sr*, the Hof van Justitie's own portal, and the 751 legislative works whose
text comes from a file on a government website (704 of them PDFs published by De
Nationale Assemblée on dna.sr). The paper's research corpus also holds 534
legislative works obtained from the private legal portal SRiS (Stichting voor de Rechtsorde
in Suriname, sris.sr) or from local archives of unknown origin. SRiS is not a government
body, and its terms allow copies for personal use only, so those works are not redistributed:
not their text, and not their records. `release/release_works.csv` in the code repository
gives the decision and reason for every work.

Citations and amendment links from a released work to a work that is not released keep
their raw text; the target's id is in the row's `metadata` as `target_not_released`. As a
result the release links fewer citations than the research corpus.

## Contents

| | Release | Research corpus (paper) |
|---|---:|---:|
| Legislative works | 751 | 1,285 |
| Case-law decisions (1952 to 2025) | 300 | 300 |
| Fragments (articles, paragraphs, considerations) | 54,490 | 68,127 |
| Considerations in case law | 2,684 | 2,684 |
| Citations extracted | 7,160 | 12,343 |
| Citations linked to a work in the corpus | 606 (8.5%) | 2,613 (21.2%) |
| Knowledge graph nodes / edges | 1,051 / 615 | 1,585 / 2,653 |

Release figures are in `corpus_stats.json` and `knowledge_graph_stats.json`; the research
corpus figures reported in the paper are in `research_corpus_stats.json`. All are recomputed
by `release/build_release.py`.

## Files

| File | Content |
|---|---|
| `lexsr_corpus_v1.0.sqlite` | the released works, one table per entity |
| `<table>.parquet` | the same tables as Parquet (zstd) |
| `knowledge_graph.graphml` | one node per work, one edge per amendment link or linked citation |
| `knowledge_graph_stats.json`, `corpus_stats.json` | summary figures for the release |
| `research_corpus_stats.json` | summary figures for the paper's research corpus |
| `raw_sources.csv` | SHA-256 of every government source file (URL for case law) |
| `work_sources.csv` | the source file(s) of each released work |
| `source_files.zip` | those source files: 1,041 government PDFs and decision pages (1.2 GB) |
| `LICENSE.txt` | licence |
| `SHA256SUMS` | checksums of all files above |

Main tables: `works` (one row per law or decision; `source_type` is `legislation` or
`case_law`), `expressions` (dated versions of a work), `fragments` (articles, paragraphs and
considerations, with text; for decisions also the text before and after the considerations), `citations` (`citation_type` is `law_to_law`,
`case_to_law`, `law_to_case` or `unknown`; `target_work_id` is set when the reference was
linked to a work in the corpus), `amendment_links` (amending act to base act; `resolved`
marks the 9 applied by the consolidation engine), `version_diffs`.

## Sources

Released legislation comes from Surinamese government websites: parliament (De Nationale
Assemblée, dna.sr, 704 PDFs), the tax authority (Belastingdienst), the maritime authority (MAS), the standards bureau (SSB), the government
portal and the justice ministry. Decisions are the first 300 in the sitemap of
*rechtspraak.sr*, the Hof van Justitie's decision portal. Scanned PDFs were read with
Tesseract OCR; text was not corrected by hand. Legislative titles are derived from source
file names, with gazette references written as "S.B. 2016 no. 63"; the texts are copies,
not authenticated publications.

## Licence and legal basis

Article 11 of the Surinamese Auteurswet 1913 (G.B. 1913 no. 15, as amended) provides that
no copyright subsists in laws, decrees and ordinances issued by public authority, nor in
judicial decisions and administrative decisions. The legislation and decisions in this
corpus fall under that provision and are redistributed without restriction.

The government sites also yielded documents outside that scope: standards-body drafts and
comment sheets, third-party technical codes and notices. They are listed in
`release/withhold_text.csv` in the code repository. Their rows, identifiers and citations
are kept, so all counts above include them, but their text fields are empty and
`works.metadata` carries `text_withheld` with the reason.

The compilation (structure, segmentation, identifiers, links, graph, documentation) is
licensed CC BY 4.0. Court decisions are as published by the court; LexSR applies no further
anonymisation.

## How it was made and known limits

LexSR was built by one researcher working with AI coding assistants; scrapers, patterns
and the evaluation gold set were produced this way, without domain-expert review. Links are
produced automatically; checked by hand on a sample, 96.5% of the links in the research corpus are correct
(40 of 48 distinct law-to-work pairs), as are all 14 decidable pairs that link to a DNA.sr text.
In the research corpus, most unlinked citations (8,349 of 9,730) are gazette references (*Staatsblad*,
*Gouvernementsblad*), which v1.0 does not link. Amendment parsing yields at least one
classified instruction for 25 of 36 amendment documents. OCR quality varies, and part of
the corpus was recognised with an English rather than a Dutch model. Government sites
change, so a fresh scrape will not return exactly these documents; `raw_sources.csv`
identifies the files used.

## Citation

```bibtex
@inproceedings{vangassen2026lexsr,
  author    = {van Gassen, Ernst},
  title     = {{LexSR}: Consolidating Legislation and Linking Case Law to Build a Legal
               Knowledge Graph for {Suriname}, a Low-Resource Jurisdiction},
  booktitle = {Legal Knowledge and Information Systems (JURIX 2026)},
  series    = {Frontiers in Artificial Intelligence and Applications},
  publisher = {IOS Press},
  year      = {2026}
}

@dataset{vangassen2026lexsrcorpus,
  author    = {van Gassen, Ernst},
  title     = {{LexSR} corpus v1.0: Surinamese legislation and case law},
  year      = {2026},
  publisher = {Zenodo},
  version   = {1.0},
  doi       = {10.5281/zenodo.23467651}
}
```

Contact: Ernst van Gassen, Arktos AI Lab, evg.gassen@gmail.com, ORCID 0009-0000-0314-6441.
