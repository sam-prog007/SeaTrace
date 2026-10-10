"""Contexte des trajets : courant, vent et vagues aux passages dans les zones, et grilles pour la carte.

Entrées (chacune facultative ; une source absente est marquée « non disponible ») :
  courants : data/raw/currents/*.nc            (src/09_currents.py, Copernicus Marine)
  vent     : data/interim/meteo_wind.parquet   (src/10_meteo.py, Open-Meteo)
             à défaut data/raw/wind/*.nc       (src/09_currents.py --wind, Copernicus Marine)
  vagues   : data/interim/meteo_waves.parquet  (src/10_meteo.py, Open-Meteo / DWD)
  à défaut de vraies données de courant ou de vent : data/simulated/ (src/12_simulate_context.py),
  marquées « simulé » jusque dans la carte.

Pour chaque position d'un trajet située dans une zone restreinte ou un tampon :
  - courant, vent et vagues interpolés linéairement en espace et en temps ; si un voisin
    de la grille est à terre, on prend la valeur de la maille la plus proche ;
  - vitesse du courant (nœuds) et direction vers laquelle il va (degrés, 0 = nord) ;
  - angle entre le cap de route du navire (COG) et la direction du courant
    (0° = courant dans le dos, 180° = courant de face), si le navire avance (≥ 0,5 nœud) ;
  - ESTIMATION de la vitesse par rapport à l'eau : vecteur vitesse fond (SOG, COG) moins
    vecteur courant. Ce n'est pas une mesure.
Ces valeurs sont ensuite moyennées par portion (trajet × zone ou tampon).

Grilles pour la carte : moyenne journalière des vecteurs courant (grille ~5 milles
nautiques) et vent (grille Open-Meteo de 0,25°), résumé par jour.

Sorties : data/processed/context/portion_context_<tag>.parquet, context_<tag>.json
"""
import datetime as dt
import importlib.util
import json
import pathlib

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
import xarray as xr

ROOT = pathlib.Path(__file__).resolve().parents[1]
ZONES = ROOT / "data" / "reference" / "kattegat_zones.geojson"
INTERIM = ROOT / "data" / "interim"
SIMULATED = ROOT / "data" / "simulated"
OUT_DIR = ROOT / "data" / "processed" / "context"
PROJ_CRS = "EPSG:25832"
MS_TO_KN = 3600 / 1852
MOVING_KN = 0.5
MAX_SNAP_KM = 10  # Open-Meteo déplace un point vers sa maille de mer la plus proche : au-delà, on l'écarte
CUR_STEP = (5, 6)  # sous-échantillonnage de la grille courant (lat, lon) : ~5 milles nautiques


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Lecture des sources, toutes ramenées à (time, latitude, longitude), en nœuds -------------
def currents_from_nc(path: pathlib.Path) -> xr.Dataset:
    ds = xr.open_dataset(path)
    u, v = ds["uo"], ds["vo"]
    if "depth" in u.dims:
        u, v = u.isel(depth=0), v.isel(depth=0)
    return xr.Dataset({"u": u * MS_TO_KN, "v": v * MS_TO_KN}).load()


def snapped_ok(df: pl.DataFrame) -> pl.DataFrame:
    """Garde les points dont la maille Open-Meteo est à moins de MAX_SNAP_KM du point demandé."""
    km = ((pl.col("lat") - pl.col("model_lat")) * 111.2).pow(2) + \
         ((pl.col("lon") - pl.col("model_lon")) * 111.2 * np.cos(np.deg2rad(56.5))).pow(2)
    return df.filter(km.sqrt() <= MAX_SNAP_KM)


def n_snapped_out(path: pathlib.Path) -> int:
    df = pl.read_parquet(path).select("lat", "lon", "model_lat", "model_lon").unique()
    return df.height - snapped_ok(df).height


