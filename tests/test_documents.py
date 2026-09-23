from mac_docling.documents import EXTENSIONS_ACCEPTEES, EXTENSIONS_REFUSEES


def test_heic_refuse():
    assert ".heic" in EXTENSIONS_REFUSEES
    assert not EXTENSIONS_REFUSEES.keys() & EXTENSIONS_ACCEPTEES


def test_orientation_exif_appliquee_a_la_normalisation(tmp_path):
    from PIL import Image

    from mac_docling.documents import COTE_MAX_IMAGE, normaliser_image

    source = tmp_path / "photo.jpg"
    image = Image.new("RGB", (COTE_MAX_IMAGE + 400, 1000), "white")
    exif = image.getexif()
    exif[0x0112] = 6   # rotation de 90° à l'affichage
    image.save(source, exif=exif)

    cible = normaliser_image(source, tmp_path / "travail")
    with Image.open(cible) as sortie:
        largeur, hauteur = sortie.size
    assert hauteur > largeur
    assert max(largeur, hauteur) == COTE_MAX_IMAGE
