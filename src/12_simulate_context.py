"""Courant et vent SIMULÉS pour la démonstration (3 jours de la carte), réalistes mais fictifs.

À utiliser seulement tant que les vraies données ne sont pas téléchargées
(courants Copernicus : src/09_currents.py ; vent Open-Meteo : src/10_meteo.py).
11_context.py prend toujours les vraies données en priorité, et la carte marque
« SIMULÉ » tout ce qui vient d'ici. Graine fixe : le résultat est reproductible.

Vent (10 m), piloté par les VAGUES RÉELLES d'Open-Meteo (data/interim/meteo_waves.parquet) :
  - direction d'où vient le vent = direction moyenne d'où viennent les vagues, heure par heure,
    + 10° (le vent tourne un peu avant la mer) + variation spatiale lisse de ±8° ;
  - vitesse déduite du 90e centile de la hauteur des vagues par la relation de mer
    pleinement développée de Pierson-Moskowitz (Hs = 0,21 U² / g), majorée de 15 % parce que
    le Kattegat est une mer à fetch limité ; variation spatiale lisse de ±10 % ;
  - rafales = 1,35 × vent moyen. Même grille de 0,25° que src/10_meteo.py.

Courant de surface, sur la grille du produit Copernicus (pas 1/60° × 1/36°, 15 min), terre masquée :
  - courant de sortie de la Baltique : vers le nord, plus fort le long de la côte suédoise
    (0,08 à 0,30 m/s), tournant vers le nord-ouest à l'entrée du Skagerrak ;
  - dérive due au vent : 2,5 % de la vitesse du vent, déviée de 30° vers la droite ;
  - marée semi-diurne (M2, 12,42 h) faible, de 0,04 m/s au sud à 0,12 m/s au nord ;
  - détroits (Øresund, Grand Belt) : écoulement le long de l'axe, jusqu'à ~0,9 m/s,
    vers le nord en général, renversé par un vent fort de nord ;
  - petite variabilité spatiale lisse (±0,03 m/s). Vitesse plafonnée à 1,5 m/s.

Sorties (ignorées par git) : data/simulated/currents_sim_<période>.nc, wind_sim_<période>.parquet
Usage : python src/12_simulate_context.py
"""
import json
import pathlib

import geopandas as gpd
import numpy as np
import pandas as pd
import polars as pl
import shapely
import xarray as xr

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "simulated"
WAVES = ROOT / "data" / "interim" / "meteo_waves.parquet"
LAND = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"
START, END = "2026-09-15", "2026-09-17"
SEED = 20260915
G = 9.81
KN = 3600 / 1852


def smooth_field(rng, lat, lon, n=3, scale=1.0):
    """Champ lisse dans [-1, 1] (somme de quelques ondes de grande longueur d'onde)."""
    f = np.zeros(np.broadcast(lat, lon).shape)
    for _ in range(n):
        kx, ky = rng.uniform(0.8, 2.5, 2)
        ph = rng.uniform(0, 2 * np.pi)
        f += np.sin(kx * lon * scale + ky * lat * scale + ph)
    return f / n


def box_weight(lat, lon, la0, la1, lo0, lo1, taper=0.05):
    """1 dans la boîte, décroissance douce sur `taper` degrés."""
    def edge(x, a, b):
        return np.clip((x - a) / taper + 0.5, 0, 1) * np.clip((b - x) / taper + 0.5, 0, 1)
    return edge(lat, la0, la1) * edge(lon, lo0, lo1)


def wind_drivers() -> pd.DataFrame:
    """Vent de base heure par heure, déduit des vagues réelles (pleine mer : points peu déplacés)."""
    w = pl.read_parquet(WAVES)
    km = np.hypot((w["lat"] - w["model_lat"]).to_numpy() * 111.2,
                  (w["lon"] - w["model_lon"]).to_numpy() * 111.2 * np.cos(np.deg2rad(56.5)))
    w = w.with_columns(pl.Series("km", km)).filter(pl.col("km") <= 10)
    h = (w.group_by("time").agg(
        pl.col("wave_height").quantile(0.9).alias("hs90"),
        pl.col("wave_direction").radians().sin().mean().alias("s"),
        pl.col("wave_direction").radians().cos().mean().alias("c")).sort("time"))
    h = h.with_columns(((pl.arctan2("s", "c").degrees() + 360) % 360).alias("wave_from"))
    df = h.to_pandas()
    df["time"] = df["time"].dt.tz_localize(None)
    df["u10"] = np.clip(1.15 * np.sqrt(G * df["hs90"] / 0.21), 1.5, 22.0)  # m/s
    df["from_deg"] = (df["wave_from"] + 10) % 360
    return df[["time", "u10", "from_deg"]]


def simulate_wind(rng, drivers: pd.DataFrame) -> pl.DataFrame:
    import importlib.util
    spec = importlib.util.spec_from_file_location("meteo", pathlib.Path(__file__).with_name("10_meteo.py"))
    meteo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meteo)
    pts = np.array(meteo.sea_grid())  # mêmes points que la vraie requête Open-Meteo
    lat, lon = pts[:, 0], pts[:, 1]
    sp_mod = 1 + 0.10 * smooth_field(rng, lat, lon)
    dir_mod = 8 * smooth_field(rng, lat, lon)
    rows = []
    for r in drivers.itertuples():
        spd = r.u10 * sp_mod * KN
        frm = (r.from_deg + dir_mod) % 360
        for i in range(len(pts)):
            rows.append((r.time, lat[i], lon[i], round(float(spd[i]), 1), round(float(frm[i])),
                         round(float(spd[i] * 1.35), 1), lat[i], lon[i]))
    df = pl.DataFrame(rows, schema=["time", "lat", "lon", "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
                                    "model_lat", "model_lon"], orient="row")
    return df.with_columns(pl.col("time").dt.replace_time_zone("UTC"))


