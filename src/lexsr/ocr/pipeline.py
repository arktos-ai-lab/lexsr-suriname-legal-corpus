"""
End-to-end OCR pipeline for legal documents.

Orchestrates:
1. PDF extraction (digital or OCR)
2. Structure detection (articles, paragraphs)
3. FRBR model conversion
4. Bronze layer storage
5. Optional search indexing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core.models import (
    Work,
    Expression,
    Fragment,
    FragmentType,
    SourceType,
    DocumentStatus,
    TemporalExtent,
)
from ..core.identifiers import law_fragment_uri
from .extractor import PDFExtractor, OCREngine, OCRResult
from .detector import StructureDetector, DetectedArticle, DetectedStructure

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of OCR pipeline processing."""
    work: Work
    expression: Expression
    fragments: List[Fragment]
    ocr_results: List[OCRResult]
    structure: DetectedStructure
    success: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """Configuration for OCR pipeline."""
    # OCR settings
    ocr_engine: OCREngine = OCREngine.TESSERACT
    tesseract_lang: str = "nld+eng"
    confidence_threshold: float = 0.5

    # Structure detection settings
    min_article_confidence: float = 0.6
    detect_amendments: bool = True

    # FRBR conversion settings
    default_jurisdiction: str = "SR"
    default_language: str = "nl"
    default_source_type: SourceType = SourceType.LEGISLATION

    # Processing options
    extract_paragraphs: bool = True
    preserve_raw_ocr: bool = True
    validate_structure: bool = True


