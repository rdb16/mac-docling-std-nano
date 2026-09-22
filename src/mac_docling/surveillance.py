"""Détection des sorties anormales d'un modèle de vision.

Nanonets-OCR2 est lancé sans chaîne d'arrêt et avec un plafond de tokens
élevé : il peut répéter un bloc indéfiniment. Ces contrôles repèrent le cas
pour qu'on puisse écarter la sortie plutôt que de la livrer.
"""

from __future__ import annotations

import re

SEUIL_LIGNES_REPETEES = 5
SEUIL_CARACTERES_PAR_PAGE = 20_000
SEUIL_TAUX_DUPLICATION = 0.5

# « |---|:--:| » : mise en forme d'un tableau, faite de motifs répétés.
_SEPARATEUR_TABLEAU = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_MOTIF_REPETE = re.compile(r"(.{10,60}?)\1{6,}", re.DOTALL)


def lignes_repetees_max(markdown: str) -> int:
    """Plus longue suite de lignes non vides identiques qui se suivent."""
    maximum = courant = 0
    precedente = None
    for ligne in markdown.splitlines():
        depouillee = ligne.strip()
        if not depouillee:
            precedente, courant = None, 0
            continue
        courant = courant + 1 if depouillee == precedente else 1
        precedente = depouillee
        maximum = max(maximum, courant)
    return maximum


def taux_lignes_dupliquees(markdown: str) -> float:
    """Part des lignes substantielles qui répètent une ligne déjà vue.

    Une boucle répète souvent un bloc entier : les répétitions ne sont alors
    pas consécutives et échappent au compteur précédent.
    """
    lignes = [
        ligne.strip() for ligne in markdown.splitlines()
        if len(ligne.strip()) >= 20 and not _SEPARATEUR_TABLEAU.match(ligne)
    ]
    if len(lignes) < 20:
        return 0.0
    return 1.0 - len(set(lignes)) / len(lignes)


def diagnostiquer(markdown: str, pages: int = 1) -> str:
    """Renvoie une description des anomalies, ou une chaîne vide."""
    alertes = []

    repetitions = lignes_repetees_max(markdown)
    if repetitions >= SEUIL_LIGNES_REPETEES:
        alertes.append(f"{repetitions} lignes identiques consécutives")

    if pages > 0 and len(markdown) / pages > SEUIL_CARACTERES_PAR_PAGE:
        alertes.append(f"{len(markdown) // max(pages, 1)} caractères par page")

    texte = "\n".join(
        ligne for ligne in markdown.splitlines()
        if not _SEPARATEUR_TABLEAU.match(ligne)
    )
    trouve = _MOTIF_REPETE.search(texte)
    # Un motif de ponctuation répétée est une décoration, pas une boucle.
    if trouve and sum(c.isalnum() for c in trouve.group(1)) >= 3:
        alertes.append(f"motif répété en boucle : {trouve.group(1)[:40]!r}")

    duplication = taux_lignes_dupliquees(markdown)
    if duplication > SEUIL_TAUX_DUPLICATION:
        alertes.append(f"{duplication:.0%} de lignes dupliquées")

    return " ; ".join(alertes)
