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

    cible, pages = normaliser_image(source, tmp_path / "travail")
    assert pages == 1
    with Image.open(cible) as sortie:
        largeur, hauteur = sortie.size
    assert hauteur > largeur
    assert max(largeur, hauteur) == COTE_MAX_IMAGE


def _tiff(chemin, cote: int, pages: int) -> None:
    from PIL import Image

    cadres = [Image.new("RGB", (cote, cote), "white") for _ in range(pages)]
    cadres[0].save(chemin, save_all=True, append_images=cadres[1:])


def test_tiff_multipage_lisible_garde_la_source(tmp_path):
    from mac_docling.documents import preparer

    source = tmp_path / "scan.tiff"
    _tiff(source, 500, 3)
    document = preparer(source, tmp_path / "travail")
    assert document.pages == 3
    assert document.chemin == source


def test_tiff_multipage_trop_grand_garde_toutes_ses_pages(tmp_path):
    from PIL import Image

    from mac_docling.documents import COTE_MAX_IMAGE, preparer

    source = tmp_path / "scan.tif"
    _tiff(source, COTE_MAX_IMAGE + 100, 3)
    document = preparer(source, tmp_path / "travail")
    assert document.pages == 3 and document.normalise
    with Image.open(document.chemin) as sortie:
        assert sortie.n_frames == 3
        assert max(sortie.size) == COTE_MAX_IMAGE


def test_chemin_libre(tmp_path):
    from mac_docling.documents import chemin_libre

    assert chemin_libre(tmp_path, "rapport", ".md").name == "rapport.md"
    (tmp_path / "rapport.md").touch()
    (tmp_path / "rapport (2).md").touch()
    assert chemin_libre(tmp_path, "rapport", ".md").name == "rapport (3).md"


def test_deux_images_homonymes_ne_s_ecrasent_pas(tmp_path):
    from PIL import Image

    from mac_docling.documents import preparer

    travail = tmp_path / "travail"
    cibles = []
    for dossier in ("a", "b"):
        (tmp_path / dossier).mkdir()
        source = tmp_path / dossier / "scan.gif"
        Image.new("RGB", (100, 100)).save(source)
        cibles.append(preparer(source, travail).chemin)
    assert cibles[0] != cibles[1]
