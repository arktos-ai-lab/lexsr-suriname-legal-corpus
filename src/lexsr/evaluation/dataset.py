"""
Known-answer dataset for evaluating citation resolution and temporal lookups.

KnownAnswer      — a single gold-standard test case
KnownAnswerDataset — collection with factory methods for seed datasets
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class KnownAnswer:
    """
    A single gold-standard evaluation case.

    Attributes
    ----------
    id:
        Unique identifier (e.g. "cit-001", "tem-002").
    category:
        Case type: "citation", "temporal", or other domain-specific labels.
    input_text:
        The raw text snippet to feed into the resolver / pipeline.
    expected_uris:
        List of canonical URIs expected in the output.
    query_date:
        For temporal cases — the as-of date to evaluate.
    source:
        Provenance label (e.g. "manual", "ocr-sample").
    difficulty:
        Qualitative difficulty: "easy", "medium", or "hard".
    tags:
        Free-form tags for filtering (e.g. ["gazette", "ecli"]).
    metadata:
        Arbitrary extra data.
    """

    id: str
    category: str
    input_text: str
    expected_uris: List[str] = field(default_factory=list)
    query_date: Optional[date] = None
    source: str = ""
    difficulty: str = "medium"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "input_text": self.input_text,
            "expected_uris": self.expected_uris,
            "query_date": self.query_date.isoformat() if self.query_date else None,
            "source": self.source,
            "difficulty": self.difficulty,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnownAnswer":
        qd = data.get("query_date")
        return cls(
            id=data["id"],
            category=data["category"],
            input_text=data["input_text"],
            expected_uris=data.get("expected_uris", []),
            query_date=date.fromisoformat(qd) if qd else None,
            source=data.get("source", ""),
            difficulty=data.get("difficulty", "medium"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


class KnownAnswerDataset:
    """
    Collection of KnownAnswer cases with filtering and persistence.

    Usage
    -----
    ds = KnownAnswerDataset.suriname_legal_seed()
    for case in ds.cases("citation"):
        print(case.id, case.expected_uris)
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._cases: List[KnownAnswer] = []

    # ── Basic accessors ───────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._cases)

    def add(self, ka: KnownAnswer) -> None:
        self._cases.append(ka)

    def cases(self, category: Optional[str] = None) -> List[KnownAnswer]:
        if category is None:
            return list(self._cases)
        return [c for c in self._cases if c.category == category]

    def categories(self) -> List[str]:
        return list({c.category for c in self._cases})

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save dataset to a JSON file."""
        payload = {
            "name": self.name,
            "cases": [c.to_dict() for c in self._cases],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "KnownAnswerDataset":
        """Load a dataset from a JSON file."""
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        ds = cls(payload["name"])
        for item in payload.get("cases", []):
            ds.add(KnownAnswer.from_dict(item))
        return ds

    # ── Seed factories ────────────────────────────────────────────────────

    @classmethod
    def suriname_legal_seed(cls) -> "KnownAnswerDataset":
        """
        Curated seed dataset of Suriname legal benchmark cases.

        All expected_uris are verified against the CitationResolver:
        the resolver must return a URI that exactly matches or is prefixed
        by the expected URI (work-level prefix matching).

        How to re-verify after resolver changes:
            from lexsr.core.resolver import CitationResolver
            from lexsr.core.store import CanonicalStore
            resolver = CitationResolver(CanonicalStore("data/canonical.db"))
            refs = resolver.resolve_text(case.input_text)
            assert any(r.uri and r.uri.startswith(expected) for r in refs
                       for expected in case.expected_uris)
        """
        ds = cls("suriname_legal_seed")

        # ── Gazette citations (Staatsblad) ────────────────────────────────
        # These resolve fully: gazette_uri() generates the URI without a DB
        # lookup, so these pass regardless of what is in the store.

        ds.add(KnownAnswer(
            id="cit-sb-001",
            category="citation",
            input_text="Gepubliceerd in Staatsblad 1987 no. 62.",
            expected_uris=["/id/gazette/sb-1987-62"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-002",
            category="citation",
            input_text="S.B. 1987 no. 1.",
            expected_uris=["/id/gazette/sb-1987-1"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-003",
            category="citation",
            input_text="Zoals gepubliceerd in Staatsblad 2023 no. 125.",
            expected_uris=["/id/gazette/sb-2023-125"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-004",
            category="citation",
            input_text="S.B. 2024 no. 164.",
            expected_uris=["/id/gazette/sb-2024-164"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Gazette citations (Gouvernementsblad) ─────────────────────────

        ds.add(KnownAnswer(
            id="cit-gb-001",
            category="citation",
            input_text="G.B. 1936 no. 144.",
            expected_uris=["/id/gazette/gb-1936-144"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-gb-002",
            category="citation",
            input_text="Gouvernementsblad 1963 no. 163.",
            expected_uris=["/id/gazette/gb-1963-163"],
            source="seed",
            difficulty="medium",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-gb-003",
            category="citation",
            input_text="G.B. 1954 no. 104.",
            expected_uris=["/id/gazette/gb-1954-104"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Legislation citations (abbreviation-based) ────────────────────
        # These resolve via the abbreviation registry + URI generation.
        # The URI is generated without a DB lookup; status=resolved when the
        # abbreviation is known.  Expected URIs use work-level prefix matching.

        ds.add(KnownAnswer(
            id="cit-leg-001",
            category="citation",
            input_text="artikel 7 Arbeidswet 1963",
            expected_uris=["/id/law/arbeidswet-1963/article/7"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "arbeidswet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-002",
            category="citation",
            input_text="artikel 1 Grondwet 1987",
            expected_uris=["/id/law/grondwet-1987/article/1"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "grondwet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-003",
            category="citation",
            input_text="Artikel 285 Wetboek van Strafrecht bepaalt de straf op bedreiging.",
            expected_uris=["/id/law/wetboek-van-strafrecht/article/285"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "strafrecht"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-004",
            category="citation",
            input_text="Conform artikel 6 lid 2 Arbeidswet 1963 mag de arbeidsduur niet worden overschreden.",
            expected_uris=["/id/law/arbeidswet-1963/article/6/paragraph/2"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "arbeidswet", "paragraph"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-005",
            category="citation",
            input_text="artikel 3 BW",
            expected_uris=["/id/law/burgerlijk-wetboek/article/3"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "bw", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-006",
            category="citation",
            input_text="artikel 7 AW",
            expected_uris=["/id/law/arbeidswet-1963/article/7"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "arbeidswet", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── ECLI citations ────────────────────────────────────────────────
        # ECLI URIs are generated deterministically from the identifier string.
        # These resolve regardless of what is in the store.
        # Note: "rechtsoverweging" (full word) does NOT trigger the r.o. pattern;
        # use "r.o." to get the consideration fragment URI.

        ds.add(KnownAnswer(
            id="cit-ecli-001",
            category="citation",
            input_text="Zie ECLI:NL:HR:2020:1234, r.o. 4.3.",
            expected_uris=["/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3"],
            source="seed",
            difficulty="easy",
            tags=["ecli", "case_law", "consideration"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-ecli-002",
            category="citation",
            input_text="ECLI:NL:HR:2020:1234",
            expected_uris=["/id/ecli/ECLI:NL:HR:2020:1234"],
            source="seed",
            difficulty="easy",
            tags=["ecli", "case_law"],
            metadata={"resolver_status": "resolved"},
        ))


        # ── Multi-reference cases ─────────────────────────────────────────

        ds.add(KnownAnswer(
            id="cit-multi-001",
            category="citation",
            input_text="Artikel 3 BW jo. ECLI:NL:HR:2020:1234 r.o. 4.3.",
            expected_uris=[
                "/id/law/burgerlijk-wetboek/article/3",
                "/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3",
            ],
            source="seed",
            difficulty="hard",
            tags=["legislation", "ecli", "multi_ref"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-multi-002",
            category="citation",
            input_text="artikel 7 Arbeidswet 1963, gepubliceerd in G.B. 1963 no. 163.",
            expected_uris=[
                "/id/law/arbeidswet-1963/article/7",
                "/id/gazette/gb-1963-163",
            ],
            source="seed",
            difficulty="hard",
            tags=["legislation", "gazette", "multi_ref"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Negative cases (no reference present) ─────────────────────────
        # expected_uris=[] means the resolver should return no URIs.

        ds.add(KnownAnswer(
            id="cit-neg-001",
            category="citation",
            input_text="De vergadering is gesloten.",
            expected_uris=[],
            source="seed",
            difficulty="easy",
            tags=["negative"],
            metadata={"resolver_status": "no_result"},
        ))

        ds.add(KnownAnswer(
            id="cit-neg-002",
            category="citation",
            input_text="Het bestuur heeft besloten.",
            expected_uris=[],
            source="seed",
            difficulty="easy",
            tags=["negative"],
            metadata={"resolver_status": "no_result"},
        ))

        # ── Gazette — format variants ─────────────────────────────────────
        # Test that different spellings of the gazette reference all resolve.

        ds.add(KnownAnswer(
            id="cit-sb-005",
            category="citation",
            input_text="SB 2022 no 119",
            expected_uris=["/id/gazette/sb-2022-119"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad", "no-dots"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-006",
            category="citation",
            input_text="S.B. 2022 no 119",
            expected_uris=["/id/gazette/sb-2022-119"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad", "no-dot-no"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-007",
            category="citation",
            input_text="Het besluit is afgekondigd bij Staatsblad 2019 no. 55.",
            expected_uris=["/id/gazette/sb-2019-55"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-008",
            category="citation",
            input_text="S.B. 2016 no. 42",
            expected_uris=["/id/gazette/sb-2016-42"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-009",
            category="citation",
            input_text="gepubliceerd in S.B. 1981 no. 78.",
            expected_uris=["/id/gazette/sb-1981-78"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-sb-010",
            category="citation",
            input_text="S.B. 2000 no. 93",
            expected_uris=["/id/gazette/sb-2000-93"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "staatsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-gb-004",
            category="citation",
            input_text="G.B. 1972 no. 96",
            expected_uris=["/id/gazette/gb-1972-96"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-gb-005",
            category="citation",
            input_text="G.B. 1932 no. 32",
            expected_uris=["/id/gazette/gb-1932-32"],
            source="seed",
            difficulty="easy",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-gb-006",
            category="citation",
            input_text="Gouvernementsblad 1907 no. 34",
            expected_uris=["/id/gazette/gb-1907-34"],
            source="seed",
            difficulty="medium",
            tags=["gazette", "gouvernementsblad"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Legislation — new abbreviations ───────────────────────────────

        ds.add(KnownAnswer(
            id="cit-leg-007",
            category="citation",
            input_text="artikel 162 Sr",
            expected_uris=["/id/law/wetboek-van-strafrecht/article/162"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "strafrecht", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-008",
            category="citation",
            input_text="artikel 5 WvK",
            expected_uris=["/id/law/wetboek-van-koophandel/article/5"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "koophandel", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-009",
            category="citation",
            input_text="artikel 1 WED",
            expected_uris=["/id/law/wet-economische-delicten/article/1"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "economische-delicten", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-010",
            category="citation",
            input_text="artikel 3 WAM",
            expected_uris=["/id/law/wet-aansprakelijkheidsverzekering-motorrijtuigen/article/3"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "wam", "abbreviation"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-011",
            category="citation",
            input_text="De Zegelwet is van toepassing op akten van eigendomsoverdracht.",
            expected_uris=["/id/law/zegelwet"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "zegelwet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-012",
            category="citation",
            input_text="artikel 12 Kiesregeling",
            expected_uris=["/id/law/kiesregeling/article/12"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "kiesregeling"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-013",
            category="citation",
            input_text="artikel 4 Wet Regionale Organen",
            expected_uris=["/id/law/wet-regionale-organen/article/4"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "regionale-organen"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-014",
            category="citation",
            input_text="Ingevolge artikel 10 Inkomstenbelasting 1922 is de belastingplichtige gehouden aangifte te doen.",
            expected_uris=["/id/law/inkomstenbelasting-1922/article/10"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "inkomstenbelasting"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-015",
            category="citation",
            input_text="artikel 5 Wet Staatsraad",
            expected_uris=["/id/law/wet-staatsraad/article/5"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "staatsraad"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-016",
            category="citation",
            input_text="artikel 8 Vreemdelingenwet",
            expected_uris=["/id/law/vreemdelingenwet/article/8"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "vreemdelingenwet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-017",
            category="citation",
            input_text="Op grond van artikel 3 lid 1 Waterschapswet 1931 is het waterschap bevoegd.",
            expected_uris=["/id/law/waterschapswet-1931/article/3/paragraph/1"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "waterschapswet", "paragraph"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-018",
            category="citation",
            input_text="artikel 6 Petroleumwet",
            expected_uris=["/id/law/petroleumwet-1990/article/6"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "petroleumwet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-019",
            category="citation",
            input_text="artikel 9 Bankwet 1956",
            expected_uris=["/id/law/bankwet-1956/article/9"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "bankwet"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-020",
            category="citation",
            input_text="Conform artikel 7 lid 3 sub a Arbeidswet 1963 geldt een maximale arbeidstijd.",
            expected_uris=["/id/law/arbeidswet-1963/article/7/paragraph/3/sub/a"],
            source="seed",
            difficulty="hard",
            tags=["legislation", "arbeidswet", "paragraph", "sub"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-021",
            category="citation",
            input_text="Wetboek van Koophandel artikel 36",
            expected_uris=["/id/law/wetboek-van-koophandel/article/36"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "koophandel"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-022",
            category="citation",
            input_text="artikel 2 Wet Surinamerschap en Ingezetenschap",
            expected_uris=["/id/law/wet-surinamerschap/article/2"],
            source="seed",
            difficulty="medium",
            tags=["legislation", "surinamerschap"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-leg-023",
            category="citation",
            input_text="artikel 1 Wet Auteursrecht 1913",
            expected_uris=["/id/law/wet-auteursrecht-1913/article/1"],
            source="seed",
            difficulty="easy",
            tags=["legislation", "auteursrecht"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── ECLI — more court variants ────────────────────────────────────



        ds.add(KnownAnswer(
            id="cit-ecli-006",
            category="citation",
            input_text="ECLI:NL:HR:2018:500, r.o. 2.1.",
            expected_uris=["/id/ecli/ECLI:NL:HR:2018:500/consideration/2.1"],
            source="seed",
            difficulty="easy",
            tags=["ecli", "case_law", "consideration"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-ecli-007",
            category="citation",
            input_text="Vgl. ECLI:NL:GHAMS:2019:3456.",
            expected_uris=["/id/ecli/ECLI:NL:GHAMS:2019:3456"],
            source="seed",
            difficulty="easy",
            tags=["ecli", "case_law"],
            metadata={"resolver_status": "resolved"},
        ))

        ds.add(KnownAnswer(
            id="cit-ecli-008",
            category="citation",
            input_text="HR 15 april 2020, ECLI:NL:HR:2020:666, r.o. 5.2.",
            expected_uris=["/id/ecli/ECLI:NL:HR:2020:666/consideration/5.2"],
            source="seed",
            difficulty="medium",
            tags=["ecli", "case_law", "consideration"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Multi-reference — three-way ───────────────────────────────────

        ds.add(KnownAnswer(
            id="cit-multi-003",
            category="citation",
            input_text="Zie artikel 5 WED jo. S.B. 2008 no. 102.",
            expected_uris=[
                "/id/law/wet-economische-delicten/article/5",
                "/id/gazette/sb-2008-102",
            ],
            source="seed",
            difficulty="hard",
            tags=["legislation", "gazette", "multi_ref"],
            metadata={"resolver_status": "resolved"},
        ))


        ds.add(KnownAnswer(
            id="cit-multi-005",
            category="citation",
            input_text="De Kiesregeling (S.B. 2019 no. 55) bepaalt in artikel 12 dat …",
            expected_uris=[
                "/id/law/kiesregeling/article/12",
                "/id/gazette/sb-2019-55",
            ],
            source="seed",
            difficulty="hard",
            tags=["legislation", "gazette", "multi_ref"],
            metadata={"resolver_status": "resolved"},
        ))

        # ── Negative — harder cases ───────────────────────────────────────

        ds.add(KnownAnswer(
            id="cit-neg-003",
            category="citation",
            input_text="In artikel 5 staat de definitie.",
            expected_uris=[],
            source="seed",
            difficulty="medium",
            tags=["negative", "no-law-name"],
            metadata={"resolver_status": "no_result",
                       "note": "artikel without law name should not resolve"},
        ))

        ds.add(KnownAnswer(
            id="cit-neg-004",
            category="citation",
            input_text="Het jaar 1987 was een bijzonder jaar voor Suriname.",
            expected_uris=[],
            source="seed",
            difficulty="medium",
            tags=["negative", "year-only"],
            metadata={"resolver_status": "no_result",
                       "note": "bare year is not a gazette reference"},
        ))

        ds.add(KnownAnswer(
            id="cit-neg-005",
            category="citation",
            input_text="De heer Venetiaan heeft het besluit ondertekend.",
            expected_uris=[],
            source="seed",
            difficulty="easy",
            tags=["negative", "person-name"],
            metadata={"resolver_status": "no_result"},
        ))

        # ── Temporal cases ────────────────────────────────────────────────

        ds.add(KnownAnswer(
            id="tem-001",
            category="temporal",
            input_text="artikel 7 Arbeidswet 1963",
            expected_uris=["/id/law/arbeidswet-1963/article/7"],
            query_date=date(1975, 1, 1),
            source="seed",
            difficulty="medium",
            tags=["temporal", "arbeidswet"],
        ))

        ds.add(KnownAnswer(
            id="tem-002",
            category="temporal",
            input_text="artikel 1 Grondwet 1987",
            expected_uris=["/id/law/grondwet-1987/article/1"],
            query_date=date(2000, 8, 15),
            source="seed",
            difficulty="medium",
            tags=["temporal", "grondwet"],
        ))

        ds.add(KnownAnswer(
            id="tem-003",
            category="temporal",
            input_text="artikel 3 Zegelwet",
            expected_uris=["/id/law/zegelwet/article/3"],
            query_date=date(2022, 10, 6),
            source="seed",
            difficulty="medium",
            tags=["temporal", "zegelwet"],
        ))

        ds.add(KnownAnswer(
            id="tem-004",
            category="temporal",
            input_text="artikel 12 Kiesregeling",
            expected_uris=["/id/law/kiesregeling/article/12"],
            query_date=date(2024, 3, 1),
            source="seed",
            difficulty="medium",
            tags=["temporal", "kiesregeling"],
        ))


        # ── Real-text and rechtspraak.sr cases ────────────────────────────

        ds.add(KnownAnswer(
            id="cit-sru-001",
            category="citation",
            input_text="SRU-K3-2016-1",
            expected_uris=[
                "/id/caselaw/sru-k3-2016-1",
            ],
            source="seed",
            difficulty="easy",
            tags=['case_law', 'sru', 'suriname'],
        ))

        ds.add(KnownAnswer(
            id="cit-multi-006",
            category="citation",
            input_text="Zie SRU-HvJ-2018-4, r.o. 12 en artikel 1990 BW.",
            expected_uris=[
                "/id/caselaw/sru-hvj-2018-4/consideration/12",
                "/id/law/burgerlijk-wetboek/article/1990",
            ],
            source="seed",
            difficulty="hard",
            tags=['case_law', 'sru', 'legislation', 'multi_ref', 'suriname'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-001",
            category="citation",
            input_text="zoals is verwoord in artikel 1827 lid 2 BW",
            expected_uris=[
                "/id/law/burgerlijk-wetboek/article/1827/paragraph/2",
            ],
            source="seed",
            difficulty="medium",
            tags=['legislation', 'real_text'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-002",
            category="citation",
            input_text="de ontvankelijkheid van verzoekers vordering ondermeer ex artikel 80 Personeelswet",
            expected_uris=[
                "/id/law/personeelswet/article/80",
            ],
            source="seed",
            difficulty="medium",
            tags=['legislation', 'real_text'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-003",
            category="citation",
            input_text="dat de wetgever in artikel 119 lid 3 van het Wetboek van Burgerlijke Rechtsvordering voorgeschreven heeft",
            expected_uris=[
                "/id/law/wetboek-van-burgerlijke-rechtsvordering/article/119/paragraph/3",
            ],
            source="seed",
            difficulty="hard",
            tags=['legislation', 'real_text'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-004",
            category="citation",
            input_text="Een van die gevallen is de in artikel 1615f BW beschreven voortzetting",
            expected_uris=[
                "/id/law/burgerlijk-wetboek/article/1615f",
            ],
            source="seed",
            difficulty="medium",
            tags=['legislation', 'real_text'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-005",
            category="citation",
            input_text="Krachtens Staatsbesluit van 10 april 2015 (S.B. 2015 no. 49) maakt verzoeker aanspraak op",
            expected_uris=[
                "/id/gazette/sb-2015-49",
            ],
            source="seed",
            difficulty="medium",
            tags=['gazette', 'staatsblad', 'real_text'],
        ))

        ds.add(KnownAnswer(
            id="cit-real-006",
            category="citation",
            input_text="in de zin van artikel 1 lid 1 van de Personeelswet (S.B. 1985 no. 41)",
            expected_uris=[
                "/id/law/personeelswet/article/1/paragraph/1",
                "/id/gazette/sb-1985-41",
            ],
            source="seed",
            difficulty="hard",
            tags=['legislation', 'gazette', 'multi_ref', 'real_text'],
        ))

        return ds

    @classmethod
    def dutch_legal_seed(cls) -> "KnownAnswerDataset":
        """Backward-compatibility alias for suriname_legal_seed()."""
        return cls.suriname_legal_seed()
