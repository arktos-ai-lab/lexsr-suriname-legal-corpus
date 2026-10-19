"""
Tests for citation_extractor.py's citation-type classification and
fuzzy-title target resolution.

Run with: python -m pytest tests/test_citation_extractor.py -v
"""

from __future__ import annotations

from datetime import date

import pytest

from lexsr.core.models import (
    DocumentStatus, Expression, Fragment, FragmentType, SourceType,
    TemporalExtent, Work,
)
from lexsr.core.resolver import ResolvedReference, ResolutionStatus, ReferenceType
from lexsr.core.store import CanonicalStore
from lexsr.ocr.citation_extractor import CitationExtractor, ExtractedCitation


def _law_fragment(uri="/id/law/arbeidswet-1963/2024-01-15/article/4"):
    return Fragment(
        id="f1", uri=uri, expression_id="e1", expression_uri="/id/law/arbeidswet-1963/2024-01-15",
        work_id="arbeidswet-1963", fragment_type=FragmentType.ARTICLE, number="4", text="",
    )


def _case_fragment(uri="/id/caselaw/sru-hvj-2022-1/consideration/2.1"):
    return Fragment(
        id="f2", uri=uri, expression_id="caselaw-sru-hvj-2022-1",
        expression_uri="/id/caselaw/sru-hvj-2022-1",
        work_id="caselaw-sru-hvj-2022-1", fragment_type=FragmentType.CONSIDERATION,
        number="2.1", text="",
    )


def _resolved_ref(uri):
    return ResolvedReference(
        raw_text="x", ref_type=ReferenceType.LEGISLATION_CITATION,
        status=ResolutionStatus.RESOLVED, uri=uri, confidence=0.9,
        char_start=0, char_end=1,
    )


class TestDetermineCitationType:
    def test_law_to_law(self):
        extractor = CitationExtractor()
        result = extractor._determine_citation_type(
            _law_fragment(), _resolved_ref("/id/law/grondwet-1987")
        )
        assert result == "law_to_law"

    def test_case_to_law_with_caselaw_scheme(self):
        # This is the bug: case-law fragments use /id/caselaw/, not /id/ecli/
        extractor = CitationExtractor()
        result = extractor._determine_citation_type(
            _case_fragment(), _resolved_ref("/id/law/arbeidswet-1963")
        )
        assert result == "case_to_law"

    def test_case_to_case_with_caselaw_scheme(self):
        extractor = CitationExtractor()
        result = extractor._determine_citation_type(
            _case_fragment(), _resolved_ref("/id/caselaw/sru-hvj-2019-3")
        )
        assert result == "case_to_case"

    def test_law_to_case_with_ecli_scheme(self):
        extractor = CitationExtractor()
        result = extractor._determine_citation_type(
            _law_fragment(), _resolved_ref("/id/ecli/ECLI:SR:HVJ:2020:1")
        )
        assert result == "law_to_case"

    def test_unknown_when_neither_scheme_recognized(self):
        extractor = CitationExtractor()
        result = extractor._determine_citation_type(
            _law_fragment(), _resolved_ref("/id/gazette/sb-2020-1")
        )
        assert result == "unknown"


@pytest.fixture
def store_with_grondwet(tmp_path):
    store = CanonicalStore(str(tmp_path / "test.db"))
    # Real corpus works get filename-derived IDs, not the resolver's
    # idealized canonical slug ("grondwet-1987").
    work = Work(
        id="grondwet-met-mvt-4fb9cced", uri="/id/law/grondwet-met-mvt-4fb9cced",
        source_type=SourceType.LEGISLATION, official_id="grondwet-met-mvt-4fb9cced",
        title="Grondwet met MvT", jurisdiction="SR",
    )
    store.save_work(work)
    expr = Expression(
        id="e1", uri="/id/law/grondwet-met-mvt-4fb9cced/2020-01-01",
        work_id=work.id, work_uri=work.uri,
        version_date=date(2020, 1, 1), in_force=TemporalExtent(start=date(2020, 1, 1)),
    )
    store.save_expression(expr)
    frag = Fragment(
        id="f1", uri="/id/law/arbeidswet-1963/2024-01-15/article/4",
        expression_id="e-arb", expression_uri="/id/law/arbeidswet-1963/2024-01-15",
        work_id="arbeidswet-1963", fragment_type=FragmentType.ARTICLE, number="4", text="",
    )
    yield store
    store.close()


