"""
Structure detection for legal documents.

Extracts articles, paragraphs, and other structural elements from raw OCR text.
Uses pattern matching and heuristics optimized for Suriname legal documents.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_HOMOGLYPHS = str.maketrans("АВСЕНІЈКМОРТХаеіорсху", "ABCEHIJKMOPTXaeiopcxy")


class ElementType(Enum):
    """Legal document element types."""
    TITLE = "title"
    PREAMBLE = "preamble"
    CHAPTER = "chapter"
    SECTION = "section"
    ARTICLE = "article"
    PARAGRAPH = "paragraph"
    SUBPARAGRAPH = "subparagraph"
    LIST_ITEM = "list_item"
    AMENDMENT_NOTE = "amendment_note"
    FOOTNOTE = "footnote"


@dataclass
class DetectedArticle:
    """A detected article from OCR text."""
    number: str
    title: Optional[str] = None
    text: str = ""
    paragraphs: List[str] = field(default_factory=list)
    confidence: float = 0.0
    heading_path: List[str] = field(default_factory=list)
    raw_text: str = ""

    # Location in original text
    start_offset: int = 0
    end_offset: int = 0

    # Metadata
    has_amendments: bool = False
    amendment_notes: List[str] = field(default_factory=list)


@dataclass
class DetectedStructure:
    """Complete document structure from OCR."""
    title: str = ""
    preamble: str = ""
    articles: List[DetectedArticle] = field(default_factory=list)
    chapters: Dict[str, List[str]] = field(default_factory=dict)  # chapter -> article numbers
    confidence: float = 0.0
    metadata: Dict[str, any] = field(default_factory=dict)


class StructureDetector:
    """
    Detect legal document structure from raw OCR text.

    Optimized for Suriname legal documents with patterns for:
    - Dutch legal formatting (Artikel, Lid, etc.)
    - Numbered articles and paragraphs
    - Chapter/section headings
    - Amendment annotations

    Supports multiple document types:
    - Laws (Wetten)
    - Decrees (Besluiten)
    - Gazette publications (Staatscouranten)
    """

    # Article patterns (Dutch legal documents)
    ARTICLE_PATTERNS = [
        r"(?:^|\n)\s*Artikel\s+(\d+[a-z]?|(?-i:[IVXLC]+)\b)\s*\.?\s*([^\n]*)",  # Artikel 4. / ARTIKEL I
        r"(?:^|\n)\s*Art\.\s*(\d+[a-z]?)\s*\.?\s*([^\n]*)",    # Art. 4. Title
        r"(?:^|\n)\s*(\d+)\s*\.\s+([A-Z][^\n]*)",              # 4. TITLE
    ]

    # Paragraph patterns (lid)
    PARAGRAPH_PATTERNS = [
        r"(?:^|\n)\s*(\d+)\s*\.?\s+([A-Z])",  # 1. Capital letter starts paragraph
        r"(?:^|\n)\s*lid\s+(\d+)\s*[.:]?\s*",  # lid 1:
    ]

    # Chapter/section patterns
    CHAPTER_PATTERNS = [
        r"(?:^|\n)\s*HOOFDSTUK\s+([IVX\d]+)\s*[-–]?\s*([^\n]*)",  # HOOFDSTUK I - Title
        r"(?:^|\n)\s*Hoofdstuk\s+([IVX\d]+)\s*[-–]?\s*([^\n]*)",
        r"(?:^|\n)\s*AFDELING\s+([IVX\d]+)\s*[-–]?\s*([^\n]*)",   # AFDELING 1 - Title
    ]

    # Amendment notes
    AMENDMENT_PATTERNS = [
        r"\[Gewijzigd bij ([^\]]+)\]",
        r"\(Gewijzigd\s+([^)]+)\)",
        r"Zoals\s+gewijzigd\s+bij\s+([^\n\.]+)",
    ]

    def __init__(
        self,
        language: str = "nl",
        min_article_confidence: float = 0.6,
    ):
        """
        Initialize structure detector.

        Args:
            language: Document language (nl, en)
            min_article_confidence: Minimum confidence to accept an article
        """
        self.language = language
        self.min_article_confidence = min_article_confidence

        # Compile patterns
        self._article_regex = [re.compile(p, re.MULTILINE | re.IGNORECASE)
                               for p in self.ARTICLE_PATTERNS]
        self._paragraph_regex = [re.compile(p, re.MULTILINE)
                                for p in self.PARAGRAPH_PATTERNS]
        self._chapter_regex = [re.compile(p, re.MULTILINE)
                              for p in self.CHAPTER_PATTERNS]
        self._amendment_regex = [re.compile(p, re.MULTILINE)
                                for p in self.AMENDMENT_PATTERNS]

    def detect(self, text: str) -> DetectedStructure:
        """
        Detect document structure from raw text.

        Args:
            text: Raw OCR text

        Returns:
            DetectedStructure with articles and hierarchy
        """
        logger.info(f"Detecting structure in {len(text)} chars of text")

        # Clean text
        text = self._clean_text(text)

        # Extract title and preamble
        title, preamble, body_start = self._extract_title_preamble(text)

        # Extract chapters
        chapters = self._extract_chapters(text)

        # Extract articles
        articles = self._extract_articles(text[body_start:], body_start)

        # Calculate overall confidence
        confidence = self._calculate_structure_confidence(articles)

        structure = DetectedStructure(
            title=title,
            preamble=preamble,
            articles=articles,
            chapters=chapters,
            confidence=confidence,
            metadata={
                "total_articles": len(articles),
                "total_chapters": len(chapters),
                "text_length": len(text),
            },
        )

        logger.info(f"Detected {len(articles)} articles with confidence {confidence:.2f}")
        return structure

    def _clean_text(self, text: str) -> str:
        """Clean OCR artifacts from text."""
        # CRITICAL FIX: Tesseract reads superscript footnotes as regular digits
        # "Artikel 1¹" becomes "Artikel 11" in OCR output
        # Heuristic: "Artikel 11" right after title/preamble is likely "Artikel 1"
        # Only fix if it's the FIRST article (not middle of document)

        # Look for "Artikel 11" in first 1000 chars (likely first article)
        if 'Artikel 11' in text[:1000]:
            # Check if there's likely no "Artikel 1" before it
            first_art11 = text.find('Artikel 11')
            if first_art11 > 0:
                before_art11 = text[:first_art11]
                # If no "Artikel 1" exists before this, it's probably a misread
                if 'Artikel 1\n' not in before_art11 and 'Artikel 1 ' not in before_art11:
                    # Replace first occurrence of "Artikel 11" with "Artikel 1"
                    text = text[:first_art11] + 'Artikel 1' + text[first_art11+10:]

        # Some digital PDFs use Cyrillic look-alikes ("АRTIKEL І"); map them to Latin
        text = text.translate(_HOMOGLYPHS)

        # Remove actual superscript chars if they exist
        text = re.sub(r'([¹²³⁴⁵⁶⁷⁸⁹⁰]+)', '', text)

        # Remove excessive whitespace
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)

        # Fix common OCR errors in Dutch legal text
        replacements = {
            r'\bl\s+i\s+d\b': 'lid',  # "l i d" -> "lid"
            r'\bA\s+r\s+t\s+i\s+k\s+e\s+l\b': 'Artikel',
            r'\bH\s+O\s+O\s+F\s+D\s+S\s+T\s+U\s+K\b': 'HOOFDSTUK',
        }

        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text)

        return text

    def _extract_title_preamble(self, text: str) -> Tuple[str, str, int]:
        """
        Extract document title and preamble.

        Returns:
            (title, preamble, body_start_offset)
        """
        # Title is typically in first few lines, all caps or title case
        lines = text.split('\n')
        title_lines = []

        for i, line in enumerate(lines[:10]):
            line = line.strip()
            if not line:
                continue

            # Title candidates: all caps, or starts with law type
            if (line.isupper() or
                re.match(r'^\s*(?:WET|BESLUIT|DECREET|REGELING)', line, re.IGNORECASE)):
                title_lines.append(line)
            else:
                break

        title = ' '.join(title_lines) if title_lines else ""

        # Preamble is between title and first article
        first_article_match = None
        for regex in self._article_regex:
            match = regex.search(text)
            if match:
                first_article_match = match
                break

        if first_article_match:
            preamble_end = first_article_match.start()
            title_end = text.find('\n'.join(title_lines)) + len('\n'.join(title_lines)) if title_lines else 0
            preamble = text[title_end:preamble_end].strip()
            body_start = preamble_end
        else:
            preamble = ""
            body_start = len('\n'.join(title_lines)) if title_lines else 0

        return title, preamble, body_start

    def _extract_chapters(self, text: str) -> Dict[str, List[str]]:
        """Extract chapter/section hierarchy."""
        chapters = {}

        for regex in self._chapter_regex:
            for match in regex.finditer(text):
                chapter_num = match.group(1)
                chapter_title = match.group(2).strip() if len(match.groups()) > 1 else ""
                chapters[f"{chapter_num}: {chapter_title}"] = []

        return chapters

    def _extract_articles(self, text: str, offset: int = 0) -> List[DetectedArticle]:
        """Extract all articles from text."""
        articles = []

        # Find all article matches
        all_matches = []
        for regex in self._article_regex:
            for match in regex.finditer(text):
                # CRITICAL FIX: Filter out footnotes that look like articles
                # "1 Gew. bij S.B..." should not be detected as Article 1
                matched_text = text[match.start():match.start()+100]
                if self._is_footnote(matched_text):
                    continue
                all_matches.append(match)

        # Sort by position
        all_matches.sort(key=lambda m: m.start())

        # Extract article content
        for i, match in enumerate(all_matches):
            article_num = match.group(1)

            # CRITICAL FIX: Better title extraction
            # Title might be multi-line or empty - need smarter extraction
            raw_title = match.group(2).strip() if len(match.groups()) > 1 else ""
            article_title = self._clean_article_title(raw_title)

            # CRITICAL FIX: Better article boundary detection
            # Start after "Artikel X" line, not after title
            # Use paragraph structure to separate title from text
            start = match.end()
            end = all_matches[i + 1].start() if i + 1 < len(all_matches) else len(text)

            full_content = text[start:end].strip()

            # Separate title from text if needed
            if not article_title and full_content:
                # Check if first paragraph is a title (short, ends with colon, etc.)
                lines = full_content.split('\n')
                first_line = lines[0].strip() if lines else ""

                # If first line looks like a title, use it
                if first_line and (len(first_line) < 150 or first_line.endswith(':')):
                    article_title = first_line
                    article_text = '\n'.join(lines[1:]).strip()
                else:
                    article_text = full_content
            else:
                article_text = full_content

            # Extract paragraphs
            paragraphs = self._extract_paragraphs(article_text)

            # Check for amendments
            amendments = []
            for amend_regex in self._amendment_regex:
                amendments.extend([m.group(1) for m in amend_regex.finditer(article_text)])

            # Calculate confidence
            confidence = self._calculate_article_confidence(
                article_num, article_text, paragraphs, article_title
            )

            if confidence >= self.min_article_confidence:
                article = DetectedArticle(
                    number=article_num,
                    title=article_title if article_title else None,
                    text=article_text,
                    paragraphs=paragraphs,
                    confidence=confidence,
                    raw_text=match.group(0) + article_text,
                    start_offset=offset + match.start(),
                    end_offset=offset + end,
                    has_amendments=len(amendments) > 0,
                    amendment_notes=amendments,
                )
                articles.append(article)
            else:
                logger.warning(f"Low confidence article {article_num}: {confidence:.2f}")

        return articles

    def _extract_paragraphs(self, article_text: str) -> List[str]:
        """Extract numbered paragraphs from article text."""
        paragraphs = []

        # Try to find numbered paragraphs
        for regex in self._paragraph_regex:
            matches = list(regex.finditer(article_text))

            if matches:
                for i, match in enumerate(matches):
                    start = match.end()
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(article_text)
                    para_text = article_text[start:end].strip()
                    paragraphs.append(para_text)
                break

        # If no numbered paragraphs, treat whole text as single paragraph
        if not paragraphs and article_text:
            paragraphs = [article_text]

        return paragraphs

    def _is_footnote(self, text: str) -> bool:
        """Check if matched text is a footnote, not an article."""
        # Footnotes typically start with number and amendment keywords
        footnote_patterns = [
            r'^\s*\d+\s+(?:Gew\.|Ingev\.|Vervallen|Opgeheven|Vernummerd)',
            r'^\s*\d+\s+[A-Z][a-z]+\.\s+bij\s+[SG]\.B\.',  # "Gew. bij S.B."
        ]

        for pattern in footnote_patterns:
            if re.match(pattern, text):
                return True

        return False

    def _clean_article_title(self, title: str) -> str:
        """Clean extracted article title."""
        if not title:
            return ""

        # Remove special characters that are OCR artifacts
        title = re.sub(r'[�°*•·¹²³⁴⁵⁶⁷⁸⁹⁰]+', '', title)

        # Remove trailing punctuation artifacts
        title = re.sub(r'[,;:]+$', '', title)

        # Trim whitespace
        title = title.strip()

        # If title is just special characters or too short, ignore it
        if len(title) < 3 or re.match(r'^[^\w\s]+$', title):
            return ""

        return title

    def _calculate_article_confidence(
        self,
        number: str,
        text: str,
        paragraphs: List[str],
        title: str = ""
    ) -> float:
        """Calculate confidence that this is a real article."""
        score = 0.0

        # Number looks valid
        if re.match(r'^(?:\d+[a-z]?|[IVXLC]+)$', number):
            score += 0.3

        # Has substantial text
        if len(text) > 20:
            score += 0.3

        # Has paragraphs
        if paragraphs:
            score += 0.2

        # Text looks like Dutch legal language
        legal_terms = ['werkgever', 'werknemer', 'bepaling', 'wordt', 'zijn',
                       'minister', 'staat', 'recht', 'wet', 'lid', 'verboden',
                       'toegestaan', 'beschikking', 'vaststellen']
        found_terms = sum(1 for term in legal_terms if term in text.lower())
        if found_terms >= 2:
            score += 0.2

        # IMPROVEMENT: Penalize if title looks like artifact
        if title and (len(title) < 3 or title in ['!', '�', ',', '*)', '°']):
            score -= 0.2

        return min(max(score, 0.0), 1.0)

    def _calculate_structure_confidence(self, articles: List[DetectedArticle]) -> float:
        """Calculate overall structure detection confidence."""
        if not articles:
            return 0.0

        # Average article confidence
        avg_confidence = sum(a.confidence for a in articles) / len(articles)

        # Bonus for sequential numbering
        numbers = [int(re.match(r'(\d+)', a.number).group(1))
                  for a in articles
                  if re.match(r'(\d+)', a.number)]

        sequential_bonus = 0.0
        if numbers and numbers == list(range(min(numbers), max(numbers) + 1)):
            sequential_bonus = 0.1

        return min(avg_confidence + sequential_bonus, 1.0)
