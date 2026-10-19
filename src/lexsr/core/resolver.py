"""
Citation resolver for Suriname and generic legal references.

Takes raw reference text extracted from documents and resolves it to
canonical fragment URIs from the core identity system.

Multi-pass extraction strategy:
  1. Deterministic extraction (ECLI, CELEX -- high confidence)
  2. Pattern-based parsing (article + law abbreviation -- medium confidence)
  3. Gazette and EU references (medium confidence)
  4. Contextual disambiguation (prefer laws already mentioned nearby)
  5. Unresolved queue for references that cannot be resolved yet

URI generation uses ELI-style identifiers where the law abbreviation
resolves to a slug ID (e.g. "arbeidswet-1963") rather than a BWB ID.

Usage:
    resolver = CitationResolver(store)
    results = resolver.resolve_text(
        "Zoals bepaald in artikel 4 lid 2 Arbeidswet 1963 "
        "(Staatsblad 1963, nr. 163)."
    )
    for r in results:
        print(r.uri, r.confidence)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .identifiers import (
    FragmentPath,
    URIScheme,
    build_uri,
    celex_work_uri,
    ecli_fragment_uri,
    ecli_work_uri,
    gazette_uri,
    get_default_registry,
    law_expression_uri,
    law_fragment_uri,
    law_work_uri,
    resolve_abbreviation,
)
from .models import FragmentType, SourceType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ReferenceType(Enum):
    """Classification of a raw reference extracted from text."""
    ECLI = "ecli"
    CASE_SR = "case_sr"                            # "SRU-HvJ-2020-7" (rechtspraak.sr)
    CELEX = "celex"
    LEGISLATION_CITATION = "legislation_citation"  # "artikel X Wet Y"
    CASE_CITATION = "case_citation"                # court name + date pattern
    GAZETTE = "gazette"                            # "Staatsblad 2024, 123"
    EU_DIRECTIVE = "eu_directive"                   # "Richtlijn 2016/679"
    EU_REGULATION = "eu_regulation"                 # "Verordening (EU) 2016/679"
    UNKNOWN = "unknown"


class ResolutionStatus(Enum):
    """Outcome of a resolution attempt."""
    RESOLVED = "resolved"
    PARTIAL = "partial"          # identified type but not exact fragment
    UNRESOLVED = "unresolved"    # could not determine target


@dataclass
class ParsedReference:
    """
    A reference after pattern extraction but before resolution.

    Not all fields will be populated -- only those relevant to the
    detected reference type.
    """
    raw_text: str
    ref_type: ReferenceType
    # Extracted components
    ecli: Optional[str] = None
    celex_id: Optional[str] = None
    law_id: Optional[str] = None            # slug ID resolved from abbreviation
    law_abbreviation: Optional[str] = None  # raw abbreviation as found in text
    article: Optional[str] = None
    lid: Optional[str] = None               # paragraph number (lid)
    sub: Optional[str] = None               # lettered sub-item
    consideration: Optional[str] = None     # r.o. number for case law
    section: Optional[str] = None           # structural section reference
    date_ref: Optional[str] = None          # date mentioned in citation
    gazette_year: Optional[str] = None
    gazette_number: Optional[str] = None
    gazette_jurisdiction: Optional[str] = None  # "sr" for Suriname
    # Position in source document
    char_start: int = 0
    char_end: int = 0
    context: str = ""
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedReference:
    """A fully or partially resolved reference with canonical URI."""
    raw_text: str
    ref_type: ReferenceType
    status: ResolutionStatus
    uri: Optional[str] = None                 # canonical URI if resolved
    work_uri: Optional[str] = None            # always populated if type known
    confidence: float = 0.0
    extraction_method: str = "pattern"
    parsed: Optional[ParsedReference] = None  # full parse details
    source_document_id: Optional[str] = None
    char_start: int = 0
    char_end: int = 0
    context: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Citation patterns -- generic / Suriname legal text
# ---------------------------------------------------------------------------

# ECLI: European Case Law Identifier -- most reliable identifier.
# Format: ECLI:CC:COURT:YEAR:NUMBER
_ECLI_RE = re.compile(
    r'ECLI:[A-Z]{2}:[A-Z]{1,8}:\d{4}:[A-Za-z0-9.]+'
)

# Surinamese decisions as identified on rechtspraak.sr: SRU-HvJ-2020-7, SRU-K1-2021-100.
# Suriname has no ECLI; this is the identifier its courts' portal assigns.
_SRU_RE = re.compile(r'\bSRU-([A-Za-z]{1,4}\d?)-(\d{4})-(\d{1,4})\b', re.IGNORECASE)

# CELEX IDs (EU legislation identifiers).
# Format: sector-digit + 4-digit year + type-letter + sequence digits
_CELEX_RE = re.compile(r'\b([0-9]\d{4}[A-Z]\d{4}[A-Za-z0-9]*)\b')

# Consideration (rechtsoverweging / r.o.) references following an ECLI.
# Matches patterns like "r.o. 4.3" or "r. o. 4.3.1"
_CONSIDERATION_RE = re.compile(
    r'r\.?\s*o\.?\s*(\d+(?:\.\d+)*)'
)

# Gazette references -- Staatsblad (S.B.) patterns for Suriname.
# Matches: "S.B. 1963 no. 163", "Staatsblad 2024, 45", "Stb. 2024, 123",
#           "S.B. 1963, 163", "G.B. 1936 no. 144"
_GAZETTE_SB_RE = re.compile(
    r'(?:'
    r'S\.?\s*B\.?'                          # S.B. / SB
    r'|St(?:aats)?b(?:lad)?\.?'             # Staatsblad / Stb.
    r')\s+'
    r'(\d{4})'                              # year
    r'\s*(?:,\s*|no\.?\s*|nr\.?\s*)'        # separator: comma, no., nr.
    r'(\d+)',
    re.IGNORECASE
)

# Gouvernementsblad (historical gazette for Suriname, pre-independence)
# Matches both abbreviated "G.B." and full "Gouvernementsblad"
_GAZETTE_GB_RE = re.compile(
    r'(?:G\.?\s*B\.?|Gouvernementsblad)\s+'
    r'(\d{4})'
    r'\s*(?:,\s*|no\.?\s*|nr\.?\s*)'
    r'(\d+)',
    re.IGNORECASE
)

# EU Directive references (Dutch and English).
# Matches: "Richtlijn 2016/679", "Directive (EU) 2016/679",
#           "Richtlijn (EG) 2003/88/EG"
_EU_DIRECTIVE_RE = re.compile(
    r'(?:Richtlijn|Directive)\s+(?:\((?:EU|EG|EEG)\)\s+)?'
    r'(\d{4})/(\d+)(?:/(?:EU|EG|EEG))?',
    re.IGNORECASE
)

# EU Regulation references (Dutch and English).
# Matches: "Verordening (EU) 2016/679", "Regulation (EU) nr. 2016/679"
_EU_REGULATION_RE = re.compile(
    r'(?:Verordening|Regulation)\s+\((?:EU|EG|EEG)\)\s+'
    r'(?:nr\.?\s*)?(\d{4})/(\d+)',
    re.IGNORECASE
)

# The main legislation citation pattern:
# "artikel X [lid Y [sub Z]] [van [de/het]] LawName"
#
# Handles Suriname and Dutch style citations:
#   "artikel 4 lid 2 Arbeidswet 1963"
#   "art. 6:162 BW"
#   "artikel 1 eerste lid Grondwet"
#   "artikel 3 lid 1 sub a Personeelswet"
#
# NOTE: The law name MUST start with an uppercase letter to avoid
# capturing prepositions as part of the law name.
_ARTICLE_LAW_RE = re.compile(
    r'[Aa]rt(?:ikel)?\.?\s+'                       # "artikel" or "art."
    r'(\d+(?:[:\.]\d+)?[a-z]?)'                    # article: 6:162, 3.2, 12a, 4
    r'(?:'
    r'\s+(?:'
    r'(?:eerste|tweede|derde|vierde|vijfde|zesde|'
    r'zevende|achtste|negende|tiende)\s+lid'        # ordinal lid
    r'|lid\s+(\d+[a-z]?)'                          # numeric lid
    r')'
    r')?'
    r'(?:\s+(?:sub|onder)\s+([a-z]))?'             # sub
    r'\s+(?:van\s+)?(?:de\s+|het\s+)?'             # connecting words
    r'([A-Z][A-Za-z0-9\s.]{0,60}?)'               # law name -- MUST start uppercase
    # Lookahead: stop at lowercase word UNLESS it is a Dutch connector ("van",
    # "de", "het", "der", "den") that is immediately followed by an uppercase
    # letter (part of a compound law name like "Wetboek van Strafrecht").
    r'(?=\s+(?!(?:van|de|het|der|den)\s+[A-Z])[a-z(]|\s*[,;.\)\]\-:!?]|\s*$)',
)

# Reverse order: "LawName artikel X" or "BW art. 6:162"
_LAW_ARTICLE_RE = re.compile(
    r'(?<![a-z])'                                   # not preceded by lowercase
    r'([A-Z][A-Za-z0-9\s.]{0,50}?)\s+'             # law name
    r'[Aa]rt(?:ikel)?\.?\s+'                        # artikel
    r'(\d+(?:[:\.]\d+)?[a-z]?)'                    # article number
    r'(?:\s+lid\s+(\d+[a-z]?))?'                   # lid
    r'(?:\s+(?:sub|onder)\s+([a-z]))?',             # sub
)

# Generic article reference without explicit law name.
# "artikel 12" standing alone -- lower confidence, needs context.
_ARTICLE_BARE_RE = re.compile(
    r'[Aa]rt(?:ikel)?\.?\s+'
    r'(\d+(?:[:\.]\d+)?[a-z]?)'
    r'(?:\s+lid\s+(\d+[a-z]?))?'
    r'(?:\s+(?:sub|onder)\s+([a-z]))?'
    r'(?=\s*[,;.\)\]]|\s+(?:van|en|of|wordt|is|bepaalt|luidt)\b|\s*$)'
)

# Dutch ordinal words -> paragraph number
_ORDINAL_MAP: Dict[str, str] = {
    "eerste": "1", "tweede": "2", "derde": "3", "vierde": "4",
    "vijfde": "5", "zesde": "6", "zevende": "7", "achtste": "8",
    "negende": "9", "tiende": "10",
}

# Default jurisdiction for gazette URIs
_DEFAULT_JURISDICTION = "sr"


# ---------------------------------------------------------------------------
# Reference Parser
# ---------------------------------------------------------------------------

class ReferenceParser:
    """
    Extract and parse raw legal references from text.

    Multi-pass approach:
      Pass 1: Deterministic identifiers (ECLI, CELEX)
      Pass 2: Structured citations (artikel X Wet Y)
      Pass 3: EU legislation references
      Pass 4: Gazette references (Staatsblad / Gouvernementsblad)
      Pass 5: Bare article references (low confidence, context-dependent)
      Pass 6: Standalone known law names (e.g. "De Zegelwet is van toepassing")
    """

    def __init__(self, jurisdiction: str = _DEFAULT_JURISDICTION):
        """
        Args:
            jurisdiction: two-letter jurisdiction code, default "sr"
                          (Suriname). Used for gazette URI construction.
        """
        self.jurisdiction = jurisdiction

    def extract_all(self, text: str) -> List[ParsedReference]:
        """Extract all recognizable references from text."""
        refs: List[ParsedReference] = []

        # Pass 1: deterministic identifiers
        refs.extend(self._extract_ecli(text))
        refs.extend(self._extract_sru(text))
        refs.extend(self._extract_celex(text))

        # Pass 2: structured legislation citations
        refs.extend(self._extract_article_law(text))

        # Pass 3: EU legislation
        refs.extend(self._extract_eu_directives(text))
        refs.extend(self._extract_eu_regulations(text))

        # Pass 4: gazette references
        refs.extend(self._extract_gazette_sb(text))
        refs.extend(self._extract_gazette_gb(text))

        # Pass 5: bare article references (only if not already captured)
        refs.extend(self._extract_bare_articles(text))

        # Pass 6: standalone known law names without article numbers
        refs.extend(self._extract_standalone_laws(text))

        # Deduplicate overlapping spans, keeping highest confidence
        refs = self._deduplicate(refs)
        return refs

    # ---- Pass 1: Deterministic identifiers --------------------------------

    def _extract_ecli(self, text: str) -> List[ParsedReference]:
        """Extract ECLI identifiers and optional r.o. references."""
        refs = []
        for m in _ECLI_RE.finditer(text):
            ecli = m.group(0)
            # Look for consideration (r.o.) reference within 80 chars
            consideration = None
            after_text = text[m.end():m.end() + 80]
            ro_match = _CONSIDERATION_RE.search(after_text)
            if ro_match:
                consideration = ro_match.group(1)

            raw = ecli
            end_pos = m.end()
            if consideration:
                raw = f"{ecli}, r.o. {consideration}"
                end_pos = m.end() + ro_match.end()

            refs.append(ParsedReference(
                raw_text=raw,
                ref_type=ReferenceType.ECLI,
                ecli=ecli,
                consideration=consideration,
                char_start=m.start(),
                char_end=end_pos,
                context=text[max(0, m.start() - 50):m.end() + 80],
                confidence=0.95 if consideration else 0.98,
            ))
        return refs

    def _extract_sru(self, text: str) -> List[ParsedReference]:
        """Extract rechtspraak.sr decision identifiers (SRU-HvJ-2020-7) and an optional r.o."""
        refs = []
        for m in _SRU_RE.finditer(text):
            sru = f"SRU-{m.group(1)}-{m.group(2)}-{m.group(3)}"
            consideration = None
            ro_match = _CONSIDERATION_RE.search(text[m.end():m.end() + 80])
            if ro_match:
                consideration = ro_match.group(1)
            refs.append(ParsedReference(
                raw_text=f"{sru}, r.o. {consideration}" if consideration else sru,
                ref_type=ReferenceType.CASE_SR,
                consideration=consideration,
                char_start=m.start(),
                char_end=m.end() + (ro_match.end() if ro_match else 0),
                context=text[max(0, m.start() - 50):m.end() + 80],
                confidence=0.95 if consideration else 0.98,
                metadata={"sru_id": sru},
            ))
        return refs

    def _extract_celex(self, text: str) -> List[ParsedReference]:
        """Extract CELEX identifiers for EU legislation."""
        refs = []
        for m in _CELEX_RE.finditer(text):
            celex_id = m.group(1)
            # Basic sanity: first digit must be a valid CELEX sector (1-9)
            if celex_id[0] == '0':
                continue
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.CELEX,
                celex_id=celex_id,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 30):m.end() + 30],
                confidence=0.90,
            ))
        return refs

    # ---- Pass 2: Structured legislation citations -------------------------

    def _extract_article_law(self, text: str) -> List[ParsedReference]:
        """
        Parse 'artikel X [lid Y] LawName' patterns.

        Handles both forward order ("artikel 4 lid 2 Arbeidswet 1963")
        and reverse order ("BW artikel 6:162").
        """
        refs = []

        # Forward order: "artikel 6:162 BW"
        for m in _ARTICLE_LAW_RE.finditer(text):
            article = m.group(1)
            lid = m.group(2)
            sub = m.group(3)
            law_name = m.group(4).strip().rstrip("., ")

            # Check for ordinal lid in the raw matched text
            if not lid:
                raw_segment = m.group(0)
                for ordinal, num in _ORDINAL_MAP.items():
                    if ordinal in raw_segment.lower():
                        lid = num
                        break

            law_id = resolve_abbreviation(law_name)
            refs.append(ParsedReference(
                raw_text=m.group(0).strip(),
                ref_type=ReferenceType.LEGISLATION_CITATION,
                law_abbreviation=law_name,
                law_id=law_id,
                article=article,
                lid=lid,
                sub=sub,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 40):m.end() + 40],
                confidence=0.85 if law_id else 0.50,
            ))

        # Reverse order: "BW artikel 6:162"
        for m in _LAW_ARTICLE_RE.finditer(text):
            law_name = m.group(1).strip().rstrip("., ")
            article = m.group(2)
            lid = m.group(3)
            sub = m.group(4)

            law_id = resolve_abbreviation(law_name)
            refs.append(ParsedReference(
                raw_text=m.group(0).strip(),
                ref_type=ReferenceType.LEGISLATION_CITATION,
                law_abbreviation=law_name,
                law_id=law_id,
                article=article,
                lid=lid,
                sub=sub,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 40):m.end() + 40],
                confidence=0.85 if law_id else 0.50,
            ))

        return refs

    # ---- Pass 3: EU legislation -------------------------------------------

    def _extract_eu_directives(self, text: str) -> List[ParsedReference]:
        """Extract EU Directive references."""
        refs = []
        for m in _EU_DIRECTIVE_RE.finditer(text):
            year, number = m.group(1), m.group(2)
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.EU_DIRECTIVE,
                date_ref=year,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 30):m.end() + 30],
                confidence=0.85,
                metadata={"year": year, "number": number},
            ))
        return refs

    def _extract_eu_regulations(self, text: str) -> List[ParsedReference]:
        """Extract EU Regulation references."""
        refs = []
        for m in _EU_REGULATION_RE.finditer(text):
            year, number = m.group(1), m.group(2)
            # Construct probable CELEX number: sector 3 + year + R + padded number
            celex = f"3{year}R{number.zfill(4)}"
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.EU_REGULATION,
                celex_id=celex,
                date_ref=year,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 30):m.end() + 30],
                confidence=0.80,
                metadata={"year": year, "number": number},
            ))
        return refs

    # ---- Pass 4: Gazette references ---------------------------------------

    def _extract_gazette_sb(self, text: str) -> List[ParsedReference]:
        """Extract Staatsblad (S.B.) references -- Suriname official gazette."""
        refs = []
        for m in _GAZETTE_SB_RE.finditer(text):
            year, number = m.group(1), m.group(2)
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.GAZETTE,
                gazette_year=year,
                gazette_number=number,
                gazette_jurisdiction=self.jurisdiction,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 30):m.end() + 30],
                confidence=0.80,
                metadata={
                    "gazette_type": "staatsblad",
                    "year": year,
                    "number": number,
                },
            ))
        return refs

    def _extract_gazette_gb(self, text: str) -> List[ParsedReference]:
        """Extract Gouvernementsblad (G.B.) references -- historical gazette."""
        refs = []
        for m in _GAZETTE_GB_RE.finditer(text):
            year, number = m.group(1), m.group(2)
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.GAZETTE,
                gazette_year=year,
                gazette_number=number,
                gazette_jurisdiction=self.jurisdiction,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 30):m.end() + 30],
                confidence=0.75,
                metadata={
                    "gazette_type": "gouvernementsblad",
                    "year": year,
                    "number": number,
                },
            ))
        return refs

    # ---- Pass 5: Bare article references ----------------------------------

    def _extract_bare_articles(self, text: str) -> List[ParsedReference]:
        """
        Extract standalone article references without an explicit law name.

        These have low confidence and typically need contextual resolution
        (e.g., the article belongs to the law being discussed in the
        surrounding text).
        """
        refs = []
        for m in _ARTICLE_BARE_RE.finditer(text):
            article = m.group(1)
            lid = m.group(2)
            sub = m.group(3)
            refs.append(ParsedReference(
                raw_text=m.group(0).strip(),
                ref_type=ReferenceType.LEGISLATION_CITATION,
                article=article,
                lid=lid,
                sub=sub,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 60):m.end() + 60],
                confidence=0.30,
                metadata={"bare_reference": True},
            ))
        return refs

    # ---- Pass 6: Standalone known law names --------------------------------

    def _extract_standalone_laws(self, text: str) -> List[ParsedReference]:
        """
        Match known law abbreviations that appear in text without an article
        number (e.g. "De Zegelwet is van toepassing…").

        Uses the default AbbreviationRegistry; only multi-char keys are used
        to avoid spurious short-abbreviation matches.
        """
        registry = get_default_registry()
        keys = [k for k in registry.all_keys() if len(k) >= 4]
        if not keys:
            return []
        pattern = r'(?<!\w)(' + '|'.join(re.escape(k) for k in keys) + r')(?!\w)'
        refs = []
        for m in re.finditer(pattern, text):
            name = m.group(1)
            law_id = resolve_abbreviation(name)
            if not law_id:
                continue
            refs.append(ParsedReference(
                raw_text=m.group(0),
                ref_type=ReferenceType.LEGISLATION_CITATION,
                law_abbreviation=name,
                law_id=law_id,
                char_start=m.start(),
                char_end=m.end(),
                context=text[max(0, m.start() - 40):m.end() + 40],
                confidence=0.70,
            ))
        return refs

    # ---- Deduplication ----------------------------------------------------

    def _deduplicate(self, refs: List[ParsedReference]) -> List[ParsedReference]:
        """
        Remove overlapping references, keeping the highest confidence match.

        When two references overlap in the source text (same character span),
        only the one with the higher confidence score is retained.
        """
        if not refs:
            return refs
        refs.sort(key=lambda r: (r.char_start, -r.confidence))
        result = [refs[0]]
        for r in refs[1:]:
            prev = result[-1]
            if r.char_start < prev.char_end:
                # Overlapping span -- keep higher confidence
                if r.confidence > prev.confidence:
                    result[-1] = r
            else:
                result.append(r)
        return result


# ---------------------------------------------------------------------------
# Context Tracker
# ---------------------------------------------------------------------------

class _ContextTracker:
    """
    Tracks laws mentioned in the surrounding text to help resolve
    bare article references.

    When a document mentions "Arbeidswet 1963" and later refers to
    "artikel 5", this tracker provides the contextual law for
    disambiguation.
    """

    def __init__(self, window_size: int = 500):
        """
        Args:
            window_size: character window within which a previously
                         mentioned law is considered "in scope".
        """
        self.window_size = window_size
        self._mentions: List[Tuple[int, str, str]] = []  # (char_pos, abbreviation, law_id)

    def record_mention(
        self, char_pos: int, abbreviation: str, law_id: str
    ) -> None:
        """Record that a law was mentioned at a given position."""
        self._mentions.append((char_pos, abbreviation, law_id))

    def get_context_law(self, char_pos: int) -> Optional[Tuple[str, str]]:
        """
        Find the most recently mentioned law within the context window.

        Returns:
            (abbreviation, law_id) tuple or None if no law in scope.
        """
        best: Optional[Tuple[int, str, str]] = None
        for pos, abbrev, law_id in self._mentions:
            distance = char_pos - pos
            if 0 < distance <= self.window_size:
                if best is None or pos > best[0]:
                    best = (pos, abbrev, law_id)
        if best:
            return best[1], best[2]
        return None

    def clear(self) -> None:
        self._mentions.clear()


# ---------------------------------------------------------------------------
# Citation Resolver
# ---------------------------------------------------------------------------

class CitationResolver:
    """
    Resolve parsed references to canonical fragment URIs.

    Uses the identity system from identifiers.py to produce URIs.
    Maintains an unresolved queue for references that need human review.
    Tracks contextual law mentions for bare article disambiguation.
    """

    def __init__(
        self,
        store=None,
        default_date: Optional[date] = None,
        unresolved_db_path: Optional[str] = None,
        jurisdiction: str = _DEFAULT_JURISDICTION,
        context_window: int = 500,
    ):
        """
        Args:
            store: CanonicalStore instance (optional, for cross-referencing).
            default_date: default version date for legislation URIs.
            unresolved_db_path: path for SQLite unresolved reference queue.
            jurisdiction: two-letter jurisdiction code (default "sr").
            context_window: character window for contextual disambiguation.
        """
        self.store = store
        self.default_date = default_date or date.today()
        self.jurisdiction = jurisdiction
        self.parser = ReferenceParser(jurisdiction=jurisdiction)
        self._context = _ContextTracker(window_size=context_window)

        # Unresolved reference queue
        self._unresolved_db: Optional[sqlite3.Connection] = None
        if unresolved_db_path:
            self._init_unresolved_db(unresolved_db_path)

    def resolve_text(
        self,
        text: str,
        source_document_id: Optional[str] = None,
        version_date: Optional[date] = None,
    ) -> List[ResolvedReference]:
        """
        Extract and resolve all legal references in text.

        Args:
            text: document text to scan.
            source_document_id: ID of the document being analyzed.
            version_date: date to use for legislation version URIs.

        Returns:
            List of ResolvedReference objects ordered by position.
        """
        vdate = version_date or self.default_date
        self._context.clear()
        parsed_refs = self.parser.extract_all(text)
        results: List[ResolvedReference] = []

        # First pass: resolve all references and build context
        for pr in parsed_refs:
            resolved = self._resolve_one(pr, vdate)
            resolved.source_document_id = source_document_id
            results.append(resolved)

            # Record successfully resolved law mentions for context
            if (pr.ref_type == ReferenceType.LEGISLATION_CITATION
                    and pr.law_id
                    and resolved.status == ResolutionStatus.RESOLVED):
                self._context.record_mention(
                    pr.char_start,
                    pr.law_abbreviation or "",
                    pr.law_id,
                )

        # Second pass: try to resolve bare references using context
        for i, resolved in enumerate(results):
            if (resolved.status in (ResolutionStatus.UNRESOLVED, ResolutionStatus.PARTIAL)
                    and resolved.parsed
                    and resolved.parsed.metadata.get("bare_reference")):
                improved = self._resolve_with_context(resolved.parsed, vdate)
                if improved.status == ResolutionStatus.RESOLVED:
                    improved.source_document_id = source_document_id
                    results[i] = improved

        # Queue any still-unresolved references
        for resolved in results:
            if (resolved.status == ResolutionStatus.UNRESOLVED
                    and self._unresolved_db):
                self._queue_unresolved(resolved, source_document_id)

        return results

    def resolve_reference(
        self,
        raw_text: str,
        version_date: Optional[date] = None,
    ) -> ResolvedReference:
        """Resolve a single reference string."""
        vdate = version_date or self.default_date
        refs = self.parser.extract_all(raw_text)
        if not refs:
            return ResolvedReference(
                raw_text=raw_text,
                ref_type=ReferenceType.UNKNOWN,
                status=ResolutionStatus.UNRESOLVED,
                confidence=0.0,
            )
        return self._resolve_one(refs[0], vdate)

    def _resolve_one(
        self, pr: ParsedReference, vdate: date
    ) -> ResolvedReference:
        """Route a single ParsedReference to its type-specific resolver."""

        dispatch = {
            ReferenceType.ECLI: self._resolve_ecli,
            ReferenceType.CASE_SR: self._resolve_sru,
            ReferenceType.CELEX: self._resolve_celex,
            ReferenceType.LEGISLATION_CITATION: self._resolve_legislation,
            ReferenceType.EU_REGULATION: self._resolve_eu_regulation,
            ReferenceType.EU_DIRECTIVE: self._resolve_eu_directive,
            ReferenceType.GAZETTE: self._resolve_gazette,
        }

        resolver_fn = dispatch.get(pr.ref_type)
        if resolver_fn is not None:
            # Legislation and gazette resolvers need the version date
            if pr.ref_type in (
                ReferenceType.LEGISLATION_CITATION,
                ReferenceType.GAZETTE,
            ):
                return resolver_fn(pr, vdate)
            return resolver_fn(pr)

        # Unknown reference type
        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=pr.ref_type,
            status=ResolutionStatus.UNRESOLVED,
            confidence=pr.confidence * 0.3,
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    # --- Type-specific resolvers -------------------------------------------

    def _resolve_sru(self, pr: ParsedReference) -> ResolvedReference:
        """Resolve a rechtspraak.sr identifier to the corpus's case-law URI, the same scheme
        the case-law pipeline gives decisions: /id/caselaw/sru-hvj-2020-7[/consideration/4]."""
        slug = re.sub(r"[^a-z0-9]+", "-", pr.metadata["sru_id"].lower()).strip("-")
        work = f"/id/caselaw/{slug}"
        uri = f"{work}/consideration/{pr.consideration}" if pr.consideration else work
        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.CASE_SR,
            status=ResolutionStatus.RESOLVED,
            uri=uri,
            work_uri=work,
            confidence=pr.confidence,
            extraction_method="deterministic_sru",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_ecli(self, pr: ParsedReference) -> ResolvedReference:
        """Resolve an ECLI identifier, optionally with r.o. fragment."""
        work = ecli_work_uri(pr.ecli)

        if pr.consideration:
            uri = ecli_fragment_uri(pr.ecli, "consideration", pr.consideration)
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.ECLI,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=work,
                confidence=pr.confidence,
                extraction_method="deterministic_ecli",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
            )

        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.ECLI,
            status=ResolutionStatus.RESOLVED,
            uri=work,
            work_uri=work,
            confidence=pr.confidence,
            extraction_method="deterministic_ecli",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_celex(self, pr: ParsedReference) -> ResolvedReference:
        """Resolve a CELEX identifier for EU legislation."""
        work = celex_work_uri(pr.celex_id)
        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.CELEX,
            status=ResolutionStatus.RESOLVED,
            uri=work,
            work_uri=work,
            confidence=pr.confidence,
            extraction_method="deterministic_celex",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_legislation(
        self, pr: ParsedReference, vdate: date
    ) -> ResolvedReference:
        """
        Resolve 'artikel X [lid Y] WetNaam' to a fragment URI.

        The law abbreviation is resolved to a slug ID via the
        abbreviation registry (e.g. "Arbeidswet" -> "arbeidswet-1963").
        """
        if not pr.law_id:
            # Unknown law abbreviation -- partial resolution
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.LEGISLATION_CITATION,
                status=ResolutionStatus.PARTIAL,
                confidence=pr.confidence * 0.5,
                extraction_method="pattern_legislation",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
                metadata={
                    "reason": f"Unknown abbreviation: {pr.law_abbreviation!r}",
                },
            )

        work = law_work_uri(pr.law_id)

        if pr.article:
            uri = law_fragment_uri(
                pr.law_id,
                vdate,
                pr.article,
                paragraph=pr.lid,
                sub=pr.sub,
            )
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.LEGISLATION_CITATION,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=work,
                confidence=pr.confidence,
                extraction_method="pattern_legislation",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
            )

        # No article number -- reference to the law as a whole
        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.LEGISLATION_CITATION,
            status=ResolutionStatus.RESOLVED,
            uri=work,
            work_uri=work,
            confidence=pr.confidence * 0.8,
            extraction_method="pattern_legislation",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_eu_regulation(self, pr: ParsedReference) -> ResolvedReference:
        """Resolve an EU Regulation reference via CELEX number."""
        if pr.celex_id:
            uri = celex_work_uri(pr.celex_id)
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.EU_REGULATION,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=uri,
                confidence=pr.confidence,
                extraction_method="pattern_eu_regulation",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
            )
        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.EU_REGULATION,
            status=ResolutionStatus.PARTIAL,
            confidence=pr.confidence * 0.5,
            extraction_method="pattern_eu_regulation",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_eu_directive(self, pr: ParsedReference) -> ResolvedReference:
        """Resolve an EU Directive reference via constructed CELEX number."""
        meta = pr.metadata or {}
        year = meta.get("year", pr.date_ref)
        number = meta.get("number", "")

        if year and number:
            celex = f"3{year}L{number.zfill(4)}"
            uri = celex_work_uri(celex)
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.EU_DIRECTIVE,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=uri,
                confidence=pr.confidence * 0.9,
                extraction_method="pattern_eu_directive",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
            )

        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.EU_DIRECTIVE,
            status=ResolutionStatus.PARTIAL,
            confidence=pr.confidence * 0.4,
            extraction_method="pattern_eu_directive",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    def _resolve_gazette(
        self, pr: ParsedReference, vdate: date
    ) -> ResolvedReference:
        """Resolve a gazette reference (Staatsblad / Gouvernementsblad)."""
        jurisdiction = pr.gazette_jurisdiction or self.jurisdiction
        year = pr.gazette_year
        number = pr.gazette_number

        if year and number:
            uri = gazette_uri(pr.metadata.get("gazette_type", ""), year, number)
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.GAZETTE,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=uri,
                confidence=pr.confidence,
                extraction_method="pattern_gazette",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
                metadata={
                    "gazette_type": pr.metadata.get("gazette_type", "unknown"),
                    "year": year,
                    "number": number,
                    "jurisdiction": jurisdiction,
                },
            )

        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=ReferenceType.GAZETTE,
            status=ResolutionStatus.PARTIAL,
            confidence=pr.confidence * 0.4,
            extraction_method="pattern_gazette",
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    # --- Contextual resolution ---------------------------------------------

    def _resolve_with_context(
        self, pr: ParsedReference, vdate: date
    ) -> ResolvedReference:
        """
        Try to resolve a bare article reference using context.

        If a law was mentioned recently in the text, assume the bare
        article reference belongs to that law.
        """
        ctx = self._context.get_context_law(pr.char_start)
        if ctx is None:
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=pr.ref_type,
                status=ResolutionStatus.UNRESOLVED,
                confidence=pr.confidence,
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
                metadata={"reason": "no contextual law in scope"},
            )

        abbreviation, law_id = ctx
        work = law_work_uri(law_id)

        if pr.article:
            uri = law_fragment_uri(
                law_id,
                vdate,
                pr.article,
                paragraph=pr.lid,
                sub=pr.sub,
            )
            return ResolvedReference(
                raw_text=pr.raw_text,
                ref_type=ReferenceType.LEGISLATION_CITATION,
                status=ResolutionStatus.RESOLVED,
                uri=uri,
                work_uri=work,
                confidence=pr.confidence * 1.5,  # boosted by context
                extraction_method="contextual_legislation",
                parsed=pr,
                char_start=pr.char_start,
                char_end=pr.char_end,
                context=pr.context,
                metadata={
                    "contextual_law": abbreviation,
                    "contextual_law_id": law_id,
                },
            )

        return ResolvedReference(
            raw_text=pr.raw_text,
            ref_type=pr.ref_type,
            status=ResolutionStatus.PARTIAL,
            confidence=pr.confidence * 0.6,
            parsed=pr,
            char_start=pr.char_start,
            char_end=pr.char_end,
            context=pr.context,
        )

    # --- Unresolved reference queue ----------------------------------------

    def _init_unresolved_db(self, db_path: str) -> None:
        """Initialize the SQLite database for unresolved references."""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._unresolved_db = sqlite3.connect(str(path))
        self._unresolved_db.execute("""
            CREATE TABLE IF NOT EXISTS unresolved_references (
                id TEXT PRIMARY KEY,
                raw_text TEXT NOT NULL,
                ref_type TEXT NOT NULL,
                source_document_id TEXT,
                context TEXT,
                confidence REAL,
                parsed_data TEXT,
                attempts INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                resolved_uri TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        self._unresolved_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_unresolved_status
            ON unresolved_references(status)
        """)
        self._unresolved_db.execute("""
            CREATE INDEX IF NOT EXISTS idx_unresolved_type
            ON unresolved_references(ref_type)
        """)
        self._unresolved_db.commit()

    def _queue_unresolved(
        self,
        ref: ResolvedReference,
        source_doc_id: Optional[str],
    ) -> None:
        """Add an unresolved reference to the queue for later resolution."""
        if not self._unresolved_db:
            return

        # Deterministic ID from raw text + type
        ref_id = hashlib.sha256(
            f"{ref.raw_text}:{ref.ref_type.value}".encode()
        ).hexdigest()[:16]

        parsed_data = json.dumps({
            "article": ref.parsed.article if ref.parsed else None,
            "lid": ref.parsed.lid if ref.parsed else None,
            "sub": ref.parsed.sub if ref.parsed else None,
            "law_abbreviation": ref.parsed.law_abbreviation if ref.parsed else None,
            "law_id": ref.parsed.law_id if ref.parsed else None,
        })

        self._unresolved_db.execute("""
            INSERT INTO unresolved_references
                (id, raw_text, ref_type, source_document_id, context,
                 confidence, parsed_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                attempts = attempts + 1,
                last_seen = datetime('now'),
                source_document_id = COALESCE(
                    excluded.source_document_id, source_document_id
                )
        """, (
            ref_id,
            ref.raw_text,
            ref.ref_type.value,
            source_doc_id,
            ref.context,
            ref.confidence,
            parsed_data,
        ))
        self._unresolved_db.commit()

    def resolve_from_queue(
        self,
        ref_id: str,
        uri: str,
    ) -> bool:
        """
        Manually resolve a queued reference by providing its canonical URI.

        Args:
            ref_id: the reference ID from the unresolved queue.
            uri: the resolved canonical URI.

        Returns:
            True if the reference was found and updated.
        """
        if not self._unresolved_db:
            return False

        cursor = self._unresolved_db.execute("""
            UPDATE unresolved_references
            SET resolved_uri = ?, status = 'resolved', last_seen = datetime('now')
            WHERE id = ? AND status = 'pending'
        """, (uri, ref_id))
        self._unresolved_db.commit()
        return cursor.rowcount > 0

    def get_unresolved_stats(self) -> Dict[str, Any]:
        """Get statistics about unresolved references."""
        if not self._unresolved_db:
            return {"error": "no unresolved queue configured"}

        cur = self._unresolved_db.cursor()

        total = cur.execute(
            "SELECT COUNT(*) FROM unresolved_references WHERE status='pending'"
        ).fetchone()[0]

        by_type: Dict[str, Any] = {}
        for row in cur.execute("""
            SELECT ref_type, COUNT(*), AVG(attempts)
            FROM unresolved_references WHERE status='pending'
            GROUP BY ref_type ORDER BY COUNT(*) DESC
        """).fetchall():
            by_type[row[0]] = {
                "count": row[1],
                "avg_attempts": round(row[2], 1),
            }

        top_frequent: List[Dict[str, Any]] = []
        for row in cur.execute("""
            SELECT raw_text, ref_type, attempts, context
            FROM unresolved_references WHERE status='pending'
            ORDER BY attempts DESC LIMIT 20
        """).fetchall():
            top_frequent.append({
                "raw_text": row[0],
                "ref_type": row[1],
                "attempts": row[2],
                "context": row[3],
            })

        return {
            "total_unresolved": total,
            "by_type": by_type,
            "top_frequent": top_frequent,
        }

    def get_unresolved_references(
        self,
        ref_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve pending unresolved references for review.

        Args:
            ref_type: optionally filter by reference type value.
            limit: maximum number of results.

        Returns:
            List of dicts with reference details.
        """
        if not self._unresolved_db:
            return []

        cur = self._unresolved_db.cursor()
        if ref_type:
            rows = cur.execute("""
                SELECT id, raw_text, ref_type, source_document_id,
                       context, confidence, parsed_data, attempts,
                       first_seen, last_seen
                FROM unresolved_references
                WHERE status='pending' AND ref_type=?
                ORDER BY attempts DESC
                LIMIT ?
            """, (ref_type, limit)).fetchall()
        else:
            rows = cur.execute("""
                SELECT id, raw_text, ref_type, source_document_id,
                       context, confidence, parsed_data, attempts,
                       first_seen, last_seen
                FROM unresolved_references
                WHERE status='pending'
                ORDER BY attempts DESC
                LIMIT ?
            """, (limit,)).fetchall()

        results = []
        for row in rows:
            parsed_data = {}
            if row[6]:
                try:
                    parsed_data = json.loads(row[6])
                except json.JSONDecodeError:
                    pass
            results.append({
                "id": row[0],
                "raw_text": row[1],
                "ref_type": row[2],
                "source_document_id": row[3],
                "context": row[4],
                "confidence": row[5],
                "parsed_data": parsed_data,
                "attempts": row[7],
                "first_seen": row[8],
                "last_seen": row[9],
            })
        return results

    def close(self) -> None:
        """Close the unresolved reference database connection."""
        if self._unresolved_db:
            self._unresolved_db.close()
            self._unresolved_db = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
