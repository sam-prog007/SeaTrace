"""Bateau test D « long silence près d'une zone interdite » (entièrement simulé).

Contrairement à A, B et C, D n'est pas la copie d'un vrai trajet : c'est une sortie de chalutier
inventée, au départ de la côte du Jutland, près de la zone DK-MSFD-A (chalut de fond interdit).

  - Ce que voit le détecteur : le bateau quitte la côte vers l'est, passe à --ecart-km (2,5 km) au sud de la
    zone, dans son tampon de 4 milles, puis le transpondeur se coupe --silence-h heures (4 h par défaut). Il réapparaît un peu plus à l'est,
    sur la même ligne, et rentre au port. La ligne droite entre les deux positions ne touche pas la zone.
  - La vérité : pendant le silence, il est monté dans la zone, y a chalutté, puis est redescendu.

La distance entre les deux positions encadrant le silence est courte devant ce que le bateau peut parcourir
pendant le silence : un passage par la zone est possible sans être certain. C'est ce que les modèles doivent estimer.

MMSI fictif 999000004, nom « SYNTH D » ; mêmes colonnes que les bateaux A, B, C (src/synthetic.py).
RÈGLE : le détecteur ne lit JAMAIS boat_D_truth.parquet (seule l'évaluation le lit).

Usage : python src/boat_d.py [--silence-h 4] [--ecart-km 2.5]
"""
import argparse
import datetime as dt
import json
import pathlib

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import nearest_points
from pyproj import Transformer

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZONES = ROOT / "data" / "reference" / "kattegat_zones.geojson"
LAND = ROOT / "data" / "reference" / "kattegat_land.geojson"
OUT = ROOT / "data" / "synthetic"
PROJ = "EPSG:25832"
FWD = Transformer.from_crs("EPSG:4326", PROJ, always_xy=True)
INV = Transformer.from_crs(PROJ, "EPSG:4326", always_xy=True)

ZONE_ID = "DK-MSFD-A"
MMSI, NAME, SCENARIO = "999000004", "SYNTH D", "D"
DEPART = pd.Timestamp("2026-09-16T03:00:00Z")
ROUTE_KN, CHALUT_KN = 8.0, 3.0  # vitesse de route et de chalutage
PAS_S = 10                      # une position toutes les 10 s, comme l'AIS de classe A
KN = 1852 / 3600                # nœuds -> m/s


def troncons(points, vitesses_kn, statuts):
    """Positions toutes les PAS_S secondes le long d'une suite de segments parcourus à vitesse donnée."""
    xs, ys, sog, cog, nav = [], [], [], [], []
    reste = 0.0
    for (a, b), v, st in zip(zip(points[:-1], points[1:]), vitesses_kn, statuts):
        d = float(np.hypot(b[0] - a[0], b[1] - a[1]))
        pas = v * KN * PAS_S
        s = reste
        while s < d:
            f = s / d
            xs.append(a[0] + f * (b[0] - a[0])); ys.append(a[1] + f * (b[1] - a[1]))
            sog.append(v); cog.append(np.degrees(np.arctan2(b[0] - a[0], b[1] - a[1])) % 360); nav.append(st)
            s += pas
        reste = s - d
    return np.array(xs), np.array(ys), np.array(sog), np.array(cog), nav


