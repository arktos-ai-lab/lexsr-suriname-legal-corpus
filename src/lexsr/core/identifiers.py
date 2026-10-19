"""
Canonical URI system for legal documents and fragments.

Design principles:
  1. ELI-style (European Legislation Identifier) URI patterns.
  2. Every Work, Expression, and Fragment gets a stable, parseable URI.
  3. URIs are opaque to consumers but internally structured for routing.
  4. Fragment URIs encode the structural path (article/paragraph/sub).
  5. Jurisdiction-agnostic with configurable abbreviation registries.

URI patterns:

  Legislation:
    Work:       /eli/{jurisdiction}/law/{year}/{slug}
    Expression: /eli/{jurisdiction}/law/{year}/{slug}/{date}
    Fragment:   /eli/{jurisdiction}/law/{year}/{slug}/{date}/article/{nr}
                /eli/{jurisdiction}/law/{year}/{slug}/{date}/article/{nr}/paragraph/{nr}

  Case law:
    Work:       /id/ecli/{ECLI}
    Fragment:   /id/ecli/{ECLI}/consideration/{nr}

  EU law:
    Work:       /id/celex/{CELEX}

  Gazette:
    Work:       /id/gazette/{sb|gb}-{year}-{number}   (Staatsblad / Gouvernementsblad)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .models import FragmentType, SourceType, FRAGMENT_ALIASES


# ---------------------------------------------------------------------------
# URI scheme
# ---------------------------------------------------------------------------

URI_PREFIX = "/id"
ELI_PREFIX = "/eli"


class URIScheme(Enum):
    """Top-level URI namespace per source system."""
    # Generic schemes
    LAW = "law"                    # Primary legislation
    DECREE = "decree"              # Executive decrees
    GAZETTE = "gazette"            # Official gazettes
    CASE = "case"                  # Case law (generic)
    ECLI = "ecli"                  # Case law (ECLI identifier)
    CELEX = "celex"                # EU legislation
    PARLIAMENTARY = "parliamentary"  # Parliamentary documents
    TREATY = "treaty"              # Treaties
    OTHER = "other"                # Catch-all


# Mapping from SourceType to URIScheme
SOURCE_TO_SCHEME: Dict[SourceType, URIScheme] = {
    SourceType.LEGISLATION: URIScheme.LAW,
    SourceType.DECREE: URIScheme.DECREE,
    SourceType.GAZETTE: URIScheme.GAZETTE,
    SourceType.SB_GAZETTE: URIScheme.GAZETTE,
    SourceType.GB_GAZETTE: URIScheme.GAZETTE,
    SourceType.CASE_LAW: URIScheme.CASE,
    SourceType.ECLI: URIScheme.ECLI,
    SourceType.CELEX: URIScheme.CELEX,
    SourceType.EURLEX: URIScheme.CELEX,
    SourceType.EU_CASELAW: URIScheme.ECLI,
    SourceType.REGULATION: URIScheme.LAW,
    SourceType.TREATY: URIScheme.TREATY,
    SourceType.PARLIAMENTARY: URIScheme.PARLIAMENTARY,
    SourceType.DNA_SR: URIScheme.PARLIAMENTARY,
    SourceType.PDF_SCAN: URIScheme.LAW,
    SourceType.PDF_DIGITAL: URIScheme.LAW,
    SourceType.XML_SOURCE: URIScheme.LAW,
    SourceType.HTML_SOURCE: URIScheme.LAW,
}


# ---------------------------------------------------------------------------
# Fragment path encoding
# ---------------------------------------------------------------------------

# Ordered segment types that can appear in a fragment path.
# Uses English canonical names from FragmentType.
FRAGMENT_SEGMENTS = {
    FragmentType.BOOK: "book",
    FragmentType.TITLE: "title",
    FragmentType.CHAPTER: "chapter",
    FragmentType.SECTION: "section",
    FragmentType.PARAGRAPH_GROUP: "paragraph_group",
    FragmentType.ARTICLE: "article",
    FragmentType.PARAGRAPH: "paragraph",
    FragmentType.SUB: "sub",
    FragmentType.ANNEX: "annex",
    FragmentType.CONSIDERATION: "consideration",
    FragmentType.FACTS: "facts",
    FragmentType.OPINION: "opinion",
    FragmentType.DECISION: "decision",
    FragmentType.DISSENT: "dissent",
    FragmentType.AGENDA_ITEM: "agenda_item",
    FragmentType.SPEAKER_TURN: "speaker_turn",
    FragmentType.MOTION: "motion",
    FragmentType.AMENDMENT: "amendment",
    FragmentType.SECTION_GENERIC: "section_generic",
    FragmentType.PREAMBLE: "preamble",
    FragmentType.RECITAL: "recital",
}

# Reverse lookup: segment name -> FragmentType
SEGMENT_TO_TYPE: Dict[str, FragmentType] = {v: k for k, v in FRAGMENT_SEGMENTS.items()}

# Also allow Dutch segment names in URIs for backward compatibility
for _dutch_name, _frag_type in FRAGMENT_ALIASES.items():
    if _dutch_name not in SEGMENT_TO_TYPE:
        SEGMENT_TO_TYPE[_dutch_name] = _frag_type


@dataclass
class FragmentPath:
    """
    Parsed structural path to a fragment.

    A path is a list of (segment_type, number/label) pairs, e.g.:
      [("article", "3:2"), ("paragraph", "1")]
    """
    segments: List[Tuple[str, str]] = field(default_factory=list)

    def append(self, segment_type: str, number: str) -> FragmentPath:
        """Add a segment and return self for chaining."""
        if segment_type not in SEGMENT_TO_TYPE:
            raise ValueError(
                f"Unknown fragment segment type: {segment_type!r}. "
                f"Valid types: {sorted(SEGMENT_TO_TYPE.keys())}"
            )
        self.segments.append((segment_type, str(number)))
        return self

    def to_uri_suffix(self) -> str:
        """Encode as URI path segments: /article/3:2/paragraph/1"""
        parts = []
        for seg_type, number in self.segments:
            parts.append(seg_type)
            if number:
                parts.append(str(number))
        return "/" + "/".join(parts) if parts else ""

    @classmethod
    def from_uri_suffix(cls, suffix: str) -> FragmentPath:
        """Parse a fragment path suffix like /article/3:2/paragraph/1"""
        path = cls()
        parts = [p for p in suffix.strip("/").split("/") if p]
        i = 0
        while i < len(parts):
            seg_type = parts[i]
            if seg_type not in SEGMENT_TO_TYPE:
                raise ValueError(f"Unknown segment type in URI: {seg_type!r}")
            # Some segments are valueless (facts, decision, etc.)
            if i + 1 < len(parts) and parts[i + 1] not in SEGMENT_TO_TYPE:
                number = parts[i + 1]
                i += 2
            else:
                number = ""
                i += 1
            path.segments.append((seg_type, number))
        return path

    @property
    def leaf_type(self) -> Optional[str]:
        """The type of the deepest segment."""
        return self.segments[-1][0] if self.segments else None

    @property
    def leaf_number(self) -> Optional[str]:
        """The number/label of the deepest segment."""
        return self.segments[-1][1] if self.segments else None

    @property
    def depth(self) -> int:
        return len(self.segments)

    def __str__(self) -> str:
        return self.to_uri_suffix()

    def __repr__(self) -> str:
        return f"FragmentPath({self.segments!r})"


# ---------------------------------------------------------------------------
# Canonical URI builder / parser
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LegalURI:
    """
    Parsed canonical URI for any legal object.

    Supports Work, Expression, Manifestation, and Fragment URIs.
    Immutable after creation.
    """
    scheme: URIScheme
    official_id: str
    version_date: Optional[date] = None
    format_type: Optional[str] = None
    fragment_path: Optional[FragmentPath] = None

    @property
    def is_work(self) -> bool:
        return (self.version_date is None and
                self.fragment_path is None and
                self.format_type is None)

    @property
    def is_expression(self) -> bool:
        return (self.version_date is not None and
                self.fragment_path is None and
                self.format_type is None)

    @property
    def is_manifestation(self) -> bool:
        return self.format_type is not None and self.fragment_path is None

    @property
    def is_fragment(self) -> bool:
        return self.fragment_path is not None and bool(self.fragment_path.segments)

    @property
    def work_uri(self) -> str:
        """Return the Work-level URI (strip version/fragment/format)."""
        return f"{URI_PREFIX}/{self.scheme.value}/{self.official_id}"

    @property
    def expression_uri(self) -> Optional[str]:
        """Return the Expression-level URI (strip fragment/format)."""
        if self.version_date is None:
            # Case law: expression == work (single version)
            if self.scheme == URIScheme.ECLI:
                return self.work_uri
            return None
        return f"{self.work_uri}/{self.version_date.isoformat()}"

    def to_uri(self) -> str:
        """Serialize to the full canonical URI string."""
        parts = [URI_PREFIX, self.scheme.value, self.official_id]

        if self.version_date is not None:
            parts.append(self.version_date.isoformat())

        if self.format_type:
            parts.append(self.format_type)

        uri = "/".join(parts)

        if self.fragment_path and self.fragment_path.segments:
            uri += self.fragment_path.to_uri_suffix()

        return uri

    def __str__(self) -> str:
        return self.to_uri()

    def __repr__(self) -> str:
        return f"LegalURI({self.to_uri()!r})"


# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

# ECLI: ECLI:CC:COURT:YEAR:NUMBER
_ECLI_PATTERN = re.compile(
    r"^ECLI:[A-Z]{2}:[A-Z]{1,8}:\d{4}:[A-Za-z0-9.]+$"
)

# CELEX: digit + year(4) + letter + number (simplified)
_CELEX_PATTERN = re.compile(r"^[0-9]\d{4}[A-Z]\d{4}[A-Za-z0-9]*$")


def _validate_official_id(scheme: URIScheme, official_id: str) -> bool:
    """Check that an official_id matches the expected pattern for its scheme."""
    validators = {
        URIScheme.ECLI: _ECLI_PATTERN,
        URIScheme.CELEX: _CELEX_PATTERN,
    }
    pattern = validators.get(scheme)
    if pattern is None:
        return True  # no validator = accept
    return bool(pattern.match(official_id))


# ---------------------------------------------------------------------------
# Public API: build / parse / normalize
# ---------------------------------------------------------------------------

def build_uri(
    scheme: URIScheme,
    official_id: str,
    version_date: Optional[date] = None,
    format_type: Optional[str] = None,
    fragment_path: Optional[FragmentPath] = None,
) -> LegalURI:
    """
    Construct a LegalURI from components.

    Raises ValueError if the official_id doesn't match the scheme's pattern.
    """
    if not _validate_official_id(scheme, official_id):
        raise ValueError(
            f"Invalid official_id {official_id!r} for scheme {scheme.value}. "
            f"Expected pattern: {_get_pattern_hint(scheme)}"
        )
    return LegalURI(
        scheme=scheme,
        official_id=official_id,
        version_date=version_date,
        format_type=format_type,
        fragment_path=fragment_path,
    )


def parse_uri(uri_string: str) -> LegalURI:
    """
    Parse a canonical URI string into a LegalURI.

    Examples:
        /id/law/arbeidswet-1963
        /id/law/arbeidswet-1963/2024-01-15
        /id/law/arbeidswet-1963/2024-01-15/article/2
        /id/ecli/ECLI:NL:HR:2020:1234
        /id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3
        /id/celex/32016R0679
        /id/gazette/sb-2024-123
    """
    # Strip leading slash, split
    parts = [p for p in uri_string.strip("/").split("/") if p]

    if len(parts) < 3 or parts[0] != "id":
        raise ValueError(f"URI must start with /id/{{scheme}}/...: {uri_string!r}")

    # Scheme
    try:
        scheme = URIScheme(parts[1])
    except ValueError:
        raise ValueError(
            f"Unknown URI scheme: {parts[1]!r}. "
            f"Valid: {[s.value for s in URIScheme]}"
        )

    # Official ID
    rest = parts[2:]
    official_id, rest = _extract_official_id(scheme, rest)

    # Now parse remaining parts: optional date, optional format, optional fragment
    version_date = None
    format_type = None
    fragment_path = None

    if rest:
        maybe_date, consumed = _try_parse_date(rest[0])
        if maybe_date is not None:
            version_date = maybe_date
            rest = rest[1:]

    if rest:
        if rest[0] in ("xml", "html", "pdf", "rdf", "json", "text"):
            format_type = rest[0]
            rest = rest[1:]

    if rest:
        fragment_suffix = "/" + "/".join(rest)
        fragment_path = FragmentPath.from_uri_suffix(fragment_suffix)

    return LegalURI(
        scheme=scheme,
        official_id=official_id,
        version_date=version_date,
        format_type=format_type,
        fragment_path=fragment_path,
    )


def normalize_uri(uri_string: str) -> str:
    """Parse and re-serialize a URI to its canonical form."""
    return parse_uri(uri_string).to_uri()


# ---------------------------------------------------------------------------
# Abbreviation Registry (config-driven, replaces hardcoded Dutch dict)
# ---------------------------------------------------------------------------

class AbbreviationRegistry:
    """
    Config-driven law abbreviation registry.

    Maps abbreviations to official IDs for a jurisdiction.
    """

    def __init__(self):
        self._abbreviations: Dict[str, str] = {}
        self._reverse: Dict[str, List[str]] = {}

    def register(self, abbreviation: str, official_id: str) -> None:
        """Register an abbreviation -> official_id mapping."""
        self._abbreviations[abbreviation] = official_id
        self._reverse.setdefault(official_id, []).append(abbreviation)

    def register_many(self, mappings: Dict[str, str]) -> None:
        """Register multiple abbreviations at once."""
        for abbr, oid in mappings.items():
            self.register(abbr, oid)

    def resolve(self, abbreviation: str) -> Optional[str]:
        """
        Resolve a law abbreviation to its official ID.

        Case-insensitive lookup with common normalization.
        Falls back to stripping a trailing year (e.g. "Arbeidswet 1963" → "Arbeidswet").
        """
        if abbreviation in self._abbreviations:
            return self._abbreviations[abbreviation]

        lower = abbreviation.lower()
        for key, val in self._abbreviations.items():
            if key.lower() == lower:
                return val

        cleaned = re.sub(r"[\s.]+", "", abbreviation)
        for key, val in self._abbreviations.items():
            if re.sub(r"[\s.]+", "", key) == cleaned:
                return val

        # Strip trailing year (e.g. "Arbeidswet 1963" → "Arbeidswet")
        without_year = re.sub(r"\s+\d{4}$", "", abbreviation.strip())
        if without_year != abbreviation:
            result = self.resolve(without_year)
            if result:
                return result

        return None

    def get_abbreviations(self, official_id: str) -> List[str]:
        """Get all abbreviations for an official ID."""
        return self._reverse.get(official_id, [])

    def all_keys(self) -> List[str]:
        """Return all registered abbreviation keys, longest first."""
        return sorted(self._abbreviations.keys(), key=len, reverse=True)

    @property
    def count(self) -> int:
        return len(self._abbreviations)


# Default Suriname abbreviation registry
_suriname_registry = AbbreviationRegistry()
_suriname_registry.register_many({
    # ── Core codes ────────────────────────────────────────────────────────
    "Arbeidswet": "arbeidswet-1963",
    "Arbeidswet 1963": "arbeidswet-1963",
    "AW": "arbeidswet-1963",
    "Grondwet": "grondwet-1987",
    "Grondwet 1987": "grondwet-1987",
    "GW": "grondwet-1987",
    # Wetboek van Strafrecht
    "WvSr": "wetboek-van-strafrecht",
    "Sr": "wetboek-van-strafrecht",
    "Wetboek van Strafrecht": "wetboek-van-strafrecht",
    "Strafrecht": "wetboek-van-strafrecht",
    # Wetboek van Strafvordering
    "WvSv": "wetboek-van-strafvordering",
    "Sv": "wetboek-van-strafvordering",
    "Wetboek van Strafvordering": "wetboek-van-strafvordering",
    # Burgerlijk Wetboek
    "BW": "burgerlijk-wetboek",
    "Burgerlijk Wetboek": "burgerlijk-wetboek",
    # Wetboek van Koophandel
    "WvK": "wetboek-van-koophandel",
    "WK": "wetboek-van-koophandel",
    "Wetboek van Koophandel": "wetboek-van-koophandel",
    # ── Civil / family law ────────────────────────────────────────────────
    "Hw": "huwelijkswet",
    "Huwelijkswet": "huwelijkswet",
    "Pw": "personeelswet",
    "Personeelswet": "personeelswet",
    "Vreemdelingenwet": "vreemdelingenwet",
    "VW": "vreemdelingenwet",
    "Onteigeningswet": "onteigeningswet",
    "OW": "onteigeningswet",
    # ── Electoral / constitutional ────────────────────────────────────────
    "Kieswet": "kieswet",
    "KW": "kieswet",
    "Kiesregeling": "kiesregeling",
    "KR": "kiesregeling",
    # ── Labour / employment ───────────────────────────────────────────────
    "Vacantiewet": "vacantiewet-1975",
    "Vacantiewet 1975": "vacantiewet-1975",
    "Wet Minimumloon": "wet-minimumloon",
    "Minimumloonwet": "wet-minimumloon",
    # ── Tax law ───────────────────────────────────────────────────────────
    "Inkomstenbelasting": "inkomstenbelasting-1922",
    "Inkomstenbelasting 1922": "inkomstenbelasting-1922",
    "IB": "inkomstenbelasting-1922",
    "Wet Loonbelasting": "wet-loonbelasting",
    "Loonbelasting": "wet-loonbelasting",
    "LB": "wet-loonbelasting",
    "Zegelwet": "zegelwet",
    "ZW": "zegelwet",
    "Wet BTW": "wet-btw",
    "BTW-wet": "wet-btw",
    "BTWS": "wet-btw",
    "Wet BTW 2022": "wet-btw",
    # ── Finance / banking ─────────────────────────────────────────────────
    "Bankwet": "bankwet-1956",
    "Bankwet 1956": "bankwet-1956",
    "Comptabiliteitsbesluit": "comptabiliteitsbesluit",
    "Deviezenwet": "deviezenwet",
    "Wet Staatsschuld": "wet-staatsschuld",
    "Wet op de Staatsschuld": "wet-staatsschuld",
    # ── Criminal / enforcement ────────────────────────────────────────────
    "WED": "wet-economische-delicten",
    "Wet Economische Delicten": "wet-economische-delicten",
    "Wet op de Economische Delicten": "wet-economische-delicten",
    # ── Transport / insurance ─────────────────────────────────────────────
    "WAM": "wet-aansprakelijkheidsverzekering-motorrijtuigen",
    "Wet Aansprakelijkheidsverzekering Motorrijtuigen": "wet-aansprakelijkheidsverzekering-motorrijtuigen",
    # ── Property / land ───────────────────────────────────────────────────
    "Stedebouwkundige Wet": "stedebouwkundige-wet",
    "Stedebouwkundige wet": "stedebouwkundige-wet",
    "Decreet Uitgifte Domeingrond": "decreet-uitgifte-domeingrond",
    "DUD": "decreet-uitgifte-domeingrond",
    "Waterschapswet": "waterschapswet-1931",
    "Waterschapswet 1931": "waterschapswet-1931",
    # ── Commerce / trade ─────────────────────────────────────────────────
    "Marktwet": "marktwet",
    "Wet Kamers van Koophandel": "wet-kamers-van-koophandel",
    "KvK-wet": "wet-kamers-van-koophandel",
    "Wet Cooperatieve Verenigingen": "wet-cooperatieve-verenigingen",
    "Wet Coöperatieve Verenigingen": "wet-cooperatieve-verenigingen",
    "Coöperatiewet": "wet-cooperatieve-verenigingen",
    # ── Energy / resources ────────────────────────────────────────────────
    "Petroleumwet": "petroleumwet-1990",
    "Petroleumwet 1990": "petroleumwet-1990",
    "Elektriciteitswet": "elektriciteitswet-2016",
    "Elektriciteitswet 2016": "elektriciteitswet-2016",
    "EW": "elektriciteitswet-2016",
    "Concessiewet": "concessiewet",
    # ── Public administration ─────────────────────────────────────────────
    "Wet Regionale Organen": "wet-regionale-organen",
    "WRO": "wet-regionale-organen",
    "Wet Staatsraad": "wet-staatsraad",
    "Wet op de Staatsraad": "wet-staatsraad",
    "Personeelswet": "personeelswet",
    "Archiefwet": "archiefwet-2006",
    "Archiefwet 2006": "archiefwet-2006",
    # ── Civil status / nationality ────────────────────────────────────────
    "Wet Surinamerschap": "wet-surinamerschap",
    "Wet Surinamerschap en Ingezetenschap": "wet-surinamerschap",
    "WSI": "wet-surinamerschap",
    "Wet Uitvoering Burgerlijke Stand": "wet-uitvoering-burgerlijke-stand",
    "WUBS": "wet-uitvoering-burgerlijke-stand",
    # ── IP / media ────────────────────────────────────────────────────────
    "Auteursrecht": "wet-auteursrecht-1913",
    "Auteursrechtwet": "wet-auteursrecht-1913",
    "Wet Auteursrecht": "wet-auteursrecht-1913",
    "Wet Auteursrecht 1913": "wet-auteursrecht-1913",
    # ── Gambling / gaming ─────────────────────────────────────────────────
    "Wet toezicht kansspelen": "wet-toezicht-kansspelen",
    "Wet Toezicht en Controle Kansspelen": "wet-toezicht-kansspelen",
    # ── Health ────────────────────────────────────────────────────────────
    "Wet Medisch Tuchtrecht": "wet-medisch-tuchtrecht",
    # ── Legal profession ──────────────────────────────────────────────────
    "Advocatenwet": "advocatenwet",
    "Advocatenbesluit": "advocatenbesluit",
})


def get_default_registry() -> AbbreviationRegistry:
    """Get the default Suriname abbreviation registry."""
    return _suriname_registry


def resolve_abbreviation(abbreviation: str) -> Optional[str]:
    """Resolve a law abbreviation using the default registry."""
    return _suriname_registry.resolve(abbreviation)


# ---------------------------------------------------------------------------
# ELI-style URI builders
# ---------------------------------------------------------------------------

def eli_uri(
    jurisdiction: str,
    doc_type: str,
    slug: str,
    version_date: Optional[date] = None,
    article: Optional[str] = None,
    paragraph: Optional[str] = None,
    sub: Optional[str] = None,
) -> str:
    """
    Build an ELI-style URI for legislation.

    Examples:
        eli_uri("sr", "law", "arbeidswet-1963")
        -> "/id/law/sr-arbeidswet-1963"

        eli_uri("sr", "law", "arbeidswet-1963", article="2")
        -> "/id/law/sr-arbeidswet-1963/{today}/article/2"
    """
    official_id = f"{jurisdiction}-{slug}"
    scheme = URIScheme.LAW if doc_type == "law" else URIScheme(doc_type)

    fp = None
    if article is not None:
        fp = FragmentPath()
        fp.append("article", article)
        if paragraph is not None:
            fp.append("paragraph", paragraph)
        if sub is not None:
            fp.append("sub", sub)

    return build_uri(scheme, official_id, version_date=version_date, fragment_path=fp).to_uri()


def law_work_uri(official_id: str) -> str:
    """Build a Work URI for legislation."""
    return build_uri(URIScheme.LAW, official_id).to_uri()


def law_expression_uri(official_id: str, version_date: date) -> str:
    """Build an Expression URI for legislation at a given date."""
    return build_uri(URIScheme.LAW, official_id, version_date=version_date).to_uri()


def law_fragment_uri(
    official_id: str,
    version_date: date,
    article: str,
    paragraph: Optional[str] = None,
    sub: Optional[str] = None,
) -> str:
    """
    Build a Fragment URI for a specific article/paragraph/sub.

    Examples:
        law_fragment_uri("sr-arbeidswet-1963", date(2024,1,15), "2")
        -> "/id/law/sr-arbeidswet-1963/2024-01-15/article/2"
    """
    fp = FragmentPath()
    fp.append("article", article)
    if paragraph is not None:
        fp.append("paragraph", paragraph)
    if sub is not None:
        fp.append("sub", sub)
    return build_uri(URIScheme.LAW, official_id, version_date, fragment_path=fp).to_uri()


def ecli_work_uri(ecli: str) -> str:
    """Build a Work URI for case law."""
    return build_uri(URIScheme.ECLI, ecli).to_uri()


def ecli_fragment_uri(ecli: str, fragment_type: str, number: str = "") -> str:
    """
    Build a Fragment URI for case law.

    Examples:
        ecli_fragment_uri("ECLI:NL:HR:2020:1234", "consideration", "4.3")
        -> "/id/ecli/ECLI:NL:HR:2020:1234/consideration/4.3"
    """
    fp = FragmentPath()
    fp.append(fragment_type, number)
    return build_uri(URIScheme.ECLI, ecli, fragment_path=fp).to_uri()


def celex_work_uri(celex: str) -> str:
    """Build a Work URI for EU legislation."""
    return build_uri(URIScheme.CELEX, celex).to_uri()


GAZETTE_CODES = {"staatsblad": "sb", "gouvernementsblad": "gb"}


def gazette_code(gazette_type: str, year: str = "") -> str:
    """'sb' (Staatsblad) or 'gb' (Gouvernementsblad) for a gazette type name or code.
    Unknown types fall back on the year: the Staatsblad replaced the Gouvernementsblad
    at independence in 1975."""
    t = (gazette_type or "").strip().lower().replace(".", "").replace(" ", "")
    if t in ("sb", "gb"):
        return t
    if t in GAZETTE_CODES:
        return GAZETTE_CODES[t]
    return "gb" if year.isdigit() and int(year) < 1975 else "sb"


def gazette_uri(gazette: str, year: str, number: str) -> str:
    """Build a URI for a gazette issue: /id/gazette/sb-2024-123 (Staatsblad) or
    /id/gazette/gb-1963-163 (Gouvernementsblad). `gazette` is 'sb', 'gb' or a type name."""
    official_id = f"{gazette_code(gazette, year)}-{year}-{number}"
    return f"{URI_PREFIX}/{URIScheme.GAZETTE.value}/{official_id}"


def parliamentary_uri(jurisdiction: str, doc_id: str) -> str:
    """Build a URI for a parliamentary document."""
    official_id = f"{jurisdiction}-{doc_id}"
    return f"{URI_PREFIX}/{URIScheme.PARLIAMENTARY.value}/{official_id}"


def treaty_uri(treaty_id: str) -> str:
    """Build a URI for a treaty."""
    return f"{URI_PREFIX}/{URIScheme.TREATY.value}/{treaty_id}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_official_id(
    scheme: URIScheme, parts: List[str]
) -> Tuple[str, List[str]]:
    """
    Extract the official_id from the remaining URI parts.

    ECLI contains colons, but they arrive as a single URL segment
    because colons don't split on /.
    """
    if not parts:
        raise ValueError("URI missing official_id after scheme")

    official_id = parts[0]
    rest = parts[1:]

    return official_id, rest


def _try_parse_date(segment: str) -> Tuple[Optional[date], bool]:
    """Try to parse a URI segment as an ISO date (YYYY-MM-DD)."""
    try:
        return date.fromisoformat(segment), True
    except (ValueError, TypeError):
        return None, False


def _get_pattern_hint(scheme: URIScheme) -> str:
    hints = {
        URIScheme.ECLI: "ECLI:CC:COURT:YEAR:NUMBER (e.g. ECLI:NL:HR:2020:1234)",
        URIScheme.CELEX: "digit + 4-digit year + letter + digits (e.g. 32016R0679)",
        URIScheme.LAW: "jurisdiction-slug (e.g. sr-arbeidswet-1963)",
        URIScheme.GAZETTE: "gazette-year-number, sb or gb (e.g. sb-2024-123, gb-1963-163)",
    }
    return hints.get(scheme, "free-form identifier")
