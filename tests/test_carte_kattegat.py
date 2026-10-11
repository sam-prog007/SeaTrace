"""Carte du Kattegat <-> algorithme SeaTrace : positions, géographie, reconstruction par lot, panneau, Flask."""
import importlib.util
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from seatrace import carte_kattegat as K
from seatrace import config, detect

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not K.disponible(ROOT), reason="reports/map.html et map_animation.html absents")


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), ROOT / "src" / name)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def fleet():
    return K.charger_positions(ROOT)


def test_positions_of_every_vessel_with_exact_gap_boundaries(fleet):
    df, gaps, vessels, source = fleet
    assert df["mmsi"].nunique() == len(vessels) == 208
    for mmsi, gs in list(gaps.items())[:20]:
        ts = set(df.loc[df["mmsi"] == mmsi, "timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert all(g[4] in ts and g[5] in ts for g in gs)
    if source["kind"] == "published_map":
        assert source["step_s"] == 600


def test_detection_threshold_is_the_map_gap(fleet):
    df, gaps, vessels, source = fleet
    P = K.params_detection(config.charger(), source["step_s"])
    assert P["detection"]["intervalle_attendu_s"] * P["detection"]["facteur_interruption"] == 30 * 60
    pos, _ = K.jeu_seatrace(df, vessels)
    pos["timestamp"] = __import__("pandas").to_datetime(pos["timestamp"], utc=True)
    mmsi = next(iter(gaps))
    a = detect.analyser_trajet(pos[pos["trip_id"] == mmsi], config.charger()["types_bateau"]["ais_peche"], P)
    starts = {p.debut.strftime("%Y-%m-%dT%H:%M:%SZ") for p in a.periodes}
    assert {g[4] for g in gaps[mmsi]} <= starts


def test_geography_masks_land_restricted_areas_and_buffers():
    cch = K.couches(ROOT)
    assert cch["land"] and {z["layer"] for z in cch["zones"]} == {"zone", "buffer"}
    P = config.charger()
    pt = P["types_bateau"]["ais_peche"]
    from seatrace.reconstruct import Observation
    origin = (56.98, 11.95)  # near Danish MSFD areas
    a, b = Observation(0.0, 0.0, 0.05, 0.0), Observation(3.0, 1.0, 0.05, 45 * 60.0)
    g = K.geographie(cch, origin, K.bornes_fenetre(a, b, pt))
    gr = K.grille(g, a, b, pt, P)
    assert K.ZONE_ALL in gr.zones and K.BUFFER_ALL in gr.zones
    assert gr.zones[K.ZONE_ALL].shape == gr.mer.shape


def test_batch_on_one_vessel_gives_probabilities_contour_and_paths(tmp_path):
    out = tmp_path / "r.json"
    m = _load("13_reconstruct.py")
    import sys
    argv = sys.argv
    sys.argv = ["13_reconstruct.py", "--only", "221458300", "217602700", "--draws", "60", "--out", str(out)]
    try:
        m.main()
    finally:
        sys.argv = argv
    r = json.loads(out.read_text(encoding="utf-8"))
    for v in r["vessels"].values():
        assert v["lvl"] in (0, 1, 2, 3)
        for g in v["gaps"]:
            assert g["status"] in ("ok", "not_reconstructed", "failed", "at_berth")
            if g["status"] == "ok":
                assert 0.0 <= g["pz"] <= 1.0 and g["heat"]["nx"] * g["heat"]["ny"] == len(g["heat"]["v"])
                assert g["c90"] and len(g["paths"]) <= 30
                lat = np.array([p[0] for _, pts in g["paths"] for p in pts])
                assert ((lat > 54) & (lat < 59)).all()
    jumps = [v for v in r["vessels"].values() if any(a["type"] == "saut_position" for a in v["alerts"])]
    assert jumps and all(v["lvl"] == 3 for v in jumps)


def test_long_gaps_are_not_extrapolated():
    a = K.chemins(ROOT)["analysis"]
    if not a.exists():
        pytest.skip("run src/13_reconstruct.py first")
    r = json.loads(a.read_text(encoding="utf-8"))
    for v in r["vessels"].values():
        for g in v["gaps"]:
            if g["min"] > 60 * r["max_gap_h"]:
                assert g["status"] == "not_reconstructed" and g["pz"] is None and "paths" not in g


def test_refreshed_panel_keeps_folium_layers_and_stays_clean(tmp_path):
    pytest.importorskip("folium")
    pytest.importorskip("geopandas")
    m = _load("08_map.py")
    page = tmp_path / "map.html"
    shutil.copy(ROOT / "reports" / "map.html", page)
    before = page.read_text(encoding="utf-8")
    fake = {"computed": "x", "source": {"kind": "published_map"}, "draws": 1, "mass": 0.9, "max_gap_h": 6,
            "coverage": {"radius_km": 10, "min_vessels": 2}, "boat_type": {},
            "vessels": {"219001682": {"lvl": 1, "pz": 0.0, "pb": 0.0, "alerts": [], "gaps": []}}}
    m.refresh_panel(page, fake)
    m.refresh_panel(page, fake)  # idempotent
    after = page.read_text(encoding="utf-8")
    assert after.count('id="st-data"') == 1 and after.count("function drawAnalysis") == 1
    assert re.findall(r"const map = (\w+)", before) == re.findall(r"const map = (\w+)", after)
    data = K.donnees_carte(page)
    assert data["analysis"]["vessels"]["219001682"]["lvl"] == 1 and data["tracks"] and data["gaps"]


def test_public_map_has_analysis_for_selected_vessel_only():
    text = (ROOT / "reports" / "map.html").read_text(encoding="utf-8")
    data = K.donnees_carte(ROOT / "reports" / "map.html")
    if not data.get("analysis"):
        pytest.skip("map not refreshed with the analysis")
    assert "drawAnalysis(mmsi)" in text and "P(zone) max" in text
    # the vessel style is untouched: the selected/other/normal styles are the same three
    assert text.count('mode === "selected" ? {color: C.selected, weight: 3, opacity: 0.9}') == 1
    assert (ROOT / "reports" / "map.html").stat().st_size < 3_011_851 + 3_000_000


def test_flask_fleet_dataset_and_screening():
    from web.app import create_app
    c = create_app().test_client()
    st = c.get("/api/state").get_json()
    assert st["kattegat"]["fleet"]
    assert any(d["id"] == "kattegat-fleet" and d["trips"] == 208 for d in st["datasets"])
    s = c.get("/api/kattegat/screening").get_json()
    if not s["available"]:
        pytest.skip("no batch result")
    assert s["rows"] and [r["score"] for r in s["rows"]] == sorted((r["score"] for r in s["rows"]), reverse=True)
    assert all(0 <= r["score"] <= 100 for r in s["rows"])  # same anomaly score as Investigate (seatrace/score.py)
    top = s["rows"][0]
    a = c.get(f"/api/datasets/kattegat-fleet/trips/{top['mmsi']}").get_json()
    assert a["geography"]["land"] and a["geography"]["zones"]
    if top["best"]:
        assert any(abs(np.datetime64(p["start_iso"][:-1]) - np.datetime64(top["best"]["start"][:-1]))
                   <= np.timedelta64(120, "s") for p in a["periods"])
