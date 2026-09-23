from mac_docling.app import _ms, barre


def test_ms():
    assert _ms(None) == "—"
    assert _ms(250) == "250 ms"
    assert _ms(1500) == "1.50 s"


def test_barre_echappe_la_note():
    rendu = barre(1, 2, "<script>")
    assert "&lt;script&gt;" in rendu
    assert "width:50.0%" in rendu
    assert "finie" not in rendu
    assert "finie" in barre(2, 2)


def test_afficher_un_document_du_lot():
    from mac_docling.app import afficher

    resultats = {"1. a": {"lignes": [[1]], "markdown": "# A"},
                 "2. a": {"lignes": [[2]], "markdown": "# A bis"}}
    assert afficher("2. a", resultats) == ([[2]], "# A bis", "# A bis")
    assert afficher(None, resultats) == ([], "", "")


def test_choix_masque_pour_un_seul_document():
    from mac_docling.app import _choix

    assert _choix({"1. a": {}})["visible"] is False
    assert _choix({"1. a": {}, "2. b": {}}, "2. b")["value"] == "2. b"
