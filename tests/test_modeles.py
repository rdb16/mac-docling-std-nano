import json
import os
import sys

import pytest
from huggingface_hub import constants

from mac_docling import modeles
from mac_docling.modeles import Modele, en_cache, telecharger

MODELE = Modele("org/depot", "main", "essai", "1 Mo", requis=True)
COMMIT = "abc123"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    return tmp_path / "models--org--depot"


def _remplir(depot, fichiers: dict[str, int], arbre: bool = True):
    (depot / "refs").mkdir(parents=True)
    (depot / "refs" / "main").write_text(COMMIT)
    (depot / "blobs").mkdir()
    instantane = depot / "snapshots" / COMMIT
    instantane.mkdir(parents=True)
    for nom, taille in fichiers.items():
        (instantane / nom).write_bytes(b"x" * taille)
    if arbre:
        (depot / "trees").mkdir()
        (depot / "trees" / f"{COMMIT}.json").write_text(json.dumps(
            {"format_version": 1,
             "files": {nom: {"size": taille} for nom, taille in fichiers.items()}}))
    return instantane


def test_absent(cache):
    assert not en_cache(MODELE)


def test_complet(cache):
    _remplir(cache, {"config.json": 10, "model.safetensors": 100})
    assert en_cache(MODELE)


def test_fichier_manquant_ou_tronque(cache):
    instantane = _remplir(cache, {"config.json": 10, "model.safetensors": 100})
    (instantane / "model.safetensors").write_bytes(b"x" * 50)
    assert not en_cache(MODELE)
    (instantane / "model.safetensors").unlink()
    assert not en_cache(MODELE)


def test_telechargement_interrompu_sans_liste(cache):
    _remplir(cache, {"config.json": 10}, arbre=False)
    (cache / "blobs" / "123.incomplete").touch()
    assert not en_cache(MODELE)


def test_restes_d_un_telechargement_annule_ignores_si_complet(cache):
    _remplir(cache, {"config.json": 10})
    (cache / "blobs" / "123.incomplete").touch()
    assert en_cache(MODELE)


def test_sans_liste_de_fichiers(cache):
    _remplir(cache, {"config.json": 10}, arbre=False)
    assert en_cache(MODELE)


def _commande(script: str):
    return lambda modele: [sys.executable, "-c", script]


def test_telecharger_rend_l_avancement():
    script = "import os\nfor n in (0, 500, 1000): print(n, 1000, flush=True)\n" \
             "assert os.environ['HF_HUB_OFFLINE'] == '0'"
    etapes = list(telecharger([MODELE, MODELE], commande=_commande(script)))
    assert [(e.rang, e.octets) for e in etapes if not e.fini][:4] == [
        (1, 0), (1, 0), (1, 500), (1, 1000)]
    finales = [e for e in etapes if e.fini]
    assert [(e.rang, e.octets, e.total) for e in finales] == [(1, 1000, 1000), (2, 1000, 1000)]


def test_erreur_de_l_enfant_relancee():
    script = "import sys\nprint('Traceback…', file=sys.stderr)\n" \
             "print('OSError: réseau coupé', file=sys.stderr)\nsys.exit(1)"
    with pytest.raises(RuntimeError, match="réseau coupé"):
        list(telecharger([MODELE], commande=_commande(script)))


def test_annulation_tue_l_enfant():
    script = ("import os, time\nprint(os.getpid(), 0, flush=True)\n"
              "while True:\n    time.sleep(0.05)")
    etapes = telecharger([MODELE], commande=_commande(script))
    next(etapes)
    pid = next(etapes).octets   # l'enfant a publié son pid en guise d'octets
    etapes.close()   # ce que fait Gradio à l'annulation
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_trois_modeles_dont_nanonets_facultatif():
    liste = modeles.modeles()
    assert len(liste) == 3
    assert [m.requis for m in liste] == [True, True, False]
    assert "Nanonets" in liste[2].repo_id
