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
