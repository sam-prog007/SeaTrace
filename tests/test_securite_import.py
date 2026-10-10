"""Tests de sécurité de l'import et des exports."""

import pytest

from seatrace import config, report, schema

P = config.charger()["import"]
ENTETE = "boat_id,trip_id,timestamp,lat,lon,sog_kn,cog_deg,accuracy_m,source,synthetic\n"
LIGNE = "B1,T1,2026-07-14T08:00:{s:02d}Z,47.30,-7.50,10,90,10,tracker,true\n"


def csv_valide(n=20):
    return (ENTETE + "".join(LIGNE.format(s=i % 60) for i in range(n))).encode()


def test_import_valide():
    r = schema.lire_csv_securise(csv_valide(), "ok.csv", P)
    assert len(r.donnees) == 20 and r.lignes_rejetees == 0
    assert len(r.empreinte) == 64 and r.nom_interne.startswith("import_")


@pytest.mark.parametrize("contenu,motif", [
    (b"", "vide"),
    (b"\x00\x01\x02binaire", "binaire"),
    ("é".encode("latin-1") + b",x\n", "UTF-8"),
    (b"a,b,c\n1,2,3\n", "obligatoires"),
    (ENTETE.encode(), "Aucune ligne"),
])
def test_fichiers_refuses(contenu, motif):
    with pytest.raises(schema.ErreurImport, match=motif):
        schema.lire_csv_securise(contenu, "x.csv", P)


def test_fichier_trop_gros():
    with pytest.raises(schema.ErreurImport, match="volumineux"):
        schema.lire_csv_securise(b"a" * (P["taille_max_octets"] + 1), "gros.csv", P)


def test_trop_de_lignes():
    petit = dict(P, lignes_max=10)
    with pytest.raises(schema.ErreurImport, match="Trop de lignes"):
        schema.lire_csv_securise(csv_valide(20), "x.csv", petit)


def test_lignes_invalides_ecartees_ou_fichier_refuse():
    mauvaises = [
        "=cmd|' /C calc'!A0,T1,2026-07-14T08:00:00Z,47.3,-7.5,1,1,1,tracker,true\n",  # formule dans un id
        "<script>,T1,2026-07-14T08:00:00Z,47.3,-7.5,1,1,1,tracker,true\n",          # HTML dans un id
        "B1,T1,2026-07-14T08:00:00,47.3,-7.5,1,1,1,tracker,true\n",                 # sans fuseau
        "B1,T1,2026-07-14T08:00:00Z,147.3,-7.5,1,1,1,tracker,true\n",               # latitude hors bornes
        "B1,T1,2026-07-14T08:00:00Z,nan,-7.5,1,1,1,tracker,true\n",                 # NaN
        "B1,T1,2099-07-14T08:00:00Z,47.3,-7.5,1,1,1,tracker,true\n",                # futur
        "B1,T1,2026-07-14T08:00:00Z,47.3,-7.5,1,1,1,exec,true\n",                   # source inconnue
        "B1,T1,2026-07-14T08:00:00Z,47.3,-7.5,1,1,1,tracker,true,extra\n",          # colonnes en trop
        "B1,T1,2026-07-14T08:00:00Z," + "4" * 100 + ",-7.5,1,1,1,tracker,true\n",  # champ trop long
    ]
    # 2 mauvaises lignes sur 22 : écartées, le reste est analysé
    r = schema.lire_csv_securise(csv_valide(20) + "".join(mauvaises[:2]).encode(), "x.csv", P)
    assert r.lignes_rejetees == 2 and len(r.donnees) == 20
    # trop de lignes invalides : refus global plutôt qu'analyse partielle trompeuse
    with pytest.raises(schema.ErreurImport, match="invalides"):
        schema.lire_csv_securise((ENTETE + "".join(mauvaises) + LIGNE.format(s=1)).encode(), "x.csv", P)


def test_nom_fourni_jamais_utilise_comme_chemin(tmp_path):
    r = schema.lire_csv_securise(csv_valide(), "../../etc/passwd", P)
    assert r.nom_fourni_affichable == "passwd"
    chemin = schema.conserver_original(csv_valide(), r, tmp_path)
    assert chemin.parent == tmp_path.resolve()
    assert chemin.name == r.nom_interne


def test_original_jamais_ecrase(tmp_path):
    r = schema.lire_csv_securise(csv_valide(), "a.csv", P)
    c = schema.conserver_original(csv_valide(), r, tmp_path)
    c.write_bytes(b"original")
    schema.conserver_original(csv_valide(), r, tmp_path)
    assert c.read_bytes() == b"original"


@pytest.mark.parametrize("valeur", ["=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx"])
def test_neutralisation_formules(valeur):
    assert schema.neutraliser_tableur(valeur).startswith("'")


def test_rapport_html_echappe_les_champs():
    import pandas as pd
    rapport = report.construire({
        "dossier": "<script>alert(1)</script>", "bateau": {"boat_id": "<img src=x onerror=alert(1)>"},
        "trip_id": "T", "periode_donnees": "-", "sources": "-", "empreinte_fichier": "-",
        "mention_synthetique": "", "qualite": {}, "score_anomalie": 0, "anomalies": [],
        "ecart_trajet_prevu": None, "reconstruction": None, "versions": {}, "decision_humaine":
        {"decision": "aucune", "note": "<b>note</b>"}, "limites": []})
    h = report.vers_html(rapport, None)
    assert "<script>alert" not in h and "<img src=x" not in h and "&lt;script&gt;" in h
    assert "Content-Security-Policy" in h
