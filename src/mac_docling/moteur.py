"""Conversion locale page par page, avec bascule sur un VLM si besoin.

Tout se passe en local : aucun service d'inférence distant n'est sollicité
(`enable_remote_services` reste à False) et aucun document ne quitte la machine.

La conversion est faite page par page. Mesuré sur un rapport de quatre pages,
cela coûte 1,1× le temps d'une conversion du document entier, pour des notes de
confiance identiques — et cela permet deux choses que la conversion globale
interdit : émettre un résultat dès qu'une page est prête, et n'envoyer au VLM
que les pages réellement faibles. La note moyenne d'un document masque ses
pages faibles : un rapport noté « good » à 0,83 contenait une page à 0,67.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum

from docling.datamodel.settings import settings

# Doit être positionné avant la première conversion, sinon result.timings
# reste vide et l'on perd le détail par étape.
settings.debug.profile_pipeline_timings = True

from mac_docling import deduplication, surveillance  # noqa: E402
from mac_docling.documents import Document  # noqa: E402

_log = logging.getLogger(__name__)

# Ordre croissant de qualité, tel que Docling le définit :
# < 0,5 poor · < 0,8 fair · < 0,9 good · >= 0,9 excellent
ORDRE_NOTES = ["poor", "fair", "good", "excellent"]

ETAPES_AFFICHEES = [
    "page_parse", "layout", "ocr", "table_structure", "layout_postprocess",
]

CARACTERES_MINIMUM = 40   # en dessous, une page est considérée comme vide


def note_declenche(note: str | None, seuil: str) -> bool:
    """La note est-elle au niveau du seuil ou en dessous ?"""
    if not note or note == "unspecified":
        return True      # pas de note exploitable : on préfère la prudence
    if note not in ORDRE_NOTES:
        return False
    return ORDRE_NOTES.index(note) <= ORDRE_NOTES.index(seuil)


def motif_bascule(reussi: bool, statut: str, note: str | None, seuil: str,
                  markdown: str) -> str:
    """Pourquoi une page doit partir au VLM, ou chaîne vide si elle reste.

    L'échec passe en premier : une page en échec n'a souvent pas de note, et
    « note None ≤ seuil » masquerait la vraie cause.
    """
    if not reussi:
        return f"conversion en échec ({statut})"
    if note_declenche(note, seuil):
        return f"note {note} ≤ seuil {seuil}" if note in ORDRE_NOTES else "note absente"
    if len(markdown.strip()) < CARACTERES_MINIMUM:
        return "page quasi vide"
    return ""


def adopter_vlm(markdown_vlm: str, markdown_standard: str, anomalie: str) -> bool:
    """La sortie du VLM doit-elle remplacer celle du pipeline standard ?

    La page n'est partie au VLM que parce que le pipeline standard la jugeait
    faible : une sortie saine du VLM fait donc foi, même plus courte — un OCR
    raté produit souvent plus de caractères parasites que de texte juste. On
    ne garde le standard que si le VLM boucle ou ne rend presque rien.
    """
    vlm = markdown_vlm.strip()
    if not vlm or anomalie:
        return False
    return len(vlm) >= CARACTERES_MINIMUM or len(vlm) > len(markdown_standard.strip())


def _nombre(valeur) -> float | None:
    try:
        valeur = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(valeur) else valeur


# --------------------------------------------------------------------------
# Événements émis vers l'interface
# --------------------------------------------------------------------------

class Genre(StrEnum):
    """Nature d'un événement, qui dit comment l'interpréter."""

    DOCUMENT_DEBUT = "document.debut"
    MODELE = "modele"
    PAGE_FIN = "page.fin"
    PAGE_BASCULE = "page.bascule"
    DOCUMENT_FIN = "document.fin"
    ERREUR = "erreur"


class Config(StrEnum):
    """Pipeline qui a produit une page."""

    STANDARD = "A_standard"
    NANONETS = "C_nanonets"


@dataclass
class Evenement:
    """Un fait à afficher."""

    genre: Genre
    document: str = ""
    message: str = ""
    donnees: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Les deux configurations
# --------------------------------------------------------------------------

def _options_standard():
    """Pipeline standard : OCR ocrmac en français, TableFormer précis."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import (
        OcrMacOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )

    options = PdfPipelineOptions()
    options.enable_remote_services = False
    options.accelerator_options = AcceleratorOptions(device="auto")   # MPS
    options.do_ocr = True
    # Le code nu « fr » est refusé : Docling veut le tag BCP-47 complet.
    options.ocr_options = OcrMacOptions(lang=["fr-FR"], recognition="accurate")
    options.do_table_structure = True
    options.table_structure_options = TableStructureOptions(
        mode=TableFormerMode.ACCURATE, do_cell_matching=True
    )
    options.generate_page_images = False
    return options


