"""
Tests for the case-law ingestion pipeline:
  RechtspraakScraper parsing helpers, CaseLawPipeline.

Run with: python -m pytest tests/test_caselaw.py -v
"""

from __future__ import annotations

from datetime import date

import pytest

from lexsr.core.models import CitationType, FragmentType, SourceType
from lexsr.core.store import CanonicalStore
from lexsr.ingestion.scrapers.base import ScrapedDocument
from lexsr.ingestion.scrapers.rechtspraak import (
    RechtspraakScraper,
    parse_dutch_date,
)
from lexsr.ingestion.caselaw_pipeline import CaseLawPipeline, split_considerations, strip_site_header


# ===================================================================
# parse_dutch_date
# ===================================================================

class TestParseDutchDate:
    def test_valid_date(self):
        assert parse_dutch_date("18 februari 2022") == date(2022, 2, 18)

    def test_single_digit_day(self):
        assert parse_dutch_date("5 mei 1999") == date(1999, 5, 5)

    def test_unknown_month(self):
        assert parse_dutch_date("18 blah 2022") is None

    def test_empty_string(self):
        assert parse_dutch_date("") is None

    def test_malformed(self):
        assert parse_dutch_date("not a date") is None


# ===================================================================
# RechtspraakScraper.scrape_decision (HTML parsing, no network)
# ===================================================================

_SAMPLE_HTML = """
<html><head><title>SRU-HvJ-2022-39 - Hof van Justitie</title></head>
<body>
<main>
<div class="page-content">
<div class="search-result clearfix">
<div class="list-label">Instantie</div><div class="list-value">Hof van Justitie</div>
<div class="list-label">Zaaknummer</div><div class="list-value">GR 14731</div>
<div class="list-label">Uitspraakdatum</div><div class="list-value">18 februari 2022</div>
<div class="list-label">Publicatiedatum</div><div class="list-value">11 oktober 2023</div>
<div class="list-label">Rechtsgebied</div><div class="list-value">Burger-overheid</div>
<div class="list-label">Inhoudsindicatie</div><div class="list-value">Test abstract text.</div>
De beoordeling
2.1 Zoals bepaald in artikel 4 lid 2 Arbeidswet 1963 heeft de werknemer recht op verlof.
2.2 Zie ook SRU-HvJ-2019-3 voor een vergelijkbare zaak.
</div>
</div>
</main>
</body></html>
"""


class TestRechtspraakScraperParsing:
    def test_scrape_decision_extracts_metadata(self, monkeypatch):
        scraper = RechtspraakScraper()
        monkeypatch.setattr(scraper, "_fetch_url", lambda url, method="GET": _SAMPLE_HTML.encode("utf-8"))

        doc = scraper.scrape_decision("https://rechtspraak.sr/sru-hvj-2022-39/")

        assert doc is not None
        assert doc.metadata["sru_id"] == "SRU-HVJ-2022-39"
        assert doc.metadata["court"] == "Hof van Justitie"
        assert doc.metadata["case_number"] == "GR 14731"
        assert doc.metadata["decision_date"] == "2022-02-18"
        assert doc.metadata["publication_date"] == "2023-10-11"
        assert doc.metadata["legal_area"] == "Burger-overheid"
        assert "Arbeidswet 1963" in doc.metadata["full_text"]

    def test_scrape_decision_returns_none_on_fetch_failure(self, monkeypatch):
        scraper = RechtspraakScraper()
        monkeypatch.setattr(scraper, "_fetch_url", lambda url, method="GET": None)

        assert scraper.scrape_decision("https://rechtspraak.sr/sru-hvj-2022-39/") is None

    def test_extract_sitemap_locs(self):
        xml = b"""<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://rechtspraak.sr/sru-hvj-2020-1/</loc></url>
          <url><loc>https://rechtspraak.sr/sru-k1-2019-3/</loc></url>
        </urlset>"""
        locs = RechtspraakScraper._extract_sitemap_locs(xml)
        assert locs == [
            "https://rechtspraak.sr/sru-hvj-2020-1/",
            "https://rechtspraak.sr/sru-k1-2019-3/",
        ]

    def test_extract_sitemap_locs_malformed_xml(self):
        assert RechtspraakScraper._extract_sitemap_locs(b"not xml") == []


