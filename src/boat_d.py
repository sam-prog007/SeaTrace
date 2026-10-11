"""Bateau test D « longue coupure juste après le port, près d'une zone interdite » (entièrement simulé).

Contrairement à A, B et C, D n'est pas la copie d'un vrai trajet : c'est une sortie de chalutier inventée,
au départ du Limfjord (Hals), à environ 23 km au sud-ouest de la zone DK-MSFD-A (chalut de fond interdit).

  - Ce que voit le détecteur : le bateau sort du fjord ; une vingtaine de minutes plus tard, encore près de
    la côte, le transpondeur se coupe --silence-h heures (5 h 30 par défaut, l'algorithme reconstruit
    jusqu'à 6 h). Il réapparaît loin à l'est, sur la grande route qui longe le sud du tampon de 4 milles,
    et continue vers l'est. La ligne droite entre les deux positions ne touche pas la zone.
  - La vérité : pendant la coupure, il a remonté la côte vers le nord-est, est entré dans la zone par
    l'ouest, y a chaluté en allers-retours vers le nord, puis est redescendu rejoindre la route.

En 5 h 30, le bateau peut aller presque partout : remonter la côte, entrer dans la zone, filer vers l'est
ou vers le sud. Plusieurs familles de trajets restent plausibles ; c'est ce que les modèles doivent estimer.

MMSI fictif 999000004, nom « SYNTH D » ; mêmes colonnes que les bateaux A, B, C (src/synthetic.py).
RÈGLE : le détecteur ne lit JAMAIS boat_D_truth.parquet (seule l'évaluation le lit).

Usage : python src/boat_d.py [--silence-h 5.5]
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
# Points (lon, lat) du trajet visible
PORT = (10.318, 56.988)         # sortie du Limfjord, Hals
COUPURE = (10.39, 57.012)       # dernière position avant la coupure, ~20 min après le départ, près de la côte
REAPPARITION = (10.86, 56.972)  # première position après la coupure, sur la route vers l'est
FIN = (11.10, 56.975)
LAT_COTE = 57.10                # la vérité remonte la côte jusqu'à cette latitude avant d'entrer dans la zone


def xy(p):
    return FWD.transform(*p)


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


def construire(silence_h: float):
    z = gpd.read_file(ZONES).to_crs(PROJ)
    zone = z[(z.layer == "zone") & (z.zone_id == ZONE_ID)].geometry.iloc[0]
    terre = gpd.read_file(LAND).to_crs(PROJ).union_all()
    port, p0, p1, fin = xy(PORT), xy(COUPURE), xy(REAPPARITION), xy(FIN)
    # Vérité : remontée de la côte à 2 km du rivage jusqu'à LAT_COTE, puis entrée dans la zone par l'ouest.
    lon = next(l for l in np.arange(10.3, 10.7, 0.002) if terre.distance(shapely.Point(xy((l, LAT_COTE)))) > 2000
               and not terre.contains(shapely.Point(xy((l, LAT_COTE)))))
    cote = xy((lon, LAT_COTE))
    interieur = zone.buffer(-500)
    entree = nearest_points(interieur, shapely.Point(cote))[0]
    dist = lambda a, b: float(np.hypot(b[0] - a[0], b[1] - a[1]))

    def chalutage(n):  # n allers-retours vers le nord dans la zone ; vitesse pour que la coupure dure silence_h
        a = [(entree.x, entree.y)]
        for k in range(1, n + 1):
            a.append((entree.x + 250 * k, entree.y + 5000) if k % 2 else (entree.x + 250 * k, entree.y))
        t_route = (dist(p0, cote) + dist(cote, a[0]) + dist(a[-1], p1)) / (ROUTE_KN * KN)
        return a, sum(dist(u, v) for u, v in zip(a[:-1], a[1:])) / (silence_h * 3600 - t_route) / KN

    allers, v_chalut = min((chalutage(n) for n in range(1, 30)), key=lambda c: abs(c[1] - CHALUT_KN))
    if not 2.0 <= v_chalut <= 4.5:
        raise SystemExit(f"vitesse de chalutage irréaliste ({v_chalut:.1f} nd) : changer --silence-h")
    if not all(interieur.buffer(10).contains(shapely.Point(p)) for p in allers):
        raise SystemExit("le chalutage sort de la zone")
    pts = [port, p0, cote] + allers + [p1, fin]
    vit = [ROUTE_KN, ROUTE_KN, ROUTE_KN] + [v_chalut] * (len(allers) - 1) + [ROUTE_KN, ROUTE_KN]
    stat = (["Under way using engine"] * 3 + ["Engaged in fishing"] * (len(allers) - 1)
            + ["Under way using engine"] * 2)
    if shapely.LineString(pts).intersects(terre.buffer(-50)):
        raise SystemExit("le trajet simulé touche la terre")
    x, yy, sog, cog, nav = troncons(pts, vit, stat)
    t = DEPART + pd.to_timedelta(np.arange(len(x)) * PAS_S, unit="s")
    t_off = DEPART + pd.Timedelta(seconds=round(dist(port, p0) / (ROUTE_KN * KN)))
    t_on = t_off + pd.Timedelta(hours=silence_h)
    cache = np.asarray((t >= t_off) & (t < t_on))
    rng = np.random.default_rng(4)
    x = x + rng.normal(0, 4, len(x)); yy = yy + rng.normal(0, 4, len(x))  # bruit GPS de quelques mètres
    lons, lats = INV.transform(x, yy)
    df = pd.DataFrame({
        "mmsi": MMSI, "timestamp": t.astype("datetime64[us, UTC]"), "lat": lats, "lon": lons,
        "sog": np.round(sog + rng.normal(0, 0.15, len(x)), 1), "cog": np.round((cog + rng.normal(0, 1.5, len(x))) % 360, 1),
        "heading": np.round(cog).astype("int64") % 360, "nav_status": nav, "vessel_length_m": np.int64(16),
        "mobile_type": "Class A", "is_class_a": True, "vessel_ship_type": "Fishing", "is_fishing_candidate": True,
        "vessel_name": NAME, "is_synthetic": True, "scenario": SCENARIO})
    dans = shapely.contains_xy(zone, x, yy)
    evenement = {"type": "long_silence_near_zone", "start": t_off.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "end": t_on.strftime("%Y-%m-%dT%H:%M:%SZ"), "minutes": round(silence_h * 60),
                 "zone_ids": [ZONE_ID], "truth_minutes_inside_zone": round(dans.sum() * PAS_S / 60, 1),
                 "straight_line_between_reports_crosses_zone": bool(shapely.LineString([p0, p1]).intersects(zone)),
                 "distance_last_report_to_zone_km": round(shapely.Point(p0).distance(zone) / 1000, 1),
                 "distance_between_reports_km": round(dist(p0, p1) / 1000, 1)}
    return df, cache, evenement


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--silence-h", type=float, default=5.5)
    a = p.parse_args()
    df, cache, ev = construire(a.silence_h)
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
