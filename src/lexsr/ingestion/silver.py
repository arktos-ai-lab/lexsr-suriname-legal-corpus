"""
Silver layer: canonical document processing.

Transforms raw Bronze records into structured Works, Expressions, and Fragments
with canonical URIs and metadata.

Uses a registry-based dispatch pattern: each source type registers a processing
function via @SilverProcessor.register, keeping the core class minimal and
making it trivial to add new source types.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional
from xml.etree import ElementTree

from ..core.identifiers import (
    URI_PREFIX,
    URIScheme,
    celex_work_uri,
    ecli_work_uri,
    gazette_code,
    gazette_uri,
    law_expression_uri,
    law_fragment_uri,
    law_work_uri,
)
from ..core.models import (
    DocumentStatus,
    Expression,
    Fragment,
    FragmentAnchor,
    FragmentType,
    SourceType,
    TemporalExtent,
    Work,
)
from .bronze import BronzeRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Processing result
# ---------------------------------------------------------------------------

@dataclass
class ProcessingResult:
    """Result of processing a Bronze record into Silver objects."""

    bronze_id: str
    source_type: str
    work: Optional[Work] = None
    expressions: List[Expression] = field(default_factory=list)
    fragments: List[Fragment] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.work is not None and len(self.errors) == 0


# ---------------------------------------------------------------------------
# Type alias for registered processor functions
# ---------------------------------------------------------------------------

ProcessorFunc = Callable[["SilverProcessor", BronzeRecord, ProcessingResult], None]


# ---------------------------------------------------------------------------
# Registry-based processor
# ---------------------------------------------------------------------------

class SilverProcessor:
    """
    Processes Bronze records into canonical Silver objects.

    Source-type handlers are registered via the ``@SilverProcessor.register``
    decorator.  The ``process()`` method dispatches to the right handler at
    runtime.
    """

    _REGISTRY: Dict[str, ProcessorFunc] = {}

    @staticmethod
    def register(source_type: str):
        """Class-level decorator that binds a handler to a *source_type* string."""
        def decorator(func: ProcessorFunc) -> ProcessorFunc:
            SilverProcessor._REGISTRY[source_type] = func
            return func
        return decorator

    # -- public API ----------------------------------------------------------

    def process(self, record: BronzeRecord) -> ProcessingResult:
        """Process a single Bronze record into Silver objects."""
        result = ProcessingResult(
            bronze_id=record.id,
            source_type=record.source_type,
        )
        handler = self._REGISTRY.get(record.source_type)
        if handler is None:
            result.errors.append(f"Unsupported source type: {record.source_type}")
            return result
        try:
            handler(self, record, result)
        except Exception as exc:
            result.errors.append(f"Processing error: {exc}")
            logger.exception("Failed to process Bronze record %s", record.id)
        return result

    # -- shared helpers ------------------------------------------------------

    @staticmethod
    def _meta_date(meta: Dict[str, Any], key: str = "date") -> date:
        """Extract an ISO date from metadata, falling back to today."""
        raw = meta.get(key, "")
        return date.fromisoformat(raw) if raw else date.today()

    @staticmethod
    def _retrieved_dt(record: BronzeRecord) -> datetime:
        return datetime.fromisoformat(record.retrieved_at)

    @staticmethod
    def _full_text(record: BronzeRecord, cap: int = 100_000) -> str:
        if record.content:
            return record.content.decode("utf-8", errors="replace")[:cap]
        return ""


# ---------------------------------------------------------------------------
# Registered handlers
# ---------------------------------------------------------------------------

@SilverProcessor.register(SourceType.LEGISLATION.value)
def _process_legislation(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """Generic legislation processor (laws, acts, decrees)."""
    meta = record.metadata
    official_id = meta.get("official_id", "")
    if not official_id:
        result.errors.append("Missing official_id in metadata")
        return

    title = meta.get("title", f"Law {official_id}")
    work_id = f"w-{official_id.lower()}"

    result.work = Work(
        id=work_id,
        uri=law_work_uri(official_id),
        source_type=SourceType.LEGISLATION,
        official_id=official_id,
        title=title,
        short_title=meta.get("short_title"),
        document_type=meta.get("document_type", "law"),
        jurisdiction=meta.get("jurisdiction", "SR"),
        status=DocumentStatus.IN_FORCE,
        source_url=record.source_url,
        source_api=meta.get("source_api"),
        retrieved_at=proc._retrieved_dt(record),
        metadata={"bronze_id": record.id},
    )

    version_date = proc._meta_date(meta, "version_date")
    expr_id = f"e-{official_id.lower()}-{version_date.isoformat()}"
    expr = Expression(
        id=expr_id,
        uri=law_expression_uri(official_id, version_date),
        work_id=work_id,
        work_uri=result.work.uri,
        version_date=version_date,
        in_force=TemporalExtent(start=version_date),
        status=DocumentStatus.IN_FORCE,
        source_url=record.source_url,
        retrieved_at=proc._retrieved_dt(record),
        checksum=record.checksum_sha256,
        metadata={"bronze_id": record.id},
    )
    result.expressions.append(expr)

    # Extract article fragments from XML content when available
    if record.content and record.content_type == "application/xml":
        _extract_xml_articles(
            record.content, official_id, version_date,
            work_id, expr_id, expr.uri, result,
        )


def _extract_xml_articles(
    content: bytes,
    official_id: str,
    version_date: date,
    work_id: str,
    expr_id: str,
    expr_uri: str,
    result: ProcessingResult,
) -> None:
    """Extract article fragments from legislation XML."""
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        result.errors.append(f"XML parse error: {exc}")
        return

    ns_prefix = ""
    if "}" in root.tag:
        ns_prefix = f"{{{root.tag.split('}')[0].strip('{')}}}"

    for pos, article_el in enumerate(root.iter(f"{ns_prefix}article")):
        nummer = article_el.get("number", article_el.get("nr", ""))
        if not nummer:
            continue

        text_parts = []
        for elem in article_el.iter():
            if elem.text:
                text_parts.append(elem.text.strip())
            if elem.tail:
                text_parts.append(elem.tail.strip())
        text = " ".join(t for t in text_parts if t)

        frag_id = f"f-{official_id.lower()}-{version_date.isoformat()}-art{nummer}"
        result.fragments.append(Fragment(
            id=frag_id,
            uri=law_fragment_uri(official_id, version_date, nummer),
            expression_id=expr_id,
            expression_uri=expr_uri,
            work_id=work_id,
            fragment_type=FragmentType.ARTICLE,
            number=nummer,
            title=f"Article {nummer}",
            position=pos,
            text=text,
            anchors=[FragmentAnchor.from_structural(
                f"/legislation/article[@nr='{nummer}']",
                text=text,
            )],
        ))


@SilverProcessor.register(SourceType.GAZETTE.value)
def _process_gazette(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """Gazette (official journal) processor."""
    meta = record.metadata
    jurisdiction = meta.get("jurisdiction", "sr")
    year = meta.get("year", "")
    number = meta.get("number", "")
    if not year or not number:
        result.errors.append("Missing year or number in metadata")
        return

    gazette = gazette_code(meta.get("gazette", ""), str(year))
    official_id = f"{gazette}-{year}-{number}"
    title = meta.get("title", f"{gazette.upper()[0]}.B. {year} no. {number}")
    work_id = f"w-gazette-{official_id}"

    result.work = Work(
        id=work_id,
        uri=gazette_uri(gazette, year, number),
        source_type=SourceType.GAZETTE,
        official_id=official_id,
        title=title,
        document_type="gazette",
        jurisdiction=jurisdiction.upper(),
        source_url=record.source_url,
        retrieved_at=proc._retrieved_dt(record),
        metadata={"bronze_id": record.id},
    )

    pub_date = proc._meta_date(meta)
    result.expressions.append(Expression(
        id=f"e-gazette-{official_id}",
        uri=gazette_uri(gazette, year, number),
        work_id=work_id,
        work_uri=result.work.uri,
        version_date=pub_date,
        source_url=record.source_url,
        retrieved_at=proc._retrieved_dt(record),
        checksum=record.checksum_sha256,
        metadata={"bronze_id": record.id},
    ))


@SilverProcessor.register(SourceType.CASE_LAW.value)
def _process_case_law(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """Generic case-law processor."""
    meta = record.metadata
    ecli = meta.get("ecli", "")
    if not ecli:
        result.errors.append("Missing ecli in metadata")
        return

    title = meta.get("title", ecli)
    work_id = f"w-{ecli.lower().replace(':', '-')}"

    result.work = Work(
        id=work_id,
        uri=ecli_work_uri(ecli),
        source_type=SourceType.CASE_LAW,
        official_id=ecli,
        title=title,
        document_type=meta.get("type", "judgment"),
        source_url=record.source_url,
        retrieved_at=proc._retrieved_dt(record),
        metadata={"bronze_id": record.id},
    )

    pub_date = proc._meta_date(meta)
    result.expressions.append(Expression(
        id=f"e-{ecli.lower().replace(':', '-')}",
        uri=ecli_work_uri(ecli),
        work_id=work_id,
        work_uri=result.work.uri,
        version_date=pub_date,
        full_text=proc._full_text(record),
        source_url=record.source_url,
        retrieved_at=proc._retrieved_dt(record),
        checksum=record.checksum_sha256,
        metadata={"bronze_id": record.id},
    ))


@SilverProcessor.register(SourceType.ECLI.value)
def _process_ecli(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """ECLI case law -- delegates to the generic case_law handler."""
    _process_case_law(proc, record, result)


@SilverProcessor.register(SourceType.PDF_SCAN.value)
def _process_pdf_scan(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """Stub for scanned-PDF pipeline (OCR required)."""
    result.errors.append(
        "pdf_scan processing requires the OCR pipeline (not yet implemented)"
    )


@SilverProcessor.register(SourceType.PDF_DIGITAL.value)
def _process_pdf_digital(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """Stub for born-digital PDF text extraction."""
    result.errors.append(
        "pdf_digital processing requires the PDF extraction pipeline "
        "(not yet implemented)"
    )


@SilverProcessor.register(SourceType.XML_SOURCE.value)
def _process_xml_source(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """XML source -- delegates to legislation with xml content type."""
    _process_legislation(proc, record, result)


@SilverProcessor.register(SourceType.HTML_SOURCE.value)
def _process_html_source(
    proc: SilverProcessor, record: BronzeRecord, result: ProcessingResult
) -> None:
    """HTML source -- delegates to legislation (fragment extraction skipped)."""
    _process_legislation(proc, record, result)
