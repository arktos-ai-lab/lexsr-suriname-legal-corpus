"""
Case-law ingestion pipeline: turns a scraped rechtspraak.sr decision into
FRBR-compatible Work/Fragment rows plus citation links, using the existing
core data model and citation resolver (no schema changes — case-specific
fields live in `metadata`, exactly like every other source type).

Decisions are stored as Work rows with source_type=CASE_LAW; considerations
(numbered paragraphs, "2.1", "2.2", ...) as Fragment rows with
fragment_type=CONSIDERATION; citations (case -> legislation, case -> case)
as rows in the existing generic `citations` table.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from ..core.models import (
    Work, Expression, Fragment, FragmentType, SourceType, DocumentStatus,
    Citation, CitationType, TemporalExtent,
)
from .scrapers.base import ScrapedDocument

logger = logging.getLogger(__name__)

# Considerations are numbered "N.N" (section.paragraph), e.g. "2.1", "4.7".
_CONSIDERATION_RE = re.compile(r"(?:^|\n)(\d{1,2}\.\d{1,2})\s+(.*?)(?=\n\d{1,2}\.\d{1,2}\s|\Z)", re.S)

# Decisions without "N.N" numbering come in two layouts (headings are short lines):
#  - recent: a "De beoordeling" heading, paragraphs "1.", "2.", ... up to "De beslissing";
#  - older:  a "Ten aanzien van het recht" heading, paragraphs opening with
#            "Overwegende, dat" (or plain "dat"), up to the dictum ("Rechtdoende").
_BEOORDELING_RE = re.compile(r"^[ \t]*(?:\d\.?[ \t]+)?(?:de[ \t]+)?(?:verdere[ \t]+)?beoordeling\b.{0,50}$",
                             re.I | re.M)
_RECHT_RE = re.compile(r"^[ \t]*(?:overwegende[ \t,]+)?(?:ten[ \t]+aanzien[ \t]+van|t\.[ \t]*a\.[ \t]*v\.)"
                       r"[ \t]+het[ \t]+recht\b.{0,30}$", re.I | re.M)
_DICTUM_RE = re.compile(
    r"^[ \t]*(?:de[ \t]+beslissing|beslissing\b|rechtdoende|beslissende|"
    r"(?:gezien|gelet[ \t]+op)[ \t]+(?:de[ \t]+)?(?:betrekkelijke|toepasselijke)[ \t]+wetsartikelen).{0,50}$",
    re.I | re.M)
# "1.", "1.1", "1.1.", "3.3.3." (a bare "12" is a date or amount, not a number)
_PLAIN_NUM_RE = re.compile(r"^[ \t]*(\d{1,2}\.(?:\d{1,2}\.?){0,2}|\d{1,2}(?:\.\d{1,2}){1,2})[ \t]+(?=\S)", re.M)
_OVERWEGENDE_RE = re.compile(r"^[ \t]*overwegende\b", re.I | re.M)
_DAT_RE = re.compile(r"^[ \t]*dat\b", re.M)


def strip_site_header(text: str) -> str:
    """Drop the rechtspraak.sr page header (Instantie, Zaaknummer, ..., Inhoudsindicatie)
    that precedes the decision; those fields are already stored in the work metadata."""
    if not text.lstrip().startswith("Instantie"):
        return text
    m = re.search(r"^[ \t]*Uitspraak[ \t]*$", text, re.M)
    return text[m.end():].lstrip("\n") if m else text


def _section(text: str, start: int) -> tuple:
    """(start, end) offsets from `start` up to the dictum heading, or the end of `text`."""
    end = _DICTUM_RE.search(text, start)
    return start, end.start() if end else len(text)


def _split_at(text: str, span: tuple, marker: re.Pattern, numbered: bool) -> List[tuple]:
    """Split text[span] at each line matching `marker`; text before the first marker is
    not part of any consideration. Returns (number, start, body_start, end) offsets,
    numbering by position unless `numbered`."""
    lo, hi = span
    hits = list(marker.finditer(text, lo, hi))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else hi
        body = m.end() if numbered else m.start()
        if text[body:end].strip():
            out.append((m.group(1).rstrip(".") if numbered else str(len(out) + 1), m.start(), body, end))
    return out


def consideration_spans(text: str) -> List[tuple]:
    """(number, start, body_start, end) offsets of a decision's considerations, or []."""
    found = [(m.group(1), m.start(1), m.start(2), m.end(2)) for m in _CONSIDERATION_RE.finditer(text)]
    if found:
        # the last consideration runs to the end of the text; stop it at the dictum
        n, s, b, e = found[-1]
        dictum = _DICTUM_RE.search(text, b, e)
        if dictum:
            found[-1] = (n, s, b, dictum.start())
        return found
    head = _BEOORDELING_RE.search(text)
    if head:
        parts = _split_at(text, _section(text, head.end()), _PLAIN_NUM_RE, numbered=True)
        if len(parts) >= 2:
            return parts
    head = _RECHT_RE.search(text)
    if head:
        # under the heading: numbered paragraphs, else "Overwegende" ones (one is enough),
        # else paragraphs opening with "dat"
        span = _section(text, head.end())
        for marker, numbered, least in ((_PLAIN_NUM_RE, True, 2), (_OVERWEGENDE_RE, False, 1),
                                        (_DAT_RE, False, 2)):
            parts = _split_at(text, span, marker, numbered)
            if len(parts) >= least:
                return parts
    elif len(_OVERWEGENDE_RE.findall(text)) >= 2:
        span = _section(text, _OVERWEGENDE_RE.search(text).start())
        return _split_at(text, span, _OVERWEGENDE_RE, numbered=False)
    return []


