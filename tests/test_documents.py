from mac_docling.documents import EXTENSIONS_ACCEPTEES, EXTENSIONS_REFUSEES


def test_heic_refuse():
    assert ".heic" in EXTENSIONS_REFUSEES
    assert not EXTENSIONS_REFUSEES.keys() & EXTENSIONS_ACCEPTEES