class OCRPipeline:
    """
    End-to-end pipeline for processing legal PDFs.

    Usage:
        pipeline = OCRPipeline(config=PipelineConfig())
        result = pipeline.process_pdf(
            pdf_path="data/raw/arbeidswet.pdf",
            work_id="arbeidswet-1963",
            title="Arbeidswet 1963",
            version_date=date(2024, 1, 15)
        )

        # Save to store
        store.save_work(result.work)
        store.save_expression(result.expression)
        for fragment in result.fragments:
            store.save_fragment(fragment)
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        extractor: Optional[PDFExtractor] = None,
        detector: Optional[StructureDetector] = None,
    ):
        """
        Initialize OCR pipeline.

        Args:
            config: Pipeline configuration
            extractor: Custom PDF extractor (optional)
            detector: Custom structure detector (optional)
        """
        self.config = config or PipelineConfig()

        # Initialize components
        self.extractor = extractor or PDFExtractor(
            prefer_engine=self.config.ocr_engine,
            tesseract_lang=self.config.tesseract_lang,
            confidence_threshold=self.config.confidence_threshold,
        )

        self.detector = detector or StructureDetector(
            min_article_confidence=self.config.min_article_confidence,
        )

    def process_pdf(
        self,
        pdf_path: str,
        work_id: str,
        title: str,
        version_date: date,
        official_id: Optional[str] = None,
        pages: Optional[List[int]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> PipelineResult:
        """
        Process a PDF through complete pipeline.

        Args:
            pdf_path: Path to PDF file
            work_id: Unique identifier for work
            title: Document title
            version_date: Version/publication date
            official_id: Official document ID (e.g., SB 2024-12)
            pages: Optional specific pages to process
            metadata: Additional metadata

        Returns:
            PipelineResult with Work, Expression, and Fragments
        """
        pdf_path = Path(pdf_path)
        logger.info(f"Processing PDF: {pdf_path}")

        result = PipelineResult(
            work=None,  # Will be created
            expression=None,
            fragments=[],
            ocr_results=[],
            structure=None,
            metadata=metadata or {},
        )

        try:
            # Step 1: Extract text from PDF
            logger.info("Step 1: Extracting text from PDF...")
            ocr_results = self.extractor.extract(str(pdf_path), pages=pages)
            result.ocr_results = ocr_results

            if not ocr_results:
                result.success = False
                result.errors.append("No text extracted from PDF")
                return result

            # Combine all page text
            full_text = "\n\n".join(r.text for r in ocr_results)
            avg_confidence = sum(r.confidence for r in ocr_results) / len(ocr_results)

            logger.info(f"Extracted {len(full_text)} chars from {len(ocr_results)} pages "
                       f"(avg confidence: {avg_confidence:.2f})")

            # Step 2: Detect document structure
            logger.info("Step 2: Detecting document structure...")
            structure = self.detector.detect(full_text)
            result.structure = structure

            if not structure.articles:
                result.warnings.append("No articles detected in document")

            logger.info(f"Detected {len(structure.articles)} articles "
                       f"(confidence: {structure.confidence:.2f})")

            # Step 3: Create FRBR models
            logger.info("Step 3: Creating FRBR models...")
            work, expression, fragments = self._create_frbr_models(
                work_id=work_id,
                title=title,
                version_date=version_date,
                official_id=official_id or work_id,
                structure=structure,
                ocr_results=ocr_results,
            )

            result.work = work
            result.expression = expression
            result.fragments = fragments

            # Citation extraction needs a CanonicalStore to resolve references
            # against, which isn't available mid-pipeline (fragments aren't
            # stored yet) — that's what `lexsr link-citations` is for, run
            # after storage. An in-pipeline attempt used to run here with
            # CitationExtractor(store=None), which can only ever return an
            # empty list; it was pure overhead (one warning log per fragment,
            # every single ingestion run) with no effect.

            # Step 4: Validate (optional)
            if self.config.validate_structure:
                logger.info("Step 4: Validating structure...")
                validation_errors = self._validate_result(result)
                if validation_errors:
                    result.warnings.extend(validation_errors)

            # Store metadata
            result.metadata.update({
                "pdf_path": str(pdf_path),
                "total_pages": len(ocr_results),
                "total_articles": len(structure.articles),
                "avg_ocr_confidence": avg_confidence,
                "structure_confidence": structure.confidence,
                "is_digital": all(r.is_digital for r in ocr_results),
                "ocr_engines": list(set(r.engine.value for r in ocr_results)),
            })

            logger.info(f"✓ Pipeline complete: {len(fragments)} fragments created")
            return result

        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            result.success = False
            result.errors.append(str(e))
            return result

    def process_batch(
        self,
        pdf_files: List[Tuple[str, str, str, date]],  # (path, work_id, title, version_date)
        save_to_store: Optional[Any] = None,
        index_search: bool = False,
    ) -> List[PipelineResult]:
        """
        Process multiple PDFs in batch.

        Args:
            pdf_files: List of (pdf_path, work_id, title, version_date) tuples
            save_to_store: Optional CanonicalStore to save results
            index_search: Whether to index in search after processing

        Returns:
            List of PipelineResults
        """
        results = []

        logger.info(f"Processing batch of {len(pdf_files)} PDFs...")

        for i, (pdf_path, work_id, title, version_date) in enumerate(pdf_files, 1):
            logger.info(f"[{i}/{len(pdf_files)}] Processing {work_id}...")

            result = self.process_pdf(
                pdf_path=pdf_path,
                work_id=work_id,
                title=title,
                version_date=version_date,
            )

            if result.success:
                # Save to store if provided
                if save_to_store:
                    try:
                        save_to_store.save_work(result.work)
                        save_to_store.save_expression(result.expression)
                        for fragment in result.fragments:
                            save_to_store.save_fragment(fragment)
                        logger.info(f"  ✓ Saved to store")
                    except Exception as e:
                        logger.error(f"  ✗ Store save failed: {e}")
                        result.warnings.append(f"Store save failed: {e}")

                # Index in search if requested
                if index_search:
                    try:
                        self._index_for_search(result)
                        logger.info(f"  ✓ Indexed for search")
                    except Exception as e:
                        logger.error(f"  ✗ Search indexing failed: {e}")
                        result.warnings.append(f"Search indexing failed: {e}")
            else:
                logger.error(f"  ✗ Processing failed: {result.errors}")

            results.append(result)

        successful = sum(1 for r in results if r.success)
        logger.info(f"Batch complete: {successful}/{len(results)} successful")

        return results

    def _create_frbr_models(
        self,
        work_id: str,
        title: str,
        version_date: date,
        official_id: str,
        structure: DetectedStructure,
        ocr_results: List[OCRResult],
    ) -> Tuple[Work, Expression, List[Fragment]]:
        """Convert detected structure to FRBR models."""

        # A detected heading that's implausibly short (misread OCR noise, a
        # stray page number, a single letter) is worse than the caller's own
        # title — using it anyway makes fuzzy title-matching (e.g. amendment
        # base-law resolution) match this work against nearly everything.
        detected_title = structure.title if structure.title and len(structure.title.strip()) >= 5 else None
        resolved_title = detected_title or title

        # Create Work
        work = Work(
            id=work_id,
            uri=f"/id/law/{work_id}",
            source_type=self.config.default_source_type,
            official_id=official_id,
            title=resolved_title,
            document_type="wet",
            jurisdiction=self.config.default_jurisdiction,
            language=self.config.default_language,
            status=DocumentStatus.IN_FORCE,
        )

        # Create Expression
        expression = Expression(
            id=f"{work_id}-{version_date.isoformat()}",
            uri=f"/id/law/{work_id}/{version_date.isoformat()}",
            work_id=work.id,
            work_uri=work.uri,
            version_date=version_date,
            title=resolved_title,
            status=DocumentStatus.IN_FORCE,
            in_force=TemporalExtent(start=version_date, end=None),
        )

        # Create Fragments from articles
        fragments = []

        for article in structure.articles:
            # Main article fragment
            fragment = Fragment(
                id=f"{work_id}-{version_date.isoformat()}-art{article.number}",
                uri=law_fragment_uri(work_id, version_date, article.number),
                expression_id=expression.id,
                expression_uri=expression.uri,
                work_id=work.id,
                fragment_type=FragmentType.ARTICLE,
                number=article.number,
                title=article.title,
                text=article.text,
                heading_path=article.heading_path,
            )
            fragments.append(fragment)

            # Extract paragraph fragments if configured
            if self.config.extract_paragraphs and len(article.paragraphs) > 1:
                for i, para_text in enumerate(article.paragraphs, 1):
                    para_fragment = Fragment(
                        id=f"{work_id}-{version_date.isoformat()}-art{article.number}-para{i}",
                        uri=f"{fragment.uri}/paragraph/{i}",
                        expression_id=expression.id,
                        expression_uri=expression.uri,
                        work_id=work.id,
                        fragment_type=FragmentType.PARAGRAPH,
                        number=str(i),
                        text=para_text,
                        parent_fragment_id=fragment.uri,
                    )
                    fragments.append(para_fragment)

        # Decrees without articles (e.g. "BESLUIT: I. Te bekrachtigen ...") are kept
        # whole rather than dropped
        full_text = "\n\n".join(r.text for r in ocr_results).strip()
        if not fragments and len(full_text) >= 200:
            fragments.append(Fragment(
                id=f"{work_id}-{version_date.isoformat()}-body",
                uri=f"{expression.uri}/body",
                expression_id=expression.id,
                expression_uri=expression.uri,
                work_id=work.id,
                fragment_type=FragmentType.SECTION,
                number="body",
                text=full_text,
            ))

        return work, expression, fragments

    def _validate_result(self, result: PipelineResult) -> List[str]:
        """Validate pipeline result for common issues."""
        errors = []

        # Check for empty fragments
        empty_fragments = [f for f in result.fragments if not f.text or not f.text.strip()]
        if empty_fragments:
            errors.append(f"{len(empty_fragments)} fragments have empty text")

        # Check for very low confidence
        if result.structure and result.structure.confidence < 0.3:
            errors.append(f"Low structure confidence: {result.structure.confidence:.2f}")

        # Check for non-sequential article numbers
        article_numbers = [f.number for f in result.fragments
                          if f.fragment_type == FragmentType.ARTICLE]
        if article_numbers:
            try:
                numbers = [int(n) for n in article_numbers if n.isdigit()]
                if numbers and numbers != list(range(min(numbers), max(numbers) + 1)):
                    errors.append("Article numbers are not sequential")
            except ValueError:
                pass  # Non-numeric article numbers are OK

        return errors

    def _index_for_search(self, result: PipelineResult) -> None:
        """Index fragments in search engines."""
        try:
            from ..search.bm25 import BM25Indexer

            indexer = BM25Indexer("data/search.db")

            for fragment in result.fragments:
                metadata = {
                    "work_title": result.work.title,
                    "version_date": result.expression.version_date.isoformat(),
                    "in_force_start": result.expression.in_force.start.isoformat()
                    if result.expression.in_force.start else None,
                    "in_force_end": result.expression.in_force.end.isoformat()
                    if result.expression.in_force.end else None,
                }
                indexer.index_fragment(fragment, metadata)

            logger.info(f"Indexed {len(result.fragments)} fragments in BM25")

        except ImportError:
            logger.warning("Search indexing skipped: search module not available")
        except Exception as e:
            logger.error(f"Search indexing failed: {e}")
            raise


# Convenience functions

def quick_process(
    pdf_path: str,
    work_id: str,
    title: str,
    version_date: date,
    **kwargs,
) -> PipelineResult:
    """
    Quick processing with default config.

    Example:
        result = quick_process(
            pdf_path="data/raw/arbeidswet.pdf",
            work_id="arbeidswet-1963",
            title="Arbeidswet 1963",
            version_date=date(2024, 1, 15),
        )
    """
    pipeline = OCRPipeline()
    return pipeline.process_pdf(
        pdf_path=pdf_path,
        work_id=work_id,
        title=title,
        version_date=version_date,
        **kwargs,
    )


def process_and_save(
    pdf_path: str,
    work_id: str,
    title: str,
    version_date: date,
    store,
    index_search: bool = True,
    **kwargs,
) -> PipelineResult:
    """
    Process PDF and save to store in one step.

    Example:
        from lexsr.core.store import CanonicalStore

        store = CanonicalStore("data/canonical.db")
        result = process_and_save(
            pdf_path="data/raw/arbeidswet.pdf",
            work_id="arbeidswet-1963",
            title="Arbeidswet 1963",
            version_date=date(2024, 1, 15),
            store=store,
        )
    """
    pipeline = OCRPipeline()

    result = pipeline.process_pdf(
        pdf_path=pdf_path,
        work_id=work_id,
        title=title,
        version_date=version_date,
        **kwargs,
    )

    if result.success:
        # Save to store
        store.save_work(result.work)
        store.save_expression(result.expression)
        for fragment in result.fragments:
            store.save_fragment(fragment)

        # Index in search
        if index_search:
            try:
                pipeline._index_for_search(result)
            except Exception as e:
                logger.warning(f"Search indexing failed: {e}")
                result.warnings.append(f"Search indexing failed: {e}")

    return result
