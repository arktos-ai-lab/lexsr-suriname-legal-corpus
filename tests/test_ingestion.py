"""
Tests for the ingestion pipeline (Sprint 4).

Covers: BronzeRecord, BronzeStore, SilverProcessor, IngestionPipeline.

Run with: python -m pytest tests/test_ingestion.py -v
"""

import hashlib
from datetime import date, datetime

import pytest

from lexsr.ingestion.bronze import BronzeRecord, BronzeStore
from lexsr.ingestion.silver import ProcessingResult, SilverProcessor
from lexsr.ingestion.pipeline import IngestionPipeline, IngestionStats
from lexsr.core.store import CanonicalStore


# ===================================================================
# Bronze Layer
# ===================================================================

class TestBronzeRecord:
    def test_auto_checksum(self):
        content = b"<xml>test</xml>"
        rec = BronzeRecord(
            id="r1",
            source_type="legislation",
            source_url="https://example.com/doc",
            content_type="application/xml",
            content=content,
        )
        expected_hash = hashlib.sha256(content).hexdigest()
        assert rec.checksum_sha256 == expected_hash
        assert rec.byte_size == len(content)
        assert rec.retrieved_at != ""

    def test_explicit_checksum(self):
        rec = BronzeRecord(
            id="r2",
            source_type="legislation",
            source_url="https://example.com",
            content_type="text/html",
            content=b"data",
            checksum_sha256="abc123",
        )
        assert rec.checksum_sha256 == "abc123"

    def test_empty_content(self):
        rec = BronzeRecord(
            id="r3",
            source_type="legislation",
            source_url="https://example.com",
            content_type="text/html",
            content=b"",
        )
        assert rec.checksum_sha256 == ""
        assert rec.byte_size == 0


class TestBronzeStore:
    @pytest.fixture
    def store(self, tmp_path):
        s = BronzeStore(str(tmp_path / "bronze_test.db"))
        yield s
        s.close()

    def _make_record(self, id_: str, content: bytes = b"test data",
                     source_type: str = "legislation") -> BronzeRecord:
        return BronzeRecord(
            id=id_,
            source_type=source_type,
            source_url=f"https://example.com/{id_}",
            content_type="application/xml",
            content=content,
        )

    def test_store_and_get(self, store):
        rec = self._make_record("r1")
        assert store.store(rec) is True

        retrieved = store.get("r1")
        assert retrieved is not None
        assert retrieved.id == "r1"
        assert retrieved.content == b"test data"
        assert retrieved.source_type == "legislation"

    def test_duplicate_detection(self, store):
        rec1 = self._make_record("r1", content=b"same content")
        rec2 = self._make_record("r2", content=b"same content")
        assert store.store(rec1) is True
        assert store.store(rec2) is False  # same content hash

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_list_by_source(self, store):
        store.store(self._make_record("r1", b"data1", "legislation"))
        store.store(self._make_record("r2", b"data2", "legislation"))
        store.store(self._make_record("r3", b"data3", "case_law"))

        legislation_records = store.list_by_source("legislation")
        assert len(legislation_records) == 2

        case_law_records = store.list_by_source("case_law")
        assert len(case_law_records) == 1

    def test_get_by_url(self, store):
        store.store(self._make_record("r1", b"data1"))
        results = store.get_by_url("https://example.com/r1")
        assert len(results) == 1

    def test_stats(self, store):
        store.store(self._make_record("r1", b"data1", "legislation"))
        store.store(self._make_record("r2", b"data2", "case_law"))

        stats = store.stats()
        assert stats["total_records"] == 2
        assert "legislation" in stats["by_source_type"]
        assert "case_law" in stats["by_source_type"]

    def test_context_manager(self, tmp_path):
        path = str(tmp_path / "ctx_test.db")
        with BronzeStore(path) as store:
            store.store(self._make_record("r1"))
        # should not raise after close


# ===================================================================
# Silver Processor
# ===================================================================

