"""Pont carte du Kattegat -> algorithme : export (src/13_seatrace_export.py), couches livrées avec un jeu local,
grille centrée sur la période, courant du jeu, et chaîne complète sur les bateaux test A, B, C."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
SYN = ROOT / "data" / "synthetic"
LAND = ROOT / "data" / "reference" / "kattegat_land.geojson"
H = {"X-SeaTrace": "1"}
ZONE = "SE0510127-A2"  # zone suédoise où toute pêche est interdite (Fladen), lon 11,65-11,80 à 57,17 N


def export_module():
    pytest.importorskip("polars")
    spec = importlib.util.spec_from_file_location("export13", ROOT / "src" / "13_seatrace_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- export (src/13)

def test_espacement_garde_les_bornes_des_trous():
    ex = export_module()
    t = np.concatenate([np.arange(0, 600, 10), np.arange(3300, 3900, 10)]).astype(float)  # trou de 45 min
    kept = t[ex.thin(t, 60)]
    assert 590 in kept and 3300 in kept, "dernière position avant le trou et première après"
    assert kept[0] == 0 and kept[-1] == 3890
    assert (np.diff(kept[:9]) == 60).all()  # flux régulier : une position par minute


def test_trajets_coupes_seulement_au_dela_de_six_heures():
    ex = export_module()
    import polars as pl
    t0 = pd.Timestamp("2026-09-15T00:00:00Z")
    secondes = np.concatenate([np.arange(0, 3600, 10), np.arange(3600 + 2700, 7200 + 2700, 10),  # trou de 45 min
                               np.arange(7200 + 2700 + 7 * 3600, 7200 + 2700 + 8 * 3600, 10)])  # trou de 7 h
    df = pl.DataFrame({
        "mmsi": ["219000001"] * len(secondes),
        "timestamp": pl.Series([t0 + pd.Timedelta(seconds=float(s)) for s in secondes]).dt.convert_time_zone("UTC"),
        "lat": 57.0 + secondes * 1e-6, "lon": 11.0 + secondes * 1e-6, "sog": 5.0, "cog": 45.0,
        "name": "TEST <b>", "length_m": 11.0, "synthetic": False, "scenario": None,
    }).with_columns(pl.col("scenario").cast(pl.String))
    pos, boats = ex.to_seatrace(df)
    assert pos["trip_id"].nunique() == 2
    premier = pd.to_datetime(pos[pos["trip_id"] == pos["trip_id"].iloc[0]]["timestamp"], utc=True)
    assert premier.diff().dt.total_seconds().max() == pytest.approx(2710, abs=1)  # le trou reste DANS le trajet
    assert boats["219000001"]["type_bateau"] == "ais_peche" and "<" not in boats["219000001"]["nom"]
    assert list(pos.columns) == ex.COLUMNS


def test_export_des_bateaux_test(tmp_path, monkeypatch):
    ex = export_module()
    if not (SYN / "boat_A_reported.parquet").exists():
        pytest.skip("data/synthetic absent")
    monkeypatch.setattr(ex, "OUT_AIS", tmp_path / "ais")
    monkeypatch.setattr(ex, "export_land", lambda: LAND if LAND.exists() else None)  # pas de téléchargement
    out = ex.build("2026-09-15", "2026-09-17", real=False, current=False, wind=False)
    pos = pd.read_csv(out, dtype={"boat_id": str})
    meta = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert set(pos["boat_id"]) == {"999000001", "999000002", "999000003"} and pos["synthetic"].all()
    assert len(pos) <= ex.import_limit()
    assert meta["couches"]["zones"] == "kattegat"
    a = pd.to_datetime(pos[pos["boat_id"] == "999000001"]["timestamp"], utc=True)
    assert a.diff().dt.total_seconds().max() >= 44 * 60  # le silence de 45 min du bateau A est dans un trajet
    assert "_truth" not in out.read_text(encoding="utf-8")[:2000] and "truth" not in json.dumps(meta)


# ---------------------------------------------------------------- interface : jeu local et ses couches

def trajet_fladen():
    """Chalutier fictif vers l'est à 6 nœuds le long de 57,17 N ; trou de ~58 min pendant la traversée de la zone."""
    t = pd.date_range("2026-09-15T08:00:00Z", periods=180, freq="60s")
    lon = 11.45 + np.arange(180) * 0.00307
    garde = (lon < 11.63) | (lon > 11.81)
    return pd.DataFrame({"boat_id": "999000009", "trip_id": "999000009-202609150800",
                         "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": 57.17, "lon": lon.round(6),
                         "sog_kn": 6.0, "cog_deg": 90.0, "accuracy_m": 10.0, "source": "ais",
                         "synthetic": True})[garde]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from seatrace import archive, pipeline
    import web.app as W
    d = tmp_path_factory.mktemp("carte")
    ais = d / "ais"
    ais.mkdir()
    trajet_fladen().to_csv(ais / "kattegat_test.csv", index=False)
    grille = [(t, la, lo) for t in pd.date_range("2026-09-15T06:00:00Z", periods=8, freq="1h")
              for la in np.arange(56.9, 57.41, 0.1) for lo in np.arange(11.3, 12.11, 0.1)]
    pd.DataFrame({"time": [g[0].strftime("%Y-%m-%dT%H:%M:%SZ") for g in grille], "latitude": [g[1] for g in grille],
                  "longitude": [g[2] for g in grille], "uo": 0.1, "vo": 0.0}).to_csv(ais / "courant.csv", index=False)
    (ais / "kattegat_test.json").write_text(json.dumps({
        "nom_jeu": "Kattegat map: test <script>", "bateaux": {"999000009": {"type_bateau": "ais_peche", "nom": "SYNTH T"}},
        "couches": {"zones": "kattegat", "terres": "data/reference/kattegat_land.geojson", "courants": "courant.csv",
                    "source_courants": "test current"}}), encoding="utf-8")
    (ais / "piege.csv").write_text((ais / "kattegat_test.csv").read_text(), encoding="utf-8")
    (ais / "piege.json").write_text(json.dumps({"couches": {"terres": "/etc/hosts", "courants": "../../config/parametres.json"}}),
                                    encoding="utf-8")
    archive.DOSSIER = d / "chemins"
    pipeline.CHEMIN_JOURNAL = d / "journal.jsonl"
    pipeline.DOSSIER_IMPORTS = d / "imports"
    ancien = W.DOSSIER_AIS
    W.DOSSIER_AIS = ais
    app = W.create_app()
    app.config["TESTING"] = True
    yield app.test_client()
    W.DOSSIER_AIS = ancien


