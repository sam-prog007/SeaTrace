"""Étape 3a : trajets des navires de pêche (classe A) à partir du jeu nettoyé.

Un trajet = suite de positions d'un même MMSI, coupée dès que l'intervalle
entre deux messages dépasse GAP_MINUTES. Les trajets de moins de MIN_POINTS
points sont écartés (et comptés). Les lignes sont simplifiées avec une
tolérance en mètres, en EPSG:25832, puis reconverties en WGS84.

Le résultat dépend des paramètres : son nom les contient, et un fichier déjà
calculé avec les mêmes paramètres est réutilisé.

Usage : python src/06_tracks.py [--start 2026-09-15] [--end 2026-09-17] [--gap-min 30] [--tol-m 30]
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
AIS = ROOT / "data" / "processed" / "ais_kattegat_2026-09.parquet"
# Jeu affiché par la carte, s'il existe (src/demo_boats.py) ; sinon le jeu réel.
MAP_INPUT = ROOT / "data" / "processed" / "map_input_2026-09-15_2026-09-17.parquet"
OUT_DIR = ROOT / "data" / "processed" / "tracks"
PROJ_CRS = "EPSG:25832"
MIN_POINTS = 3

DEFAULTS = {"start": "2026-09-15", "end": "2026-09-17", "gap_min": 30, "tol_m": 30}


def tag(start: str, end: str, gap_min: float, tol_m: float) -> str:
    return f"{start}_{end}_gap{gap_min:g}_tol{tol_m:g}"


def load_points(start: str, end: str, gap_min: float) -> pl.DataFrame:
    """Positions de la période, triées, avec un identifiant de trajet."""
    t0 = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
    t1 = dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    src = pl.scan_parquet(MAP_INPUT) if MAP_INPUT.exists() else \
        pl.scan_parquet(str(AIS / "**" / "*.parquet"), hive_partitioning=True)
    df = (src
          .filter((pl.col("timestamp") >= t0) & (pl.col("timestamp") < t1))
          .select("mmsi", "timestamp", "lat", "lon", "sog", "cog", "heading", "nav_status", "vessel_ship_type",
                  "vessel_length_m", "vessel_name")
          .sort(["mmsi", "timestamp"])
          .collect())
    gap = (df["timestamp"].diff().dt.total_seconds() / 60)
    new_vessel = df["mmsi"] != df["mmsi"].shift(1)
    new_track = new_vessel.fill_null(True) | (gap > gap_min).fill_null(True)
    df = df.with_columns(new_track.cum_sum().alias("seg"))
    tr = Transformer.from_crs("EPSG:4326", PROJ_CRS, always_xy=True)
    x, y = tr.transform(df["lon"].to_numpy(), df["lat"].to_numpy())
    return df.with_columns(pl.Series("x", x), pl.Series("y", y))


def build_tracks(start: str, end: str, gap_min: float, tol_m: float) -> pathlib.Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = tag(start, end, gap_min, tol_m)
    out = OUT_DIR / f"tracks_{name}.parquet"
    pts_out = OUT_DIR / f"points_{name}.parquet"
    # Les positions doivent porter le cap de route (cog), utilisé par 11_context.py.
    src_file = MAP_INPUT if MAP_INPUT.exists() else None
    stale = src_file is not None and out.exists() and out.stat().st_mtime < src_file.stat().st_mtime
    if out.exists() and pts_out.exists() and "cog" in pl.read_parquet_schema(pts_out) and not stale:
        print(f"[skip] {out.relative_to(ROOT)} déjà calculé")
        return out
    df = load_points(start, end, gap_min)
    seg = (df.group_by("seg", maintain_order=True)
           .agg(pl.col("mmsi").first(), pl.len().alias("n_points"),
                pl.col("timestamp").min().alias("start"), pl.col("timestamp").max().alias("end"),
                pl.col("sog").mean().alias("sog_mean_kn"),
                (pl.col("nav_status") == "Engaged in fishing").mean().alias("share_engaged_fishing"),
                pl.col("vessel_ship_type").first().alias("ship_type"),
                pl.col("vessel_length_m").first().alias("length_m"),
                pl.col("vessel_name").first().alias("name")))
    kept = seg.filter(pl.col("n_points") >= MIN_POINTS)
    pts = df.join(kept.select("seg"), on="seg", how="semi")
    # Une ligne par trajet, en mètres, puis simplification.
    offsets = np.r_[0, np.cumsum(kept["n_points"].to_numpy())]
    xy = np.column_stack([pts["x"].to_numpy(), pts["y"].to_numpy()])
    lines = [shapely.LineString(xy[a:b]) for a, b in zip(offsets[:-1], offsets[1:])]
    raw_len = shapely.length(lines)
    simple = shapely.simplify(lines, tol_m, preserve_topology=False)
    gdf = gpd.GeoDataFrame(kept.to_pandas(), geometry=list(simple), crs=PROJ_CRS)
    gdf["track_id"] = [f"{m}-{i}" for i, m in enumerate(gdf["mmsi"])]
    gdf["length_km"] = raw_len / 1000
    gdf["n_vertices_simplified"] = shapely.get_num_coordinates(simple)
    gdf["duration_h"] = (gdf["end"] - gdf["start"]).dt.total_seconds() / 3600
    gdf = gdf.to_crs("EPSG:4326")
    meta = {"start": start, "end": end, "gap_min": gap_min, "tol_m": tol_m, "min_points": MIN_POINTS,
            "positions": df.height, "segments_total": seg.height, "segments_kept": kept.height,
            "segments_dropped_lt_min_points": seg.height - kept.height,
            "positions_dropped": df.height - pts.height,
            "vessels": int(kept["mmsi"].n_unique()),
            "vertices_raw": int(pts.height), "vertices_simplified": int(gdf["n_vertices_simplified"].sum())}
    gdf.drop(columns="seg").to_parquet(out)
    ids = pl.DataFrame({"track_id": gdf["track_id"].tolist(), "seg": kept["seg"]})  # même type de clé (Windows)
    pts.join(ids, on="seg") \
        .drop("seg").write_parquet(pts_out)
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"[out ] {out.relative_to(ROOT)}")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULTS["start"])
    p.add_argument("--end", default=DEFAULTS["end"])
    p.add_argument("--gap-min", type=float, default=DEFAULTS["gap_min"])
    p.add_argument("--tol-m", type=float, default=DEFAULTS["tol_m"])
    a = p.parse_args()
    build_tracks(a.start, a.end, a.gap_min, a.tol_m)


if __name__ == "__main__":
    main()
