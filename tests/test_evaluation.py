"""
Tests for the evaluation framework (Sprint 3).

Covers: KnownAnswer, KnownAnswerDataset, CitationMetrics,
TemporalMetrics, CompletenessMetrics, QualityReport, BenchmarkSuite.

Run with: python -m pytest tests/test_evaluation.py -v
"""

from datetime import date

import pytest

from lexsr.evaluation.dataset import KnownAnswer, KnownAnswerDataset
from lexsr.evaluation.metrics import (
    CitationMetrics,
    CompletenessMetrics,
    QualityReport,
    TemporalMetrics,
)
from lexsr.evaluation.benchmark import BenchmarkCase, BenchmarkResult, BenchmarkSuite
from lexsr.core.resolver import CitationResolver


# ===================================================================
# KnownAnswer & Dataset
# ===================================================================

class TestKnownAnswer:
    def test_basic_construction(self):
        ka = KnownAnswer(
            id="test-1",
            category="citation",
            input_text="artikel 3 BW",
            expected_uris=["/id/law/burgerlijk-wetboek"],
        )
        assert ka.id == "test-1"
        assert ka.category == "citation"
        assert ka.difficulty == "medium"
        assert ka.tags == []

    def test_with_temporal(self):
        ka = KnownAnswer(
            id="temp-1",
            category="temporal",
            input_text="What was article 1 on 2020-01-01?",
            query_date=date(2020, 1, 1),
            expected_uris=["/id/law/grondwet-1987"],
        )
        assert ka.query_date == date(2020, 1, 1)


class TestKnownAnswerDataset:
    def test_empty_dataset(self):
        ds = KnownAnswerDataset("empty")
        assert ds.count == 0
        assert ds.cases() == []
        assert ds.categories() == []

    def test_add_and_retrieve(self):
        ds = KnownAnswerDataset("test")
        ds.add(KnownAnswer(id="c1", category="citation", input_text="text1"))
        ds.add(KnownAnswer(id="t1", category="temporal", input_text="text2"))
        assert ds.count == 2
        assert sorted(ds.categories()) == ["citation", "temporal"]

    def test_filter_by_category(self):
        ds = KnownAnswerDataset("test")
        ds.add(KnownAnswer(id="c1", category="citation", input_text="a"))
        ds.add(KnownAnswer(id="c2", category="citation", input_text="b"))
        ds.add(KnownAnswer(id="t1", category="temporal", input_text="c"))
        assert len(ds.cases("citation")) == 2
        assert len(ds.cases("temporal")) == 1
        assert len(ds.cases("nonexistent")) == 0

    def test_suriname_legal_seed(self):
        """Test the primary suriname_legal_seed() factory method."""
        ds = KnownAnswerDataset.suriname_legal_seed()
        assert ds.name == "suriname_legal_seed"
        assert ds.count >= 9
        assert "citation" in ds.categories()
        assert "temporal" in ds.categories()

    def test_dutch_legal_seed_alias(self):
        """Test that dutch_legal_seed() still works as a backward-compat alias."""
        ds = KnownAnswerDataset.dutch_legal_seed()
        assert ds.name == "suriname_legal_seed"
        assert ds.count >= 9
        assert "citation" in ds.categories()
        assert "temporal" in ds.categories()

    def test_suriname_seed_contains_gazette_cases(self):
        """The seed dataset includes gazette references (S.B. and G.B.)."""
        ds = KnownAnswerDataset.suriname_legal_seed()
        gazette_cases = [c for c in ds.cases() if "gazette" in c.tags]
        assert len(gazette_cases) >= 2

    def test_suriname_seed_expected_uris(self):
        """Spot-check that seed URIs use the new ELI-style identifiers."""
        ds = KnownAnswerDataset.suriname_legal_seed()
        cases_by_id = {c.id: c for c in ds.cases()}

        # cit-leg-001: an input naming an article expects the article, not just the law
        assert cases_by_id["cit-leg-001"].expected_uris == ["/id/law/arbeidswet-1963/article/7"]

        # cit-ecli-001: ECLI with consideration
        assert cases_by_id["cit-ecli-001"].expected_uris == [
            "/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3"
        ]

        # cit-leg-002: Grondwet
        assert cases_by_id["cit-leg-002"].expected_uris == ["/id/law/grondwet-1987/article/1"]

        # cit-multi-001: multi-ref with BW
        assert "/id/law/burgerlijk-wetboek/article/3" in cases_by_id["cit-multi-001"].expected_uris
        assert "/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3" in cases_by_id["cit-multi-001"].expected_uris

        # cit-sb-001: Staatsblad gazette reference (the Staatsblad exists from 1975)
        assert cases_by_id["cit-sb-001"].expected_uris == ["/id/gazette/sb-1987-62"]

        # no invented Surinamese ECLIs; Surinamese decisions use rechtspraak.sr identifiers
        assert not any("ECLI:SR" in c.input_text for c in ds.cases())
        assert cases_by_id["cit-sru-001"].expected_uris == ["/id/caselaw/sru-k3-2016-1"]

        # cit-gb-001: Gouvernementsblad gazette reference
        assert cases_by_id["cit-gb-001"].expected_uris == ["/id/gazette/gb-1936-144"]

    def test_save_and_load(self, tmp_path):
        ds = KnownAnswerDataset("roundtrip")
        ds.add(KnownAnswer(
            id="c1",
            category="citation",
            input_text="artikel 1 Grondwet",
            expected_uris=["/id/law/grondwet-1987"],
            query_date=date(2024, 1, 15),
            source="test",
            difficulty="easy",
            tags=["grondwet"],
            metadata={"note": "test"},
        ))

        path = str(tmp_path / "test_dataset.json")
        ds.save(path)

        loaded = KnownAnswerDataset.load(path)
        assert loaded.name == "roundtrip"
        assert loaded.count == 1
        c = loaded.cases()[0]
        assert c.id == "c1"
        assert c.expected_uris == ["/id/law/grondwet-1987"]
        assert c.query_date == date(2024, 1, 15)
        assert c.tags == ["grondwet"]


