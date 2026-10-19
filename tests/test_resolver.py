"""
Tests for the citation resolver and link integrity checker.

Run with: python -m pytest tests/test_resolver.py -v
"""

from datetime import date

import pytest

from lexsr.core.resolver import (
    CitationResolver,
    ParsedReference,
    ReferenceParser,
    ReferenceType,
    ResolutionStatus,
    ResolvedReference,
)
from lexsr.core.integrity import (
    IntegrityReport,
    LinkIntegrityChecker,
)
from lexsr.core.store import CanonicalStore
from lexsr.core.models import (
    DocumentStatus,
    Expression,
    Fragment,
    FragmentType,
    SourceType,
    TemporalExtent,
    Work,
)
from lexsr.core.identifiers import law_fragment_uri, ecli_fragment_uri


# ===================================================================
# Reference Parser
# ===================================================================

class TestReferenceParserECLI:
    parser = ReferenceParser()

    def test_basic_ecli(self):
        text = "Zie ECLI:NL:HR:2020:1234 voor meer informatie."
        refs = self.parser.extract_all(text)
        assert len(refs) >= 1
        ecli_refs = [r for r in refs if r.ref_type == ReferenceType.ECLI]
        assert len(ecli_refs) == 1
        assert ecli_refs[0].ecli == "ECLI:NL:HR:2020:1234"
        assert ecli_refs[0].confidence >= 0.90

    def test_ecli_with_consideration(self):
        text = "ECLI:NL:HR:2020:1234, r.o. 4.3, bepaalt dat..."
        refs = self.parser.extract_all(text)
        ecli_refs = [r for r in refs if r.ref_type == ReferenceType.ECLI]
        assert len(ecli_refs) == 1
        assert ecli_refs[0].consideration == "4.3"

    def test_ecli_with_consideration_no_dots(self):
        text = "ECLI:NL:RBAMS:2023:3197, ro 3.1 overweegt de rechtbank"
        refs = self.parser.extract_all(text)
        ecli_refs = [r for r in refs if r.ref_type == ReferenceType.ECLI]
        assert len(ecli_refs) == 1
        assert ecli_refs[0].consideration == "3.1"

    def test_multiple_ecli(self):
        text = (
            "In ECLI:NL:HR:2020:1234 en ECLI:NL:GHDHA:2019:567 "
            "is overwogen dat..."
        )
        refs = self.parser.extract_all(text)
        ecli_refs = [r for r in refs if r.ref_type == ReferenceType.ECLI]
        assert len(ecli_refs) == 2