def _options_nanonets(max_tokens: int):
    """Pipeline VLM Nanonets-OCR2-3B sur MLX, plafonné contre les boucles."""
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
    from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions

    vlm = VlmConvertOptions.from_preset(
        "nanonets_ocr2", engine_options=MlxVlmEngineOptions()
    )
    # Le preset monte à 15000 tokens sans chaîne d'arrêt : on plafonne.
    vlm.model_spec.max_new_tokens = min(vlm.model_spec.max_new_tokens, max_tokens)

    options = VlmPipelineOptions(vlm_options=vlm)
    options.enable_remote_services = False
    options.accelerator_options = AcceleratorOptions(device="auto")
    return options


class Moteur:
    """Garde les convertisseurs chauds entre deux documents."""

    def __init__(self, max_tokens_vlm: int = 6000) -> None:
        self.max_tokens_vlm = max_tokens_vlm
        self._standard = None
        self._nanonets = None
        # Cause de l'échec de chargement du VLM : on ne retente pas à chaque
        # page, il faut compléter le cache puis relancer le serveur.
        self.nanonets_echec: str | None = None

    @staticmethod
    def _construire(options, vlm: bool):
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import (
            DocumentConverter,
            ImageFormatOption,
            PdfFormatOption,
        )
        from docling.pipeline.vlm_pipeline import VlmPipeline

        supplement = {"pipeline_cls": VlmPipeline} if vlm else {}
        convertisseur = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=options, **supplement),
                InputFormat.IMAGE: ImageFormatOption(
                    pipeline_options=options, **supplement
                ),
            },
        )
        convertisseur.initialize_pipeline(InputFormat.PDF)
        convertisseur.initialize_pipeline(InputFormat.IMAGE)
        return convertisseur

    def standard(self):
        if self._standard is None:
            self._standard = self._construire(_options_standard(), vlm=False)
        return self._standard

    def nanonets(self):
        if self._nanonets is None:
            if self.nanonets_echec:
                raise RuntimeError(self.nanonets_echec)
            try:
                self._nanonets = self._construire(
                    _options_nanonets(self.max_tokens_vlm), vlm=True
                )
            except Exception as err:
                self.nanonets_echec = f"{type(err).__name__} — {err}"
                raise
        return self._nanonets

    @property
    def standard_charge(self) -> bool:
        return self._standard is not None

    @property
    def nanonets_charge(self) -> bool:
        return self._nanonets is not None


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------

def _etapes(resultat) -> dict[str, int]:
    """Durées par étape, en millisecondes."""
    mesures = {}
    for nom in ETAPES_AFFICHEES:
        profil = resultat.timings.get(nom)
        if profil and profil.times:
            mesures[nom] = round(sum(profil.times) * 1000)
    return mesures


def _confiance(confiance) -> dict:
    """Note d'une page : moyenne, mention, et détail par axe.

    Accepte le rapport d'un résultat comme les scores d'une de ses pages.
    """
    if confiance is None:
        return {}
    moyenne = _nombre(confiance.mean_score)
    return {
        "moyenne": round(moyenne, 3) if moyenne is not None else None,
        "note": confiance.mean_grade.value,
        "ocr": _nombre(confiance.ocr_score),
        "layout": _nombre(confiance.layout_score),
        "table": _nombre(confiance.table_score),
        "parse": _nombre(confiance.parse_score),
    }


