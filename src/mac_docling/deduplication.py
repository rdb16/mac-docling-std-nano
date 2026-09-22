"""Retrait des en-têtes et pieds de page répétés d'une page à l'autre.

Les pages sont converties séparément : chacune rapporte l'en-tête et le pied
du document. Concaténées telles quelles, ces lignes reviennent autant de fois
qu'il y a de pages. Sur un compte rendu de laboratoire de treize pages, cela
représentait un tiers des lignes produites.

Le critère est la fréquence **entre les pages**, pas la position dans la page :
après remise en ordre de lecture, Docling place souvent l'en-tête au milieu du
Markdown. Une ligne vue sur la plupart des pages est un en-tête ou un pied,
où qu'elle se trouve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Proportion de pages où une ligne doit apparaître pour être tenue pour
# répétitive. 0,6 laisse passer une date présente sur cinq pages sur treize,
# qui est du contenu, et retient un pied présent sur onze.
RATIO_PAR_DEFAUT = 0.6

# En dessous de trois pages, deux lignes identiques ne prouvent rien.
PAGES_MINIMUM = 3

# Une ligne très longue est du contenu, pas un en-tête.
LONGUEUR_MAXIMALE = 200

_LIGNE_TABLEAU = re.compile(r"^\s*\|")


@dataclass
class Rapport:
    """Ce que la déduplication a retiré."""

    lignes_retirees: int = 0
    motifs: list[tuple[str, int]] = field(default_factory=list)

    def resume(self) -> str:
        if not self.lignes_retirees:
            return ""
        exemples = ", ".join(f"{m[:38]!r}×{n}" for m, n in self.motifs[:3])
        return (f"{self.lignes_retirees} ligne(s) d'en-tête ou de pied retirée(s)"
                + (f" — {exemples}" if exemples else ""))


def _eligible(ligne: str) -> bool:
    """Une ligne peut-elle être un en-tête ou un pied de page ?"""
    depouillee = ligne.strip()
    if not depouillee or len(depouillee) > LONGUEUR_MAXIMALE:
        return False
    # Les lignes de tableau sont du contenu : deux pages peuvent légitimement
    # porter la même ligne de données.
    return not _LIGNE_TABLEAU.match(depouillee)


def reperer_repetitions(pages: list[str], ratio: float = RATIO_PAR_DEFAUT,
                        pages_minimum: int = PAGES_MINIMUM) -> dict[str, int]:
    """Lignes vues sur assez de pages pour être des en-têtes ou des pieds.

    Renvoie {ligne : nombre de pages où elle apparaît}.
    """
    if len(pages) < pages_minimum:
        return {}

    presence: dict[str, int] = {}
    for page in pages:
        vues = {
            ligne.strip() for ligne in page.splitlines() if _eligible(ligne)
        }
        for ligne in vues:
            presence[ligne] = presence.get(ligne, 0) + 1

    exigence = max(pages_minimum, round(ratio * len(pages)))
    return {
        ligne: nombre for ligne, nombre in presence.items() if nombre >= exigence
    }


def assembler(pages: list[str], ratio: float = RATIO_PAR_DEFAUT,
              actif: bool = True) -> tuple[str, Rapport]:
    """Concatène les pages en retirant les répétitions d'en-tête et de pied.

    La première occurrence de chaque ligne répétitive est conservée : le
    document garde son en-tête une fois, sans le répéter à chaque page.
    """
    rapport = Rapport()
    pages = [p for p in pages if p.strip()]
    if not pages:
        return "", rapport

    if not actif:
        return "\n\n".join(p.strip() for p in pages) + "\n", rapport

    repetitions = reperer_repetitions(pages, ratio)
    if not repetitions:
        return "\n\n".join(p.strip() for p in pages) + "\n", rapport

    deja_vues: set[str] = set()
    morceaux: list[str] = []
    retraits: dict[str, int] = {}

    for page in pages:
        gardees: list[str] = []
        for ligne in page.splitlines():
            depouillee = ligne.strip()
            if depouillee in repetitions:
                if depouillee in deja_vues:
                    retraits[depouillee] = retraits.get(depouillee, 0) + 1
                    rapport.lignes_retirees += 1
                    continue
                deja_vues.add(depouillee)
            gardees.append(ligne)

        # Une page réduite à des lignes vides ne mérite pas de séparateur.
        texte = "\n".join(gardees).strip()
        texte = re.sub(r"\n{3,}", "\n\n", texte)
        if texte:
            morceaux.append(texte)

    rapport.motifs = sorted(retraits.items(), key=lambda paire: -paire[1])
    return "\n\n".join(morceaux) + "\n", rapport
