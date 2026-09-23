import pytest

from mac_docling.moteur import adopter_vlm, motif_bascule, note_declenche


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


TEXTE = "Un paragraphe de contenu suffisamment long pour compter."


def test_motif_echec_prime_sur_la_note():
    assert motif_bascule(False, "failure", None, "fair", "") == "conversion en échec (failure)"


def test_motif_note():
    assert motif_bascule(True, "success", "poor", "fair", TEXTE) == "note poor ≤ seuil fair"
    assert motif_bascule(True, "success", None, "fair", TEXTE) == "note absente"


def test_motif_page_vide_puis_aucun():
    assert motif_bascule(True, "success", "good", "fair", "  ") == "page quasi vide"
    assert motif_bascule(True, "success", "good", "fair", TEXTE) == ""


def test_adoption_vlm_plus_court_mais_sain():
    parasites = "~ ¦ l1l ,. ' " * 20
    assert adopter_vlm(TEXTE, parasites, "")


def test_adoption_refusee_si_anomalie_ou_vide():
    assert not adopter_vlm(TEXTE, "", "motif répété")
    assert not adopter_vlm("   ", "abc", "")


def test_adoption_vlm_court_seulement_s_il_apporte_plus():
    assert adopter_vlm("abc", "", "")
    assert not adopter_vlm("abc", "abcdef", "")