# ===================================================================
# Metrics
# ===================================================================

class TestCitationMetrics:
    def test_empty_metrics(self):
        m = CitationMetrics()
        assert m.precision == 0.0
        assert m.recall == 0.0
        assert m.f1 == 0.0
        assert m.resolution_rate == 0.0

    def test_perfect_metrics(self):
        m = CitationMetrics(
            total_references=10,
            resolved_count=10,
            true_positives=10,
            false_positives=0,
            false_negatives=0,
        )
        assert m.precision == 1.0
        assert m.recall == 1.0
        assert m.f1 == 1.0
        assert m.resolution_rate == 1.0

    def test_mixed_metrics(self):
        m = CitationMetrics(
            total_references=10,
            resolved_count=6,
            partial_count=2,
            unresolved_count=2,
            true_positives=5,
            false_positives=1,
            false_negatives=3,
        )
        assert m.resolution_rate == 0.6
        assert abs(m.precision - 5 / 6) < 0.001
        assert abs(m.recall - 5 / 8) < 0.001
        assert m.f1 > 0

    def test_summary(self):
        m = CitationMetrics(total_references=5, resolved_count=3, true_positives=3)
        s = m.summary()
        assert "precision" in s
        assert "recall" in s
        assert "f1" in s
        assert s["resolution_rate"] == 0.6


class TestTemporalMetrics:
    def test_empty(self):
        m = TemporalMetrics()
        assert m.accuracy == 0.0

    def test_perfect(self):
        m = TemporalMetrics(total_queries=5, correct_version=5)
        assert m.accuracy == 1.0

    def test_mixed(self):
        m = TemporalMetrics(total_queries=10, correct_version=7, wrong_version=2, no_result=1)
        assert m.accuracy == 0.7

    def test_summary(self):
        m = TemporalMetrics(total_queries=4, correct_version=3, wrong_version=1)
        s = m.summary()
        assert s["accuracy"] == 0.75
        assert s["total_queries"] == 4


class TestCompletenessMetrics:
    def test_empty(self):
        m = CompletenessMetrics(source_type="legislation")
        assert m.expression_coverage == 0.0
        assert m.fragment_coverage == 0.0

    def test_full_coverage(self):
        m = CompletenessMetrics(
            source_type="legislation",
            total_works=10,
            works_with_expressions=10,
            total_expressions=10,
            expressions_with_fragments=10,
        )
        assert m.expression_coverage == 1.0
        assert m.fragment_coverage == 1.0

    def test_summary(self):
        m = CompletenessMetrics(
            source_type="case_law",
            total_works=100,
            works_with_expressions=80,
            total_expressions=80,
            expressions_with_fragments=60,
        )
        s = m.summary()
        assert s["source_type"] == "case_law"
        assert s["expression_coverage"] == 0.8
        assert s["fragment_coverage"] == 0.75