def split_considerations(text: str) -> List[tuple]:
    """(number, text) pairs for the considerations of a decision, or [] if none found."""
    return [(n, text[b:e].strip()) for n, _, b, e in consideration_spans(text)]

# Self-citations to other rechtspraak.sr decisions, e.g. "SRU-HvJ-2019-3".
_CASE_CITATION_RE = re.compile(r"\bSRU-[A-Za-z]{1,4}-\d{1,4}(?:-\d{1,4})?\b", re.IGNORECASE)


@dataclass
class CaseLawResult:
    work: Optional[Work] = None
    expression: Optional[Expression] = None
    fragments: List[Fragment] = field(default_factory=list)
    citations: List[Citation] = field(default_factory=list)
    success: bool = True
    errors: List[str] = field(default_factory=list)


def _slugify(sru_id: str) -> str:
    slug = sru_id.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug


class CaseLawPipeline:
    """
    Converts scraped rechtspraak.sr decisions into stored FRBR objects.

    Citations to legislation are NOT extracted here — after fragments are
    saved, run them through the existing `CitationExtractor` (the same one
    `lexsr link-citations` uses), which already knows how to resolve legal
    references against the canonical store. This pipeline only extracts
    case-to-case citations (SRU-id self-references), which nothing else
    handles.
    """

    def process_document(self, doc: ScrapedDocument) -> CaseLawResult:
        result = CaseLawResult()

        try:
            meta = doc.metadata or {}
            sru_id = meta.get("sru_id") or doc.title
            full_text = strip_site_header(meta.get("full_text", ""))
            if not full_text.strip():
                result.success = False
                result.errors.append("No decision text extracted")
                return result

            work_id = f"caselaw-{_slugify(sru_id)}"
            decision_date = _parse_iso_date(meta.get("decision_date"))
            publication_date = _parse_iso_date(meta.get("publication_date"))

            work = Work(
                id=work_id,
                uri=f"/id/caselaw/{_slugify(sru_id)}",
                source_type=SourceType.CASE_LAW,
                official_id=sru_id,
                title=meta.get("case_number") or sru_id,
                document_type="rechterlijke uitspraak",
                jurisdiction="SR",
                status=DocumentStatus.IN_FORCE,
                date_enacted=decision_date,
                source_url=doc.url,
                retrieved_at=datetime.combine(doc.scraped_at or date.today(), datetime.min.time()),
                metadata={
                    "sru_id": sru_id,
                    "court": meta.get("court", ""),
                    "case_number": meta.get("case_number", ""),
                    "legal_area": meta.get("legal_area", ""),
                    "abstract": meta.get("abstract", ""),
                    "decision_date": meta.get("decision_date"),
                    "publication_date": meta.get("publication_date"),
                },
            )
            result.work = work

            version_date = decision_date or publication_date or date.today()
            expression = Expression(
                id=work_id,
                uri=work.uri,
                work_id=work_id,
                work_uri=work.uri,
                version_date=version_date,
                in_force=TemporalExtent(start=version_date),
                title=work.title,
                status=DocumentStatus.IN_FORCE,
                source_url=doc.url,
            )
            result.expression = expression

            fragments = self._build_fragments(work_id, work.uri, full_text)

            if not fragments:
                # No numbered considerations detected; keep the whole
                # decision as a single fragment so the text isn't lost.
                fragments = [Fragment(
                    id=f"{work_id}-full",
                    uri=f"{work.uri}/text",
                    expression_id=work_id,
                    expression_uri=work.uri,
                    work_id=work_id,
                    fragment_type=FragmentType.CONSIDERATION,
                    number="1",
                    text=full_text,
                )]

            result.fragments = fragments
            for fragment in fragments:
                result.citations.extend(self._extract_case_citations(work, fragment))

        except Exception as exc:
            logger.error(f"Case-law pipeline failed for {doc.url}: {exc}", exc_info=True)
            result.success = False
            result.errors.append(str(exc))

        return result

    @staticmethod
    def _build_fragments(work_id: str, work_uri: str, full_text: str) -> List[Fragment]:
        """Considerations, plus the text before them (parties, procedure, facts) as a
        FACTS fragment and the text after them (dictum, signatures) as a DECISION
        fragment, so no part of the decision is dropped."""
        spans = consideration_spans(full_text)
        if not spans:
            return []

        def frag(fid, uri, ftype, number, text):
            return Fragment(id=fid, uri=uri, expression_id=work_id, expression_uri=work_uri,
                            work_id=work_id, fragment_type=ftype, number=number, text=text,
                            position=len(fragments))

        fragments: List[Fragment] = []
        before = full_text[:spans[0][1]].strip()
        if before:
            fragments.append(frag(f"{work_id}-facts", f"{work_uri}/facts", FragmentType.FACTS, "", before))
        seen: dict = {}
        for number, _, body, end in spans:
            text = full_text[body:end].strip()
            if not text:
                continue
            # A decision can restart its numbering (e.g. a second "2.1" under a
            # later heading); suffix repeats so they don't overwrite each other.
            seen[number] = seen.get(number, 0) + 1
            key = number if seen[number] == 1 else f"{number}-{seen[number]}"
            fragments.append(frag(f"{work_id}-ro{key.replace('.', '-')}", f"{work_uri}/consideration/{key}",
                                  FragmentType.CONSIDERATION, number, text))
        after = full_text[spans[-1][3]:].strip()
        if after:
            fragments.append(frag(f"{work_id}-decision", f"{work_uri}/decision", FragmentType.DECISION, "", after))
        return fragments

    @staticmethod
    def _extract_case_citations(work: Work, fragment: Fragment) -> List[Citation]:
        """Self-citations to other decisions (SRU-XXX-YYYY-N mentions)."""
        citations: List[Citation] = []

        for m in _CASE_CITATION_RE.finditer(fragment.text):
            raw = m.group(0)
            target_work_id = f"caselaw-{_slugify(raw)}"
            if target_work_id == work.id:
                continue  # a decision citing its own SRU id (header, etc.)
            citations.append(Citation(
                id=_citation_id(fragment.id, target_work_id, "case_to_case"),
                citation_type=CitationType.CASE_TO_CASE,
                source_work_id=work.id,
                source_fragment_id=fragment.id,
                target_work_id=target_work_id,
                raw_text=raw,
                confidence=0.85,
            ))

        return citations


def _parse_iso_date(raw: Optional[str]) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _citation_id(source_fragment_id: str, target: str, citation_type: str) -> str:
    return hashlib.sha1(f"{citation_type}:{source_fragment_id}:{target}".encode()).hexdigest()[:16]
