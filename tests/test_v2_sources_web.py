"""Tests des connecteurs de données ouvertes et de l'interface Flask."""

import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

from seatrace import config, geo, sources

P = config.charger()


def csv_dma(n=40):
    """Extrait SYNTHÉTIQUE au format des fichiers de l'Autorité maritime danoise."""
    lignes = ["# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,Heading,IMO,"
              "Callsign,Name,Ship type,Cargo type,Width,Length,Type of position fixing device,Draught,Destination,"
              "ETA,Data source type,A,B,C,D"]
    for i in range(n):
        lignes.append(f"15/06/2024 10:{i:02d}:00,Class B,219000001,{55.0 + i * 0.002:.6f},{10.5 + i * 0.002:.6f},"
                      "Under way using engine,,5.2,45.0,45,Unknown,,Sailboat <b>Test</b>,Sailing,,3,10,GPS,,,,AIS,,,,")
        lignes.append(f"15/06/2024 10:{i:02d}:30,Class B,219000001,{55.0 + i * 0.002 + 0.001:.6f},"
                      f"{10.5 + i * 0.002 + 0.001:.6f},Under way,,102.3,360.0,511,,,Sailboat <b>Test</b>,Sailing,,,,,,,,AIS,,,,")
    lignes.append("15/06/2024 10:00:00,Base Station,002190001,55.1,10.6,,,,,,,,,Undefined,,,,,,,,AIS,,,,")
    lignes.append("15/06/2024 10:00:00,Class A,219000002,91.0,181.0,,,,,,,,Cargo X,Cargo,,,,,,,,AIS,,,,")
    return ("\n".join(lignes) + "\n").encode("utf-8")


def csv_noaa(n=30):
    lignes = ["MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,"
              "Draft,Cargo,TransceiverClass"]
    for i in range(n):
        lignes.append(f"367000001,2024-01-01T10:{i:02d}:00,{29.0 + i * 0.001:.6f},{-90.0 + i * 0.001:.6f},"
                      "6.1,90.0,90,SEA BREEZE,,,37,0,12,4,1.5,,B")
    return ("\n".join(lignes) + "\n").encode("utf-8")


def test_conversion_dma():
    df, bateaux, info = sources.convertir_ais(csv_dma())
    assert info["format"] == "dma"
    assert set(df["boat_id"]) == {"219000001"}  # station de base et position invalide écartées
    assert bateaux["219000001"]["type_bateau"] == "ais_voilier"
    assert "<" not in bateaux["219000001"]["nom"]
    assert df["sog_kn"].isna().sum() > 0  # 102.3 = non disponible
    assert (df["cog_deg"].dropna() < 360).all()
    assert (df["source"] == "ais").all() and not df["synthetic"].any()


def test_conversion_dma_zip_et_filtres():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("aisdk-2024-06-15.csv", csv_dma())
    df, _, _ = sources.convertir_ais(buf.getvalue(), bbox=(54.9, 55.05, 10.4, 10.6))
    assert df["lat"].max() <= 55.05
    with pytest.raises(ValueError):
        sources.convertir_ais(csv_dma(), types=["ais_peche"])


def test_conversion_noaa_et_import_securise():
    from seatrace import schema
    df, bateaux, info = sources.convertir_ais(csv_noaa())
    assert info["format"] == "noaa" and bateaux["367000001"]["type_bateau"] == "ais_plaisance"
    res = schema.lire_csv_securise(df.to_csv(index=False).encode(), "noaa.csv", P["import"])
    assert res.lignes_rejetees == 0


def test_detection_format():
    assert sources.detecter_format(["boat_id", "trip_id", "timestamp", "lat", "lon"]) == "seatrace"
    assert sources.detecter_format(["# Timestamp", "MMSI"]) == "dma"
    assert sources.detecter_format(["mmsi", "base_date_time", "latitude"]) == "noaa"
    assert sources.detecter_format(["a", "b"]) == "inconnu"


