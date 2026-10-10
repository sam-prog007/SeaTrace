"""Étape 3b : portions de trajet dans les zones restreintes et leurs tampons.

Calcul sur les positions brutes (pas sur les lignes simplifiées) : chaque paire
de positions successives d'un trajet forme un petit segment ; la part de sa
longueur située dans une zone, multipliée par l'intervalle de temps, donne le
temps passé dans la zone (vitesse supposée constante entre deux messages).

Aucune conclusion d'infraction : un navire en transit, engins relevés, est en
règle. Le résultat s'appelle « portion de trajet dans une zone ».

Sorties (nom suffixé par les paramètres des trajets) :
  data/processed/tracks/crossing_<tag>.parquet   une ligne par (trajet, zone)
  data/processed/tracks/portions_<tag>.parquet   géométries des portions (WGS84)

Usage : python src/07_crossing.py [mêmes options que 06_tracks.py]
"""
import argparse
import importlib.util
import pathlib

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import shapely

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZONES = ROOT / "data" / "reference" / "kattegat_zones.geojson"
PROJ_CRS = "EPSG:25832"
LAYERS = ("zone", "buffer")  # zones restreintes (Areas 1/2/3) et tampons 4 NM

_spec = importlib.util.spec_from_file_location("tracks", pathlib.Path(__file__).with_name("06_tracks.py"))
tracks_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tracks_mod)


def pair_segments(pts: pl.DataFrame):
    """Segments entre positions successives d'un même trajet."""
    same = (pts["track_id"] == pts["track_id"].shift(-1)).fill_null(False).to_numpy()
    i = np.flatnonzero(same)
    x, y = pts["x"].to_numpy(), pts["y"].to_numpy()
    coords = np.stack([np.column_stack([x[i], y[i]]), np.column_stack([x[i + 1], y[i + 1]])], axis=1)
    segs = shapely.linestrings(coords)
    ts = pts["timestamp"].to_numpy()
    dt_s = (ts[i + 1] - ts[i]).astype("timedelta64[s]").astype(float)
    return i, segs, dt_s


