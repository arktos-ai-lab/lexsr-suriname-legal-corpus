"""
PDF text extraction and OCR engine.

Handles both digital and scanned PDFs with automatic detection.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OCREngine(Enum):
    """OCR engine types."""
    PYPDF = "pypdf"              # Digital PDF text extraction
    TESSERACT = "tesseract"      # Tesseract OCR (free, good)
    EASYOCR = "easyocr"          # EasyOCR (ML-based, better)
    QWEN_VL = "qwen_vl"          # Qwen2.5-VL-7B-Instruct (vision model, best quality)


@dataclass
class OCRResult:
    """Result of OCR processing."""
    text: str
    confidence: float
    engine: OCREngine
    page_number: int
    is_digital: bool
    language: str = "nl"
    bounding_boxes: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class PDFExtractor:
    """
    Extract text from PDFs (digital or scanned).

    Automatic detection:
    1. Try digital extraction (fast)
    2. If confidence < 0.5, assume scanned
    3. Fall back to OCR

    Engines:
    - PyPDF2: Digital PDFs (fast, accurate)
    - Tesseract: Scanned PDFs (free, good)
    - EasyOCR: Better quality (optional)
    """

    def __init__(
        self,
        prefer_engine: OCREngine = OCREngine.TESSERACT,
        tesseract_lang: str = "nld+eng",  # Dutch + English
        confidence_threshold: float = 0.5,
    ):
        """
        Initialize PDF extractor.

        Args:
            prefer_engine: Preferred OCR engine for scanned docs
            tesseract_lang: Tesseract language codes
            confidence_threshold: Min confidence to consider digital
        """
        self.prefer_engine = prefer_engine
        self.tesseract_lang = tesseract_lang
        self.confidence_threshold = confidence_threshold

        # Poppler path for PDF to image conversion
        self.poppler_path = r"C:\poppler\poppler-24.08.0\Library\bin"

        # Lazy imports
        self._pypdf = None
        self._pytesseract = None
        self._easyocr = None
        self._pdf2image = None

    def extract(
        self,
        pdf_path: str,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """
        Extract text from PDF (auto-detect digital vs scanned).

        Args:
            pdf_path: Path to PDF file
            pages: Optional list of page numbers (1-indexed)

        Returns:
            List of OCRResult objects (one per page)
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info(f"Extracting text from: {pdf_path}")

        # Poppler cannot open paths beyond the Windows 260-character limit
        if len(str(pdf_path.resolve())) > 240:
            import shutil, tempfile
            short = Path(tempfile.mkdtemp()) / "doc.pdf"
            shutil.copyfile(pdf_path, short)
            pdf_path = short

        # Try digital extraction first
        results = self._extract_digital(pdf_path, pages)

        # Check if we need OCR
        needs_ocr = False
        for result in results:
            if result.confidence < self.confidence_threshold:
                needs_ocr = True
                break

        if needs_ocr:
            logger.info("Low confidence detected, switching to OCR")
            results = self._extract_ocr(pdf_path, pages)

        logger.info(f"Extracted {len(results)} pages")
        return results

    def _extract_digital(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """Extract text from digital PDF using PyPDF2."""
        if self._pypdf is None:
            try:
                import PyPDF2
                self._pypdf = PyPDF2
            except ImportError:
                raise ImportError(
                    "PyPDF2 required for digital PDF extraction. "
                    "Install with: pip install PyPDF2"
                )

        results = []

        with open(pdf_path, "rb") as f:
            reader = self._pypdf.PdfReader(f)
            total_pages = len(reader.pages)

            # Determine which pages to process
            if pages is None:
                page_nums = range(total_pages)
            else:
                page_nums = [p - 1 for p in pages if 0 < p <= total_pages]

            for page_num in page_nums:
                page = reader.pages[page_num]
                text = page.extract_text()

                # Calculate confidence based on text length and quality
                confidence = self._calculate_digital_confidence(text)

                result = OCRResult(
                    text=text,
                    confidence=confidence,
                    engine=OCREngine.PYPDF,
                    page_number=page_num + 1,
                    is_digital=True,
                    metadata={
                        "text_length": len(text),
                        "has_text": len(text.strip()) > 0,
                    },
                )
                results.append(result)

        return results

    def _extract_ocr(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """Extract text from scanned PDF using OCR."""
        if self.prefer_engine == OCREngine.QWEN_VL:
            return self._extract_qwen_vl(pdf_path, pages)
        elif self.prefer_engine == OCREngine.TESSERACT:
            return self._extract_tesseract(pdf_path, pages)
        elif self.prefer_engine == OCREngine.EASYOCR:
            return self._extract_easyocr(pdf_path, pages)
        else:
            logger.warning(f"Engine {self.prefer_engine} not implemented, using Tesseract")
            return self._extract_tesseract(pdf_path, pages)

    def _upright(self, image):
        """Rotate a page so its text runs horizontally (Tesseract orientation detection)."""
        try:
            osd = self._pytesseract.image_to_osd(image, config="--psm 0")
        except Exception:
            return image  # too little text to tell; leave as is
        import re
        m = re.search(r"Rotate:\s*(\d+)", osd)
        angle = int(m.group(1)) if m else 0
        if angle:
            logger.info(f"  rotating page by {angle} degrees")
            return image.rotate(-angle, expand=True)
        return image

    def _split_spread(self, image):
        """Split a landscape sheet holding two portrait pages at the blank gutter between them."""
        w, h = image.size
        if w < 1.2 * h:
            return [image]
        gray = image.convert("L")
        small = gray.resize((400, max(1, int(400 * h / w))))
        sw, sh = small.size
        px = small.load()
        ink = [sum(1 for y in range(sh) if px[x, y] < 128) for x in range(sw)]
        lo, hi = int(sw * 0.35), int(sw * 0.65)
        col = min(range(lo, hi), key=lambda x: ink[x])
        # A real gutter is (nearly) empty; a wide single page has text running through the middle
        if ink[col] > 0.02 * sh or max(ink[:lo] + ink[hi:]) == 0:
            return [image]
        cut = int(col * w / sw)
        logger.info(f"  two-page spread, splitting at x={cut}")
        return [image.crop((0, 0, cut, h)), image.crop((cut, 0, w, h))]

    def _extract_tesseract(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """Extract text using Tesseract OCR."""
        # Lazy imports
        if self._pytesseract is None:
            try:
                import pytesseract
                self._pytesseract = pytesseract
            except ImportError:
                raise ImportError(
                    "pytesseract required for OCR. "
                    "Install with: pip install pytesseract\n"
                    "Also install Tesseract binary: https://github.com/tesseract-ocr/tesseract"
                )

        if self._pdf2image is None:
            try:
                from pdf2image import convert_from_path
                self._pdf2image = convert_from_path
            except ImportError:
                raise ImportError(
                    "pdf2image required for PDF to image conversion. "
                    "Install with: pip install pdf2image"
                )

        results = []

        # Convert PDF to images
        logger.info("Converting PDF to images...")
        try:
            if pages:
                images = self._pdf2image(str(pdf_path), first_page=min(pages), last_page=max(pages), poppler_path=self.poppler_path)
            else:
                images = self._pdf2image(str(pdf_path), poppler_path=self.poppler_path)
        except Exception as e:
            logger.error(f"PDF to image conversion failed: {e}")
            raise

        logger.info(f"Running Tesseract OCR on {len(images)} pages...")

        for i, image in enumerate(images):
            page_num = pages[i] if pages else i + 1

            # Run OCR with detailed data
            try:
                # Turn sideways scans upright, then read a two-page spread
                # (two A5 Staatsblad pages on one landscape sheet) one half at a time
                halves = self._split_spread(self._upright(image))
                texts, confidences, words = [], [], []
                for part in halves:
                    data = self._pytesseract.image_to_data(
                        part,
                        lang=self.tesseract_lang,
                        output_type=self._pytesseract.Output.DICT,
                    )
                    texts.append(self._pytesseract.image_to_string(part, lang=self.tesseract_lang))
                    confidences += [int(float(c)) for c in data["conf"] if str(c) != "-1"]
                    words += [w for w in data["text"] if w.strip()]
                text = "\n\n".join(texts)
                data = {"text": words}
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0
                avg_confidence = avg_confidence / 100.0  # Convert to 0-1 scale

                result = OCRResult(
                    text=text,
                    confidence=avg_confidence,
                    engine=OCREngine.TESSERACT,
                    page_number=page_num,
                    is_digital=False,
                    metadata={
                        "word_count": len([w for w in data["text"] if w.strip()]),
                        "avg_confidence": avg_confidence,
                        "min_confidence": min(confidences) / 100.0 if confidences else 0,
                        "max_confidence": max(confidences) / 100.0 if confidences else 0,
                    },
                )
                results.append(result)

                logger.info(f"  Page {page_num}: {len(text)} chars, "
                          f"confidence {avg_confidence:.2f}")

            except Exception as e:
                logger.error(f"OCR failed for page {page_num}: {e}")
                # Add empty result
                result = OCRResult(
                    text="",
                    confidence=0.0,
                    engine=OCREngine.TESSERACT,
                    page_number=page_num,
                    is_digital=False,
                    metadata={"error": str(e)},
                )
                results.append(result)

        return results

    def _extract_easyocr(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """Extract text using EasyOCR (ML-based, better quality)."""
        if self._easyocr is None:
            try:
                import easyocr
                self._easyocr = easyocr.Reader(["nl", "en"])
            except ImportError:
                raise ImportError(
                    "easyocr required. Install with: pip install easyocr"
                )

        if self._pdf2image is None:
            from pdf2image import convert_from_path
            self._pdf2image = convert_from_path

        results = []

        # Convert PDF to images
        logger.info("Converting PDF to images...")
        if pages:
            images = self._pdf2image(str(pdf_path), first_page=min(pages), last_page=max(pages), poppler_path=self.poppler_path)
        else:
            images = self._pdf2image(str(pdf_path), poppler_path=self.poppler_path)

        logger.info(f"Running EasyOCR on {len(images)} pages...")

        for i, image in enumerate(images):
            page_num = pages[i] if pages else i + 1

            # Convert PIL image to numpy array
            import numpy as np
            image_np = np.array(image)

            # Run EasyOCR
            try:
                ocr_results = self._easyocr.readtext(image_np)

                # Extract text and confidences
                text_parts = []
                confidences = []
                for bbox, text, conf in ocr_results:
                    text_parts.append(text)
                    confidences.append(conf)

                text = "\n".join(text_parts)
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0

                result = OCRResult(
                    text=text,
                    confidence=avg_confidence,
                    engine=OCREngine.EASYOCR,
                    page_number=page_num,
                    is_digital=False,
                    metadata={
                        "word_count": len(text_parts),
                        "avg_confidence": avg_confidence,
                    },
                )
                results.append(result)

                logger.info(f"  Page {page_num}: {len(text)} chars, "
                          f"confidence {avg_confidence:.2f}")

            except Exception as e:
                logger.error(f"EasyOCR failed for page {page_num}: {e}")
                result = OCRResult(
                    text="",
                    confidence=0.0,
                    engine=OCREngine.EASYOCR,
                    page_number=page_num,
                    is_digital=False,
                    metadata={"error": str(e)},
                )
                results.append(result)

        return results

    def _extract_qwen_vl(
        self,
        pdf_path: Path,
        pages: Optional[List[int]] = None,
    ) -> List[OCRResult]:
        """
        Extract text using Qwen2.5-VL-7B-Instruct (local vision model).

        Uses local Qwen2.5-VL running via vLLM for best OCR quality.
        """
        if self._pdf2image is None:
            from pdf2image import convert_from_path
            self._pdf2image = convert_from_path

        results = []

        # Convert PDF to images
        logger.info("Converting PDF to images for Qwen2.5-VL OCR...")
        try:
            if pages:
                images = self._pdf2image(str(pdf_path), first_page=min(pages), last_page=max(pages), poppler_path=self.poppler_path)
            else:
                images = self._pdf2image(str(pdf_path), poppler_path=self.poppler_path)
        except Exception as e:
            logger.error(f"PDF to image conversion failed: {e}")
            raise

        logger.info(f"Running Qwen2.5-VL vLLM OCR on {len(images)} pages...")

        import subprocess
        import base64
        import io

        for i, image in enumerate(images):
            page_num = pages[i] if pages else i + 1

            try:
                # Convert image to base64
                img_buffer = io.BytesIO()
                image.save(img_buffer, format='PNG')
                img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')

                # Build prompt for Qwen2.5-VL
                prompt = """Extract all text from this legal document image.
Preserve the exact structure including:
- Article numbers (Artikel N)
- Paragraph numbers (lid N)
- All text content
- Formatting and line breaks

Return ONLY the extracted text, no explanation or commentary."""

                # Call local Qwen2.5-VL vLLM via direct script
                # Uses Qwen2.5-VL-7B-Instruct for vision OCR
                result = subprocess.run(
                    [
                        'python',
                        'C:/Users/evgga/.claude/qwen-vision.py',  # Vision model script
                        prompt,
                        img_base64,  # Image as context
                        '4096',      # max_tokens
                        '0.1',       # temperature (low for OCR accuracy)
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode == 0:
                    text = result.stdout.strip()

                    # Qwen2.5-VL doesn't provide per-word confidence,
                    # but we can assume high confidence if text is returned
                    confidence = 0.95 if len(text) > 50 else 0.80

                    ocr_result = OCRResult(
                        text=text,
                        confidence=confidence,
                        engine=OCREngine.QWEN_VL,
                        page_number=page_num,
                        is_digital=False,
                        metadata={
                            "word_count": len(text.split()),
                            "char_count": len(text),
                            "model": "Qwen2.5-VL-7B-Instruct",
                        },
                    )
                    results.append(ocr_result)

                    logger.info(f"  Page {page_num}: {len(text)} chars extracted "
                              f"(confidence {confidence:.2f})")
                else:
                    logger.error(f"Qwen2.5-VL OCR failed for page {page_num}: {result.stderr}")
                    # Fall back to empty result
                    ocr_result = OCRResult(
                        text="",
                        confidence=0.0,
                        engine=OCREngine.QWEN_VL,
                        page_number=page_num,
                        is_digital=False,
                        metadata={"error": result.stderr},
                    )
                    results.append(ocr_result)

            except subprocess.TimeoutExpired:
                logger.error(f"Qwen2.5-VL OCR timeout for page {page_num}")
                results.append(OCRResult(
                    text="",
                    confidence=0.0,
                    engine=OCREngine.QWEN_VL,
                    page_number=page_num,
                    is_digital=False,
                    metadata={"error": "Timeout"},
                ))
            except Exception as e:
                logger.error(f"Qwen2.5-VL OCR failed for page {page_num}: {e}")
                results.append(OCRResult(
                    text="",
                    confidence=0.0,
                    engine=OCREngine.QWEN_VL,
                    page_number=page_num,
                    is_digital=False,
                    metadata={"error": str(e)},
                ))

        return results

    def _calculate_digital_confidence(self, text: str) -> float:
        """
        Calculate confidence that a PDF is digital (not scanned).

        Heuristics:
        - Length of extracted text
        - Presence of normal words
        - Lack of OCR artifacts
        """
        if not text or len(text.strip()) < 10:
            return 0.0

        # Length score (longer = more likely digital)
        length_score = min(len(text) / 1000, 1.0)

        # Word score (presence of actual words)
        words = text.split()
        word_score = min(len(words) / 100, 1.0)

        # Clean text score (no weird characters)
        clean_chars = sum(1 for c in text if c.isalnum() or c.isspace())
        clean_score = clean_chars / len(text) if text else 0

        # Combined confidence
        confidence = (length_score * 0.3 + word_score * 0.4 + clean_score * 0.3)

        return min(confidence, 1.0)