STATUTS_REUSSIS = ("success", "partial_success")


@dataclass
class _PageStandard:
    """Ce que le pipeline standard a produit pour une page."""

    numero: int
    total: int
    statut: str
    markdown: str
    confiance: dict
    etapes: dict[str, int]
    ms: int

    @property
    def reussi(self) -> bool:
        return self.statut in STATUTS_REUSSIS


def _pages_une_a_une(document: Document, moteur: Moteur
                     ) -> Iterator[_PageStandard | Evenement]:
    """Convertit chaque page séparément : le cas nominal."""
    total = max(document.pages, 1)
    for numero in range(1, total + 1):
        depart = time.perf_counter()
        try:
            resultat = moteur.standard().convert(
                document.chemin, raises_on_error=False, page_range=(numero, numero)
            )
        except Exception as err:
            yield Evenement(Genre.ERREUR, document.nom,
                            f"page {numero} : {type(err).__name__} — {err}",
                            {"page": numero})
            continue

        statut = resultat.status.value
        yield _PageStandard(
            numero, total, statut,
            resultat.document.export_to_markdown() if statut in STATUTS_REUSSIS else "",
            _confiance(getattr(resultat, "confidence", None)),
            _etapes(resultat),
            round((time.perf_counter() - depart) * 1000),
        )


def _pages_globales(document: Document, moteur: Moteur
                    ) -> Iterator[_PageStandard | Evenement]:
    """Convertit le document d'un bloc, puis le découpe par page.

    Sert quand l'inventaire n'a pas su compter les pages : sans nombre de
    pages, la boucle page par page ne convertirait que la première. Le
    découpage garde une note par page, donc le routage vers le VLM ; seuls
    les temps, mesurés pour le document entier, sont répartis à parts égales.
    """
    depart = time.perf_counter()
    resultat = moteur.standard().convert(document.chemin, raises_on_error=False)
    duree = round((time.perf_counter() - depart) * 1000)

    statut = resultat.status.value
    total = resultat.document.num_pages() if resultat.document else 0
    if not total:
        yield Evenement(Genre.ERREUR, document.nom,
                        f"conversion globale sans aucune page ({statut})")
        return

    yield Evenement(Genre.MODELE, document.nom,
                    f"conversion globale : {total} page(s) trouvée(s)")
    etapes = {nom: round(ms / total) for nom, ms in _etapes(resultat).items()}
    rapport = getattr(resultat, "confidence", None)
    for numero in range(1, total + 1):
        yield _PageStandard(
            numero, total, statut,
            resultat.document.export_to_markdown(page_no=numero)
            if statut in STATUTS_REUSSIS else "",
            _confiance(rapport.pages.get(numero) if rapport else None),
            etapes, round(duree / total),
        )


def _vlm_indisponible(document: Document, moteur: Moteur) -> Evenement:
    return Evenement(
        Genre.ERREUR, document.nom,
        f"Nanonets-OCR2 indisponible ({moteur.nanonets_echec}) : les pages "
        "faibles restent en pipeline standard",
    )