# ===================================================================
# CaseLawPipeline
# ===================================================================

def _make_doc(full_text: str, **meta_overrides) -> ScrapedDocument:
    meta = {
        "sru_id": "SRU-HVJ-2022-39",
        "court": "Hof van Justitie",
        "case_number": "GR 14731",
        "decision_date": "2022-02-18",
        "publication_date": "2023-10-11",
        "legal_area": "Burger-overheid",
        "abstract": "Test abstract",
        "full_text": full_text,
    }
    meta.update(meta_overrides)
    return ScrapedDocument(
        title="SRU-HvJ-2022-39",
        url="https://rechtspraak.sr/sru-hvj-2022-39/",
        document_type="html",
        source="rechtspraak",
        scraped_at=date(2026, 1, 1),
        metadata=meta,
    )


class TestCaseLawPipeline:
    def test_process_document_builds_work_and_expression(self):
        doc = _make_doc("De beoordeling\n2.1 Eerste overweging.\n2.2 Tweede overweging.")
        result = CaseLawPipeline().process_document(doc)

        assert result.success
        assert result.work.id == "caselaw-sru-hvj-2022-39"
        assert result.work.source_type == SourceType.CASE_LAW
        assert result.work.title == "GR 14731"
        assert result.expression.id == result.work.id
        assert result.expression.version_date == date(2022, 2, 18)

    def test_process_document_splits_considerations(self):
        doc = _make_doc(
            "De beoordeling\n"
            "2.1 Eerste overweging over de feiten.\n"
            "2.2 Tweede overweging over het recht.\n"
            "3.1 De slotsom."
        )
        result = CaseLawPipeline().process_document(doc)

        considerations = [f for f in result.fragments if f.fragment_type == FragmentType.CONSIDERATION]
        assert [f.number for f in considerations] == ["2.1", "2.2", "3.1"]
        assert "Eerste overweging" in considerations[0].text
        # the heading before the first consideration is kept, not dropped
        assert result.fragments[0].fragment_type == FragmentType.FACTS
        assert result.fragments[0].text == "De beoordeling"

    def test_process_document_falls_back_to_single_fragment(self):
        doc = _make_doc("Geen genummerde overwegingen in deze korte uitspraak.")
        result = CaseLawPipeline().process_document(doc)

        assert len(result.fragments) == 1
        assert result.fragments[0].number == "1"
        assert "Geen genummerde" in result.fragments[0].text

    def test_process_document_fails_on_empty_text(self):
        doc = _make_doc("")
        result = CaseLawPipeline().process_document(doc)

        assert not result.success
        assert result.errors

    def test_case_to_case_citation_extracted(self):
        doc = _make_doc("2.1 Zie ook SRU-HvJ-2019-3 voor een vergelijkbare zaak.")
        result = CaseLawPipeline().process_document(doc)

        assert len(result.citations) == 1
        cit = result.citations[0]
        assert cit.citation_type == CitationType.CASE_TO_CASE
        assert cit.target_work_id == "caselaw-sru-hvj-2019-3"
        assert cit.source_work_id == "caselaw-sru-hvj-2022-39"

    def test_self_citation_not_recorded(self):
        # A decision's own SRU id appearing in its header/footer shouldn't
        # produce a self-referential citation.
        doc = _make_doc("2.1 Deze zaak SRU-HvJ-2022-39 betreft...")
        result = CaseLawPipeline().process_document(doc)

        assert result.citations == []

    def test_process_document_persists_via_canonical_store(self, tmp_path):
        doc = _make_doc("2.1 Eerste overweging.\n2.2 Tweede overweging.")
        result = CaseLawPipeline().process_document(doc)

        store = CanonicalStore(str(tmp_path / "test.db"))
        store.save_work(result.work)
        store.save_expression(result.expression)
        for fragment in result.fragments:
            store.save_fragment(fragment)

        stored_work = store.get_work(result.work.id)
        assert stored_work is not None
        assert stored_work.source_type == SourceType.CASE_LAW

        stored_fragments = store.get_fragments_for_expression(result.expression.id)
        assert len(stored_fragments) == 2
        store.close()


