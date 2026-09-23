from mac_docling.deduplication import assembler, reperer_repetitions


def _pages(n: int, corps: str = "contenu") -> list[str]:
    return [f"En-tête du rapport\n{corps} {i}\nPied de page" for i in range(n)]


def test_moins_de_trois_pages_rien_n_est_retire():
    assert reperer_repetitions(_pages(2)) == {}


def test_entetes_et_pieds_gardes_une_seule_fois():
    texte, rapport = assembler(_pages(4))
    assert texte.count("En-tête du rapport") == 1
    assert texte.count("Pied de page") == 1
    assert rapport.lignes_retirees == 6
    for i in range(4):
        assert f"contenu {i}" in texte


def test_lignes_de_tableau_conservees():
    pages = ["| a | b |\ntexte " + str(i) for i in range(4)]
    texte, rapport = assembler(pages)
    assert texte.count("| a | b |") == 4
    assert rapport.lignes_retirees == 0


def test_desactivee():
    texte, rapport = assembler(_pages(4), actif=False)
    assert texte.count("En-tête du rapport") == 4
    assert rapport.resume() == ""


def test_pages_vides_ignorees():
    texte, _ = assembler(["", "  ", "bonjour"])
    assert texte == "bonjour\n"
