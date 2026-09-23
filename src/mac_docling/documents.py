"""Inventaire et préparation des fichiers d'entrée.

Rien n'est modifié dans le dossier source : les images que Docling ne sait pas
lire, ou qui sont trop grandes pour l'OCR, sont recopiées normalisées dans un
dossier de travail.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

EXTENSIONS_PDF = {".pdf"}
EXTENSIONS_IMAGE = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
EXTENSIONS_A_TRANSCODER = {".jp2", ".j2k", ".jpf", ".jpx", ".gif"}
# Les photos HEIC d'iPhone sont trop lourdes pour l'OCR et Pillow ne les lit
# pas sans greffon : on les refuse avec une consigne plutôt que de les
# confondre avec un fichier illisible.
EXTENSIONS_REFUSEES = {
    ".heic": "HEIC refusé, trop volumineux : exportez la photo en JPEG",
    ".heif": "HEIF refusé, trop volumineux : exportez la photo en JPEG",
}
# Seuls les TIFF sont lus page par page : les images d'un GIF animé ne sont
# pas des pages.
EXTENSIONS_MULTIPAGES = {".tif", ".tiff"}
EXTENSIONS_ACCEPTEES = EXTENSIONS_PDF | EXTENSIONS_IMAGE | EXTENSIONS_A_TRANSCODER

# Un scan dépasse souvent 20 Mpx ; l'OCR le rééchantillonne ensuite d'un facteur
# 3, ce qui déclenche la protection « decompression bomb » de Pillow et gaspille
# du temps. 3000 px sur le plus grand côté valent environ 300 dpi sur une A4.
COTE_MAX_IMAGE = 3000


@dataclass
class Document:
    """Un document prêt à convertir."""

    nom: str              # nom normalisé en NFC, sans extension
    source: Path          # le fichier d'origine, jamais modifié
    chemin: Path          # ce qu'on donne à Docling
    type_document: str    # pdf_natif | pdf_scanne | pdf_mixte | image
    pages: int
    normalise: bool = False


def detecter_type_pdf(chemin: Path) -> tuple[str, int]:
    """Classe un PDF d'après la couche texte réellement présente.

    Utilise pypdfium2 : c'est le seul backend qui expose un accès page par page,
    celui par défaut travaillant en flux.
    """
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument
    from docling_core.types.doc import BoundingBox, CoordOrigin

    entree = InputDocument(
        path_or_stream=chemin,
        format=InputFormat.PDF,
        backend=PyPdfiumDocumentBackend,
        filename=chemin.name,
    )
    backend = entree._backend
    try:
        nb_pages = backend.page_count()
        avec_texte = 0
        for numero in range(nb_pages):
            page = backend.load_page(numero)
            largeur, hauteur = page.get_size().as_tuple()
            cadre = BoundingBox(
                l=0, t=0, r=largeur, b=hauteur, coord_origin=CoordOrigin.TOPLEFT
            )
            try:
                texte = page.get_text_in_rect(cadre)
            except Exception:
                texte = ""
            if len(texte.strip()) > 50:
                avec_texte += 1
        if avec_texte == 0:
            return "pdf_scanne", nb_pages
        if avec_texte == nb_pages:
            return "pdf_natif", nb_pages
        return "pdf_mixte", nb_pages
    finally:
        backend.unload()


def _redresser(image, nom: str):
    """Une page prête pour l'OCR : orientée, en RGB, au plus COTE_MAX_IMAGE."""
    from PIL import Image, ImageOps

    # Docling applique l'orientation EXIF aux images qu'il lit, mais le
    # fichier réécrit ici perd l'EXIF : on tourne donc les pixels avant.
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if max(image.size) > COTE_MAX_IMAGE:
        # Dimensions relues : la rotation a pu échanger les côtés.
        facteur = COTE_MAX_IMAGE / max(image.size)
        taille = (round(image.width * facteur), round(image.height * facteur))
        image = image.resize(taille, Image.LANCZOS)
        _log.info("%s rééchantillonné en %dx%d", nom, *taille)
    return image


def normaliser_image(source: Path, dossier_travail: Path) -> tuple[Path, int] | None:
    """Rend une image lisible par Docling sans toucher à la source.

    Renvoie le fichier à convertir et son nombre de pages : un TIFF peut en
    porter plusieurs, que Docling lit comme les pages d'un PDF.
    """
    from PIL import Image

    multipage = source.suffix.lower() in EXTENSIONS_MULTIPAGES
    try:
        with Image.open(source) as image:
            pages = getattr(image, "n_frames", 1) if multipage else 1
            cote_max = 0
            for index in range(pages):
                image.seek(index)
                cote_max = max(cote_max, *image.size)
    except Exception as err:
        _log.error("Image illisible, %s ignoré : %s", source.name, err)
        return None

    lisible = source.suffix.lower() in EXTENSIONS_IMAGE
    if lisible and cote_max <= COTE_MAX_IMAGE:
        return source, pages

    dossier_travail.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as image:
            cadres = []
            for index in range(pages):
                image.seek(index)
                cadres.append(_redresser(image, source.name))
        # Le PNG ne porte qu'une image : plusieurs pages restent en TIFF.
        if pages > 1:
            cible = dossier_travail / f"{source.stem}.tif"
            cadres[0].save(cible, format="TIFF", save_all=True,
                           append_images=cadres[1:], compression="tiff_lzw")
        else:
            cible = dossier_travail / f"{source.stem}.png"
            cadres[0].save(cible, format="PNG")
    except Exception as err:
        _log.error("Normalisation impossible pour %s : %s", source.name, err)
        return None
    return cible, pages


def preparer(chemin: Path, dossier_travail: Path) -> Document | None:
    """Construit le Document correspondant à un fichier déposé."""
    extension = chemin.suffix.lower()
    # macOS stocke les noms de fichiers en NFD : « é » y est un « e » suivi d'un
    # accent combinant. On normalise pour que les noms restent comparables.
    nom = unicodedata.normalize("NFC", chemin.stem)

    if extension in EXTENSIONS_PDF:
        try:
            type_document, pages = detecter_type_pdf(chemin)
        except Exception as err:
            _log.warning("Type de %s indéterminé : %s", chemin.name, err)
            type_document, pages = "pdf_inconnu", 0
        return Document(nom, chemin, chemin, type_document, pages)

    if extension in EXTENSIONS_IMAGE | EXTENSIONS_A_TRANSCODER:
        normalisee = normaliser_image(chemin, dossier_travail)
        if normalisee is None:
            return None
        cible, pages = normalisee
        return Document(nom, chemin, cible, "image", pages, normalise=cible != chemin)

    _log.info("Extension non gérée, %s ignoré", chemin.name)
    return None
