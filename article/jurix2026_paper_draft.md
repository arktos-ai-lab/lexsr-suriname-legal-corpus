# LexSR: Consolidating Legislation and Linking Case Law to Build a Legal Knowledge Graph for Suriname, a Low-Resource Jurisdiction

> **Status**: accepted at JURIX 2026 (paper #258), long paper; camera-ready revision.
> **Format**: IOS Press, Frontiers in Artificial Intelligence and Applications (FAIA);
> 10 pages excluding references.
>
> **Author**: Ernst van Gassen, Independent researcher at Arktos AI Lab
> (evg.gassen@gmail.com), ORCID 0009-0000-0314-6441. The ORCID goes in the submission
> form, not the paper.
>
> **This file mirrors `lexsr_jurix2026.tex`**, which is the source of record and what gets
> submitted. Section and table numbers below are the resolved values from the PDF.
> The corpus DOI 10.5281/zenodo.23467651 is reserved; it resolves once the Zenodo record is
> published (see `../release/PUBLISH.md`).

---

# Abstract

Access to consolidated, machine-readable legislation and linked case law is a prerequisite for effective legal information systems. No such resource previously existed for Suriname, a Dutch-speaking jurisdiction of about 640,000 people whose legal archive is mostly scanned. We present LexSR, a pipeline that ingests raw legislative PDFs and the Hof van Justitie’s case-law portal, applies OCR, classifies documents, automatically consolidates *wijzigingswetten* (amendment acts) into dated versions of each act, modelled on the Work and Expression levels of FRBR (Functional Requirements for Bibliographic Records), and links case law to the legislation it applies. The pipeline produces a corpus of 1,585 works (1,285 legislative, 300 judicial) and 68,127 fragments, from which we construct a unified knowledge graph of 2,653 edges spanning amendment, citation, and case-application relationships. We report two evaluations: a synthetic citation-resolution benchmark, where our resolver passes 62 of 65 curated cases at article level (100% precision, 93.5% recall), and, as the paper’s methodological contribution, a measurement of how much of that accuracy holds on the real, messily titled corpus the resolver was built for. Only 21% of extracted citations resolve to an in-corpus work, against 11% for exact title matching. We attribute the gap to a structural mismatch between the canonical identifiers assumed by curated benchmarks and the source-derived titles of scraped, OCR’d documents, and to gazette references, which the corpus does not yet model. Amendment parsing yields a classified instruction for 25 of 36 amendment documents (69%); most failures trace to operative text missing from the ingested source. We discuss implications for low-resource legal NLP and make the code, evaluation scripts and the government-sourced part of the corpus publicly available.

**Keywords.** legal knowledge graph; legislative consolidation; case-law citation linking; low-resource legal NLP; FRBR; Suriname

# Introduction

Consolidation (merging an amendment act’s instructions into the base law it modifies) and citation linkage (connecting case law to the provisions it applies) are both legally critical and technically demanding. Most European jurisdictions maintain authoritative consolidated legislation and case-law databases through governmental portals (e.g., *wetten.overheid.nl* and *rechtspraak.nl* in the Netherlands, EUR-Lex for EU law). Small and developing jurisdictions often lack the institutional capacity for either, leaving practitioners to track amendment chains and cross-reference case law by hand, or to rely on commercial services, with no public integrated view of the law in force.

Suriname, a Dutch-speaking jurisdiction of about 640,000 people, shows this gap clearly. The *Staatsblad* (official gazette; before independence in 1975 the *Gouvernementsblad*) is available as individual scanned PDFs, scattered over government sites and a private legal portal, and the Hof van Justitie publishes case decisions on a dedicated portal (*rechtspraak.sr*, launched 2019), but no system links the two: a decision citing “artikel 4 Arbeidswet 1963” offers no way to jump to that provision, and no portal shows any act’s consolidated text as amended.

This paper makes four contributions:

1.  **LexSR corpus**: a structured, FRBR-modelled database of 1,285 Surinamese legislative works and 300 Hof van Justitie decisions, with 68,127 fragments, derived from eleven government sources, the private legal portal SRiS, the judicial case-law portal, and locally digitised archives.

2.  **Amendment parser**: a hybrid regex + LLM system that extracts structured consolidation instructions from *wijzigingswetten*, evaluated on every amendment act in the corpus.

3.  **Case-law integration**: a scraper and ingestion pipeline for *rechtspraak.sr* that segments decisions into numbered considerations and links them to cited legislation, reusing the same citation-resolution machinery built for legislation-to-legislation citations.

4.  **Knowledge graph and an accuracy audit**: a unified graph of amendment, citation, and case-application edges over the full corpus, together with a measurement of the gap between synthetic-benchmark citation accuracy and real-corpus linkage rate, and an account of why the two diverge.

Code, evaluation scripts and the government-sourced part of the corpus are public.[^1]

# Related Work

## Legal Document Modelling

The FRBR (Functional Requirements for Bibliographic Records) model provides a well-established abstraction for legislative documents, distinguishing the abstract Work (a law), its Expression (a version at a given date), its Manifestation (a particular encoding), and individual Items \[1\]. Akoma Ntoso \[2\] is the primary XML schema for legislation structured around these concepts and has been adopted by the UN, EU, and several national parliaments. The European Legislation Identifier (ELI) \[3\] provides URI conventions compatible with this model. Where a provision sits in this model varies: FRBRoo treats a part of a text as an Expression Fragment \[9\], while component-level approaches make each provision a Work in its own right \[10\]. We follow the first reading and extend it to case law: a court decision is a Work with a single Expression, and its numbered considerations are fragments, the case-law analogue of legislative articles.

## Legal Knowledge Systems for Consolidation

Automated consolidation has received sustained attention in the legal knowledge systems community. Biagioli, Francesconi et al. \[4\] describe the *Norme in Rete* project’s XML standards and tools for cross-referencing and consolidating Italian legislation. Spinosa et al. \[5\] apply NLP-based metadata extraction to detect and classify textual amendments (repeal, substitution, integration) for the same consolidation task. The *legislation.gov.uk* system \[6\] handles consolidation for UK law at scale with a semi-automated editorial workflow. Vitali and Zeni \[7\] discuss amendment formalisation in Akoma Ntoso. The Dutch *wetten.overheid.nl* portal maintains consolidated texts for all Dutch and former-colonial legislation through a government editorial process, but does not publish its consolidation tooling \[8\]. LexSR differs from these in targeting a low-resource setting (no existing structured archive, predominantly scanned PDFs, and no editorial support) with a fully automated pipeline.

## Legal Knowledge Graphs and Citation Networks

Citation network analysis of case law has a long history in AI and Law, from early work on precedent networks to more recent graph-based approaches to legal information retrieval and argumentation mining \[13\]. Legal knowledge graphs unifying legislation and case law have been built for well-resourced jurisdictions; Dhani et al. \[14\], for example, construct one from Indian court judgments and statutes for case-similarity recommendation, and Lex Graph \[11\] builds one over UK legislation. Linkoln \[12\] detects and links legislative references in Italian texts and is deployed on the Italian Senate website. These approaches resolve references against structured sources with an authoritative naming scheme; LexSR must first create the identifiers it resolves to, from scanned PDFs and an unindexed case portal.

## NLP for Legal Amendment Text

Information extraction from legal amendment provisions has been studied for several European languages. Lesmo, Mazzei, Palmirani et al. \[15\] present TULSI, an NLP system combining deep syntactic analysis with a shallow semantic interpreter to extract and classify modificatory provisions in Italian legislative texts. For Dutch, Boer et al. \[16\] propose the MetaLex/LKIF XML standards for structuring legislation, and Hoekstra et al. \[17\] extend this with the LKIF Core ontology of basic legal concepts, but explicit amendment/consolidation parsing for Dutch has received limited attention in the JURIX and ICAIL literature. The use of large language models for legal text understanding is by now standard \[18,19\]. Our hybrid approach, regex-first with an LLM fallback for low-confidence cases, is motivated by the need for determinism and auditability while retaining flexibility for novel constructions.

## Legal Corpora for Small Jurisdictions

Multilingual legal corpora remain concentrated on the major European languages \[20\], and Krasadakis et al. \[21\] survey the challenges this poses for low-resource legal NLP. For Suriname, no machine-readable corpus linking legislation and case law had been published before LexSR.

# Corpus and Ingestion Pipeline

## Sources

LexSR ingests legislation from eleven government sites (parliament DNA.sr, the tax and maritime authorities, the standards bureau, the government portal, six ministries), the private legal portal SRiS (Stichting voor de Rechtsorde in Suriname), and locally digitised PDF archives \[22\]. Of the 1,285 legislative works, 704 take their text from PDFs published by parliament, 47 from other government sites, 499 from SRiS and 35 from local archives. For the 215 acts available from both parliament and SRiS the parliament copy is used, as SRiS, a private foundation, publishes copies, not authenticated texts. Six further sites our scrapers support were excluded because their configured URLs no longer match the live site structure (Section 7.1). Case law is sourced separately from *rechtspraak.sr*, the Hof van Justitie’s decision portal, described in Section 5.1. Legislative source files are retained unchanged and tied to their works through the filename-derived identifier; case-law works record source URL and retrieval time. A document obtained under several file names is kept once: works with identical text, or with the same gazette issue and near-identical OCR text, are merged. A SHA-256 manifest of all source files is in the repository \[25\]. Article 11 of the Surinamese Auteurswet 1913 excludes legislation, judicial decisions and administrative decisions from copyright; documents outside that scope (theses, standards drafts, third-party technical codes) are released without their text.

## Data Model

The canonical store maps FRBR entities as shown in Table 1. Items are not modelled, and each fragment belongs to one Expression. The schema defines Manifestations, but the corpus does not yet populate them.

<div id="tab:frbr">

| **FRBR entity** | **LexSR entity** | **Legislation example** | **Case-law example** |
|:---|:---|:---|:---|
| Work | `Work` | Arbeidswet 1963 | SRU-HvJ-2022-39 |
| Expression | `Expression` | version_date=2024-01-15 | single expression |
| Manifestation | `Manifestation` | source PDF | source HTML page |
| Expression Fragment | `Fragment` | Artikel 4, lid 2 | consideration 2.1 |

FRBR entities and LexSR entities

</div>

Stable URIs follow an ELI-inspired pattern rooted at `/id/{scheme}/{id}`, with `law` and `caselaw` as the two schemes currently populated. The amendment relation is represented by an `AmendmentLink` table connecting an amending Work to the base Work it modifies; the citation relation (legislation-to-legislation, case-to-legislation, and case-to-case) is represented by a single generic `Citation` table typed by `citation_type`, avoiding a schema split between the two document kinds.

## Corpus Statistics

Table 2 summarizes the corpus. The legislative works whose gazette issue is known (991) span 1904 to 2026; two thirds date from 2010 onward, when digital-native PDFs became available. The case-law sample spans 1952 to 2025 and was drawn from a discoverable pool of 900+ decisions on *rechtspraak.sr* (Section 5.1); this paper reports on the first 300 ingested.

<div id="tab:stats">

| **Metric**                                       |     **Value** |
|:-------------------------------------------------|--------------:|
| Legislative works                                |         1,285 |
| Case-law works (decisions)                       |           300 |
| Expressions                                      |         1,594 |
| Fragments (articles, paragraphs, considerations) |        68,127 |
| Considerations extracted from case law           |         2,684 |
| Citations extracted                              |        12,343 |
| Citations resolved to an in-corpus work          | 2,613 (21.2%) |
| Amendment links identified                       |            40 |
| Amendment links fully applied by the engine      |             9 |
| Knowledge graph nodes / edges                    | 1,585 / 2,653 |

Corpus statistics

</div>

## OCR and Structure Extraction

For scanned PDFs, text is extracted using a pipeline that applies standard PDF text extraction first and falls back to Tesseract OCR \[23\] for image-based pages, after turning sideways pages upright and splitting scans that hold two gazette pages. Article boundaries are detected heuristically using the standard Dutch/Surinamese legislative structure: “Artikel N” headings, “Lid N” sub-paragraphs, and lettered or numbered sub-items. OCR quality varies substantially across the corpus, and a subset carries the lower text quality of an English rather than a Dutch recognition model. We record this as a property of the corpus, since it bears on the parsing coverage reported in Section 6.3.

# Amendment Consolidation

## Structure of a *Wijzigingswet*

A Surinamese amendment act (*wijzigingswet* or *wijzigingsbesluit*) follows a standard template. The operative article names the base law and lists labelled instructions, as in this excerpt from S.B. 2002 no. 113 \[24\]:

> ARTIKEL I\
> In de Wet Huurwaardebelasting 1995 (S.B. 1995 no.108) worden\
> de volgende wijzigingen aangebracht:\
> A. Artikel 5 lid 2 wordt vervangen door: De belasting wordt\
> ten minste vastgesteld op Sf. 20.000,-.\
> B. In artikel 6 lid 1 wordt de zinsnede ‘de vrije\
> verkoopwaarde \[...\]’ vervangen door: \[...\]\
> C. Artikel 7 lid 1 komt te luiden als volgt: \[...\]

Later articles (Artikel II, ...) may amend a second base law or specify the in-force date. The act typically concludes with a *Memorie van Toelichting* (explanatory memorandum), which must be excluded from instruction extraction.

## Parser Architecture

The `AmendmentParser` operates in two passes. **Pass 1 (regex)** splits the document into sections by “Artikel N” boundaries (after stripping explanatory-memorandum text), extracts the target law name from the section header or preamble, splits labelled instruction blocks (A., B., C. or Roman-numeral labels), and classifies each block against a priority-ordered set of $`\sim`$<!-- -->20 regex patterns covering the standard Dutch amendment constructions (repeal, replacement, insertion, phrase substitution). Confidence is a fixed prior per pattern, not a learned score: 0.95 for an explicit repeal, 0.70 to 0.90 for substitutions and insertions whose new text was isolated, 0.45 to 0.55 when the pattern matched but the new text could not be isolated, and 0.20 for an unclassified block. **Pass 2 (LLM fallback)**, used only when an instruction’s confidence falls below 0.70, re-sends the section to an LLM with a structured JSON prompt (in the repository \[25\]); LLM output overrides regex output where its confidence is higher. The fallback is disabled for every result reported here, so all figures below describe regex-only behaviour and the fallback itself is not evaluated.

## Consolidation Engine

`ConsolidationEngine` consumes `AmendmentInstruction` objects and applies them to the current expression of the base law to produce a new `Expression` at the amendment’s effective date. Base-law resolution maps the amendment’s target-law reference to a Work by title matching, since the stated law name rarely matches the filename-derived work ID. Let $`n(s)`$ lowercase $`s`$, delete the Dutch articles and prepositions *de, het, van, der, den, een* and collapse whitespace. A reference $`r`$ resolves to a work with title $`t`$ by the first step that succeeds: (i) $`r = t`$ ignoring case; (ii) $`n(r) = n(t)`$. Steps (iii) and (iv) need a law name in $`r`$ once article, *lid* and *sub* pointers are removed, and skip amending acts (titles with *wijziging*, *verbeterblad*, *intrekking*) unless $`r`$ names one: (iii) one of $`n(r), n(t)`$ contains the other, both at least four characters, the closest in length winning; (iv) $`r`$ and $`t`$ share a four-digit year and $`t`$ contains a word of $`n(r)`$ longer than three characters. This matching logic is shared with citation resolution, which requires the identical fallback for the identical reason (Section 6.2).

# Case-Law Integration and Knowledge Graph

## Scraping *rechtspraak.sr*

*rechtspraak.sr* is a WordPress-based site with no listing or search API; the only reliable enumeration mechanism is the WordPress XML sitemap, which we parse recursively (sitemap index $`\rightarrow`$ per-type sub-sitemaps $`\rightarrow`$ individual decision URLs). This alone surfaced 900+ decisions spanning 1952 to 2025. Each decision page exposes clean structured metadata (court, case number, decision and publication dates, legal area) as label/value pairs, and full decision text as HTML, considerably cleaner input than the OCR’d legislative PDFs, since no scanning or recognition step is involved.

## Consideration Segmentation

Surinamese court decisions carry no explicit “r.o.” (*rechtsoverweging*) label, the marker of each numbered consideration in Dutch judgments. Recent decisions number their reasoning under a “De beoordeling” (assessment) heading, as section.paragraph (“2.1”, “4.7”) or as plain paragraphs (“1.”, “2.”); older decisions give it under “Ten aanzien van het recht” as paragraphs opening with “Overwegende, dat”. We segment on these markers up to the dictum; the text before the reasoning and the dictum are kept as two further fragments, so no text is dropped. This splits 286 of the 300 decisions into 2,670 considerations; the other 14 fit neither layout and are kept whole (2,684 consideration fragments in all).

## Citation Extraction from Case Law

Legislative citations in case-law text (“artikel 4 lid 2 Arbeidswet 1963”) are extracted with the same resolver and pipeline as legislation-to-legislation citations, with no case-law-specific logic, yielding 583 case-to-legislation citations across the 300 decisions. Case-to-case citations by the portal’s own identifiers (“SRU-HvJ-2019-3”) use a separate regex; none were found, because decisions cite each other by docket number instead (Section 7.1).

## Knowledge Graph Construction

We assemble a unified graph directly from the canonical store, adding no schema or storage layer, with one node per Work (typed `legislation` or `case_law`) and three edge types: AMENDS (from `AmendmentLink`), CITES (legislation-to-legislation citations), and APPLIES (case-to-legislation citations). The resulting graph over the full corpus has 1,585 nodes and 2,653 edges (2,178 CITES, 435 APPLIES, 40 AMENDS), with a largest connected component of 687 nodes. The ten most-cited works are, in order: Personeelswet (civil service law), Burgerlijk Wetboek (Civil Code), Wetboek van Strafrecht (Criminal Code), Grondwet (Constitution), Kiesregeling (electoral law), Wetboek van Strafvordering (Code of Criminal Procedure), Wetboek van Koophandel (Commercial Code), Wet Loonbelasting (wage tax), Inkomstenbelasting 1922 (income tax) and Wet Regionale Organen (regional bodies). This distribution matches expectation for a general legal system and offers a face-validity check on the extraction pipeline independent of the benchmark in Section 6.1.

# Evaluation

## Citation Resolution: Synthetic Benchmark

We evaluate the citation resolver on a 65-case known-answer dataset (61 citation and 4 temporal cases, including gazette references and deliberately unresolvable negatives), whose expected answers we checked by hand (all correct). Most cases were generated with an AI coding assistant together with the resolver patterns; six are quoted from decisions in the corpus, and decisions are cited by their *rechtspraak.sr* identifiers, as Suriname has no ECLI. Cases naming an article are scored at article level, ignoring the version date (the temporal cases thus do not test the version). The resolver passes 62 of 65 (precision 100%, recall 93.5%, F1 96.7%). The three misses are a Dutch Hoge Raad judgment cited by ECLI (Surinamese decisions also cite Dutch case law), where the resolver keeps the sentence-final full stop in the identifier; an article separated from its law by a bracketed gazette reference; and the Wetboek van Burgerlijke Rechtsvordering, which the abbreviation registry lacks. Because patterns and most cases share an origin, this result shows pattern coverage on well-formed input, not generalisation; the next section removes that condition.

## Citation Resolution: Real-Corpus Linkage Gap

Synthetic-benchmark accuracy measures whether the resolver identifies *that* a reference exists and which canonical URI it maps to. It does not measure whether that URI names anything in the corpus. The two come apart because the benchmark and the corpus use different names: the resolver’s abbreviation registry maps “Grondwet” to the canonical slug `grondwet-1987`, while a scraped corpus with no naming authority identifies its works by their source documents (`grondwet-met-mvt-4fb9cced`, titled “Grondwet met Mvt”). No registry slug is the identifier of a work in the corpus, so canonical lookup alone links nothing. Resolution therefore falls back on the title matching of Section 4, which prefers the closest title and skips amending acts.

With this fallback, 2,613 of the 12,343 citations in the corpus (21.2%) resolve to an in-corpus work. As a baseline, linking each extracted reference to a work by exact, case-insensitive title match links 1,363 citations (11.0%). Checked by hand on a sample, 96.5% of the links are correct (40 of 48 distinct law-to-work pairs), as are all 14 decidable pairs that link to a DNA.sr text; the errors link to an implementing decree, match on the year alone, or read a tax name as a law reference. The remaining 79% are overwhelmingly gazette references (*Staatsblad*/*Gouvernementsblad* citations). The resolver does not link these, because the corpus does not model gazette issues as entities. Fuzzy title matching is no substitute: its substring step pairs “G.B. 1973 no. 9” with an act numbered 1973 no. 73. Yet 2,625 unresolved gazette references name a gazette, year and number that appear together in the title of a work in the corpus; checked by hand, that work is the cited act for all 74 of 75 sampled references that could be decided. An exact gazette-number match is thus a direct next step (Section 7.3).

A resolver validated only against a curated benchmark can thus report near-perfect accuracy while linking little of its corpus. We suggest a checklist item for similar projects: *measure resolution against the real corpus’s actual identifiers, not just against curated ground truth.*

## Amendment Parsing Coverage

We evaluate the amendment parser (regex pass only) on all 36 amendment documents in the corpus, per document rather than per `AmendmentLink` row (40 links, since a document may target more than one base law). A document counts as parsed when the regex pass extracts at least one classified instruction from it, that is, an instruction other than the 0.20 fallback; whether the extracted instructions are correct is not assessed by this measure. 25 of the 36 documents (69.4%) are parsed.

Checked by hand, 9 of the 11 failing documents contain no operative instruction in the stored text: it holds only the closing formula (in-force clause and signatures), the explanatory memorandum, a bare enumeration without its amending sentence, or OCR too damaged to read. One repeals an earlier decree in its closing provisions (“vervalt het Presidentieel Besluit …”), which the patterns miss, and in one the OCR misread the article numbers (“Artikel \| lid I” for “Artikel 1 lid 1”). Parsing failures thus mostly reflect what ingestion captured from the source, not the parser rules.

## Engine Validation

Nine amendments have been fully applied by the consolidation engine and are available for inspection in the canonical store; the remaining parseable documents fail engine-level base-law resolution or produce low-confidence instructions withheld pending review.

# Discussion and Future Work

## Limitations

**Source stability.** Six candidate sources were excluded because their URLs or site structure changed; such sites rarely expose a stable API, so scrapers decay silently.

**Amendment parsing and upstream stages.** The 69% coverage figure (Section 6.3) counts documents with at least one classified instruction and says nothing about instruction correctness. OCR, document classification and downstream tasks such as retrieval are not evaluated separately.

**Other jurisdictions.** The pipeline is untested outside Suriname. For Spanish-, French- or Portuguese-language gazettes in Latin America or Africa we expect OCR to carry over and scrapers and patterns to need rewriting; Indigenous-language texts, with few OCR models or evaluation data, are harder.

**Case-to-case citation coverage.** The extractor found no case-to-case citations (Section 5) because it matches only the portal’s own identifiers, yet 59 of the 300 decisions cite a first-instance judgment by docket number (“A.R. no. 11-4959”). Linking appeal chains needs this pattern and the first-instance decisions on the portal.

## Effort, Operation and Generalisability

LexSR was built by one researcher with legal training (Dutch tax law) but no specialist knowledge of Surinamese law, working with AI coding assistants; no Surinamese legal expert reviewed the output. The scrapers, regex patterns, abbreviation registry and 65-case gold set were produced this way, and corpus text was not manually corrected. New publications are added by re-running the scrapers, which skip stored documents; we have not measured throughput or compute cost. Porting LexSR to another jurisdiction means replacing four jurisdiction-specific parts: scraper configurations, the citation abbreviation registry, the gazette-reference patterns, and the $`\sim`$<!-- -->20 amendment patterns. Suriname shares Dutch-language drafting templates with Aruba, Curaçao and Sint Maarten, where the last two should carry over largely unchanged.

## Future Work

Next steps are: replacing fuzzy title matching with a resolution service of the kind URN:NIR provides for Italian law, mapping canonical names to stored documents through a maintained alias table; taking titles and identifiers from the printed heading rather than the file name; linking gazette references by exact year and number, then modelling *Staatsblad*/*Gouvernementsblad* issues as entities; ingesting the remaining 600+ decisions on *rechtspraak.sr*; an instruction-level gold standard for the amendment parser; and automated detection of scraper configuration decay.

# Conclusion

We presented LexSR, a versioned, consolidated corpus of Surinamese legislation and case law with a knowledge graph over it. Its central contribution is methodological: we measured how much of the resolver’s synthetic-benchmark accuracy (100% precision, 93.5% recall) holds on the real corpus (21.2% linkage, against 11.0% for exact titles), and traced the gap to the mismatch between canonical and source-derived identifiers and to gazette references. Amendment parsing covers 25 of 36 amendment documents; most failures trace to text that ingestion did not capture.

<div class="thebibliography">

99

IFLA Study Group. Functional Requirements for Bibliographic Records. IFLA Publications; 1998.

Palmirani M, Vitali F. Akoma-Ntoso for Legal Documents. In: Sartor G, et al., editors. Legislative XML for the Semantic Web. Springer; 2011. p. 75-100.

Publications Office of the European Union. European Legislation Identifier (ELI). Official Journal 2012/C 325/02; 2012.

Biagioli C, Francesconi E, Spinosa P, Taddei M. XML Documents Within a Legal Domain: Standards and Tools for the Italian Legislative Environment. In: Document Analysis Systems VI (DAS 2004). Lecture Notes in Computer Science, vol 3163. Springer; 2004.

Spinosa P, Giardiello G, Cherubini M, Marchi S, Venturi G, Montemagni S. NLP-based metadata extraction for legal text consolidation. In: Proceedings of the 12th International Conference on Artificial Intelligence and Law (ICAIL 2009); 2009. p. 40-49.

Sheridan J, Tennison J. Linking UK government data. In: Proceedings of LDOW 2010; 2010.

Vitali F, Zeni F. Towards a country-independent data format: the Akoma Ntoso experience. In: Proceedings of the V Legislative XML Workshop. Florence; 2007.

KOOP. Officiële bekendmakingen: wetten.overheid.nl. Kennis- en exploitatiecentrum voor officiële overheidspublicaties; 2022.

Bekiari C, Doerr M, Le Boeuf P, Riva P, editors. FRBR object-oriented definition and mapping from FRBRER, FRAD and FRSAD, version 2.4. IFLA; 2015.

de Martim H. Modeling the Diachronic Evolution of Legal Norms: An LRMoo-Based, Component-Level, Event-Centric Approach to Legal Knowledge Graphs. arXiv:2506.07853; 2025.

Incubator for Artificial Intelligence (i.AI), UK Government. Lex Graph: building a knowledge graph of UK legislation \[software\]; 2025. Available from: <https://github.com/i-dot-ai/lex-graph>.

Bacci L, Agnoloni T, Marchetti C, Battistoni R. Improving Public Access to Legislation Through Legal Citations Detection: The Linkoln Project at the Italian Senate. In: Knowledge of the Law in the Big Data Age. Frontiers in Artificial Intelligence and Applications. IOS Press; 2019. p. 149-158.

Bench-Capon T, et al. A history of AI and Law in 50 papers. Artificial Intelligence and Law. 2012;20(3):215-319.

Dhani JS, Bhatt R, Ganesan B, Sirohi P, Bhatnagar V. Similar Cases Recommendation using Legal Knowledge Graphs. arXiv:2107.04771; 2021.

Lesmo L, Mazzei A, Palmirani M, Radicioni DP. TULSI: an NLP system for extracting legal modificatory provisions. Artificial Intelligence and Law. 2013;21:139-172.

Boer A, Winkels R, Vitali F. Proposed XML standards for law: MetaLex and LKIF. In: Proceedings of JURIX 2007. IOS Press; 2007.

Hoekstra R, Breuker J, Di Bello M, Boer A. The LKIF core ontology of basic legal concepts. In: Proceedings of the LOAIT Workshop. CEUR Workshop Proceedings, vol 321; 2007. p. 43-63.

Chalkidis I, et al. LEGAL-BERT: The muppets straight out of law school. In: Findings of EMNLP 2020; 2020.

Koreeda Y, Manning C. ContractNLI: A dataset for document-level natural language inference for contracts. In: Findings of EMNLP 2021; 2021.

Niklaus J, et al. MultiLegalPile: A 689GB Multilingual Legal Corpus. In: Proceedings of ACL 2024 (Volume 1: Long Papers); 2024.

Krasadakis P, Sakkopoulos E, Verykios VS. A Survey on Challenges and Advances in Natural Language Processing with a Focus on Legal Informatics and Low-Resource Languages. Electronics. 2024;13(3):648.

Sources of the corpus (accessed 2026): De Nationale Assemblée, <https://www.dna.sr/wetgeving/surinaamse-wetten/>; Belastingdienst Suriname, <https://belastingdienst.sr>; Maritieme Autoriteit Suriname, <https://mas.sr>; Suriname Standards Bureau, <https://www.ssb.sr>; Government of Suriname and its ministries, <https://gov.sr>; Stichting voor de Rechtsorde in Suriname, <https://www.sris.sr>; Hof van Justitie, <https://rechtspraak.sr>.

Smith R. An Overview of the Tesseract OCR Engine. In: Proceedings of the Ninth International Conference on Document Analysis and Recognition (ICDAR 2007). IEEE; 2007. p. 629-633.

Wet van 27 december 2002, houdende wijziging van de Wet Huurwaardebelasting 1995. Staatsblad van de Republiek Suriname 2002 no. 113. Available from: <https://www.dna.sr/media/5iooda3s/sb-2002-no-113.pdf>.

van Gassen E. LexSR: code, evaluation scripts and source manifest \[software\]; 2026. Available from: <https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus>.

</div>

[^1]: Code and evaluation scripts: <https://github.com/arktos-ai-lab/lexsr-suriname-legal-corpus>. The works obtained from government sources (the 300 decisions and 751 legislative works) are archived with their source files under CC BY 4.0 at <https://doi.org/10.5281/zenodo.23467651>; works obtained from SRiS are not redistributed.
