"""Tests du journal chaîné et de la validation des paramètres."""

import json

import pytest

from seatrace import audit, config


def test_journal_detecte_une_modification(tmp_path):
    j = audit.Journal(tmp_path / "j.jsonl")
    for i in range(3):
        j.ajouter("test", {"i": i})
    assert j.verifier()[0]
    lignes = (tmp_path / "j.jsonl").read_text().splitlines()
    e = json.loads(lignes[1])
    e["details"]["i"] = 99
    lignes[1] = json.dumps(e)
    (tmp_path / "j.jsonl").write_text("\n".join(lignes) + "\n")
    ok, idx, _ = j.verifier()
    assert not ok and idx == 1


def test_journal_detecte_une_suppression(tmp_path):
    j = audit.Journal(tmp_path / "j.jsonl")
    for i in range(3):
        j.ajouter("test", {"i": i})
    lignes = (tmp_path / "j.jsonl").read_text().splitlines()
    (tmp_path / "j.jsonl").write_text("\n".join([lignes[0], lignes[2]]) + "\n")
    assert not j.verifier()[0]


@pytest.mark.parametrize("section,cle,valeur", [
    ("modele", "persistance_cap", 1.5), ("modele", "dispersion_relative", -1),
    ("detection", "marge_vitesse", "1.2"), ("import", "lignes_max", 10**9),
])
def test_parametres_hors_bornes_refuses(section, cle, valeur):
    p = config.copie(config.charger())
    p[section][cle] = valeur
    with pytest.raises(config.ErreurParametres):
        config.valider(p)


def test_empreinte_change_avec_parametres():
    p = config.charger()
    q = config.copie(p)
    q["modele"]["persistance_cap"] = 0.5
    assert config.empreinte(p) != config.empreinte(q)
