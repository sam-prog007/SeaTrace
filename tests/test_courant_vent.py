"""Courant et vent branchés sur les modèles : lecture des tables, dérive, sensibilité, interface."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from seatrace import comparatif as CP, config, geo, pipeline, reconstruct as R, sources

P = config.charger()
PT = P["types_bateau"]["semi_rigide_location"]
G = geo.geographie_demo()
GRILLE = geo.construire_grille(G, P["modele"]["taille_case_km"])
H = {"X-SeaTrace": "1"}


def obs(x, y, t):
    return R.Observation(x, y, 0.05, t)


def test_vent_lu_en_direction_vers_ou_il_va():
    """Open-Meteo donne la direction D'OÙ vient le vent : un vent de nord pousse vers le sud."""
    csv = ("time,latitude,longitude,wind_speed_kn,wind_from_deg\n"
           "2026-09-15T00:00:00Z,56.0,11.0,10,0\n"
           "2026-09-15T00:00:00Z,56.0,11.5,10,90\n"
           "2026-09-15T00:00:00Z,56.0,12.0,500,90\n").encode()
    t = sources.lire_vents(csv)
    assert len(t) == 2  # 500 nœuds : aberrant, retiré
    assert t.loc[0, "u_kmh"] == pytest.approx(0.0, abs=1e-9) and t.loc[0, "v_kmh"] == pytest.approx(-18.52)
    assert t.loc[1, "u_kmh"] == pytest.approx(-18.52) and t.loc[1, "v_kmh"] == pytest.approx(0.0, abs=1e-9)


def test_vent_noms_open_meteo_acceptes():
    csv = b"time,lat,lon,wind_speed_10m,wind_direction_10m\n2026-09-15T00:00:00Z,56,11,5,180\n"
    t = sources.lire_vents(csv)
    assert t.loc[0, "v_kmh"] == pytest.approx(5 * 1.852)  # vent de sud : vers le nord
    with pytest.raises(ValueError):
        sources.lire_vents(b"time,lat,lon,u,v\n2026-09-15T00:00:00Z,56,11,1,1\n")


def test_moyenne_de_vent_sur_l_emprise_et_le_trou():
    """La même moyenne spatio-temporelle que le courant (pipeline.courant_du_jeu) sert au vent."""
    origine = (56.0, 11.0)
    t0 = pd.Timestamp("2026-09-15T10:00:00Z")
    lignes = ["time,latitude,longitude,wind_speed_kn,wind_from_deg"]
    for h, vit in ((9, 30), (10, 10), (11, 10), (14, 30)):  # 9 h et 14 h : hors du trou (± 30 min)
        lignes.append(f"{(t0 + pd.Timedelta(hours=h - 10)).strftime('%Y-%m-%dT%H:%M:%SZ')},56.0,11.0,{vit},270")
    lignes.append(f"{t0.strftime('%Y-%m-%dT%H:%M:%SZ')},58.0,13.0,40,270")  # loin : hors emprise
    table = sources.lire_vents("\n".join(lignes).encode())
    u, v, n = pipeline.courant_du_jeu(table, (-5.0, 5.0, -5.0, 5.0), origine, t0, t0 + pd.Timedelta(hours=1))
    assert n == 2 and u == pytest.approx(10 * 1.852) and v == pytest.approx(0.0, abs=1e-9)  # vent d'ouest


def test_derive_courant_plus_part_du_vent():
    p = config.copie(P)
    p["modeles"].update(courant_kmh=[1.0, 0.0], vent_kmh=[0.0, -20.0], derive_vent=0.03)
    d, courant, vent, k = CP.derive(p)
    assert d == pytest.approx((1.0, -0.6)) and courant == (1.0, 0.0) and vent == (0.0, -20.0) and k == 0.03
    sans, double = CP.variantes_vent(courant, vent, k)
    assert sans[1] == (1.0, 0.0) and double[1] == pytest.approx((1.0, -1.2))
    assert CP.variantes_vent(courant, (0.0, 0.0), k) == []  # sans vent : aucune variante


def test_parametres_de_vent_valides():
    p = config.copie(P)
    p["modeles"]["derive_vent"] = 0.5
    with pytest.raises(config.ErreurParametres):
        config.valider(p)
    p = config.copie(P)
    p["modeles"]["vent_kmh"] = [0.0, 400.0]
    with pytest.raises(config.ErreurParametres):
        config.valider(p)


