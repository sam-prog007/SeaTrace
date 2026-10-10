"""Bateaux test synthétiques pour évaluer le futur détecteur (scénarios A, B, C).

Chaque bateau est une COPIE d'un vrai trajet de chalutier présumé (navire de pêche
en classe A dont la majorité du temps de route se fait entre 2 et 5 nœuds : l'AIS
ne dit pas l'engin, c'est donc supposé). Les données réelles ne sont jamais modifiées.

  A « silence en zone »    : le transpondeur se coupe pendant la traversée d'une zone
                             restreinte (SILENCE_MIN, 45 min par défaut) et réapparaît hors zone.
  B « saut de position »   : pendant SHIFT_MIN minutes, les positions sont décalées de
                             SHIFT_KM km (10 km par défaut) dans la direction SHIFT_BEARING,
                             puis reviennent à la normale.
  C « trou innocent »      : témoin négatif, loin de toute zone restreinte ; le signal
                             disparaît GAP_MIN minutes là où la couverture de réception
                             (positions reçues par cellule de 2 km) est la plus faible.

MMSI fictifs 999000001 (A), 999000002 (B), 999000003 (C) ; champs is_synthetic et scenario.

Sorties :
  data/synthetic/boat_<X>_reported.parquet   ce que le détecteur voit
  data/synthetic/boat_<X>_truth.parquet      la vérité (positions réelles, manipulation, source)

RÈGLE : le détecteur ne lit JAMAIS les fichiers *_truth*. Seule l'évaluation les lit,
via load_truth(..., for_evaluation=True). Un test (tests/test_synthetic.py) le vérifie.

Usage : python src/synthetic.py [--silence-min 45] [--shift-km 10] [--shift-min 60] [--gap-min 45]
"""
import argparse
import datetime as dt
import json
import pathlib

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from pyproj import Transformer

ROOT = pathlib.Path(__file__).resolve().parents[1]
TAG = "2026-09-15_2026-09-17_gap30_tol30"
POINTS = ROOT / "data" / "processed" / "tracks" / f"points_{TAG}.parquet"
ZONES = ROOT / "data" / "reference" / "kattegat_zones.geojson"
INTERIM = ROOT / "data" / "interim"
OUT = ROOT / "data" / "synthetic"
PROJ = "EPSG:25832"

# Trajets réels choisis (raisons dans le résumé imprimé et dans README) et paramètres par défaut.
SOURCES = {"A": "220345000-162", "B": "220317000-152", "C": "266388000-315"}
MMSI = {"A": "999000001", "B": "999000002", "C": "999000003"}
NAMES = {"A": "SYNTH A", "B": "SYNTH B", "C": "SYNTH C"}
DEFAULTS = {"silence_min": 45, "shift_km": 10.0, "shift_min": 60, "shift_bearing": 60.0, "gap_min": 45}
COLS = ["mmsi", "timestamp", "lat", "lon", "sog", "cog", "heading", "nav_status"]

FWD = Transformer.from_crs("EPSG:4326", PROJ, always_xy=True)
INV = Transformer.from_crs(PROJ, "EPSG:4326", always_xy=True)


def iso(t) -> str:
    """Heure UTC au format ISO (les horodatages numpy sont en UTC sans fuseau)."""
    return np.datetime_as_string(np.datetime64(t, "s"), unit="s") + "Z"