class TestSilverProcessor:
    processor = SilverProcessor()

    def test_unsupported_source_type(self):
        rec = BronzeRecord(
            id="r1", source_type="unknown_source",
            source_url="https://example.com", content_type="text/plain",
            content=b"test",
        )
        result = self.processor.process(rec)
        assert not result.success
        assert any("Unsupported" in e for e in result.errors)

    def test_legislation_missing_official_id(self):
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com", content_type="application/xml",
            content=b"<xml/>",
            metadata={},
        )
        result = self.processor.process(rec)
        assert not result.success
        assert any("official_id" in e for e in result.errors)

    def test_legislation_basic_processing(self):
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"<legislation/>",
            metadata={
                "official_id": "sr-arbeidswet-1963",
                "title": "Arbeidswet 1963",
                "version_date": "2024-01-15",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert result.work.official_id == "sr-arbeidswet-1963"
        assert result.work.title == "Arbeidswet 1963"
        assert len(result.expressions) == 1
        assert result.expressions[0].version_date == date(2024, 1, 15)

    def test_legislation_work_uri(self):
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"<legislation/>",
            metadata={
                "official_id": "sr-arbeidswet-1963",
                "title": "Arbeidswet 1963",
                "version_date": "2024-01-15",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert "/id/law/" in result.work.uri

    def test_legislation_xml_fragment_extraction(self):
        xml_content = b"""<?xml version="1.0"?>
        <legislation>
            <article number="1">
                <paragraph>First paragraph text.</paragraph>
            </article>
            <article number="2">
                <paragraph>Second paragraph text.</paragraph>
            </article>
        </legislation>"""
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com",
            content_type="application/xml",
            content=xml_content,
            metadata={"official_id": "sr-arbeidswet-1963", "version_date": "2024-01-15"},
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert len(result.fragments) == 2
        assert result.fragments[0].number == "1"
        assert result.fragments[1].number == "2"
        assert "First paragraph" in result.fragments[0].text

    def test_legislation_xml_fragment_extraction_nr_attr(self):
        """Verify that the 'nr' attribute is also accepted for article numbering."""
        xml_content = b"""<?xml version="1.0"?>
        <legislation>
            <article nr="3">
                <paragraph>Article three text.</paragraph>
            </article>
        </legislation>"""
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com",
            content_type="application/xml",
            content=xml_content,
            metadata={"official_id": "sr-test-law", "version_date": "2024-06-01"},
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert len(result.fragments) == 1
        assert result.fragments[0].number == "3"

    def test_legislation_invalid_xml(self):
        rec = BronzeRecord(
            id="r1", source_type="legislation",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"not valid xml at all <><>",
            metadata={"official_id": "sr-arbeidswet-1963", "version_date": "2024-01-15"},
        )
        result = self.processor.process(rec)
        # Should have work and expression but fragment extraction errors
        assert result.work is not None
        assert len(result.errors) >= 1

    def test_case_law_basic(self):
        rec = BronzeRecord(
            id="r1", source_type="case_law",
            source_url="https://example.com/case",
            content_type="text/plain",
            content=b"The court considers the following.",
            metadata={
                "ecli": "ECLI:SR:KG:2020:1234",
                "title": "Kantongerecht Uitspraak",
                "date": "2020-06-15",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert result.work.official_id == "ECLI:SR:KG:2020:1234"
        assert len(result.expressions) == 1

    def test_case_law_missing_ecli(self):
        rec = BronzeRecord(
            id="r1", source_type="case_law",
            source_url="https://example.com",
            content_type="text/plain",
            content=b"text",
            metadata={},
        )
        result = self.processor.process(rec)
        assert not result.success

    def test_gazette_basic(self):
        rec = BronzeRecord(
            id="r1", source_type="gazette",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"<gazette/>",
            metadata={
                "jurisdiction": "sr",
                "year": "2024",
                "number": "123",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert result.work.uri == "/id/gazette/sb-2024-123"
        assert len(result.expressions) == 1

    def test_gazette_missing_year_or_number(self):
        rec = BronzeRecord(
            id="r1", source_type="gazette",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"<gazette/>",
            metadata={"jurisdiction": "sr"},
        )
        result = self.processor.process(rec)
        assert not result.success
        assert any("year" in e or "number" in e for e in result.errors)

    def test_pdf_scan_stub(self):
        rec = BronzeRecord(
            id="r1", source_type="pdf_scan",
            source_url="https://example.com/scan.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.4 fake",
            metadata={},
        )
        result = self.processor.process(rec)
        assert not result.success
        assert any("OCR" in e or "pdf_scan" in e for e in result.errors)

    def test_pdf_digital_stub(self):
        rec = BronzeRecord(
            id="r1", source_type="pdf_digital",
            source_url="https://example.com/doc.pdf",
            content_type="application/pdf",
            content=b"%PDF-1.4 fake",
            metadata={},
        )
        result = self.processor.process(rec)
        assert not result.success
        assert any("pdf_digital" in e or "PDF" in e for e in result.errors)

    def test_xml_source_delegates_to_legislation(self):
        rec = BronzeRecord(
            id="r1", source_type="xml_source",
            source_url="https://example.com",
            content_type="application/xml",
            content=b"<legislation/>",
            metadata={
                "official_id": "sr-test-xml",
                "title": "XML Source Law",
                "version_date": "2024-03-01",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert result.work.official_id == "sr-test-xml"

    def test_html_source_delegates_to_legislation(self):
        rec = BronzeRecord(
            id="r1", source_type="html_source",
            source_url="https://example.com",
            content_type="text/html",
            content=b"<html><body>Law text</body></html>",
            metadata={
                "official_id": "sr-test-html",
                "title": "HTML Source Law",
                "version_date": "2024-04-01",
            },
        )
        result = self.processor.process(rec)
        assert result.work is not None
        assert result.work.official_id == "sr-test-html"

    def test_all_registered_source_types_have_handlers(self):
        """Verify every key in _REGISTRY points to a callable handler."""
        for source_type, handler in SilverProcessor._REGISTRY.items():
            assert callable(handler), f"Handler for {source_type} is not callable"


# ===================================================================
# Ingestion Pipeline
# ===================================================================

class TestIngestionPipeline:
    @pytest.fixture
    def pipeline(self, tmp_path):
        bronze = BronzeStore(str(tmp_path / "bronze.db"))
        canonical = CanonicalStore(str(tmp_path / "canonical.db"))
        pipe = IngestionPipeline(bronze_store=bronze, canonical_store=canonical)
        yield pipe
        bronze.close()
        canonical.close()

    def _make_legislation_record(
        self, official_id: str, content: bytes = b"<legislation/>"
    ) -> BronzeRecord:
        return BronzeRecord(
            id=f"r-{official_id.lower()}",
            source_type="legislation",
            source_url=f"https://example.com/{official_id}",
            content_type="application/xml",
            content=content,
            metadata={
                "official_id": official_id,
                "title": f"Law {official_id}",
                "version_date": "2024-01-15",
            },
        )

    def test_ingest_single_record(self, pipeline):
        rec = self._make_legislation_record("sr-arbeidswet-1963")
        stats = pipeline.ingest_records([rec])
        assert stats.bronze_stored == 1
        assert stats.silver_processed == 1
        assert stats.works_saved == 1
        assert stats.expressions_saved == 1
        assert stats.finished_at is not None

    def test_ingest_multiple_records(self, pipeline):
        records = [
            self._make_legislation_record(
                "sr-arbeidswet-1963", b"<legislation>data1</legislation>"
            ),
            self._make_legislation_record(
                "sr-grondwet-1987", b"<legislation>data2</legislation>"
            ),
        ]
        stats = pipeline.ingest_records(records)
        assert stats.bronze_stored == 2
        assert stats.works_saved == 2
        assert stats.expressions_saved == 2

    def test_ingest_duplicate(self, pipeline):
        rec = self._make_legislation_record("sr-arbeidswet-1963")
        stats1 = pipeline.ingest_records([rec])
        assert stats1.bronze_stored == 1

        stats2 = pipeline.ingest_records([rec])
        assert stats2.bronze_duplicates == 1

    def test_ingest_with_errors(self, pipeline):
        bad_rec = BronzeRecord(
            id="r-bad",
            source_type="legislation",
            source_url="https://example.com/bad",
            content_type="text/plain",
            content=b"not a law",
            metadata={},  # missing official_id
        )
        stats = pipeline.ingest_records([bad_rec])
        assert stats.silver_errors == 1
        assert len(stats.errors) >= 1

    def test_ingest_single_method(self, pipeline):
        rec = self._make_legislation_record("sr-arbeidswet-1963")
        result = pipeline.ingest_single(rec)
        assert result.success
        assert result.work is not None

    def test_stats_summary(self):
        stats = IngestionStats(
            bronze_stored=5,
            bronze_duplicates=2,
            silver_processed=5,
            works_saved=5,
            expressions_saved=5,
            fragments_saved=10,
        )
        s = stats.summary()
        assert s["bronze"]["stored"] == 5
        assert s["store"]["works"] == 5