def wind_from_openmeteo(path: pathlib.Path) -> xr.Dataset:
    """Open-Meteo donne la direction d'où vient le vent : on passe en composantes (vers où il va)."""
    df = snapped_ok(pl.read_parquet(path)).with_columns(pl.col("time").dt.replace_time_zone(None))
    rad = np.deg2rad(df["wind_direction_10m"].to_numpy())
    spd = df["wind_speed_10m"].to_numpy()
    pdf = df.select("time", "lat", "lon", "wind_gusts_10m").to_pandas()
    pdf["u"], pdf["v"] = -spd * np.sin(rad), -spd * np.cos(rad)
    ds = pdf.rename(columns={"lat": "latitude", "lon": "longitude", "wind_gusts_10m": "gust"}) \
        .set_index(["time", "latitude", "longitude"]).to_xarray()
    return ds


def wind_from_nc(path: pathlib.Path) -> xr.Dataset:
    ds = xr.open_dataset(path)
    return xr.Dataset({"u": ds["eastward_wind"] * MS_TO_KN, "v": ds["northward_wind"] * MS_TO_KN}).load()


def waves_from_openmeteo(path: pathlib.Path) -> xr.Dataset:
    df = snapped_ok(pl.read_parquet(path)).with_columns(pl.col("time").dt.replace_time_zone(None))
    return (df.select("time", pl.col("lat").alias("latitude"), pl.col("lon").alias("longitude"),
                      pl.col("wave_height").alias("hs")).to_pandas()
            .set_index(["time", "latitude", "longitude"]).to_xarray())


def load_sources() -> dict:
    cur_mod = _load("09_currents")
    src = {"currents": None, "wind": None, "waves": None, "labels": {}, "simulated": {}}
    sim_cur = sorted(SIMULATED.glob("currents_sim_*.nc"))
    sim_wind = sorted(SIMULATED.glob("wind_sim_*.parquet"))
    p = cur_mod.out_path("currents")
    if p.exists():
        src["currents"] = currents_from_nc(p)
        src["labels"]["currents"] = "Copernicus Marine Service, BALTICSEA_ANALYSISFORECAST_PHY_003_006 (DOI 10.48670/moi-00010)"
    elif sim_cur:
        src["currents"] = currents_from_nc(sim_cur[-1])
        src["labels"]["currents"] = "SIMULATION SeaTrace (données fictives)"
        src["simulated"]["currents"] = True
    if (INTERIM / "meteo_wind.parquet").exists():
        src["wind"] = wind_from_openmeteo(INTERIM / "meteo_wind.parquet")
        src["labels"]["wind"] = "Open-Meteo, archive météo (vent à 10 m)"
    elif cur_mod.out_path("wind").exists():
        src["wind"] = wind_from_nc(cur_mod.out_path("wind"))
        src["labels"]["wind"] = "Copernicus Marine Service, WIND_GLO_PHY_L4_NRT_012_004 (DOI 10.48670/moi-00305)"
    elif sim_wind:
        src["wind"] = wind_from_openmeteo(sim_wind[-1])
        src["labels"]["wind"] = "SIMULATION SeaTrace (données fictives)"
        src["simulated"]["wind"] = True
    if (INTERIM / "meteo_waves.parquet").exists():
        src["waves"] = waves_from_openmeteo(INTERIM / "meteo_waves.parquet")
        src["labels"]["waves"] = "Open-Meteo, API marine, modèles de vagues du DWD"
    return src


# --- Interpolation -----------------------------------------------------------------------
def interp(ds: xr.Dataset, var: str, times, lats, lons) -> np.ndarray:
    """Linéaire en espace et en temps ; maille la plus proche si un voisin manque ; NaN hors période."""
    t = xr.DataArray(times, dims="p")
    la, lo = xr.DataArray(lats, dims="p"), xr.DataArray(lons, dims="p")
    lin = ds[var].interp(time=t, latitude=la, longitude=lo).values
    miss = np.flatnonzero(np.isnan(lin))
    for i in range(0, len(miss), 20000):  # par lots : (temps x points) reste petit en mémoire
        idx = miss[i:i + 20000]
        near = ds[var].sel(latitude=xr.DataArray(lats[idx], dims="p"),
                           longitude=xr.DataArray(lons[idx], dims="p"), method="nearest").transpose("time", "p").values
        # Interpolation linéaire dans le temps, point par point (indices et poids calculés directement).
        tg = ds.time.values.astype("datetime64[ns]").astype(np.int64)
        tq = times[idx].astype("datetime64[ns]").astype(np.int64)
        i1 = np.clip(np.searchsorted(tg, tq), 1, len(tg) - 1)
        i0 = i1 - 1
        w = (tq - tg[i0]) / np.maximum(tg[i1] - tg[i0], 1)
        cols = np.arange(len(idx))
        lin[idx] = near[i0, cols] * (1 - w) + near[i1, cols] * w
    tmin, tmax = ds.time.values.min(), ds.time.values.max()
    lin[(times < tmin) | (times > tmax)] = np.nan
    return lin


