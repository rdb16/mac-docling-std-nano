#!/usr/bin/env python3
"""Vérifie qu'aucune donnée ne sort de la machine pendant une conversion.

Lance une conversion réelle à travers l'interface et échantillonne en continu
les connexions réseau du processus serveur. Toute connexion vers autre chose
que la boucle locale est signalée.

Usage : .venv/bin/python verifier_reseau.py <fichier> [<fichier>…]
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

URL = "http://127.0.0.1:7860/"
LOCALES = ("127.0.0.1", "[::1]", "localhost", "*:")


def pid_serveur() -> int | None:
    sortie = subprocess.run(
        ["pgrep", "-f", "mac_docling.app"], capture_output=True, text=True
    ).stdout.split()
    return int(sortie[0]) if sortie else None


def connexions(pid: int) -> set[str]:
    """Connexions réseau du processus, hors boucle locale."""
    try:
        brut = subprocess.run(
            ["lsof", "-nP", "-i", "-a", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except subprocess.TimeoutExpired:
        return set()

    suspectes = set()
    for ligne in brut.splitlines()[1:]:
        adresse = re.search(r"(TCP|UDP)\s+(\S+)", ligne)
        if not adresse:
            continue
        cible = adresse.group(2)
        if not any(cible.startswith(l) or f"->{l}" in cible for l in LOCALES):
            suspectes.add(f"{adresse.group(1)} {cible}")
    return suspectes


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2

    pid = pid_serveur()
    if pid is None:
        print("Le serveur n'est pas lancé. Démarrez ./lancer.sh d'abord.")
        return 1
    print(f"Serveur trouvé : PID {pid}")

    from gradio_client import Client, handle_file

    observees: set[str] = set()
    fini = threading.Event()

    def surveiller() -> None:
        while not fini.is_set():
            observees.update(connexions(pid))
            time.sleep(0.4)

    veilleur = threading.Thread(target=surveiller, daemon=True)
    veilleur.start()

    client = Client(URL, verbose=False)
    depart = time.perf_counter()
    try:
        # L'interface renvoie d'abord la barre d'avancement, puis le journal.
        _, etat, *_ = client.predict(
            fichiers=[handle_file(str(Path(c).resolve())) for c in argv],
            seuil="fair", routage_actif=True, dedupliquer=True,
            api_name="/traiter",
        )
    finally:
        fini.set()
        veilleur.join(timeout=2)

    duree = time.perf_counter() - depart
    print(f"\nConversion terminée en {duree:.1f} s.")
    print(etat.splitlines()[-1] if etat else "")

    print(f"\n{'=' * 60}")
    if observees:
        print("CONNEXIONS HORS BOUCLE LOCALE DÉTECTÉES :")
        for connexion in sorted(observees):
            print(f"  ✗ {connexion}")
        return 1
    print("Aucune connexion hors 127.0.0.1 pendant toute la conversion.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