class TestQualityReport:
    def test_empty_report(self):
        r = QualityReport()
        s = r.summary()
        assert "generated_at" in s

    def test_full_report(self):
        r = QualityReport(
            citation_metrics=CitationMetrics(total_references=5, resolved_count=4, true_positives=4),
            temporal_metrics=TemporalMetrics(total_queries=3, correct_version=3),
            completeness=[CompletenessMetrics(source_type="legislation", total_works=10)],
            integrity_summary={"errors": 0, "warnings": 1},
        )
        s = r.summary()
        assert "citations" in s
        assert "temporal" in s
        assert "completeness" in s
        assert "integrity" in s


# ===================================================================
# Benchmark Suite
# ===================================================================

class TestBenchmarkResult:
    def test_empty_result(self):
        r = BenchmarkResult(suite_name="test")
        assert r.pass_rate == 0.0

    def test_pass_rate(self):
        r = BenchmarkResult(suite_name="test", total_cases=10, passed=7, failed=3)
        assert r.pass_rate == 0.7

    def test_summary(self):
        r = BenchmarkResult(suite_name="test", total_cases=5, passed=3, failed=2)
        s = r.summary()
        assert s["suite"] == "test"
        assert s["pass_rate"] == 0.6

    def test_failed_cases(self):
        r = BenchmarkResult(suite_name="test", total_cases=2, passed=1, failed=1)
        r.cases = [
            BenchmarkCase(case_id="c1", category="citation", passed=True,
                          expected_uris=[], actual_uris=[]),
            BenchmarkCase(case_id="c2", category="citation", passed=False,
                          expected_uris=["/id/law/burgerlijk-wetboek"], actual_uris=[]),
        ]
        failed = r.failed_cases()
        assert len(failed) == 1
        assert failed[0].case_id == "c2"


class TestBenchmarkSuite:
    def test_run_without_resolver(self):
        suite = BenchmarkSuite()
        ds = KnownAnswerDataset("test")
        ds.add(KnownAnswer(
            id="c1", category="citation",
            input_text="artikel 3 BW",
            expected_uris=["/id/law/burgerlijk-wetboek"],
        ))
        result = suite.run(ds)
        assert result.total_cases == 1
        assert result.failed == 1  # no resolver configured

    def test_run_with_resolver(self):
        resolver = CitationResolver(default_date=date(2024, 1, 15))
        suite = BenchmarkSuite(resolver=resolver)
        ds = KnownAnswerDataset("basic")
        ds.add(KnownAnswer(
            id="c1", category="citation",
            input_text="artikel 3 BW",
            expected_uris=["/id/law/burgerlijk-wetboek"],
        ))
        result = suite.run(ds)
        assert result.total_cases == 1
        # Should resolve the BW reference
        assert result.passed >= 1

    def test_run_seed_dataset(self):
        resolver = CitationResolver(default_date=date(2024, 1, 15))
        suite = BenchmarkSuite(resolver=resolver)
        ds = KnownAnswerDataset.suriname_legal_seed()
        result = suite.run(ds)
        assert result.total_cases == ds.count
        assert result.pass_rate > 0.0  # at least some should pass
        assert result.quality_report is not None

    def test_run_seed_dataset_via_alias(self):
        """Ensure the backward-compat alias also works end-to-end."""
        resolver = CitationResolver(default_date=date(2024, 1, 15))
        suite = BenchmarkSuite(resolver=resolver)
        ds = KnownAnswerDataset.dutch_legal_seed()
        result = suite.run(ds)
        assert result.total_cases == ds.count
        assert result.pass_rate > 0.0

    def test_unknown_category(self):
        suite = BenchmarkSuite()
        ds = KnownAnswerDataset("test")
        ds.add(KnownAnswer(id="u1", category="unknown_cat", input_text="x"))
        result = suite.run(ds)
        assert result.failed == 1


def test_article_must_match_not_just_law():
    """A resolved law without the named article no longer passes (no prefix matching)."""
    from lexsr.evaluation.benchmark import BenchmarkSuite
    from lexsr.evaluation.dataset import KnownAnswer, KnownAnswerDataset

    class LawOnly:
        def resolve_text(self, text, version_date=None):
            class R:
                uri = "/id/law/arbeidswet-1963"
                work_uri = "/id/law/arbeidswet-1963"
            return [R()]

    ds = KnownAnswerDataset("t")
    ds.add(KnownAnswer(id="a", category="citation", input_text="artikel 7 Arbeidswet 1963",
                       expected_uris=["/id/law/arbeidswet-1963/article/7"]))
    assert not BenchmarkSuite(resolver=LawOnly()).run(ds).cases[0].passed