def crossing(start, end, gap_min, tol_m) -> tuple[pathlib.Path, pathlib.Path]:
    t = tracks_mod.tag(start, end, gap_min, tol_m)
    out_dir = tracks_mod.OUT_DIR
    out = out_dir / f"crossing_{t}.parquet"
    out_geo = out_dir / f"portions_{t}.parquet"
    out_tot = out_dir / f"crossing_tracks_{t}.parquet"
    if out.exists() and out_geo.exists() and out_tot.exists():
        print(f"[skip] {out.relative_to(ROOT)} déjà calculé")
        return out, out_geo
    tracks_mod.build_tracks(start, end, gap_min, tol_m)
    pts = pl.read_parquet(out_dir / f"points_{t}.parquet")
    zones = gpd.read_file(ZONES)
    zones = zones[zones.layer.isin(LAYERS)].to_crs(PROJ_CRS).reset_index(drop=True)
    if not zones.is_valid.all():
        raise SystemExit("Zones invalides : relancer src/03_zones.py et lire ses messages.")

    i, segs, dt_s = pair_segments(pts)
    tree = shapely.STRtree(zones.geometry.values)
    seg_idx, zone_idx = tree.query(segs, predicate="intersects")
    print(f"[info] {len(segs):,} segments, {len(seg_idx):,} couples segment-zone candidats")
    zg = zones.geometry.values[zone_idx]
    s = segs[seg_idx]
    inter = shapely.intersection(s, zg)
    seg_len = shapely.length(s)
    # Segment de longueur nulle (navire immobile) : compté si le point est dans la zone.
    still_in = shapely.within(shapely.get_point(s, 0), zg).astype(float)
    frac = np.where(seg_len > 0, shapely.length(inter) / np.where(seg_len > 0, seg_len, 1), still_in)
    p0 = i[seg_idx]
    rows = pl.DataFrame({
        "track_id": pts["track_id"].to_numpy()[p0],
        "mmsi": pts["mmsi"].to_numpy()[p0],
        "zone_id": zones.zone_id.to_numpy(dtype=object)[zone_idx],
        "layer": zones.layer.to_numpy(dtype=object)[zone_idx],
        "seconds_in": dt_s[seg_idx] * frac,
        "km_in": shapely.length(inter) / 1000,
        "t0": pts["timestamp"].to_numpy()[p0],
        "sog": pl.Series(pts["sog"].to_numpy()[p0], nan_to_null=True),
        "engaged": (pts["nav_status"].to_numpy()[p0] == "Engaged in fishing"),
    })
    per_zone = (rows.group_by(["track_id", "mmsi", "zone_id", "layer"])
                .agg((pl.col("seconds_in").sum() / 60).alias("minutes_in"),
                     pl.col("km_in").sum(), pl.col("t0").min().alias("first_in"), pl.col("t0").max().alias("last_in"),
                     pl.len().alias("n_segments"), pl.col("sog").mean().alias("sog_mean_in_kn"),
                     pl.col("engaged").mean().alias("share_engaged_fishing_in"))
                .sort(["track_id", "zone_id"]))
    zinfo = pl.DataFrame({c: zones[c].tolist() for c in ["zone_id", "name", "country", "category", "restriction"]})
    per_zone = per_zone.join(zinfo, on="zone_id", how="left")
    per_zone.write_parquet(out)

    # Totaux par trajet sur l'union de chaque couche : les tampons se chevauchent,
    # additionner les zones compterait deux fois le même temps.
    tot = pl.DataFrame({"track_id": pts["track_id"].unique().to_list()})
    for layer in LAYERS:
        u = zones[zones.layer == layer].union_all()
        cand = np.unique(seg_idx[zones.layer.to_numpy(dtype=object)[zone_idx] == layer])
        su = segs[cand]
        lu = shapely.length(su)
        iu = shapely.length(shapely.intersection(su, u))
        fu = np.where(lu > 0, iu / np.where(lu > 0, lu, 1), shapely.within(shapely.get_point(su, 0), u).astype(float))
        agg = (pl.DataFrame({"track_id": pts["track_id"].to_numpy()[i[cand]], "s": dt_s[cand] * fu})
               .group_by("track_id").agg((pl.col("s").sum() / 60).alias(f"minutes_in_{layer}")))
        tot = tot.join(agg, on="track_id", how="left").with_columns(pl.col(f"minutes_in_{layer}").fill_null(0.0))
    tot.write_parquet(out_tot)

    # Géométries des portions, pour l'affichage : intersection des trajets
    # simplifiés (06) avec chaque couche. Les durées, elles, viennent des
    # positions brutes ci-dessus (la dérive GPS au port produirait sinon des
    # millions de fragments).
    tracks = gpd.read_parquet(out_dir / f"tracks_{t}.parquet").to_crs(PROJ_CRS)
    parts = []
    for layer in LAYERS:
        u = zones[zones.layer == layer].union_all()
        g = tracks[["track_id", "geometry"]].copy()
        g["geometry"] = shapely.intersection(g.geometry.values, u)
        g = g[~g.is_empty].assign(layer=layer)
        parts.append(g)
    port = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=PROJ_CRS)
    port = port[port.geom_type.isin(["LineString", "MultiLineString"])]
    port.to_crs("EPSG:4326").to_parquet(out_geo)
    n_tracks = per_zone.select(pl.col("track_id").n_unique()).item()
    print(f"[out ] {out.relative_to(ROOT)} : {per_zone.height} couples trajet-zone, {n_tracks} trajets concernés")
    return out, out_geo


def main():
    d = tracks_mod.DEFAULTS
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=d["start"])
    p.add_argument("--end", default=d["end"])
    p.add_argument("--gap-min", type=float, default=d["gap_min"])
    p.add_argument("--tol-m", type=float, default=d["tol_m"])
    a = p.parse_args()
    crossing(a.start, a.end, a.gap_min, a.tol_m)


if __name__ == "__main__":
    main()