def test_couches_livrees_avec_le_jeu(client):
    if not LAND.exists():
        pytest.skip("terres du Kattegat absentes")
    st = client.get("/api/state").get_json()
    jeu = next(x for x in st["datasets"] if x["id"] == "ais-kattegat_test")
    assert jeu["layers"]["land"] and jeu["layers"]["current"] and jeu["layers"]["zones"] >= 27
    assert "<" not in jeu["name"] and jeu["name"].startswith("Kattegat map")
    piege = client.get("/api/datasets/ais-piege/trips").get_json()
    assert not piege["layers"]["land"] and not piege["layers"]["current"]  # chemins hors data/ refusés
    assert sum("not found or not allowed" in w for w in piege["warnings"]) == 2


def test_trajet_reel_sur_plusieurs_jours_et_reconstruction_avec_courant(client):
    if not LAND.exists():
        pytest.skip("terres du Kattegat absentes")
    trip = "999000009-202609150800"
    a = client.get(f"/api/datasets/ais-kattegat_test/trips/{trip}").get_json()
    assert ZONE in {z["id"] for z in a["geography"]["zones"]}
    per = max(a["periods"], key=lambda p: p["duration_min"])
    assert per["duration_min"] > 50 and per["start_utc"].startswith("2026-09-15T")
    r = client.post("/api/reconstruct", json={"dataset": "ais-kattegat_test", "trip": trip, "period": per["id"],
                                              "zone": ZONE, "models": ["hmm"], "current": "dataset"}, headers=H)
    assert r.status_code == 200, r.get_json()
    run = r.get_json()
    assert run["context"]["current_kmh"] == pytest.approx([0.36, 0.0], abs=1e-6)
    assert "mean of" in run["context"]["current_source"]
    assert run["context"]["grid_cell_km"] <= 0.3  # emprise : ce qui est atteignable en ~1 h à 16 nœuds
    assert run["models"]["hmm"]["crossing_probability"] > 0.8
    assert run["context"]["synthetic_note"].startswith("test boat")
    rep = client.get(f"/api/runs/{run['run_id']}/report.html").data.decode()
    assert "test current" in rep and "copy of a real track" in rep