class TestToCitationFuzzyFallback:
    def test_falls_back_to_title_match_for_law_uri(self, store_with_grondwet):
        extractor = CitationExtractor(store_with_grondwet)
        # Source fragment must exist in the store for to_citation to proceed.
        arb_frag = Fragment(
            id="f-arb", uri="/id/law/arbeidswet-1963/2024-01-15/article/4",
            expression_id="e-arb", expression_uri="/id/law/arbeidswet-1963/2024-01-15",
            work_id="arbeidswet-1963", fragment_type=FragmentType.ARTICLE, number="4", text="",
        )
        store_with_grondwet.save_work(Work(
            id="arbeidswet-1963", uri="/id/law/arbeidswet-1963",
            source_type=SourceType.LEGISLATION, official_id="arbeidswet-1963",
            title="Arbeidswet 1963", jurisdiction="SR",
        ))
        expr = Expression(
            id="e-arb", uri="/id/law/arbeidswet-1963/2024-01-15", work_id="arbeidswet-1963",
            work_uri="/id/law/arbeidswet-1963", version_date=date(2024, 1, 15),
            in_force=TemporalExtent(start=date(2024, 1, 15)),
        )
        store_with_grondwet.save_expression(expr)
        store_with_grondwet.save_fragment(arb_frag)

        ec = ExtractedCitation(
            source_fragment_uri=arb_frag.uri,
            target_uri="/id/law/grondwet-1987",  # doesn't exist under this ID
            raw_text="Grondwet",
            confidence=0.6,
            position=0,
            citation_type="law_to_law",
        )
        cit = extractor.to_citation(ec)
        assert cit is not None
        assert cit.target_work_id == "grondwet-met-mvt-4fb9cced"

    def test_no_fallback_for_gazette_uri(self, store_with_grondwet):
        # Gazette references aren't Works, so fuzzy title matching them
        # against work titles should never be attempted.
        extractor = CitationExtractor(store_with_grondwet)
        store_with_grondwet.save_work(Work(
            id="arbeidswet-1963", uri="/id/law/arbeidswet-1963",
            source_type=SourceType.LEGISLATION, official_id="arbeidswet-1963",
            title="Arbeidswet 1963", jurisdiction="SR",
        ))
        expr = Expression(
            id="e-arb", uri="/id/law/arbeidswet-1963/2024-01-15", work_id="arbeidswet-1963",
            work_uri="/id/law/arbeidswet-1963", version_date=date(2024, 1, 15),
            in_force=TemporalExtent(start=date(2024, 1, 15)),
        )
        store_with_grondwet.save_expression(expr)
        frag = Fragment(
            id="f-arb", uri="/id/law/arbeidswet-1963/2024-01-15/article/4",
            expression_id="e-arb", expression_uri="/id/law/arbeidswet-1963/2024-01-15",
            work_id="arbeidswet-1963", fragment_type=FragmentType.ARTICLE, number="4", text="",
        )
        store_with_grondwet.save_fragment(frag)

        ec = ExtractedCitation(
            source_fragment_uri=frag.uri,
            target_uri="/id/gazette/sb-2020-1",
            raw_text="S.B. 2020 no. 1",
            confidence=0.8,
            position=0,
            citation_type="unknown",
        )
        cit = extractor.to_citation(ec)
        assert cit is not None
        assert cit.target_work_id is None
