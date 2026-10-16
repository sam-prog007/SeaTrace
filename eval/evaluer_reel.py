"""Real-data check of the core claim: hide real stretches of AIS fishing tracks, reconstruct them through the
interface API, and compare with what the vessels really did.

Prerequisite: a day of real AIS exported for SeaTrace, e.g.
    python src/01_download.py --start 2026-09-15 --end 2026-09-15
    python src/02_filter.py   --start 2026-09-15 --end 2026-09-15
    python src/06_tracks.py --start 2026-09-15 --end 2026-09-15 && python src/07_crossing.py --start 2026-09-15 --end 2026-09-15
    python src/13_seatrace_export.py --start 2026-09-15 --end 2026-09-15 --no-synthetic
Run:  python eval/evaluer_reel.py [--jeu data/ais/kattegat_2026-09-15_2026-09-15.csv] [--n 120]

Method (self-supervised: real tracks are their own answer key)
  - In each real trip, find stretches with continuous reception (no interval above 5 min) and hide a window of
    15 to 120 min in which the vessel moves at least 1 km. The hidden real positions are the truth.
  - Up to 70 % of the windows are drawn so that the truth passes within 5 km of a restricted Kattegat zone
    (enriched sample, as in the synthetic bench), the rest anywhere; at most 3 windows per trip.
  - Each case is a copy of the trip cut to +/- 2 h around the window, put in a temporary dataset with the same land
    and zones as the map export. Detection and reconstruction go through the Flask API, as for an analyst, with no
    current and no wind (only simulated context exists for these days).
  - The zone examined is the restricted zone nearest to the true hidden path; truth = the real hidden path enters
    it. Cases where a boundary position is already inside the zone are counted apart (trivially certain).
Measures per model: Brier score of the crossing probability (on the cases where the zone lies in the trip's map
area, so that every model gives a probability; baselines are scored on the same cases), probabilities above 50 %
when the vessel did not enter, mean error of the weighted mean path, share of steps
whose true position lies inside the disc holding 90 % of the path weight. Baselines: straight-line interpolation
(crossing 0/1, position) and the sample base rate.
Output: eval/resultats/reel_kattegat.json. Limits: one day, one sea area, fishing vessels only, AIS class A.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
import shapely.geometry
from pyproj import Transformer

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import archive, config, pipeline, schema  # noqa: E402

ZONES = RACINE / "data" / "reference" / "kattegat_zones.geojson"
SORTIE = RACINE / "eval" / "resultats" / "reel_kattegat.json"
MODELES = ["hmm", "appris", "particules", "pont", "boosting"]
H = {"X-SeaTrace": "1"}
FWD = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
CONTEXTE_S = 2 * 3600
RECEPTION_MAX_S = 300
PAR_CAS = 50  # cases per temporary dataset file (import limit: 50 000 rows)


def zones_restreintes():
    feats = [f for f in json.loads(ZONES.read_text(encoding="utf-8"))["features"] if f["properties"]["layer"] == "zone"]
    ids = [f["properties"]["zone_id"] for f in feats]
    geoms = [shapely.transform(shapely.geometry.shape(f["geometry"]),
                               lambda c: np.column_stack(FWD.transform(c[:, 0], c[:, 1]))) for f in feats]
    return ids, geoms


def xy(lat, lon):
    return np.column_stack(FWD.transform(np.asarray(lon, float), np.asarray(lat, float)))


def entre(ligne_xy, geom) -> bool:
    if len(ligne_xy) == 1:
        return bool(geom.contains(shapely.Point(ligne_xy[0])))
    return bool(shapely.LineString(ligne_xy).intersects(geom))


def tirer_cas(pos: pd.DataFrame, ids, geoms, n, rng):
    """Windows to hide: (trip, index before, index after, nearest zone, distance km)."""
    proches, loin = [], []
    for trip, g in pos.groupby("trip_id"):
        g = g.reset_index()
        t = (g["timestamp"] - g["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
        if t[-1] < 3600:
            continue
        p = xy(g["lat"], g["lon"])
        dt = np.diff(t)
        tries = 0
        for _ in range(12):
            if tries >= 3:
                break
            d = rng.uniform(15, 120) * 60
            i = int(rng.integers(1, len(t) - 2))
            j = int(np.searchsorted(t, t[i] + d))
            if j >= len(t) - 1 or j - i < 3:
                continue
            if dt[max(i - 1, 0):j + 1].max() > RECEPTION_MAX_S:
                continue  # natural gap inside or right next to the window: truth incomplete
            chemin = p[i:j + 1]
            if np.hypot(*np.diff(chemin, axis=0).T).sum() < 1000:
                continue
            ligne = shapely.LineString(chemin)
            dist = [ligne.distance(z) for z in geoms]
            k = int(np.argmin(dist))
            (proches if dist[k] <= 5000 else loin).append((trip, int(g["index"].iloc[i]), int(g["index"].iloc[j]),
                                                           ids[k], dist[k] / 1000))
            tries += 1
    rng.shuffle(proches)
    rng.shuffle(loin)
    n_proches = min(len(proches), int(round(n * 0.7)))
    return proches[:n_proches] + loin[:n - n_proches]


def ecrire_jeux(pos, cas, dossier: Path):
    """Copies of the trips around each window, the hidden positions removed; one CSV + side file per 50 cases."""
    fichiers = []
    for c0 in range(0, len(cas), PAR_CAS):
        lignes, bateaux = [], {}
        for k, (trip, i, j, *_rest) in enumerate(cas[c0:c0 + PAR_CAS], start=c0):
            g = pos.loc[pos["trip_id"] == trip]
            ti, tj = pos.at[i, "timestamp"], pos.at[j, "timestamp"]
            keep = ((g["timestamp"] >= ti - pd.Timedelta(seconds=CONTEXTE_S)) & (g["timestamp"] <= tj + pd.Timedelta(seconds=CONTEXTE_S))
                    & ~((g["timestamp"] > ti) & (g["timestamp"] < tj)))
            c = g[keep].copy()
            c["trip_id"] = f"R{k:03d}"
            c["boat_id"] = f"R{k:03d}"  # one pseudo-boat per case: no cross-case effects
            bateaux[f"R{k:03d}"] = {"type_bateau": "ais_peche", "nom": f"real case {k}"}
            lignes.append(c)
        df = pd.concat(lignes)
        df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        f = dossier / f"reel_{c0 // PAR_CAS}.csv"
        df[schema.COLONNES].to_csv(f, index=False)
        f.with_suffix(".json").write_text(json.dumps({
            "nom_jeu": f"Real-data check {c0 // PAR_CAS}", "bateaux": bateaux,
            "couches": {"zones": "kattegat", "terres": "data/reference/kattegat_land.geojson"}}), encoding="utf-8")
        fichiers.append(f)
    return fichiers


def disque_90(lat, lon, w, xv, yv):
    """Mean error of the weighted mean path and share of steps whose truth is inside the 90 % weight disc."""
    x, y = FWD.transform(lon, lat)
    x, y = np.asarray(x), np.asarray(y)
    mx, my = (w[:, None] * x).sum(0), (w[:, None] * y).sum(0)
    err = np.hypot(mx - xv, my - yv)
    dans = []
    for s in range(x.shape[1]):
        d = np.hypot(x[:, s] - mx[s], y[:, s] - my[s])
        o = np.argsort(d)
        r90 = d[o][np.searchsorted(np.cumsum(w[o]), 0.9)]
        dans.append(err[s] <= max(r90, 50.0))
    return float(err.mean() / 1000), float(np.mean(dans))


def brier(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2)) if len(p) else None


def bloc(rows, modeles):
    """Crossing scores on the cases where a zone was offered (every model then has a probability), so models and
    baselines are scored on the same cases; position scores on every reconstructed case."""
    scored = [r for r in rows if all(r["proba"].get(m) is not None for m in modeles if m in r["proba"])
              and any(v is not None for v in r["proba"].values())]
    out = {"cases": len(rows), "cases_with_zone": len(scored), "crossings": int(sum(r["truth_crosses"] for r in scored))}
    if scored:
        y = [float(r["truth_crosses"]) for r in scored]
        base = float(np.mean(y))
        out["base_rate"] = round(base, 3)
        out["brier"] = {m: round(brier([r["proba"][m] for r in scored], y), 4)
                        for m in modeles if all(m in r["proba"] for r in scored)}
        out["brier"]["interpolation"] = round(brier([float(r["interp_crosses"]) for r in scored], y), 4)
        out["brier"]["base_rate"] = round(brier([base] * len(y), y), 4)
        out["false_high_over_50pct"] = {m: int(sum(r["proba"][m] > 0.5 and not r["truth_crosses"] for r in scored))
                                        for m in out["brier"] if m in modeles}
        out["false_high_over_50pct"]["interpolation"] = int(sum(r["interp_crosses"] and not r["truth_crosses"]
                                                                for r in scored))
    out["mean_error_km"] = {m: round(float(np.mean([r["error_km"][m] for r in rows if m in r["error_km"]])), 3)
                            for m in modeles + ["interp"] if any(m in r["error_km"] for r in rows)}
    out["inside_90_disc"] = {m: round(float(np.mean([r["inside_90"][m] for r in rows if m in r["inside_90"]])), 3)
                             for m in modeles if any(m in r["inside_90"] for r in rows)}
    return out


def resumer(lignes, modeles):
    ok = [r for r in lignes if "proba" in r]
    non_triv = [r for r in ok if not r["boundary_inside"]]
    return {
        "period_found": sum(r["period_found"] for r in lignes),
        "period_matches_window": sum(bool(r.get("period_matches_window")) for r in lignes),
        "near_zone_cases": sum(r["zone_distance_km"] <= 5 for r in lignes),
        "errors": [r for r in lignes if "error" in r],
        "all": bloc(ok, modeles), "non_trivial": bloc(non_triv, modeles),
        "by_duration": {lab: bloc([r for r in non_triv if lo <= r["duration_min"] < hi], modeles)
                        for lab, lo, hi in (("15-45 min", 15, 45), ("45-80 min", 45, 80), ("80-120 min", 80, 121))},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jeu", default=str(RACINE / "data" / "ais" / "kattegat_2026-09-15_2026-09-15.csv"))
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--modeles", nargs="*", default=MODELES)
    ap.add_argument("--resume", action="store_true", help="recompute the summary from the saved cases only")
    a = ap.parse_args()
    if a.resume:
        d = json.loads(SORTIE.read_text(encoding="utf-8"))
        d.update(resumer(d["cases"], d.get("models", a.modeles)))
        SORTIE.write_text(json.dumps(d, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in d.items() if k != "cases"}, indent=1, default=str))
        return
    params = config.charger()
    jeu = Path(a.jeu)
    p_imp = {**params["import"], "taille_max_octets": params["import"]["taille_max_ais_octets"]}
    pos = schema.lire_csv_securise(jeu.read_bytes(), jeu.name, p_imp).donnees
    pos = pos[~pos["synthetic"]].reset_index(drop=True)
    ids, geoms = zones_restreintes()
    geom_par_id = dict(zip(ids, geoms))
    rng = np.random.default_rng(2026)
    cas = tirer_cas(pos, ids, geoms, a.n, rng)
    print(f"{len(cas)} hidden windows from {pos['trip_id'].nunique()} real trips ({pos['boat_id'].nunique()} vessels)",
          flush=True)

    lignes = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "ais").mkdir()
        fichiers = ecrire_jeux(pos, cas, tmp / "ais")
        import web.app as W
        archive.DOSSIER = tmp / "chemins"
        pipeline.CHEMIN_JOURNAL = tmp / "journal.jsonl"
        pipeline.DOSSIER_IMPORTS = tmp / "imports"
        W.DOSSIER_AIS = tmp / "ais"
        client = W.create_app().test_client()
        client.get("/api/state")
        t0 = time.perf_counter()
        for k, (trip, i, j, zone, dist_km) in enumerate(cas):
            did, tid = f"ais-{fichiers[k // PAR_CAS].stem}", f"R{k:03d}"
            ti, tj = pos.at[i, "timestamp"], pos.at[j, "timestamp"]
            an = client.get(f"/api/datasets/{did}/trips/{tid}").get_json()
            per = [q for q in an["periods"] if pd.Timestamp(q["start_utc"]) <= tj and pd.Timestamp(q["end_utc"]) >= ti]
            row = {"case": tid, "source_trip": trip, "duration_min": round((tj - ti).total_seconds() / 60, 1),
                   "zone": zone, "zone_distance_km": round(dist_km, 2), "period_found": bool(per)}
            if not per:
                lignes.append(row)
                continue
            q = max(per, key=lambda q: q["duration_min"])
            row["period_matches_window"] = (abs((pd.Timestamp(q["start_utc"]) - ti).total_seconds()) <= 1
                                            and abs((pd.Timestamp(q["end_utc"]) - tj).total_seconds()) <= 1)
            offered = {z["id"].split("#")[0]: z["id"] for z in an["geography"]["zones"]}
            zid = offered.get(zone)
            # truth, on the real positions (boundaries included)
            truth = pos.iloc[i:j + 1]
            txy = xy(truth["lat"], truth["lon"])
            g = geom_par_id[zone]
            row["truth_crosses"] = entre(txy, g)
            row["boundary_inside"] = bool(g.contains(shapely.Point(txy[0])) or g.contains(shapely.Point(txy[-1])))
            row["interp_crosses"] = entre(txy[[0, -1]], g)
            r = client.post("/api/reconstruct", json={"dataset": did, "trip": tid, "period": q["id"], "zone": zid,
                                                      "models": a.modeles, "current": [0, 0], "wind": [0, 0]},
                            headers=H)
            if r.status_code != 200:
                row["error"] = (r.get_json() or {}).get("error", str(r.status_code))
                lignes.append(row)
                continue
            run = r.get_json()
            row["grid_cell_km"] = run["context"].get("grid_cell_km")
            row["proba"] = {m: v["crossing_probability"] for m, v in run["models"].items()}
            # positions: truth interpolated at each model step
            tv = (truth["timestamp"] - truth["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
            row["error_km"], row["inside_90"] = {}, {}
            for m, v in run["models"].items():
                if not v["paths"]:
                    continue
                n_pas = len(v["paths"][0]["lat"])
                ts = np.arange(n_pas) * float(v["dt_s"])
                xv = np.interp(ts, tv, txy[:, 0])
                yv = np.interp(ts, tv, txy[:, 1])
                w = np.array([c["weight"] or 0.0 for c in v["paths"]], float)
                w = w / w.sum() if w.sum() > 0 else np.full(len(w), 1 / len(w))
                e, c90 = disque_90(np.array([c["lat"] for c in v["paths"]]), np.array([c["lon"] for c in v["paths"]]),
                                   w, xv, yv)
                row["error_km"][m], row["inside_90"][m] = round(e, 3), round(c90, 3)
                if "interp" not in row["error_km"]:
                    f = ts / max(ts[-1], 1)
                    ix, iy = txy[0, 0] + f * (txy[-1, 0] - txy[0, 0]), txy[0, 1] + f * (txy[-1, 1] - txy[0, 1])
                    row["error_km"]["interp"] = round(float(np.hypot(ix - xv, iy - yv).mean() / 1000), 3)
            lignes.append(row)
            if (k + 1) % 10 == 0:
                print(f"  {k + 1}/{len(cas)} cases, {time.perf_counter() - t0:.0f} s", flush=True)

    resultats = {
        "dataset": str(jeu.relative_to(RACINE)) if jeu.is_relative_to(RACINE) else str(jeu),
        "real_trips": int(pos["trip_id"].nunique()), "vessels": int(pos["boat_id"].nunique()),
        "cases_drawn": len(cas), "models": a.modeles, **resumer(lignes, a.modeles), "cases": lignes,
    }
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(resultats, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in resultats.items() if k not in ("cases",)}, indent=1, default=str))
    print(f"-> {SORTIE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