def geojson_carre(lon0, lat0, d, nom="Île <script>"):
    return json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": nom},
        "geometry": {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon0 + d, lat0], [lon0 + d, lat0 + d],
                                                         [lon0, lat0 + d], [lon0, lat0]]]}}]}).encode()


def test_geojson_projete_et_decoupe():
    polys = sources.lire_geojson(geojson_carre(10.0, 54.9, 1.0))
    assert "<" not in polys[0][0]
    origine = (55.0, 10.5)
    km = sources.polygones_km(polys, origine, (-5, 5, -5, 5))
    assert len(km) == 1
    xs = [p[0] for p in km[0][2]]
    ys = [p[1] for p in km[0][2]]
    assert min(xs) >= -5 - 1e-9 and max(xs) <= 5 + 1e-9 and min(ys) >= -5 - 1e-9 and max(ys) <= 5 + 1e-9
    assert sources.polygones_km(polys, (40.0, 0.0), (-5, 5, -5, 5)) == []  # hors emprise


def test_geojson_refuse():
    with pytest.raises(ValueError):
        sources.lire_geojson(b"\xff\xfe pas du json")
    with pytest.raises(ValueError):
        sources.lire_geojson(b'{"type":"FeatureCollection","features":[]}')
    with pytest.raises(ValueError):
        sources.lire_geojson(b"x" * 100, taille_max=10)


def test_courant_et_observations_externes():
    csv = b"longitude,latitude,time,uo,vo\n10.5,55.0,2024-06-15T10:00:00Z,0.5,0.0\n10.6,55.1,2024-06-15T11:00:00Z,0.5,0.2\n"
    u, v, n = sources.courant_moyen(csv, (54, 56, 10, 11))
    assert u == pytest.approx(1.8) and v == pytest.approx(0.36) and n == 2
    obs = sources.lire_observations_externes(
        b"obs_id,timestamp,lat,lon,precision_m,proba_attribution,source\n"
        b"S1-001,2024-06-15T10:20:00Z,55.02,10.52,300,0.6,sentinel1\n"
        b"=cmd,2024-06-15T10:20:00Z,55.02,10.52,300,1.5,inconnue\n"
        b"S2-001,2024-06-15T10:30:00Z,55.03,10.53,50,0.4,pirate\n")
    assert list(obs["obs_id"]) == ["S1-001", "S2-001"]  # probabilité hors [0, 1] rejetée
    assert list(obs["source"]) == ["sentinel1", "autre"]


def test_projection_locale_aller_retour():
    o = (55.3, 10.8)
    x, y = geo.vers_km(55.4, 11.0, o)
    lat, lon = geo.vers_latlon(x, y, o)
    assert float(lat) == pytest.approx(55.4) and float(lon) == pytest.approx(11.0)
    assert 12 < float(x) < 13.5  # ~12.7 km à cette latitude


# ---------------------------------------------------------------- Flask interface

@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from seatrace import archive, pipeline
    from web.app import create_app
    d = tmp_path_factory.mktemp("data")
    archive.DOSSIER = d / "chemins"
    pipeline.CHEMIN_JOURNAL = d / "journal.jsonl"
    pipeline.DOSSIER_IMPORTS = d / "imports"
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


H = {"X-SeaTrace": "1"}