def test_sensibilite_garde_le_courant_de_reference():
    """Avant correction, toutes les variantes étaient recalculées SANS courant : la ligne « hypothèses de
    référence » ne correspondait pas au résultat affiché quand un courant était utilisé."""
    deb, fin = obs(13.8, 7.5, 0), obs(8.8, 5.4, 25 * 60)
    courant = (6.0, -3.0)
    ref = R.reconstruire(GRILLE, deb, fin, PT, P["modele"], "Z1", echantillonner=False, courant_kmh=courant)
    sans = R.reconstruire(GRILLE, deb, fin, PT, P["modele"], "Z1", echantillonner=False)
    assert abs(ref.proba_passage - sans.proba_passage) > 1e-3  # le courant change bien le résultat
    sens = R.analyse_sensibilite(GRILLE, deb, fin, PT, P["modele"], "Z1", courant_kmh=courant,
                                 variantes_derive=[("no wind drift", (0.0, 0.0))])
    assert sens[0]["proba_passage"] == pytest.approx(ref.proba_passage)
    ligne = next(l for l in sens if l["variation"] == "no wind drift")
    assert ligne["proba_passage"] == pytest.approx(sans.proba_passage)


def test_reconstruire_tous_applique_la_derive_et_ajoute_les_variantes_de_vent():
    p = config.copie(P)
    p["modeles"].update(courant_kmh=[1.0, 0.5], vent_kmh=[30.0, 0.0], derive_vent=0.03)
    comp = CP.reconstruire_tous(GRILLE, obs(13.8, 7.5, 0), obs(8.8, 5.4, 20 * 60), PT, p, "Z1",
                                modeles=["hmm"], graine=1)
    h = comp.hypotheses_communes
    assert h["derive_totale_kmh"] == pytest.approx([1.9, 0.5]) and h["vent_kmh"] == [30.0, 0.0]
    variations = [l["variation"] for l in comp.resultats["hmm"].diagnostics["sensitivity"]]
    assert any(v.startswith("no wind drift") for v in variations)
    assert any(v.startswith("wind drift × 2") for v in variations)


# ------------------------------------------------------------------------------ interface
ROOT = Path(__file__).resolve().parent.parent
CONTEXTE = ROOT / "data" / "processed" / "seatrace" / "context_2026-09-15_2026-09-17.json"


@pytest.fixture
def client():
    from web.app import create_app
    app = create_app()
    app.testing = True
    return app.test_client()


def test_web_bateau_test_reconstruit_avec_courant_et_vent_du_jeu(client):
    if not CONTEXTE.exists() or not (ROOT / "data" / "synthetic" / "boat_A_reported.parquet").exists():
        pytest.skip("tables de courant et de vent non exportées (python src/13_seatrace_export.py --context-only)")
    st = client.get("/api/state").get_json()
    lay = next(d for d in st["datasets"] if d["id"] == "kattegat-test")["layers"]
    assert lay["land"] and lay["current"] and lay["wind"]
    a = client.get("/api/datasets/kattegat-test/trips/TEST-A").get_json()
    gap = max(a["periods"], key=lambda p: p["duration_min"])
    r = client.post("/api/reconstruct", json={"dataset": "kattegat-test", "trip": "TEST-A", "period": gap["id"],
                                              "zone": "SE0510127-A2", "models": ["hmm"],
                                              "current": "dataset", "wind": "dataset"}, headers=H)
    assert r.status_code == 200, r.get_json()
    c = r.get_json()["context"]
    assert "mean of" in c["current_source"] and "mean of" in c["wind_source"]
    assert 0 < np.hypot(*c["wind_kmh"]) < 80  # vent réel de septembre sur le Kattegat, km/h
    k = c["wind_drift_share"]
    attendu = [cu + k * w for cu, w in zip(c["current_kmh"], c["wind_kmh"])]
    assert c["drift_kmh"] == pytest.approx(attendu, abs=2e-3)
    # sans vent demandé : dérive = courant seul
    r0 = client.post("/api/reconstruct", json={"dataset": "kattegat-test", "trip": "TEST-A", "period": gap["id"],
                                               "zone": "SE0510127-A2", "models": ["hmm"],
                                               "current": "dataset", "wind": [0, 0]}, headers=H).get_json()
    assert r0["context"]["wind_source"] == "none"
    assert r0["context"]["drift_kmh"] == pytest.approx(r0["context"]["current_kmh"], abs=2e-3)


def test_web_vent_du_jeu_refuse_sans_table(client):
    a = client.get("/api/datasets/demo/trips/S5-trou-zone").get_json()
    r = client.post("/api/reconstruct", json={"dataset": "demo", "trip": "S5-trou-zone", "period": a["periods"][0]["id"],
                                              "models": ["hmm"], "wind": "dataset"}, headers=H)
    assert r.status_code == 400 and "wind" in r.get_json()["error"]
