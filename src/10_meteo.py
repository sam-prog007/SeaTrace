"""Contexte météo : vent à 10 m et hauteur des vagues (Open-Meteo), pour la période de la carte.

- Vent : API d'archive Open-Meteo (archive-api.open-meteo.com), vitesse, direction
  d'où vient le vent et rafales à 10 m, en nœuds.
- Vagues : API marine Open-Meteo (marine-api.open-meteo.com), hauteur, direction
  et période des vagues (modèles de vagues du service météorologique allemand, DWD).

Grille grossière de 0,25° sur le Kattegat (55-58 N, 10-13 E), points en mer
seulement (trait de côte Natural Earth). Un seul appel groupé par API ; la
réponse est mise en cache dans data/raw/meteo/ et n'est jamais redemandée.
Une source qui ne répond pas est signalée, jamais remplacée.

Sorties :
  data/interim/meteo_wind.parquet, data/interim/meteo_waves.parquet  (format long)
  data/interim/meteo_status.json                                     (disponibilité, contrôles)

Usage : python src/10_meteo.py
"""
import json
import pathlib
import time

import geopandas as gpd
import numpy as np
import polars as pl
import requests
from shapely.geometry import Point

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "meteo"
INTERIM = ROOT / "data" / "interim"
LAND = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"

START, END = "2026-09-15", "2026-09-17"  # période de la carte (UTC)
LAT = (55.0, 58.0)
LON = (10.0, 13.0)
STEP = 0.25

SOURCES = {
    "wind": {
        "url": "https://archive-api.open-meteo.com/v1/archive",
        "hourly": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m"],
        "extra": {"wind_speed_unit": "kn"},
        # Bornes au-delà desquelles une valeur est jugée aberrante (signalée, pas corrigée).
        "max": {"wind_speed_10m": 80, "wind_gusts_10m": 110},
    },
    "waves": {
        "url": "https://marine-api.open-meteo.com/v1/marine",
        "hourly": ["wave_height", "wave_direction", "wave_period"],
        "extra": {},
        "max": {"wave_height": 15, "wave_period": 30},
    },
}


class SourceError(Exception):
    pass


def sea_grid() -> list[tuple[float, float]]:
    land = gpd.read_file(LAND, bbox=(LON[0] - 1, LAT[0] - 1, LON[1] + 1, LAT[1] + 1)).union_all()
    pts = []
    for lat in np.arange(LAT[0], LAT[1] + 1e-9, STEP):
        for lon in np.arange(LON[0], LON[1] + 1e-9, STEP):
            if not land.contains(Point(lon, lat)):
                pts.append((round(float(lat), 2), round(float(lon), 2)))
    return pts


def fetch(key: str, pts: list[tuple[float, float]]) -> list[dict]:
    """Un appel groupé pour tous les points, mis en cache ; erreurs explicites."""
    cache = RAW / f"{key}_{START}_{END}_step{STEP}.json"
    if cache.exists():
        print(f"[skip] {cache.relative_to(ROOT)} déjà en cache")
        return json.loads(cache.read_text(encoding="utf-8"))
    s = SOURCES[key]
    params = {"latitude": ",".join(str(p[0]) for p in pts), "longitude": ",".join(str(p[1]) for p in pts),
              "start_date": START, "end_date": END, "hourly": ",".join(s["hourly"]), "timezone": "GMT", **s["extra"]}
    last = None
    for wait in (0, 10, 30):
        time.sleep(wait)
        try:
            r = requests.get(s["url"], params=params, timeout=120)
        except requests.RequestException as e:
            last = f"pas de réponse ({type(e).__name__})"
            continue
        try:
            body = r.json()
        except ValueError:
            last = f"réponse illisible (HTTP {r.status_code})"
            continue
        if r.status_code == 200 and not (isinstance(body, dict) and body.get("error")):
            body = body if isinstance(body, list) else [body]
            if len(body) != len(pts):
                raise SourceError(f"{len(body)} réponses pour {len(pts)} points demandés")
            RAW.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(body), encoding="utf-8")
            return body
        last = f"HTTP {r.status_code} : {body.get('reason') if isinstance(body, dict) else body}"
        if r.status_code == 429 and "Daily" in str(last):
            break  # quota journalier : inutile d'insister
    raise SourceError(last)


def to_long(key: str, pts, body: list[dict]) -> pl.DataFrame:
    frames = []
    for (lat, lon), b in zip(pts, body):
        h = b["hourly"]
        df = pl.DataFrame({"time": h["time"], **{v: h.get(v) for v in SOURCES[key]["hourly"]}},
                          schema_overrides={v: pl.Float64 for v in SOURCES[key]["hourly"]}, strict=False)
        frames.append(df.with_columns(pl.lit(lat).alias("lat"), pl.lit(lon).alias("lon"),
                                      pl.lit(b["latitude"]).alias("model_lat"),
                                      pl.lit(b["longitude"]).alias("model_lon")))
    df = pl.concat(frames).with_columns(pl.col("time").str.strptime(pl.Datetime("us", "UTC"), "%Y-%m-%dT%H:%M"))
    return df


def check(key: str, df: pl.DataFrame) -> dict:
    s = SOURCES[key]
    var0 = s["hourly"][0]
    per_pt = df.group_by(["lat", "lon"]).agg(pl.col(var0).is_null().all().alias("all_null"))
    empty = per_pt.filter(pl.col("all_null"))
    out = {"points": per_pt.height, "points_without_data": empty.height,
           "points_without_data_list": empty.select("lat", "lon").rows(),
           "null_share": {v: round(float(df[v].null_count() / df.height), 4) for v in s["hourly"]},
           "aberrant": {v: int((df[v] > mx).sum()) for v, mx in s["max"].items()},
           "range": {v: [df[v].min(), df[v].max()] for v in s["hourly"]}}
    return out


def main():
    pts = sea_grid()
    print(f"[info] {len(pts)} points de grille en mer (pas {STEP}°)")
    status = {"period": [START, END], "grid_step_deg": STEP, "n_points": len(pts)}
    INTERIM.mkdir(parents=True, exist_ok=True)
    for key in SOURCES:
        try:
            body = fetch(key, pts)
        except SourceError as e:
            status[key] = {"available": False, "reason": str(e), "source": SOURCES[key]["url"]}
            print(f"[miss] {key} : non disponible ({e})")
            continue
        df = to_long(key, pts, body)
        df.write_parquet(INTERIM / f"meteo_{key}.parquet")
        status[key] = {"available": True, "source": SOURCES[key]["url"], **check(key, df)}
        print(f"[out ] data/interim/meteo_{key}.parquet : {df.height} lignes ; contrôles : "
              f"{json.dumps({k: status[key][k] for k in ('points_without_data', 'null_share', 'aberrant', 'range')})}")
    (INTERIM / "meteo_status.json").write_text(json.dumps(status, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
