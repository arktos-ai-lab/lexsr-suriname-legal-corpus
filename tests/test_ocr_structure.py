"""Structure detection and page handling for scanned gazette PDFs."""
from datetime import date

from PIL import Image, ImageDraw

from lexsr.ocr.detector import StructureDetector
from lexsr.ocr.extractor import OCREngine, OCRResult, PDFExtractor
from lexsr.ocr.pipeline import OCRPipeline, PipelineConfig

AMENDING_ACT = """2011 STAATSBLAD No. 100
WET van 3 augustus 2011, houdende nadere wijziging van de Inkomstenbelasting 1922.

ARTIKEL I
In de Inkomstenbelasting 1922 (G.B. 1921 no. 112, zoals laatstelijk gewijzigd bij S.B. 2005
no. 58) wordt artikel 34 lid 2 gewijzigd als volgt:
De op grond van dit artikel verschuldigde belasting wordt door de minister vastgesteld.

ARTIKEL II
Deze wet treedt in werking met ingang van de dag na die van afkondiging
en de Minister van Financiën is belast met de uitvoering van deze wet.
Zij wordt in het Staatsblad geplaatst.
"""


def test_roman_numeral_articles():
    numbers = [a.number for a in StructureDetector().detect(AMENDING_ACT).articles]
    assert numbers == ["I", "II"]


def test_cyrillic_lookalikes_in_article_heading():
    text = AMENDING_ACT.replace("ARTIKEL I\n", "АRTIKEL І\n")
    numbers = [a.number for a in StructureDetector().detect(text).articles]
    assert numbers == ["I", "II"]


def test_lowercase_word_after_artikel_is_not_a_roman_numeral():
    text = "WET van 1 mei 2020.\n\nartikel vervalt in zijn geheel en de wet wordt gewijzigd.\n"
    assert StructureDetector().detect(text).articles == []


def test_decree_without_articles_kept_whole():
    text = ("2018 STAATSBLAD No. 13\nPRESIDENTIEEL BESLUIT van 2 februari 2018, houdende bekrachtiging "
            "van de Overeenkomst.\nGELET OP: de artikelen 103 en 104 van de Grondwet.\nBESLUIT:\n"
            "I. Te bekrachtigen de Overeenkomst tussen de Regering van de Republiek Suriname en de "
            "Regering van de Republiek India.\nII. Dit besluit treedt in werking.")
    pipe = OCRPipeline(PipelineConfig(ocr_engine=OCREngine.TESSERACT))
    page = OCRResult(text=text, confidence=1.0, engine=OCREngine.PYPDF, page_number=1, is_digital=True)
    _, _, frags = pipe._create_frbr_models("w", "t", date(2018, 2, 2), "w",
                                          pipe.detector.detect(text), [page])
    assert len(frags) == 1 and frags[0].number == "body" and "bekrachtigen" in frags[0].text


def _page(w, h, boxes):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    for box in boxes:
        d.rectangle(box, fill="black")
    return img


def test_two_page_spread_is_split_at_the_gutter():
    # two text columns with a blank gutter in the middle
    img = _page(1600, 1000, [(80, 100, 740, 900), (860, 100, 1520, 900)])
    halves = PDFExtractor()._split_spread(img)
    assert len(halves) == 2
    assert 740 <= halves[0].size[0] <= 860


def test_wide_single_page_is_not_split():
    img = _page(1600, 1000, [(80, 100, 1520, 900)])
    assert len(PDFExtractor()._split_spread(img)) == 1


def test_portrait_page_is_not_split():
    img = _page(1000, 1600, [(80, 100, 900, 1500)])
    assert len(PDFExtractor()._split_spread(img)) == 1
