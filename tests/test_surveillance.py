from mac_docling.surveillance import diagnostiquer, lignes_repetees_max


def test_sortie_saine():
    texte = "\n".join(f"Ligne numéro {i} du document" for i in range(30))
    assert diagnostiquer(texte) == ""


def test_lignes_consecutives_identiques():
    texte = "début\n" + "boucle infinie\n" * 6
    assert lignes_repetees_max(texte) == 6
    assert "lignes identiques consécutives" in diagnostiquer(texte)


def test_separateurs_de_tableau_ignores():
    texte = "| a | b |\n|---|---|\n| 1 | 2 |\n" + "|---|---|\n" * 3
    assert "motif répété" not in diagnostiquer(texte)


def test_motif_repete():
    texte = "Le patient présente " * 10
    assert "motif répété en boucle" in diagnostiquer(texte)