def enrich_points(pts: pl.DataFrame, src: dict) -> pl.DataFrame:
    times = pts["timestamp"].dt.replace_time_zone(None).to_numpy()
    lats, lons = pts["lat"].to_numpy(), pts["lon"].to_numpy()
    sog = pts["sog"].fill_null(np.nan).to_numpy().astype(float)
    cog = pts["cog"].fill_null(np.nan).to_numpy().astype(float)
    out = {}
    nan = np.full(len(pts), np.nan)
    if src["currents"] is not None:
        uc = interp(src["currents"], "u", times, lats, lons)
        vc = interp(src["currents"], "v", times, lats, lons)
        out["cur_kn"] = np.hypot(uc, vc)
        out["cur_dir_to"] = (np.degrees(np.arctan2(uc, vc)) + 360) % 360
        moving = sog >= MOVING_KN
        diff = np.abs((cog - out["cur_dir_to"] + 180) % 360 - 180)
        out["angle_cog_cur"] = np.where(moving, diff, np.nan)
        ve, vn = sog * np.sin(np.deg2rad(cog)), sog * np.cos(np.deg2rad(cog))
        out["water_kn_est"] = np.hypot(ve - uc, vn - vc)
    else:
        out.update(cur_kn=nan, cur_dir_to=nan, angle_cog_cur=nan, water_kn_est=nan)
    if src["wind"] is not None:
        uw = interp(src["wind"], "u", times, lats, lons)
        vw = interp(src["wind"], "v", times, lats, lons)
        out["wind_kn"] = np.hypot(uw, vw)
        out["wind_u"], out["wind_v"] = uw, vw
    else:
        out.update(wind_kn=nan, wind_u=nan, wind_v=nan)
    out["wave_m"] = interp(src["waves"], "hs", times, lats, lons) if src["waves"] is not None else nan
    return pts.with_columns([pl.Series(k, v, nan_to_null=True) for k, v in out.items()])


def wind_from_dir(u, v):
    """Direction d'où vient le vent (degrés, 0 = nord) à partir des composantes vers où il va."""
    return (np.degrees(np.arctan2(-u, -v)) + 360) % 360


# --- Grilles journalières pour la carte -------------------------------------------------------
def daily_grid(ds: xr.Dataset, days: list[str], step=(1, 1)) -> dict:
    sub = ds.isel(latitude=slice(0, None, step[0]), longitude=slice(0, None, step[1]))
    spd = np.hypot(sub["u"], sub["v"])
    out = {}
    for day, g in sub.groupby("time.date"):
        if str(day) not in days:
            continue  # marge de téléchargement hors période de la carte
        sg = spd.sel(time=g.time)
        mu, mv = g["u"].mean("time"), g["v"].mean("time")
        ms, mx = sg.mean("time"), sg.max("time")
        rows = []
        for i, la in enumerate(g.latitude.values):
            for j, lo in enumerate(g.longitude.values):
                a, b = float(mu[i, j]), float(mv[i, j])
                if np.isnan(a) or np.isnan(b):
                    continue
                rows.append([round(float(la), 4), round(float(lo), 4), round(a, 3), round(b, 3),
                             round(float(ms[i, j]), 2), round(float(mx[i, j]), 2)])
        out[str(day)] = rows
    return out