def convertir(document: Document, moteur: Moteur, seuil: str = "fair",
              routage_actif: bool = True,
              dedupliquer: bool = True) -> Iterator[Evenement]:
    """Convertit un document page par page en émettant un événement par étape.

    Le Markdown final assemble les pages, chaque page routée étant remplacée
    par la sortie du VLM, puis retire les en-têtes et pieds de page répétés.
    """
    decompte = (f"{document.pages} page(s)" if document.pages > 0
                else "nombre de pages inconnu, conversion globale")
    yield Evenement(
        Genre.DOCUMENT_DEBUT, document.nom,
        f"{document.type_document}, {decompte}",
        {"pages": document.pages, "type": document.type_document,
         "normalise": document.normalise},
    )

    debut_document = time.perf_counter()

    if not moteur.standard_charge:
        yield Evenement(Genre.MODELE, document.nom, "chargement du pipeline standard…")
        depart = time.perf_counter()
        try:
            moteur.standard()
        except Exception as err:
            yield Evenement(Genre.ERREUR, document.nom,
                            f"pipeline standard indisponible : {type(err).__name__} — {err}")
            return
        yield Evenement(Genre.MODELE, document.nom, "pipeline standard prêt",
                        {"ms": round((time.perf_counter() - depart) * 1000)})

    morceaux: list[str] = []
    pages_routees: list[int] = []
    vlm_signale = False   # l'indisponibilité du VLM n'est dite qu'une fois
    pages = (_pages_une_a_une(document, moteur) if document.pages > 0
             else _pages_globales(document, moteur))

    for page in pages:
        if isinstance(page, Evenement):
            yield page
            continue

        numero, total, markdown, confiance = (
            page.numero, page.total, page.markdown, page.confiance
        )
        yield Evenement(
            Genre.PAGE_FIN, document.nom, "",
            {"page": numero, "total": total, "config": Config.STANDARD,
             "ms": page.ms, "etapes": page.etapes,
             "confiance": confiance, "caracteres": len(markdown),
             "statut": page.statut},
        )

        motif = motif_bascule(page.reussi, page.statut, confiance.get("note"),
                              seuil, markdown)

        if routage_actif and motif and moteur.nanonets_echec:
            if not vlm_signale:
                vlm_signale = True
                yield _vlm_indisponible(document, moteur)

        elif routage_actif and motif:
            yield Evenement(
                Genre.PAGE_BASCULE, document.nom,
                f"page {numero} : {motif} → Nanonets-OCR2",
                {"page": numero, "motif": motif},
            )

            # Le chargement du VLM est chronométré à part : le mêler au temps
            # de la page rendrait la première bascule incomparable aux suivantes.
            if not moteur.nanonets_charge:
                yield Evenement(Genre.MODELE, document.nom,
                                "chargement de Nanonets-OCR2…")
                depart = time.perf_counter()
                try:
                    moteur.nanonets()
                except Exception:
                    vlm_signale = True
                    yield _vlm_indisponible(document, moteur)
                    morceaux.append(markdown)
                    continue
                yield Evenement(
                    Genre.MODELE, document.nom, "Nanonets-OCR2 prêt",
                    {"ms": round((time.perf_counter() - depart) * 1000)},
                )

            depart = time.perf_counter()
            try:
                vlm = moteur.nanonets().convert(
                    document.chemin, raises_on_error=False,
                    page_range=(numero, numero),
                )
                markdown_vlm = (
                    vlm.document.export_to_markdown()
                    if vlm.status.value in STATUTS_REUSSIS else ""
                )
            except Exception as err:
                markdown_vlm = ""
                yield Evenement(Genre.ERREUR, document.nom,
                                f"page {numero} via Nanonets : {err}",
                                {"page": numero})

            duree_vlm = round((time.perf_counter() - depart) * 1000)
            anomalie = surveillance.diagnostiquer(markdown_vlm, 1)

            adoptee = adopter_vlm(markdown_vlm, markdown, anomalie)
            if adoptee:
                markdown = markdown_vlm
                pages_routees.append(numero)

            yield Evenement(
                Genre.PAGE_FIN, document.nom, anomalie,
                {"page": numero, "total": total, "config": Config.NANONETS,
                 "ms": duree_vlm, "etapes": {},
                 "confiance": {}, "caracteres": len(markdown_vlm),
                 "adoptee": adoptee, "alerte": anomalie,
                 "statut": "ecartee" if not adoptee else "success"},
            )

        morceaux.append(markdown)

    complet, rapport = deduplication.assembler(morceaux, actif=dedupliquer)
    yield Evenement(
        Genre.DOCUMENT_FIN, document.nom, rapport.resume(),
        {"ms": round((time.perf_counter() - debut_document) * 1000),
         "pages_routees": pages_routees, "caracteres": len(complet),
         "markdown": complet, "lignes_retirees": rapport.lignes_retirees},
    )