class TestRepeatedConsiderationNumbers:
    def test_repeated_numbers_get_distinct_ids(self):
        text = "1.1 Eerste procedure.\n2.1 Eerste beoordeling.\n2.1 Tweede reeks, zelfde nummer."
        frs = CaseLawPipeline._build_fragments("caselaw-x", "/id/caselaw/x", text)
        assert [f.number for f in frs] == ["1.1", "2.1", "2.1"]
        assert len({f.id for f in frs}) == 3
        assert frs[2].uri.endswith("/consideration/2.1-2")
        assert "Tweede reeks" in frs[2].text and "Eerste beoordeling" in frs[1].text


class TestUnnumberedLayouts:
    def test_site_header_stripped(self):
        text = "Instantie\nHof van Justitie\nZaaknummer\nGR-1\nInhoudsindicatie\nKort.\nUitspraak\nHET HOF"
        assert strip_site_header(text) == "HET HOF"

    def test_plain_numbers_under_beoordeling(self):
        text = ("Het procesverloop\n1. Pleitnota.\nDe beoordeling\n1. Het beroep is tijdig.\n"
                "2. Het Hof oordeelt.\nVervolg van twee.\nDe beslissing in hoger beroep\nHet Hof: wijst af.")
        parts = split_considerations(text)
        assert [n for n, _ in parts] == ["1", "2"]
        assert "Vervolg van twee." in parts[1][1] and "wijst af" not in parts[1][1]

    def test_overwegende_under_ten_aanzien_van_het_recht(self):
        text = ("TEN AANZIEN VAN DE FEITEN:\nOverwegende, dat feiten.\nTEN AANZIEN VAN HET RECHT:\n"
                "Overwegende, dat het beroep tijdig is.\nOverwegende, dat de grief faalt;\n"
                "RECHTDOENDE IN HOGER BEROEP:\nBevestigt.")
        parts = split_considerations(text)
        assert [n for n, _ in parts] == ["1", "2"]
        assert parts[0][1].startswith("Overwegende, dat het beroep") and "Bevestigt" not in parts[1][1]

    def test_no_text_lost_around_considerations(self):
        text = ("Gezien de stukken; artikel 1 Personeelswet.\nTEN AANZIEN VAN HET RECHT:\n"
                "Overwegende, dat het beroep tijdig is.\nOverwegende, dat de grief faalt;\n"
                "RECHTDOENDE IN HOGER BEROEP:\nBevestigt.")
        frs = CaseLawPipeline._build_fragments("caselaw-x", "/id/caselaw/x", text)
        assert [f.fragment_type for f in frs] == [FragmentType.FACTS, FragmentType.CONSIDERATION,
                                                  FragmentType.CONSIDERATION, FragmentType.DECISION]
        assert "Personeelswet" in frs[0].text and frs[-1].text.startswith("RECHTDOENDE")
        assert [f.position for f in frs] == [0, 1, 2, 3]

    def test_dictum_not_inside_last_numbered_consideration(self):
        text = "De beoordeling\n2.1 Eerste.\n2.2 Laatste overweging.\nDe beslissing in hoger beroep\nHet Hof: bevestigt."
        frs = CaseLawPipeline._build_fragments("caselaw-x", "/id/caselaw/x", text)
        assert frs[-2].number == "2.2" and "bevestigt" not in frs[-2].text
        assert frs[-1].fragment_type == FragmentType.DECISION and "bevestigt" in frs[-1].text