def utc(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


STRICT = ("Areas 1", "Areas 2")  # engins de fond interdits (DK) ou toute pêche interdite (SE)


def restricted_union(strict: bool = False):
    z = gpd.read_file(ZONES).to_crs(PROJ)
    z = z[(z.layer == "zone") & (z.category.isin(STRICT) if strict else True)]
    return z.union_all(), z


def source_points(track_id: str) -> pl.DataFrame:
    p = pl.read_parquet(POINTS).filter(pl.col("track_id") == track_id).sort("timestamp")
    if p.is_empty():
        raise SystemExit(f"Trajet {track_id} introuvable : relancer src/06_tracks.py")
    return p


def base_frame(src: pl.DataFrame, scenario: str) -> pl.DataFrame:
    """Copie anonymisée du trajet réel : MMSI et nom fictifs, attributs du navire conservés."""
    return src.select(COLS + ["vessel_length_m"]).with_columns(
        pl.lit(MMSI[scenario]).alias("mmsi"),
        pl.lit("Class A").alias("mobile_type"),
        pl.lit(True).alias("is_class_a"),
        pl.lit("Fishing").alias("vessel_ship_type"),
        pl.lit(True).alias("is_fishing_candidate"),
        pl.lit(NAMES[scenario]).alias("vessel_name"),
        pl.lit(True).alias("is_synthetic"),
        pl.lit(scenario).alias("scenario"),
    )


def inside(lon, lat, geom) -> np.ndarray:
    x, y = FWD.transform(np.asarray(lon), np.asarray(lat))
    return shapely.contains_xy(geom, x, y)


# --- Scénario A -----------------------------------------------------------------------------
def scenario_a(minutes: float):
    src = source_points(SOURCES["A"])
    zone, _ = restricted_union()                  # toutes les zones restreintes
    strict, zones = restricted_union(strict=True)  # zones où le chalut de fond est interdit
    ts = src["timestamp"].to_numpy()
    ins = inside(src["lon"], src["lat"], strict)
    ins_any = inside(src["lon"], src["lat"], zone)
    # Passages consécutifs dans la zone ; le silence commence pendant le passage
    # et doit se terminer quand le navire est sorti (et n'y est pas revenu).
    runs, start = [], None
    for i, v in enumerate(ins):
        if v and start is None:
            start = i
        if (not v or i == len(ins) - 1) and start is not None:
            runs.append((start, i - 1 if not v else i))
            start = None
    best = None
    for a, b in runs:
        run_min = (ts[b] - ts[a]) / np.timedelta64(1, "m")
        t0 = ts[b] - np.timedelta64(int(min(15, run_min / 2) * 60), "s")  # dans la zone, avant la sortie
        t1 = t0 + np.timedelta64(int(minutes * 60), "s")
        after = np.flatnonzero(ts >= t1)
        if t0 < ts[a] or not len(after) or ins_any[after[0]]:
            continue  # il doit réapparaître hors de toute zone restreinte
        if best is None or run_min > best[2]:
            best = (t0, t1, run_min, a, b)
    if best is None:
        raise SystemExit("Scénario A : aucun passage en zone compatible avec ce silence")
    t0, t1 = best[0], best[1]
    hidden = (ts >= t0) & (ts < t1)
    zid = zones[zones.contains(shapely.Point(*FWD.transform(src["lon"][best[4]], src["lat"][best[4]])))].zone_id.tolist()
    event = {"type": "silence_in_zone", "start": iso(t0), "end": iso(t1), "minutes": minutes,
             "zone_ids": zid, "zone_run_minutes": round(best[2], 1)}
    return src, hidden, None, event


# --- Scénario B -----------------------------------------------------------------------------
def scenario_b(km: float, minutes: float, bearing: float):
    src = source_points(SOURCES["B"])
    ts = src["timestamp"].to_numpy()
    sog = src["sog"].fill_null(0).to_numpy()
    dtm = np.diff(ts) / np.timedelta64(1, "m")
    win = np.timedelta64(int(minutes * 60), "s")
    mid = ts[0] + (ts[-1] - ts[0]) / 2
    # Fenêtre la plus proche du milieu du trajet où le navire avance (≥ 2 nd) sans trou de signal.
    best = None
    for i in range(0, len(ts), 30):
        j = np.searchsorted(ts, ts[i] + win)
        if j >= len(ts):
            break
        if (sog[i:j] >= 2).mean() < 0.95 or dtm[i:j].max(initial=0) > 2:
            continue
        d = abs((ts[i] - mid) / np.timedelta64(1, "m"))
        if best is None or d < best[0]:
            best = (d, i, j)
    if best is None:
        raise SystemExit("Scénario B : pas de fenêtre en route assez longue")
    i, j = best[1], best[2]
    shifted = np.zeros(len(ts), bool)
    shifted[i:j] = True
    x, y = FWD.transform(src["lon"].to_numpy(), src["lat"].to_numpy())
    dx, dy = km * 1000 * np.sin(np.deg2rad(bearing)), km * 1000 * np.cos(np.deg2rad(bearing))
    lo_s, la_s = INV.transform(x + dx, y + dy)
    lo = np.where(shifted, lo_s, src["lon"].to_numpy())  # hors fenêtre : copie exacte du réel
    la = np.where(shifted, la_s, src["lat"].to_numpy())
    event = {"type": "position_jump", "start": iso(ts[i]), "end": iso(ts[j]), "minutes": minutes,
             "shift_km": km, "bearing_deg": bearing}
    return src, None, (shifted, lo, la), event


# --- Scénario C -----------------------------------------------------------------------------
def coverage_lookup(cell_m=2000):
    """Positions reçues par cellule de 2 km (tous émetteurs mobiles, comme reports/coverage_map.png)."""
    lf = pl.scan_parquet(str(INTERIM / "day=*.parquet")).filter(
        ~pl.col("mobile_type").is_in(["Base Station", "AtoN"])).select("lon", "lat")
    p = lf.collect()
    x, y = FWD.transform(p["lon"].to_numpy(), p["lat"].to_numpy())
    cells = pl.DataFrame({"cx": (x // cell_m).astype(np.int64), "cy": (y // cell_m).astype(np.int64)})
    return cells.group_by(["cx", "cy"]).len()


def scenario_c(minutes: float):
    src = source_points(SOURCES["C"])
    ts = src["timestamp"].to_numpy()
    zone, _ = restricted_union()
    x, y = src["x"].to_numpy(), src["y"].to_numpy()
    cov = coverage_lookup()
    cell = pl.DataFrame({"cx": (x // 2000).astype(np.int64), "cy": (y // 2000).astype(np.int64)})
    counts = cell.join(cov, on=["cx", "cy"], how="left")["len"].fill_null(0).to_numpy()
    dtm = np.diff(ts) / np.timedelta64(1, "m")
    win = np.timedelta64(int(minutes * 60), "s")
    best = None
    for i in range(60, len(ts), 20):  # pas au tout début du trajet
        j = np.searchsorted(ts, ts[i] + win)
        if j >= len(ts) - 60:
            break
        if dtm[i:j].max(initial=0) > 5:  # éviter de superposer un vrai trou
            continue
        c = float(np.median(counts[i:j]))
        if best is None or c < best[0]:
            best = (c, i, j)
    i, j = best[1], best[2]
    hidden = np.zeros(len(ts), bool)
    hidden[i:j] = True
    event = {"type": "innocent_gap", "start": iso(ts[i]), "end": iso(ts[j]), "minutes": minutes,
             "coverage_in_gap_median": best[0], "coverage_track_median": float(np.median(counts)),
             "min_distance_to_restricted_zone_km": round(shapely.LineString(np.column_stack([x, y])).distance(zone) / 1000, 1)}
    return src, hidden, None, event


# --- Écriture -------------------------------------------------------------------------------
def write(scenario: str, src: pl.DataFrame, hidden, shift, event: dict) -> dict:
    base = base_frame(src, scenario)
    truth = base.with_columns(
        pl.Series("reported_present", ~hidden if hidden is not None else np.ones(base.height, bool)),
        pl.Series("is_manipulated", (hidden if hidden is not None else shift[0])),
        pl.lit(event["type"]).alias("manipulation"),
        pl.lit(event["start"]).alias("event_start"), pl.lit(event["end"]).alias("event_end"),
        pl.lit(SOURCES[scenario]).alias("source_track_id"),
        pl.lit(SOURCES[scenario].split("-")[0]).alias("source_mmsi"),
    )
    if shift is not None:
        truth = truth.with_columns(pl.Series("reported_lat", shift[2]), pl.Series("reported_lon", shift[1]))
        reported = base.with_columns(pl.Series("lat", shift[2]), pl.Series("lon", shift[1]))
    else:
        truth = truth.with_columns(pl.col("lat").alias("reported_lat"), pl.col("lon").alias("reported_lon"))
        reported = base.filter(pl.Series(~hidden))
    OUT.mkdir(parents=True, exist_ok=True)
    reported.write_parquet(OUT / f"boat_{scenario}_reported.parquet")
    truth.write_parquet(OUT / f"boat_{scenario}_truth.parquet")
    return {"scenario": scenario, "mmsi": MMSI[scenario], "source_track_id": SOURCES[scenario],
            "rows_truth": truth.height, "rows_reported": reported.height, **event}


def load_reported(scenario: str) -> pl.DataFrame:
    """Ce que le détecteur a le droit de lire."""
    return pl.read_parquet(OUT / f"boat_{scenario}_reported.parquet")


def load_truth(scenario: str, *, for_evaluation: bool = False) -> pl.DataFrame:
    """Réservé à l'évaluation : le détecteur ne doit jamais appeler cette fonction."""
    if not for_evaluation:
        raise PermissionError("Les fichiers truth sont réservés à l'évaluation (for_evaluation=True).")
    return pl.read_parquet(OUT / f"boat_{scenario}_truth.parquet")


def verify(summary: list[dict]) -> None:
    """Contrôles indépendants sur les fichiers écrits."""
    zone, zones = restricted_union()
    for s in summary:
        X = s["scenario"]
        rep, tru = load_reported(X), load_truth(X, for_evaluation=True)
        t0, t1 = utc(s["start"]), utc(s["end"])
        if X == "A":
            before = rep.filter(pl.col("timestamp") < t0).tail(1)
            after = rep.filter(pl.col("timestamp") >= t1).head(1)
            gap = (after["timestamp"][0] - before["timestamp"][0]).total_seconds() / 60
            hid = tru.filter(~pl.col("reported_present"))
            share_in = inside(hid["lon"], hid["lat"], zone).mean()
            s["check"] = {
                "last_reported_inside_zone": bool(inside(before["lon"], before["lat"], zone)[0]),
                "first_reported_after_inside_zone": bool(inside(after["lon"], after["lat"], zone)[0]),
                "reported_gap_min": round(gap, 1),
                "hidden_positions": hid.height, "hidden_share_inside_zone": round(float(share_in), 2),
                "truth_positions_inside_zone": int(inside(tru["lon"], tru["lat"], zone).sum()),
            }
        elif X == "B":
            m = tru.filter(pl.col("is_manipulated"))
            x0, y0 = FWD.transform(m["lon"].to_numpy(), m["lat"].to_numpy())
            x1, y1 = FWD.transform(m["reported_lon"].to_numpy(), m["reported_lat"].to_numpy())
            d = np.hypot(x1 - x0, y1 - y0) / 1000
            s["check"] = {"shifted_positions": m.height, "shift_km_min": round(float(d.min()), 2),
                          "shift_km_max": round(float(d.max()), 2),
                          "unshifted_identical": bool((tru.filter(~pl.col("is_manipulated"))["lat"] ==
                                                       tru.filter(~pl.col("is_manipulated"))["reported_lat"]).all())}
        else:
            before = rep.filter(pl.col("timestamp") < t0).tail(1)
            after = rep.filter(pl.col("timestamp") >= t1).head(1)
            s["check"] = {"reported_gap_min": round((after["timestamp"][0] - before["timestamp"][0]).total_seconds() / 60, 1),
                          "any_truth_position_in_zone": bool(inside(tru["lon"], tru["lat"], zone).any())}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--silence-min", type=float, default=DEFAULTS["silence_min"])
    p.add_argument("--shift-km", type=float, default=DEFAULTS["shift_km"])
    p.add_argument("--shift-min", type=float, default=DEFAULTS["shift_min"])
    p.add_argument("--shift-bearing", type=float, default=DEFAULTS["shift_bearing"])
    p.add_argument("--gap-min", type=float, default=DEFAULTS["gap_min"])
    a = p.parse_args()
    summary = [write("A", *scenario_a(a.silence_min)),
               write("B", *scenario_b(a.shift_km, a.shift_min, a.shift_bearing)),
               write("C", *scenario_c(a.gap_min))]
    verify(summary)
    (OUT / "summary_truth.json").write_text(json.dumps(
        {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "params": vars(a),
         "boats": summary}, indent=2, ensure_ascii=False, default=str))
    for s in summary:
        print(json.dumps(s, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