def simulate_currents(rng, drivers: pd.DataFrame) -> xr.Dataset:
    lat = 53.008297 + 0.016665840 * np.arange(120, 301)   # 55,008 -> 58,008 N
    lon = 9.041582 + 0.027778313 * np.arange(35, 144)      # 10,014 -> 13,013 E
    lat = lat[(lat >= 55) & (lat <= 58)]
    lon = lon[(lon >= 10) & (lon <= 13)]
    time = pd.date_range(f"{START} 00:00", pd.Timestamp(END) + pd.Timedelta(days=1), freq="15min")
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    land = gpd.read_file(LAND, bbox=(9.5, 54.5, 13.5, 58.5)).union_all()
    on_land = shapely.contains_xy(land, LO, LA)

    # 1. Courant de sortie de la Baltique (permanent).
    w_east = np.clip((LO - 11.2) / 1.0, 0, 1)
    out_speed = 0.08 + 0.22 * w_east
    out_dir = -45 * np.clip((LA - 57.0) / 0.7, 0, 1)  # 0° (nord) -> -45° (nord-ouest)
    ub = out_speed * np.sin(np.deg2rad(out_dir))
    vb = out_speed * np.cos(np.deg2rad(out_dir))
    ub = ub + 0.03 * smooth_field(rng, LA, LO, scale=2.0)
    vb = vb + 0.03 * smooth_field(rng, LA, LO, scale=2.0)

    # 2. Vent : interpolé au pas de 15 min, en composantes « vers où il va » (m/s).
    d = drivers.set_index("time").reindex(time).interpolate(limit_direction="both")
    to = np.deg2rad((d["from_deg"].to_numpy() + 180) % 360)
    wu, wv = d["u10"].to_numpy() * np.sin(to), d["u10"].to_numpy() * np.cos(to)
    th = np.deg2rad(30)  # déviation vers la droite
    du, dv = 0.025 * (wu * np.cos(th) + wv * np.sin(th)), 0.025 * (-wu * np.sin(th) + wv * np.cos(th))

    # 3. Marée M2.
    hours = (time - time[0]).total_seconds().to_numpy() / 3600
    om = 2 * np.pi / 12.42
    amp = 0.04 + 0.08 * np.clip((LA - 56.0) / 1.8, 0, 1)
    phase = 0.6 * (LA - 55.0)

    # 4. Détroits.
    w_ore = box_weight(LA, LO, 55.30, 56.05, 12.45, 12.95)
    w_gb = box_weight(LA, LO, 55.00, 55.90, 10.75, 11.20)

    u = np.empty((len(time), *LA.shape), dtype="float32")
    v = np.empty_like(u)
    for k in range(len(time)):
        ut = 0.3 * amp * np.sin(om * hours[k] + phase)
        vt = amp * np.cos(om * hours[k] + phase)
        uu, vv = ub + du[k] + ut, vb + dv[k] + vt
        strait = np.clip(0.35 + 0.04 * wv[k], -0.9, 0.9)  # vent vers le sud fort -> flux vers le sud
        vv = vv * (1 - w_ore) + strait * w_ore
        uu = uu * (1 - w_ore)
        vv = vv * (1 - w_gb) + 0.7 * strait * w_gb
        uu = uu * (1 - w_gb)
        spd = np.hypot(uu, vv)
        f = np.where(spd > 1.5, 1.5 / np.maximum(spd, 1e-9), 1.0)
        uu, vv = uu * f, vv * f
        uu[on_land], vv[on_land] = np.nan, np.nan
        u[k], v[k] = uu, vv
    ds = xr.Dataset({"uo": (("time", "latitude", "longitude"), u), "vo": (("time", "latitude", "longitude"), v)},
                    coords={"time": time, "latitude": lat, "longitude": lon})
    ds.attrs.update(title="Courant de surface SIMULÉ (démonstration SeaTrace)", simulated="true", seed=SEED,
                    method="voir docstring de src/12_simulate_context.py")
    return ds


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    drivers = wind_drivers()
    wind = simulate_wind(rng, drivers)
    wpath = OUT / f"wind_sim_{START}_{END}.parquet"
    wind.write_parquet(wpath)
    cur = simulate_currents(rng, drivers)
    cpath = OUT / f"currents_sim_{START}_{END}.nc"
    enc = {k: {"zlib": True, "complevel": 4} for k in ("uo", "vo")}
    cur.to_netcdf(cpath, encoding=enc)
    spd = np.hypot(cur.uo, cur.vo) * KN
    daily_w = wind.group_by(pl.col("time").dt.date().alias("jour")).agg(
        pl.col("wind_speed_10m").mean().round(1).alias("vent_moyen_nd"),
        pl.col("wind_speed_10m").max().round(1).alias("vent_max_nd")).sort("jour")
    print(daily_w)
    print(f"[out ] {wpath.relative_to(ROOT)} ; {cpath.relative_to(ROOT)} ({cpath.stat().st_size / 1e6:.1f} Mo)")
    print(f"[chk ] courant simulé : moyenne {float(spd.mean()):.2f} nd, p99 {float(spd.quantile(0.99)):.2f} nd, "
          f"max {float(spd.max()):.2f} nd ; part de terre {float(np.isnan(cur.uo.isel(time=0)).mean()):.0%}")
    (OUT / "README.json").write_text(json.dumps({
        "simulated": True, "seed": SEED, "period": [START, END],
        "note": "Données fictives pour la démonstration. Vent piloté par les vagues réelles Open-Meteo.",
    }, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
