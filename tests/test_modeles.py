import json

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


def test_telechargement_interrompu(cache):
    _remplir(cache, {"config.json": 10})
    (cache / "blobs" / "123.incomplete").touch()
    assert not en_cache(MODELE)


def test_sans_liste_de_fichiers(cache):
    _remplir(cache, {"config.json": 10}, arbre=False)
    assert en_cache(MODELE)


def _faux_telechargement(repo_id, revision, tqdm_class):
    barre = tqdm_class(total=1000, unit="B")
    for _ in range(4):
        barre.update(250)
    barre.close()
    return "/nulle/part"


def test_telecharger_rend_l_avancement_et_retablit_le_hors_ligne(monkeypatch):
    vus = []

    def espion(repo_id, revision, tqdm_class):
        vus.append(constants.HF_HUB_OFFLINE)
        return _faux_telechargement(repo_id, revision, tqdm_class)

    etapes = list(telecharger([MODELE, MODELE], intervalle=0.01, telecharge=espion))
    assert vus == [False, False]
    assert constants.HF_HUB_OFFLINE is True
    finales = [e for e in etapes if e.fini]
    assert [e.rang for e in finales] == [1, 2]
    assert finales[0].octets == finales[0].total == 1000


def test_erreur_relancee_et_hors_ligne_retabli():
    def echec(repo_id, revision, tqdm_class):
        raise OSError("réseau coupé")

    with pytest.raises(OSError, match="réseau coupé"):
        list(telecharger([MODELE], intervalle=0.01, telecharge=echec))
    assert constants.HF_HUB_OFFLINE is True


def test_trois_modeles_dont_nanonets_facultatif():
    liste = modeles.modeles()
    assert len(liste) == 3
    assert [m.requis for m in liste] == [True, True, False]
    assert "Nanonets" in liste[2].repo_id