def construire(silence_h: float, ecart_km: float):
    z = gpd.read_file(ZONES).to_crs(PROJ)
    zone = z[(z.layer == "zone") & (z.zone_id == ZONE_ID)].geometry.iloc[0]
    terre = gpd.read_file(LAND).to_crs(PROJ).union_all()
    y = zone.bounds[1] - ecart_km * 1000  # route vers l'est, ecart_km au sud de la pointe de la zone
    # Départ : la côte à l'ouest de la zone, sur cette latitude, à 800 m du rivage.
    cote = shapely.LineString([(zone.bounds[0] - 60000, y), (zone.centroid.x, y)]).intersection(terre)
    x_dep = max(g.bounds[2] for g in getattr(cote, "geoms", [cote])) + 800
    p0 = (zone.bounds[0] - 2000, y)  # dernière position avant le silence (sud-ouest de la zone)
    p1 = (p0[0] + 9000, y)           # première position après le silence, 9 km plus à l'est
    # Vérité pendant le silence : montée dans la zone, chalutage en allers-retours, redescente vers p1.
    entree = nearest_points(zone.buffer(-1500), shapely.Point(p0))[0]
    nord = (entree.x + 600, entree.y + 2800)
    dist = lambda a, b: float(np.hypot(b[0] - a[0], b[1] - a[1]))

    def chalutage(n):  # n allers-retours ; vitesse de chalutage pour que le silence dure silence_h
        a = [p0, (entree.x, entree.y)]
        for k in range(1, n + 1):
            a.append((nord[0] + 300 * k, nord[1]) if k % 2 else (entree.x + 300 * k, entree.y))
        t_route = (dist(p0, a[1]) + dist(a[-1], p1)) / (ROUTE_KN * KN)
        return a, sum(dist(u, v) for u, v in zip(a[1:-1], a[2:])) / (silence_h * 3600 - t_route) / KN

    allers, v_chalut = min((chalutage(n) for n in range(1, 25)), key=lambda c: abs(c[1] - CHALUT_KN))
    if not 2.0 <= v_chalut <= 4.5:
        raise SystemExit(f"vitesse de chalutage irréaliste ({v_chalut:.1f} nd) : changer --silence-h")
    retour = (x_dep, y - 3000)
    pts = [(x_dep, y), p0] + allers[1:] + [p1, (p1[0] + 6000, y), retour]
    vit = [ROUTE_KN, ROUTE_KN] + [v_chalut] * (len(allers) - 2) + [ROUTE_KN, ROUTE_KN, ROUTE_KN]
    stat = ["Under way using engine"] * 2 + ["Engaged in fishing"] * (len(allers) - 2) + ["Under way using engine"] * 3
    route = shapely.LineString(pts)
    if route.intersects(terre.buffer(-50)):
        raise SystemExit("le trajet simulé touche la terre")
    x, yy, sog, cog, nav = troncons(pts, vit, stat)
    t = DEPART + pd.to_timedelta(np.arange(len(x)) * PAS_S, unit="s")
    t_off = DEPART + pd.Timedelta(seconds=np.hypot(p0[0] - x_dep, 0) / (ROUTE_KN * KN))
    t_on = t_off + pd.Timedelta(hours=silence_h)
    cache = np.asarray((t >= t_off) & (t < t_on))
    rng = np.random.default_rng(4)
    x = x + rng.normal(0, 4, len(x)); yy = yy + rng.normal(0, 4, len(x))  # bruit GPS de quelques mètres
    lon, lat = INV.transform(x, yy)
    df = pd.DataFrame({
        "mmsi": MMSI, "timestamp": t.astype("datetime64[us, UTC]"), "lat": lat, "lon": lon,
        "sog": np.round(sog + rng.normal(0, 0.15, len(x)), 1), "cog": np.round((cog + rng.normal(0, 1.5, len(x))) % 360, 1),
        "heading": np.round(cog).astype("int64") % 360, "nav_status": nav, "vessel_length_m": np.int64(16),
        "mobile_type": "Class A", "is_class_a": True, "vessel_ship_type": "Fishing", "is_fishing_candidate": True,
        "vessel_name": NAME, "is_synthetic": True, "scenario": SCENARIO})
    dans = shapely.contains_xy(zone, x, yy)
    evenement = {"type": "long_silence_near_zone", "start": t_off.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "end": t_on.strftime("%Y-%m-%dT%H:%M:%SZ"), "minutes": round(silence_h * 60),
                 "zone_ids": [ZONE_ID], "truth_minutes_inside_zone": round(dans.sum() * PAS_S / 60, 1),
                 "straight_line_between_reports_crosses_zone": bool(shapely.LineString([p0, p1]).intersects(zone)),
                 "distance_last_report_to_zone_km": round(shapely.Point(p0).distance(zone) / 1000, 1)}
    return df, cache, evenement


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--silence-h", type=float, default=4.0)
    p.add_argument("--ecart-km", type=float, default=2.5)
    a = p.parse_args()
    df, cache, ev = construire(a.silence_h, a.ecart_km)
    truth = df.assign(reported_present=~cache, is_manipulated=cache, manipulation=ev["type"],
                      event_start=ev["start"], event_end=ev["end"], source_track_id="simulated (no real source)",
                      source_mmsi="", reported_lat=df["lat"], reported_lon=df["lon"])
    reported = df[~cache].reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    reported.to_parquet(OUT / "boat_D_reported.parquet", index=False)
    truth.to_parquet(OUT / "boat_D_truth.parquet", index=False)
    resume_f = OUT / "summary_truth.json"
    resume = json.loads(resume_f.read_text(encoding="utf-8"))
    resume["boats"] = [b for b in resume["boats"] if b["scenario"] != SCENARIO] + [
        {"scenario": SCENARIO, "mmsi": MMSI, "source_track_id": "simulated (no real source)",
         "rows_truth": len(truth), "rows_reported": len(reported), **ev,
         "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}]
    resume_f.write_text(json.dumps(resume, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(resume["boats"][-1], ensure_ascii=False))


if __name__ == "__main__":
    main()
