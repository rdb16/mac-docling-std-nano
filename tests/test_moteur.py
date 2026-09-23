import pytest

from mac_docling.moteur import note_declenche


@pytest.mark.parametrize(
    ("note", "seuil", "attendu"),
    [
        ("poor", "fair", True),
        ("fair", "fair", True),
        ("good", "fair", False),
        ("excellent", "good", False),
        (None, "poor", True),
        ("unspecified", "poor", True),
        ("inconnue", "good", False),
    ],
)
def test_note_declenche(note, seuil, attendu):
    assert note_declenche(note, seuil) is attendu
