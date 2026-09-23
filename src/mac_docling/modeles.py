"""Modèles Hugging Face nécessaires, présence dans le cache et téléchargement.

L'application tourne hors ligne (`HF_HUB_OFFLINE`) : aucun document ne peut
déclencher d'accès au réseau pendant une conversion. Le seul moment où la
machine parle au Hub est le téléchargement des modèles manquants, lancé
explicitement depuis l'interface ; le mode hors ligne est levé pour sa seule
durée, puis rétabli.

Le cache est vérifié sans réseau : pour chaque révision, Hugging Face garde la
liste des fichiers du dépôt et leur taille (`trees/<commit>.json`). Un modèle
n'est tenu pour présent que si chacun de ces fichiers est dans l'instantané,
à la bonne taille — un téléchargement interrompu est donc repéré.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Modele:
    """Un dépôt Hugging Face dont la conversion a besoin."""

    repo_id: str
    revision: str
    role: str
    taille: str        # indicative : la taille exacte vient du Hub au téléchargement
    requis: bool       # False : seule la bascule vers le VLM en a besoin


def modeles() -> list[Modele]:
    """Les trois dépôts, lus dans la configuration effective de Docling.

    Lire les dépôts dans les options plutôt que les recopier évite qu'une mise
    à jour de Docling change de révision sans que la vérification suive.
    """
    from mac_docling.moteur import _options_nanonets, _options_standard

    mise_en_page = _options_standard().layout_options.model_spec
    vlm = _options_nanonets(max_tokens=1).vlm_options
    moteur_vlm = vlm.engine_options.engine_type
    return [
        Modele(mise_en_page.repo_id, mise_en_page.revision,
               "analyse de la mise en page", "172 Mo", requis=True),
        # Docling écrit ce dépôt en dur dans TableStructureModel.download_models,
        # sans l'exposer dans ses options.
        Modele("docling-project/docling-models", "v2.3.0",
               "structure des tableaux", "358 Mo", requis=True),
        Modele(vlm.model_spec.get_repo_id(moteur_vlm),
               vlm.model_spec.get_revision(moteur_vlm),
               "bascule des pages faibles (Nanonets-OCR2)", "7,5 Go", requis=False),
    ]


# --------------------------------------------------------------------------
# Vérification du cache, sans réseau
# --------------------------------------------------------------------------

def _dossier_depot(modele: Modele) -> Path:
    from huggingface_hub import constants

    return Path(constants.HF_HUB_CACHE) / f"models--{modele.repo_id.replace('/', '--')}"


def en_cache(modele: Modele) -> bool:
    """Le modèle est-il entièrement présent dans le cache local ?"""
    depot = _dossier_depot(modele)
    reference = depot / "refs" / modele.revision
    if not reference.is_file():
        return False
    commit = reference.read_text().strip()
    instantane = depot / "snapshots" / commit
    if not instantane.is_dir() or any((depot / "blobs").glob("*.incomplete")):
        return False

    arbre = depot / "trees" / f"{commit}.json"
    if not arbre.is_file():
        # Cache écrit par une version plus ancienne de huggingface_hub : faute
        # de liste de fichiers, un instantané non vide fait foi.
        return any(instantane.iterdir())
    try:
        fichiers = json.loads(arbre.read_text())["files"]
    except (ValueError, KeyError) as err:
        _log.warning("Liste de fichiers illisible pour %s : %s", modele.repo_id, err)
        return any(instantane.iterdir())
    return all(
        (instantane / nom).is_file() and (instantane / nom).stat().st_size == meta["size"]
        for nom, meta in fichiers.items()
    )


def manquants() -> list[Modele]:
    return [modele for modele in modeles() if not en_cache(modele)]


# --------------------------------------------------------------------------
# Mode hors ligne
# --------------------------------------------------------------------------

def hors_ligne(actif: bool) -> None:
    """Coupe ou rétablit l'accès au Hub, en cours d'exécution.

    huggingface_hub lit HF_HUB_OFFLINE une fois, à l'import, dans
    `constants.HF_HUB_OFFLINE` ; chaque requête interroge ensuite
    `constants.is_offline_mode()`, qui relit cet attribut. Le modifier suffit
    donc, sans relancer le serveur. La variable d'environnement est tenue à
    jour pour les sous-processus.
    """
    from huggingface_hub import constants

    os.environ["HF_HUB_OFFLINE"] = "1" if actif else "0"
    constants.HF_HUB_OFFLINE = actif


# --------------------------------------------------------------------------
# Téléchargement
# --------------------------------------------------------------------------

@dataclass
class Avancement:
    """Où en est le téléchargement du modèle courant."""

    rang: int           # 1 pour le premier modèle à télécharger
    nombre: int         # nombre de modèles à télécharger
    modele: Modele
    octets: int = 0
    total: int = 0      # 0 tant que le Hub n'a pas donné la taille
    fini: bool = False


def _suivi_tqdm(barres: list):
    """Classe de barre de progression qui ne s'affiche pas mais se laisse lire.

    snapshot_download agrège la progression de tous les fichiers dans une
    barre « Downloading bytes ». On hérite du tqdm d'origine, et non de celui
    de huggingface_hub : ce dernier désactive ses barres hors terminal, et une
    barre désactivée ne compte plus rien.
    """
    from tqdm import tqdm

    class Suivi(tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["file"] = open(os.devnull, "w")  # fermé par close()
            super().__init__(*args, **kwargs)
            barres.append(self)

        def close(self):
            fichier = self.fp
            super().close()
            fichier.close()

    return Suivi


def _octets(barres: list) -> tuple[int, int]:
    """Octets reçus et attendus, lus sur la barre la plus avancée."""
    en_octets = [b for b in barres if b.unit == "B" and b.total]
    if not en_octets:
        return 0, 0
    barre = max(en_octets, key=lambda b: b.n / b.total)
    return int(barre.n), int(barre.total)


def telecharger(a_telecharger: list[Modele], intervalle: float = 0.5,
                telecharge: Callable[..., str] | None = None) -> Iterator[Avancement]:
    """Télécharge les modèles un à un, en rendant l'avancement au fil de l'eau.

    Le mode hors ligne n'est levé que pendant l'appel et toujours rétabli,
    même en cas d'erreur ou d'annulation. `telecharge` remplace
    snapshot_download dans les tests.
    """
    if telecharge is None:
        from huggingface_hub import snapshot_download as telecharge

    hors_ligne(False)
    try:
        for rang, modele in enumerate(a_telecharger, start=1):
            avancement = Avancement(rang, len(a_telecharger), modele)
            barres: list = []
            erreur: list[BaseException] = []
            termine = threading.Event()

            def tache(modele=modele, barres=barres, erreur=erreur, termine=termine):
                try:
                    telecharge(modele.repo_id, revision=modele.revision,
                               tqdm_class=_suivi_tqdm(barres))
                except BaseException as err:  # relancée dans le générateur
                    erreur.append(err)
                finally:
                    termine.set()

            threading.Thread(target=tache, daemon=True).start()
            yield replace(avancement)
            while not termine.wait(intervalle):
                avancement.octets, avancement.total = _octets(barres)
                yield replace(avancement)
            if erreur:
                raise erreur[0]
            avancement.octets, avancement.total = _octets(barres)
            avancement.octets = avancement.total
            avancement.fini = True
            yield replace(avancement)
    finally:
        hors_ligne(True)