def test_web_headers_and_protection(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert client.post("/api/reconstruct", json={}).status_code == 403
    assert client.post("/api/datasets").status_code == 403
    assert client.get("/api/datasets/..%2F..%2Fetc/trips").status_code in (400, 404)
    assert client.get("/api/runs/../../x/paths.csv").status_code == 404


def test_web_pages_are_in_english(client):
    html = client.get("/").data.decode()
    assert '<html lang="en">' in html and "Model comparison" in html and "Path families (PCA)" in html
    js = client.get("/static/app.js").data.decode()
    for word in ("Comparaison", "Reconstruire", "Familles", "Décision", "Évaluation", "Probabilité"):
        assert word not in js and word not in html
    st = client.get("/api/state").get_json()
    assert all("é" not in m["label"] for m in st["models"])
    trip = client.get("/api/datasets/demo/trips/S6-ambigu").get_json()
    assert "sailboat" in trip["type_label"].lower() or "sailing" in trip["type_label"].lower()
    assert all(a["label"].isascii() for a in trip["alerts"])


def test_web_import_ais_drawn_zone_and_reconstruction(client):
    r = client.post("/api/datasets", data={"file": (io.BytesIO(csv_dma()), "aisdk-2024-06-15.csv")}, headers=H,
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    did = r.get_json()["id"]
    assert r.get_json()["format"] == "dma"
    trips = client.get(f"/api/datasets/{did}/trips").get_json()["trips"]
    trip = trips[0]["trip_id"]
    a = client.get(f"/api/datasets/{did}/trips/{trip}").get_json()
    assert a["geography"]["no_land"] and a["boat_type"] == "ais_voilier"
    r = client.post(f"/api/datasets/{did}/layers", data={"role": "land",
                    "file": (io.BytesIO(geojson_carre(10.46, 55.0, 0.02)), "land.geojson")}, headers=H,
                    content_type="multipart/form-data")
    assert r.status_code == 200
    z = client.post(f"/api/datasets/{did}/zones", json={"polygon": [[55.02, 10.51], [55.02, 10.55], [55.05, 10.55],
                                                                    [55.05, 10.51]]}, headers=H).get_json()
    a = client.get(f"/api/datasets/{did}/trips/{trip}").get_json()
    assert any(x["id"] == z["id"] for x in a["geography"]["zones"])
    assert len(a["geography"]["land"]) == 1


def test_web_kattegat_zones_and_map(client):
    from pathlib import Path
    if not (Path(__file__).resolve().parent.parent / "data" / "reference" / "kattegat_zones.geojson").exists():
        pytest.skip("Kattegat zones not in this checkout")
    r = client.post("/api/datasets/demo/kattegat-zones", headers=H)
    assert r.status_code == 200 and r.get_json()["polygons"] >= 30
    # zones outside the demo bay are simply clipped away: the layer is accepted but harmless
    assert client.get("/api/datasets/demo/trips/S5-trou-zone").status_code == 200
    m = client.get("/kattegat/map.html")
    if m.status_code == 200:
        text = m.data.decode()
        assert "Study a vessel" in text and "Étudier un navire" not in text
        assert "cdn.jsdelivr.net" in m.headers["Content-Security-Policy"]
        assert m.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert client.get("/kattegat/../app.py").status_code == 404
    assert client.get("/kattegat/secret.html").status_code == 404


def test_web_kattegat_test_boats(client):
    from pathlib import Path
    if not (Path(__file__).resolve().parent.parent / "data" / "synthetic" / "boat_A_reported.parquet").exists():
        pytest.skip("data/synthetic not in this checkout")
    st = client.get("/api/state").get_json()
    assert any(d["id"] == "kattegat-test" and d["trips"] == 4 for d in st["datasets"])
    a = client.get("/api/datasets/kattegat-test/trips/TEST-A").get_json()
    assert any(z["id"] == "SE0510127-A2" for z in a["geography"]["zones"])
    assert a["boat_type"] == "ais_peche" and not a["geography"]["no_land"]  # Natural Earth land of the Kattegat map
    gap = max(a["periods"], key=lambda p: p["duration_min"])
    assert gap["cause"] == "interruption" and 44 < gap["duration_min"] < 47
    assert all(x["type"] != "rejeu" for x in a["alerts"])  # no false replay alert on a real, unmodified track
    r = client.post("/api/reconstruct", json={"dataset": "kattegat-test", "trip": "TEST-A", "period": gap["id"],
                                              "zone": "SE0510127-A2", "models": ["hmm", "particules"]}, headers=H)
    assert r.status_code == 200, r.get_json()
    run = r.get_json()
    mins = [m["diagnostics"]["expected_minutes_in_zone"] for m in run["models"].values()]
    assert all(5 < x < 30 for x in mins), mins  # the boat really stayed 14 min in that zone
    # the description must not give the answer away
    trips = client.get("/api/datasets/kattegat-test/trips").get_json()["trips"]
    assert all("silence" not in t["description"].lower() and "jump" not in t["description"].lower() for t in trips)
    b = client.get("/api/datasets/kattegat-test/trips/TEST-B").get_json()
    assert any(x["type"] == "saut_position" for x in b["alerts"]) and any(x["type"] == "identite_dedoublee" for x in b["alerts"])


def test_web_reconstruction_without_zone_does_not_crash(client):
    a = client.get("/api/datasets/demo/trips/S5-trou-zone").get_json()
    per = max(a["periods"], key=lambda p: p["duration_min"])["id"]
    r = client.post("/api/reconstruct", json={"dataset": "demo", "trip": "S5-trou-zone", "period": per, "zone": None,
                                              "models": ["hmm", "particules"]}, headers=H)
    assert r.status_code == 200, r.get_json()
    run = r.get_json()
    assert run["models"]["hmm"]["crossing_probability"] is None and run["models"]["hmm"]["n_paths"] > 0
    assert "No zone chosen" in run["synthesis"]["reading"]


def test_kattegat_map_fully_translated():
    """Both pages of the team's map come out without French (so a new French string in a regenerated map is caught)."""
    import re
    from pathlib import Path
    from web import kattegat_i18n as K
    reports = Path(__file__).resolve().parent.parent / "reports"
    if not (reports / "map.html").exists():
        pytest.skip("reports/map.html not in this checkout")
    for name in ("map.html", "map_animation.html"):
        text = K.translate((reports / name).read_text(encoding="utf-8"))
        text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        left = [m.group(0) for m in re.finditer(r"[^\n]{0,50}[éèêàùçôîÉÀ«»’œ][^\n]{0,50}", text)
                if "//" not in m.group(0) and "base64" not in m.group(0)]
        assert not left, (name, left[:3])
    html = K.translate((reports / "map.html").read_text(encoding="utf-8"))
    for phrase in ("Study a vessel", "Sea conditions (context)", "Surface current", "Flows towards the"):
        assert phrase in html, phrase


def test_web_reconstruction_demo_exports_and_escaping(client):
    client.post("/api/datasets/demo/kattegat-zones", headers=H) if False else None
    a = client.get("/api/datasets/demo/trips/S5-trou-zone").get_json()
    per = max(a["periods"], key=lambda p: p["duration_min"])["id"]
    r = client.post("/api/reconstruct", json={"dataset": "demo", "trip": "S5-trou-zone", "period": per, "zone": "Z1",
                                              "models": ["hmm", "particules", "pont", "boosting"]}, headers=H)
    assert r.status_code == 200, r.get_json()
    run = r.get_json()
    assert list(run["models"]) == ["hmm", "particules", "pont", "boosting"]
    assert run["models"]["hmm"]["crossing_probability"] > 0.7
    assert run["segmentation"]["interpretation"]["components"]
    rid = run["run_id"]
    csv = client.get(f"/api/runs/{rid}/paths.csv")
    assert csv.status_code == 200 and csv.data.startswith(b"run_id,model,path_id,family,weight")
    gj = client.get(f"/api/runs/{rid}/paths.geojson").get_json()
    assert gj["features"] and all(f["properties"]["estimated"] for f in gj["features"])
    assert client.post(f"/api/runs/{rid}/decision", json={"decision": "informations", "analyst": "<b>X",
                                                           "note": "<script>alert(1)</script>"}, headers=H).status_code == 200
    html = client.get(f"/api/runs/{rid}/report.html").data.decode()
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "model comparison report" in html and "Common reading" in html
    assert client.post(f"/api/runs/{rid}/decision", json={"decision": "sanction"}, headers=H).status_code == 400
    assert client.get("/api/journal").get_json()["intact"]
