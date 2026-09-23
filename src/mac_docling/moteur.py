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


def _nombre(valeur) -> float | None:
    try:
        valeur = float(valeur)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(valeur) else valeur


# --------------------------------------------------------------------------
# Événements émis vers l'interface
# --------------------------------------------------------------------------

@dataclass
class Evenement:
    """Un fait à afficher. `genre` dit comment l'interpréter."""

    genre: str            # document.debut | modele | page.fin | page.bascule
                          # | document.fin | erreur
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
            self._nanonets = self._construire(
                _options_nanonets(self.max_tokens_vlm), vlm=True
            )
        return self._nanonets

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


def _confiance(resultat) -> dict:
    """Note de la page : moyenne, mention, et détail par axe."""
    confiance = getattr(resultat, "confidence", None)
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


def convertir(document: Document, moteur: Moteur, seuil: str = "fair",
              routage_actif: bool = True,
              dedupliquer: bool = True) -> Iterator[Evenement]:
    """Convertit un document page par page en émettant un événement par étape.

    Le Markdown final assemble les pages, chaque page routée étant remplacée
    par la sortie du VLM, puis retire les en-têtes et pieds de page répétés.
    """
    yield Evenement(
        "document.debut", document.nom,
        f"{document.type_document}, {document.pages} page(s)",
        {"pages": document.pages, "type": document.type_document,
         "normalise": document.normalise},
    )

    debut_document = time.perf_counter()

    if moteur._standard is None:
        yield Evenement("modele", document.nom, "chargement du pipeline standard…")
        depart = time.perf_counter()
        moteur.standard()
        yield Evenement("modele", document.nom, "pipeline standard prêt",
                        {"ms": round((time.perf_counter() - depart) * 1000)})

    morceaux: list[str] = []
    pages_routees: list[int] = []
    total = max(document.pages, 1)

    for numero in range(1, total + 1):
        depart = time.perf_counter()
        try:
            resultat = moteur.standard().convert(
                document.chemin, raises_on_error=False, page_range=(numero, numero)
            )
        except Exception as err:
            yield Evenement("erreur", document.nom,
                            f"page {numero} : {type(err).__name__} — {err}",
                            {"page": numero})
            continue

        duree = round((time.perf_counter() - depart) * 1000)
        reussi = resultat.status.value in ("success", "partial_success")
        markdown = resultat.document.export_to_markdown() if reussi else ""
        confiance = _confiance(resultat)

        yield Evenement(
            "page.fin", document.nom, "",
            {"page": numero, "total": total, "config": "A_standard",
             "ms": duree, "etapes": _etapes(resultat),
             "confiance": confiance, "caracteres": len(markdown),
             "statut": resultat.status.value},
        )

        note = confiance.get("note")
        besoin = routage_actif and (
            not reussi
            or note_declenche(note, seuil)
            or len(markdown.strip()) < CARACTERES_MINIMUM
        )

        if besoin:
            motif = (f"note {note} ≤ seuil {seuil}" if note_declenche(note, seuil)
                     else "page quasi vide" if reussi else resultat.status.value)
            yield Evenement(
                "page.bascule", document.nom,
                f"page {numero} : {motif} → Nanonets-OCR2",
                {"page": numero, "motif": motif},
            )

            # Le chargement du VLM est chronométré à part : le mêler au temps
            # de la page rendrait la première bascule incomparable aux suivantes.
            if not moteur.nanonets_charge:
                yield Evenement("modele", document.nom,
                                "chargement de Nanonets-OCR2…")
                depart = time.perf_counter()
                moteur.nanonets()
                yield Evenement(
                    "modele", document.nom, "Nanonets-OCR2 prêt",
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
                    if vlm.status.value in ("success", "partial_success") else ""
                )
            except Exception as err:
                markdown_vlm = ""
                yield Evenement("erreur", document.nom,
                                f"page {numero} via Nanonets : {err}",
                                {"page": numero})

            duree_vlm = round((time.perf_counter() - depart) * 1000)
            anomalie = surveillance.diagnostiquer(markdown_vlm, 1)

            # On n'adopte la sortie du VLM que si elle apporte vraiment du
            # texte et ne boucle pas.
            adoptee = bool(markdown_vlm.strip()) and not anomalie and (
                len(markdown_vlm.strip()) > len(markdown.strip())
            )
            if adoptee:
                markdown = markdown_vlm
                pages_routees.append(numero)

            yield Evenement(
                "page.fin", document.nom, anomalie,
                {"page": numero, "total": total, "config": "C_nanonets",
                 "ms": duree_vlm, "etapes": {},
                 "confiance": {}, "caracteres": len(markdown_vlm),
                 "adoptee": adoptee, "alerte": anomalie,
                 "statut": "ecartee" if not adoptee else "success"},
            )

        morceaux.append(markdown)

    complet, rapport = deduplication.assembler(morceaux, actif=dedupliquer)
    yield Evenement(
        "document.fin", document.nom, rapport.resume(),
        {"ms": round((time.perf_counter() - debut_document) * 1000),
         "pages_routees": pages_routees, "caracteres": len(complet),
         "markdown": complet, "lignes_retirees": rapport.lignes_retirees},
    )