def test_courant_du_jeu_absent_refuse(client):
    a = client.get("/api/datasets/demo/trips/S5-trou-zone").get_json()
    per = max(a["periods"], key=lambda p: p["duration_min"])["id"]
    r = client.post("/api/reconstruct", json={"dataset": "demo", "trip": "S5-trou-zone", "period": per, "zone": "Z1",
                                              "models": ["hmm"], "current": "dataset"}, headers=H)
    assert r.status_code == 400 and "no current table" in r.get_json()["error"]


def test_reconstruction_sans_zone(client):
    """Sans zone choisie, aucun modèle ne donne de probabilité : le comparatif ne doit pas échouer."""
    a = client.get("/api/datasets/demo/trips/S5-trou-zone").get_json()
    per = max(a["periods"], key=lambda p: p["duration_min"])["id"]
    r = client.post("/api/reconstruct", json={"dataset": "demo", "trip": "S5-trou-zone", "period": per, "zone": None,
                                              "models": ["hmm", "appris", "particules"]}, headers=H)
    assert r.status_code == 200, r.get_json()
    run = r.get_json()
    assert run["synthesis"]["code"] == "sans_zone"
    assert all(m["crossing_probability"] is None for m in run["models"].values())


# ---------------------------------------------------------------- chaîne complète sur les bateaux test de la carte

def test_bateaux_test_de_la_carte_bout_en_bout(tmp_path, monkeypatch):
    """A : le silence de 45 min est trouvé et le passage dans la zone est jugé certain ; B : le bloc décalé de
    10 km pendant 60 min est entièrement mis à l'écart. (Valeurs de référence : data/synthetic/summary_truth.json.)"""
    ex = export_module()
    if not (SYN / "boat_A_reported.parquet").exists() or not LAND.exists():
        pytest.skip("bateaux test ou terres absents")
    from seatrace import archive, config, detect, pipeline, schema
    import web.app as W
    monkeypatch.setattr(ex, "OUT_AIS", tmp_path / "ais")
    monkeypatch.setattr(ex, "export_land", lambda: LAND)
    out = ex.build("2026-09-15", "2026-09-17", real=False, current=False, wind=False)
    verite = {b["scenario"]: b for b in json.loads((SYN / "summary_truth.json").read_text())["boats"]}
    monkeypatch.setattr(archive, "DOSSIER", tmp_path / "chemins")
    monkeypatch.setattr(pipeline, "CHEMIN_JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(W, "DOSSIER_AIS", tmp_path / "ais")
    c = W.create_app().test_client()
    did = f"ais-{out.stem}"
    trips = {t["boat_id"]: t["trip_id"] for t in c.get(f"/api/datasets/{did}/trips").get_json()["trips"]}

    a = c.get(f"/api/datasets/{did}/trips/{trips['999000001']}").get_json()
    per = [p for p in a["periods"] if p["start_utc"] <= verite["A"]["end"] and verite["A"]["start"] <= p["end_utc"]]
    assert len(per) == 1 and 44 <= per[0]["duration_min"] <= 47
    assert not [x for x in a["alerts"] if x["category"] == "integrity"], "pas de fausse alerte sur un vrai trajet"
    r = c.post("/api/reconstruct", json={"dataset": did, "trip": trips["999000001"], "period": per[0]["id"],
                                         "zone": verite["A"]["zone_ids"][0], "models": ["hmm"]}, headers=H).get_json()
    assert r["models"]["hmm"]["crossing_probability"] > 0.95
    assert r["context"]["grid_cell_km"] < 0.3  # sur tout le trajet de 3 jours (~90 km), les cases feraient ~0,6 km

    P = config.charger()
    pos = schema.lire_csv_securise(out.read_bytes(), out.name,
                                   {**P["import"], "taille_max_octets": P["import"]["taille_max_ais_octets"]}).donnees
    b = pos[pos["boat_id"] == "999000002"]
    det = detect.analyser_trajet(b, P["types_bateau"]["ais_peche"], P, pipeline.origine_pour(pos))
    pts = det.points
    fen = (pts["timestamp"] >= pd.Timestamp(verite["B"]["start"])) & (pts["timestamp"] < pd.Timestamp(verite["B"]["end"]))
    assert fen.sum() > 30 and (pts.loc[fen, "statut"] == "suspecte").all()
    assert "rejeu" not in {x.type for x in det.alertes}
