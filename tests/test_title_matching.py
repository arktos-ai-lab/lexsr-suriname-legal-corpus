"""Fuzzy title fallback: base law over amending act, closest title, no bare article refs."""
from lexsr.core.models import SourceType, Work
from lexsr.core.title_matching import resolve_work_by_title


def _w(i, title):
    return Work(id=i, uri=f"/id/law/{i}", source_type=SourceType.LEGISLATION, official_id=i, title=title)


WORKS = [
    _w("wijz-arb", "S.B. 2023 no. 125 Wijziging Arbeidswet 1963"),
    _w("arb", "Arbeidswet 1963 bijgewerkt"),
    _w("kies-long", "Kiesregeling bijgewerkt tm S.B. 2019 no. 55 met toelichting en bijlagen"),
    _w("kies", "Kiesregeling"),
    _w("hazard", "Wet Hazardspelen wijziging Wetboek van strafrecht Wetboek van strafvordering"),
    _w("besluit", "S.B. 2023 no. 118 Staatsbesluit van 7 augustus ter uitvoering van artikel 8"),
]


def test_base_law_not_amending_act():
    assert resolve_work_by_title("Arbeidswet 1963", WORKS).id == "arb"


def test_amending_act_when_reference_names_one():
    assert resolve_work_by_title("Wijziging Arbeidswet 1963", WORKS).id == "wijz-arb"


def test_closest_title_wins():
    assert resolve_work_by_title("Kiesregeling", WORKS).id == "kies"


def test_law_only_mentioned_in_amending_title_stays_unlinked():
    assert resolve_work_by_title("Wetboek van Strafvordering", WORKS) is None


def test_bare_article_reference_not_linked():
    assert resolve_work_by_title("artikel 8", WORKS) is None


def test_article_pointer_before_law_name_is_fine():
    assert resolve_work_by_title("artikel 4 lid 2 Arbeidswet 1963", WORKS).id == "arb"
