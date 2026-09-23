"""Modèles Hugging Face nécessaires, présence dans le cache et téléchargement.

L'application tourne hors ligne (`HF_HUB_OFFLINE=1`) : aucun document ne peut
déclencher d'accès au réseau pendant une conversion. Le seul moment où la
machine parle au Hub est le téléchargement des modèles manquants, lancé
explicitement depuis l'interface et exécuté dans un processus à part.

Le cache est vérifié sans réseau : pour chaque révision, Hugging Face garde la
liste des fichiers du dépôt et leur taille (`trees/<commit>.json`). Un modèle
n'est tenu pour présent que si chacun de ces fichiers est dans l'instantané,
à la bonne taille — un téléchargement interrompu est donc repéré.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from functools import cache
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


@cache
def modeles() -> tuple[Modele, ...]:
    """Les trois dépôts, lus dans la configuration effective de Docling.

    Lire les dépôts dans les options plutôt que les recopier évite qu'une mise
    à jour de Docling change de révision sans que la vérification suive.
    """
    from mac_docling.moteur import _options_nanonets, _options_standard

    mise_en_page = _options_standard().layout_options.model_spec
    vlm = _options_nanonets(max_tokens=1).vlm_options
    moteur_vlm = vlm.engine_options.engine_type
    return (
        Modele(mise_en_page.repo_id, mise_en_page.revision,
               "analyse de la mise en page", "172 Mo", requis=True),
        # Docling écrit ce dépôt en dur dans TableStructureModel.download_models,
        # sans l'exposer dans ses options.
        Modele("docling-project/docling-models", "v2.3.0",
               "structure des tableaux", "358 Mo", requis=True),
        Modele(vlm.model_spec.get_repo_id(moteur_vlm),
               vlm.model_spec.get_revision(moteur_vlm),
               "bascule des pages faibles (Nanonets-OCR2)", "7,5 Go", requis=False),
    )


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
    if not instantane.is_dir():
        return False

    # La liste des fichiers fait foi. Les fichiers « .incomplete » ne servent
    # qu'à défaut : un téléchargement annulé en laisse, que le suivant ne
    # supprime pas une fois le modèle complet.
    arbre = depot / "trees" / f"{commit}.json"
    try:
        fichiers = json.loads(arbre.read_text())["files"]
    except (OSError, ValueError, KeyError) as err:
        # Cache écrit par une version plus ancienne de huggingface_hub.
        _log.info("Pas de liste de fichiers pour %s (%s)", modele.repo_id, err)
        return (any(instantane.iterdir())
                and not any((depot / "blobs").glob("*.incomplete")))
    return all(
        (instantane / nom).is_file() and (instantane / nom).stat().st_size == meta["size"]
        for nom, meta in fichiers.items()
    )


def manquants() -> list[Modele]:
    return [modele for modele in modeles() if not en_cache(modele)]


# --------------------------------------------------------------------------
# Téléchargement, dans un sous-processus
# --------------------------------------------------------------------------
#
# Le serveur reste hors ligne en permanence : le téléchargement tourne dans un
# processus à part, seul autorisé à joindre le Hub. Trois raisons :
# - le moteur de transfert xet (Rust) garde ses connexions ouvertes après
#   usage, et Python ne sait pas les fermer ; elles meurent avec le processus ;
# - aucun code du serveur ne peut atteindre le réseau, même pendant un
#   téléchargement ;
# - « Annuler » arrête vraiment le transfert, en tuant le processus.
#
# Le processus enfant écrit sur sa sortie standard une ligne « octets total »
# toutes les demi-secondes, et rien d'autre.


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


def _enfant(repo_id: str, revision: str, intervalle: float = 0.5) -> int:
    """Corps du processus de téléchargement."""
    from huggingface_hub import snapshot_download

    barres: list = []
    termine = threading.Event()

    def rapporter() -> None:
        while not termine.wait(intervalle):
            print(*_octets(barres), flush=True)

    threading.Thread(target=rapporter, daemon=True).start()
    try:
        snapshot_download(repo_id, revision=revision, tqdm_class=_suivi_tqdm(barres))
    finally:
        termine.set()
    print(*_octets(barres), flush=True)
    return 0


def commande_enfant(modele: Modele) -> list[str]:
    return [sys.executable, "-m", "mac_docling.modeles", modele.repo_id, modele.revision]


def telecharger(a_telecharger: list[Modele],
                commande: Callable[[Modele], list[str]] = commande_enfant,
                ) -> Iterator[Avancement]:
    """Télécharge les modèles un à un, en rendant l'avancement au fil de l'eau.

    Fermer le générateur (annulation) tue le processus en cours. `commande`
    remplace le processus enfant dans les tests.
    """
    environnement = {**os.environ, "HF_HUB_OFFLINE": "0"}
    for rang, modele in enumerate(a_telecharger, start=1):
        avancement = Avancement(rang, len(a_telecharger), modele)
        yield replace(avancement)

        # Les erreurs vont dans un fichier plutôt qu'un tube : un tube plein
        # bloquerait l'enfant, qu'on ne lit qu'à la fin.
        with tempfile.TemporaryFile("w+") as erreurs:
            processus = subprocess.Popen(
                commande(modele), stdout=subprocess.PIPE, stderr=erreurs,
                text=True, env=environnement,
            )
            try:
                for ligne in processus.stdout:
                    try:
                        avancement.octets, avancement.total = map(int, ligne.split())
                    except ValueError:
                        continue
                    yield replace(avancement)
                code = processus.wait()
            finally:
                if processus.poll() is None:
                    processus.kill()
                    processus.wait()
                processus.stdout.close()

            if code:
                erreurs.seek(0)
                dernieres = [ligne for ligne in erreurs.read().splitlines() if ligne.strip()]
                raise RuntimeError(dernieres[-1] if dernieres
                                   else f"le téléchargement a échoué (code {code})")

        avancement.octets = avancement.total
        avancement.fini = True
        yield replace(avancement)


if __name__ == "__main__":
    sys.exit(_enfant(*sys.argv[1:3]))