def summary(src: dict, days: list[str]) -> dict:
    res = {}
    for d in days:
        t0, t1 = np.datetime64(d), np.datetime64(d) + np.timedelta64(1, "D")
        row = {}
        if src["wind"] is not None:
            w = src["wind"].sel(time=slice(t0, t1 - np.timedelta64(1, "s")))
            s = np.hypot(w["u"], w["v"])
            row["wind_mean_kn"] = round(float(s.mean()), 1) if s.size else None
            row["wind_max_kn"] = round(float(s.max()), 1) if s.size else None
        if src["waves"] is not None:
            h = src["waves"]["hs"].sel(time=slice(t0, t1 - np.timedelta64(1, "s")))
            row["wave_max_m"] = round(float(h.max()), 2) if h.size else None
            row["wave_mean_m"] = round(float(h.mean()), 2) if h.size else None
        if src["currents"] is not None:
            c = src["currents"].sel(time=slice(t0, t1 - np.timedelta64(1, "s")))
            s = np.hypot(c["u"], c["v"])
            row["cur_mean_kn"] = round(float(s.mean()), 2) if s.size else None
            row["cur_max_kn"] = round(float(s.max()), 2) if s.size else None
        res[d] = row
    return res


def build_context(start: str, end: str, gap_min: float, tol_m: float, src: dict | None = None,
                  out_dir: pathlib.Path | None = None) -> pathlib.Path:
    tracks_mod = _load("06_tracks")
    tag = tracks_mod.tag(start, end, gap_min, tol_m)
    src = src or load_sources()
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    avail = {k: src[k] is not None for k in ("currents", "wind", "waves")}

    # Positions dans une zone ou un tampon.
    pts = pl.read_parquet(tracks_mod.OUT_DIR / f"points_{tag}.parquet")
    zones = gpd.read_file(ZONES).to_crs(PROJ_CRS)
    x, y = pts["x"].to_numpy(), pts["y"].to_numpy()
    layer = np.full(len(pts), None, dtype=object)
    for lay in ("buffer", "zone"):  # la zone l'emporte (les tampons excluent déjà les zones)
        layer[shapely.contains_xy(zones[zones.layer == lay].union_all(), x, y)] = lay
    pts = pts.with_columns(pl.Series("layer", layer.tolist(), dtype=pl.String)).filter(pl.col("layer").is_not_null())
    pts = enrich_points(pts, src)
    agg = (pts.group_by(["track_id", "layer"]).agg(
        pl.len().alias("n_points"),
        pl.col("sog").mean().alias("sog_mean_kn"),
        pl.col("cur_kn").is_not_null().mean().alias("share_current_available"),
        pl.col("cur_kn").mean().alias("cur_mean_kn"), pl.col("cur_kn").max().alias("cur_max_kn"),
        pl.col("angle_cog_cur").mean().alias("angle_mean_deg"),
        pl.col("water_kn_est").mean().alias("water_speed_est_kn"),
        pl.col("wind_kn").mean().alias("wind_mean_kn"),
        pl.col("wind_u").mean().alias("wu"), pl.col("wind_v").mean().alias("wv"),
        pl.col("wave_m").mean().alias("wave_mean_m"), pl.col("wave_m").max().alias("wave_max_m"),
    ))
    wdir = wind_from_dir(agg["wu"].to_numpy(), agg["wv"].to_numpy())
    agg = agg.with_columns(pl.Series("wind_from_deg", wdir, nan_to_null=True)).drop("wu", "wv")
    agg.write_parquet(out_dir / f"portion_context_{tag}.parquet")

    d0 = dt.date.fromisoformat(start)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((dt.date.fromisoformat(end) - d0).days + 1)]
    ctx = {
        "available": avail, "labels": src["labels"], "simulated": src.get("simulated", {}), "days": days, "summary": summary(src, days),
        "grids": {
            "currents": daily_grid(src["currents"], days, CUR_STEP) if avail["currents"] else {},
            "wind": daily_grid(src["wind"], days) if avail["wind"] else {},
        },
        "points_in_zones": pts.height,
        "openmeteo_points_excluded": {k: n_snapped_out(INTERIM / f"meteo_{k}.parquet")
                                      for k in ("wind", "waves") if (INTERIM / f"meteo_{k}.parquet").exists()},
        "max_snap_km": MAX_SNAP_KM,
    }
    out = out_dir / f"context_{tag}.json"
    out.write_text(json.dumps(ctx, ensure_ascii=False))
    print(f"[out ] {out} : sources {avail}, {pts.height} positions en zone ou tampon, {agg.height} portions")
    return out


def main():
    d = _load("06_tracks").DEFAULTS
    build_context(d["start"], d["end"], d["gap_min"], d["tol_m"])


if __name__ == "__main__":
    main()