class TestReferenceParserLegislation:
    parser = ReferenceParser()

    def test_artikel_bw(self):
        text = "Op grond van artikel 6:162 BW is er sprake van onrechtmatige daad."
        refs = self.parser.extract_all(text)
        leg = [r for r in refs if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        assert leg[0].article == "6:162"
        assert leg[0].law_id is not None

    def test_art_lid_sub(self):
        text = "art. 3:2 lid 1 sub a BW vereist een wilsverklaring."
        refs = self.parser.extract_all(text)
        leg = [r for r in refs if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        r = leg[0]
        assert r.article == "3:2"
        assert r.lid == "1"

    def test_artikel_grondwet(self):
        text = "Op basis van artikel 1 Grondwet worden allen gelijk behandeld."
        refs = self.parser.extract_all(text)
        leg = [r for r in refs if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        assert leg[0].law_id == "grondwet-1987"

    def test_unknown_law_lower_confidence(self):
        text = "artikel 5 Wet bijzondere opsporingsbevoegdheden"
        refs = self.parser.extract_all(text)
        leg = [r for r in refs if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        if leg:
            assert leg[0].confidence <= 0.60

    def test_strafrecht(self):
        text = "artikel 287 Sr stelt doodslag strafbaar."
        refs = self.parser.extract_all(text)
        leg = [r for r in refs if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        assert leg[0].law_id == "wetboek-van-strafrecht"


class TestReferenceParserEU:
    parser = ReferenceParser()

    def test_eu_regulation(self):
        text = "Verordening (EU) 2016/679 (de AVG) beschermt persoonsgegevens."
        refs = self.parser.extract_all(text)
        eu = [r for r in refs if r.ref_type == ReferenceType.EU_REGULATION]
        assert len(eu) >= 1
        assert eu[0].celex_id is not None

    def test_eu_directive(self):
        text = "Richtlijn 2014/65/EU (MiFID II) reguleert financiele markten."
        refs = self.parser.extract_all(text)
        eu = [r for r in refs if r.ref_type == ReferenceType.EU_DIRECTIVE]
        assert len(eu) >= 1

    def test_gazette(self):
        text = "Gepubliceerd in S.B. 2024 no. 123."
        refs = self.parser.extract_all(text)
        gaz = [r for r in refs if r.ref_type == ReferenceType.GAZETTE]
        assert len(gaz) == 1
        assert gaz[0].gazette_year == "2024"
        assert gaz[0].gazette_number == "123"


class TestReferenceParserGazette:
    parser = ReferenceParser()

    def test_staatsblad_sb(self):
        text = "Zie S.B. 2024, 456 voor de regeling."
        refs = self.parser.extract_all(text)
        gaz = [r for r in refs if r.ref_type == ReferenceType.GAZETTE]
        assert len(gaz) == 1
        assert gaz[0].gazette_year == "2024"
        assert gaz[0].gazette_number == "456"

    def test_gouvernementsblad(self):
        text = "Gepubliceerd in G.B. 1936 no. 144."
        refs = self.parser.extract_all(text)
        gaz = [r for r in refs if r.ref_type == ReferenceType.GAZETTE]
        assert len(gaz) == 1
        assert gaz[0].gazette_year == "1936"
        assert gaz[0].gazette_number == "144"


# ===================================================================
# Citation Resolver
# ===================================================================

class TestCitationResolver:
    @pytest.fixture
    def resolver(self):
        return CitationResolver(default_date=date(2024, 1, 15))

    def test_resolve_ecli(self, resolver):
        result = resolver.resolve_reference("ECLI:NL:HR:2020:1234")
        assert result.status == ResolutionStatus.RESOLVED
        assert result.uri == "/id/ecli/ECLI:NL:HR:2020:1234"
        assert result.confidence >= 0.90

    def test_resolve_ecli_with_consideration(self, resolver):
        result = resolver.resolve_reference(
            "ECLI:NL:HR:2020:1234, r.o. 4.3"
        )
        assert result.status == ResolutionStatus.RESOLVED
        assert result.uri == "/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3"

    def test_resolve_artikel_bw(self, resolver):
        results = resolver.resolve_text(
            "Op grond van artikel 6:162 BW is sprake van onrechtmatige daad."
        )
        leg = [r for r in results if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        assert leg[0].status == ResolutionStatus.RESOLVED
        assert "/article/6:162" in leg[0].uri

    def test_resolve_artikel_paragraph(self, resolver):
        results = resolver.resolve_text("artikel 1 lid 1 Grondwet")
        leg = [r for r in results if r.ref_type == ReferenceType.LEGISLATION_CITATION]
        assert len(leg) >= 1
        r = leg[0]
        assert r.status == ResolutionStatus.RESOLVED
        assert "/id/law/grondwet-1987/" in r.uri
        assert "/article/1" in r.uri
        assert "/paragraph/1" in r.uri

    def test_resolve_eu_regulation(self, resolver):
        results = resolver.resolve_text("Verordening (EU) 2016/679")
        eu = [r for r in results if r.ref_type == ReferenceType.EU_REGULATION]
        assert len(eu) >= 1
        assert eu[0].status == ResolutionStatus.RESOLVED
        assert "/id/celex/" in eu[0].uri

    def test_unknown_law_partial(self, resolver):
        result = resolver.resolve_reference(
            "artikel 5 Wet bijzondere onbekende wet"
        )
        if result.status == ResolutionStatus.PARTIAL:
            assert result.confidence < 0.50

    def test_resolve_text_complex(self, resolver):
        text = (
            "Zoals bepaald in artikel 6:162 BW (zie HR 15 juni 2020, "
            "ECLI:NL:HR:2020:1234, r.o. 4.3) en artikel 287 Sr."
        )
        results = resolver.resolve_text(text)
        resolved = [r for r in results if r.status == ResolutionStatus.RESOLVED]
        assert len(resolved) >= 2

    def test_resolve_gazette(self, resolver):
        result = resolver.resolve_reference("S.B. 2024, 123")
        assert result.ref_type == ReferenceType.GAZETTE
        assert result.status == ResolutionStatus.RESOLVED
        assert result.uri == "/id/gazette/sb-2024-123"

    def test_source_document_tracking(self, resolver):
        results = resolver.resolve_text(
            "ECLI:NL:HR:2020:1234",
            source_document_id="doc-123",
        )
        assert results[0].source_document_id == "doc-123"


class TestCitationResolverWithUnresolvedQueue:
    def test_unresolved_queue(self, tmp_path):
        db_path = str(tmp_path / "unresolved.db")
        resolver = CitationResolver(
            default_date=date(2024, 1, 15),
            unresolved_db_path=db_path,
        )

        resolver.resolve_text("some reference that cannot be parsed at all")

        stats = resolver.get_unresolved_stats()
        assert isinstance(stats, dict)
        assert "total_unresolved" in stats

        resolver.close()


# ===================================================================
# Link Integrity Checker
# ===================================================================

class TestLinkIntegrityChecker:
    @pytest.fixture
    def store(self, tmp_path):
        s = CanonicalStore(str(tmp_path / "integrity_test.db"))
        yield s
        s.close()

    def test_clean_store(self, store):
        checker = LinkIntegrityChecker(store)
        report = checker.run_full_check()
        assert report.is_clean

    def test_detects_orphan_fragment(self, store):
        work = Work(
            id="w-test", uri="/id/law/wetboek-van-strafrecht",
            source_type=SourceType.LEGISLATION, official_id="wetboek-van-strafrecht",
            title="Test",
        )
        store.save_work(work)

        frag = Fragment(
            id="f-orphan",
            uri="/id/law/wetboek-van-strafrecht/2024-01-15/article/1",
            expression_id="e-nonexistent",
            expression_uri="/id/law/wetboek-van-strafrecht/2024-01-15",
            work_id=work.id,
            fragment_type=FragmentType.ARTICLE,
            number="1",
        )
        store._conn.execute("PRAGMA foreign_keys=OFF")
        store.save_fragment(frag)
        store._conn.execute("PRAGMA foreign_keys=ON")

        checker = LinkIntegrityChecker(store)
        report = checker.run_full_check()
        orphans = [i for i in report.issues if i.issue_type == "orphan_fragment"]
        assert len(orphans) == 1

    def test_detects_empty_fragment(self, store):
        work = Work(
            id="w-test", uri="/id/law/wetboek-van-strafrecht",
            source_type=SourceType.LEGISLATION, official_id="wetboek-van-strafrecht",
            title="Test",
        )
        store.save_work(work)
        expr = Expression(
            id="e-test", uri="/id/law/wetboek-van-strafrecht/2024-01-15",
            work_id=work.id, work_uri=work.uri,
            version_date=date(2024, 1, 15),
        )
        store.save_expression(expr)
        frag = Fragment(
            id="f-empty",
            uri="/id/law/wetboek-van-strafrecht/2024-01-15/article/1",
            expression_id=expr.id, expression_uri=expr.uri,
            work_id=work.id,
            fragment_type=FragmentType.ARTICLE,
            number="1",
            text="",
        )
        store.save_fragment(frag)

        checker = LinkIntegrityChecker(store)
        report = checker.run_full_check()
        empty = [i for i in report.issues if i.issue_type == "empty_fragment"]
        assert len(empty) == 1
        assert empty[0].severity == "info"

    def test_report_summary(self, store):
        checker = LinkIntegrityChecker(store)
        report = checker.run_full_check()
        summary = report.summary()
        assert "totals" in summary
        assert "issues" in summary
        assert summary["clean"] is True

    def test_check_resolved_references_broken_target(self, store):
        checker = LinkIntegrityChecker(store)

        fake_ref = ResolvedReference(
            raw_text="artikel 99 BW",
            ref_type=ReferenceType.LEGISLATION_CITATION,
            status=ResolutionStatus.RESOLVED,
            uri="/id/law/burgerlijk-wetboek/2024-01-15/article/99",
            confidence=0.85,
        )
        issues = checker.check_resolved_references([fake_ref])
        broken = [i for i in issues if i.issue_type == "broken_target"]
        assert len(broken) == 1


# ===================================================================
# Integration: Resolver + Store + Integrity
# ===================================================================

class TestResolverStoreIntegration:
    def test_resolve_then_verify(self, tmp_path):
        store = CanonicalStore(str(tmp_path / "integration.db"))

        work = Work(
            id="w-bw", uri="/id/law/burgerlijk-wetboek",
            source_type=SourceType.LEGISLATION, official_id="burgerlijk-wetboek",
            title="Burgerlijk Wetboek",
        )
        store.save_work(work)

        expr = Expression(
            id="e-bw-2024", uri="/id/law/burgerlijk-wetboek/2024-01-15",
            work_id=work.id, work_uri=work.uri,
            version_date=date(2024, 1, 15),
        )
        store.save_expression(expr)

        frag = Fragment(
            id="f-bw-art6162",
            uri=law_fragment_uri("burgerlijk-wetboek", date(2024, 1, 15), "6:162"),
            expression_id=expr.id, expression_uri=expr.uri,
            work_id=work.id,
            fragment_type=FragmentType.ARTICLE,
            number="6:162",
            text="Hij die jegens een ander een onrechtmatige daad pleegt...",
        )
        store.save_fragment(frag)

        resolver = CitationResolver(store=store, default_date=date(2024, 1, 15))
        results = resolver.resolve_text("artikel 6:162 BW")
        resolved = [r for r in results if r.status == ResolutionStatus.RESOLVED]
        assert len(resolved) >= 1

        checker = LinkIntegrityChecker(store)
        issues = checker.check_resolved_references(resolved, store)
        broken = [i for i in issues if i.issue_type == "broken_target"]
        assert len(broken) == 0

        report = checker.run_full_check()
        assert report.total_works == 1
        assert report.total_fragments == 1

        store.close()


class TestGazetteKind:
    @pytest.fixture
    def resolver(self):
        return CitationResolver(default_date=date(2024, 1, 15))

    def test_staatsblad_and_gouvernementsblad_get_different_uris(self, resolver):
        sb = resolver.resolve_reference("Staatsblad 1987 no. 62")
        gb = resolver.resolve_reference("G.B. 1963 no. 163")
        assert sb.uri == "/id/gazette/sb-1987-62"
        assert gb.uri == "/id/gazette/gb-1963-163"


class TestRechtspraakSrIdentifiers:
    @pytest.fixture
    def resolver(self):
        return CitationResolver(default_date=date(2024, 1, 15))

    def test_sru_with_consideration(self, resolver):
        refs = resolver.resolve_text("Zie SRU-HvJ-2018-4, r.o. 12.")
        assert any(r.uri == "/id/caselaw/sru-hvj-2018-4/consideration/12" for r in refs)
        assert any(r.work_uri == "/id/caselaw/sru-hvj-2018-4" for r in refs)
