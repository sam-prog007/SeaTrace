"""Étape 5 : passer les données de la carte à l'algorithme SeaTrace (détection et cinq modèles).

La carte (08_map.py) et l'algorithme (seatrace/, interface `python serveur.py`) ne lisent pas le même
format. Ce script fait le pont, sans modifier aucun fichier de la carte. Il lit :
  data/processed/ais_kattegat_<AAAA-MM>.parquet        navires de pêche en classe A (facultatif)
  data/synthetic/boat_<X>_reported.parquet              bateaux test A, B, C, D : seulement ce que voit le détecteur
  data/processed/tracks/crossing_tracks_<tag>.parquet   temps en zone et en tampon (facultatif, pour le tri)
  data/raw/basemap/ne_10m_land.geojson                  terres Natural Earth (téléchargées si absentes)
  le courant de 11_context.load_sources()               Copernicus Marine, ou simulé (12_simulate_context.py)

Il écrit :
  data/ais/kattegat_<début>_<fin>.csv     positions au format SeaTrace (boat_id, trip_id, timestamp, lat, lon,
                                          sog_kn, cog_deg, accuracy_m, source, synthetic)
  data/ais/kattegat_<début>_<fin>.json    bateaux, et couches chargées avec le jeu (zones, terres, courant)
  data/reference/kattegat_land.geojson    terres découpées sur le Kattegat (Natural Earth, domaine public)
  data/processed/seatrace/currents_<début>_<fin>.csv   courant horaire (uo, vo en m/s), si une source existe
  data/processed/seatrace/wind_<début>_<fin>.csv       vent horaire à 10 m (nœuds, direction d'où il vient)
  data/processed/seatrace/context_<début>_<fin>.json   sources du courant et du vent (réel ou simulé)

L'interface charge ce jeu au démarrage, avec les zones du règlement (UE) 2022/952, les terres et le
courant : choisir le jeu « Kattegat map », un trajet, une période, une zone, puis reconstruire.

Deux choix diffèrent volontairement de la carte :
  - un trajet n'est coupé qu'au-delà de TRIP_GAP_H heures sans position (la carte coupe à 30 min) :
    un trou plus court doit rester DANS le trajet pour pouvoir être reconstruit ;
  - les positions sont espacées d'au moins STEP_S secondes (le détecteur attend environ 60 s), mais la
    dernière position avant un trou et la première après sont toujours gardées : les bornes ne bougent pas.

Usage : python src/13_seatrace_export.py [--start 2026-09-15] [--end 2026-09-17] [--mmsi 219000001 ...]
                                         [--max-rows 50000] [--no-real] [--no-synthetic] [--no-current]
                                         [--no-wind] [--context-only]
"""
import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import re

import numpy as np
import pandas as pd
import polars as pl

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
SYNTHETIC = ROOT / "data" / "synthetic"
TRACKS = PROCESSED / "tracks"
OUT_AIS = ROOT / "data" / "ais"
OUT_CUR = PROCESSED / "seatrace"
LAND_RAW = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_land.geojson"
LAND_OUT = ROOT / "data" / "reference" / "kattegat_land.geojson"
LAND_BBOX = (9.0, 54.5, 13.8, 58.6)  # lon_min, lat_min, lon_max, lat_max : le rectangle d'étude et ses abords
PARAMS = ROOT / "config" / "parametres.json"

DEFAULTS = {"start": "2026-09-15", "end": "2026-09-17"}
STEP_S = 60  # espacement minimal des positions gardées
TRIP_GAP_H = 6.0  # au-delà, nouveau trajet (= durée maximale qu'un modèle accepte de reconstruire)
MIN_POINTS = 20  # trajets plus courts écartés
ACCURACY_M = 10.0  # précision supposée d'une position AIS de classe A
CUR_STEP = (5, 6)  # sous-échantillonnage de la grille de courant (lat, lon) : environ 5 milles nautiques
KN_TO_MS = 1852 / 3600
BOAT_TYPE = "ais_peche"  # type SeaTrace : navire de pêche (AIS), vitesse maximale 16 nœuds
COLUMNS = ["boat_id", "trip_id", "timestamp", "lat", "lon", "sog_kn", "cog_deg", "accuracy_m", "source", "synthetic"]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rel(p: pathlib.Path) -> str:
    """Chemin affiché (relatif au dépôt quand c'est possible)."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def import_limit() -> int:
    """Nombre maximal de lignes que l'interface accepte pour un jeu (config/parametres.json)."""
    return int(json.loads(PARAMS.read_text(encoding="utf-8"))["import"]["lignes_max"])


# --- Lecture ------------------------------------------------------------------------------
def standard(df: pl.DataFrame, synthetic: bool) -> pl.DataFrame:
    """Colonnes communes aux deux sources, horodatage en UTC."""
    ts = df["timestamp"]
    if ts.dtype.time_zone is None:
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    else:
        df = df.with_columns(pl.col("timestamp").dt.convert_time_zone("UTC"))
    return df.select(
        pl.col("mmsi").cast(pl.String), "timestamp", pl.col("lat").cast(pl.Float64), pl.col("lon").cast(pl.Float64),
        pl.col("sog").cast(pl.Float64), pl.col("cog").cast(pl.Float64),
        pl.col("vessel_name").cast(pl.String).alias("name") if "vessel_name" in df.columns else pl.lit(None, pl.String).alias("name"),
        pl.col("vessel_length_m").cast(pl.Float64).alias("length_m") if "vessel_length_m" in df.columns
        else pl.lit(None, pl.Float64).alias("length_m"),
        pl.lit(synthetic).alias("synthetic"),
        pl.col("scenario").cast(pl.String) if "scenario" in df.columns else pl.lit(None, pl.String).alias("scenario"),
    )


def period(start: str, end: str):
    """Bornes UTC de la période (jour de fin inclus)."""
    t0 = dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
    return t0, dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)


def read_real(start: str, end: str, mmsi=None) -> pl.DataFrame | None:
    folders = sorted(PROCESSED.glob("ais_kattegat_*.parquet"))
    if not folders:
        return None
    t0, t1 = period(start, end)
    lf = pl.concat([pl.scan_parquet(str(f / "**" / "*.parquet"), hive_partitioning=True) for f in folders],
                   how="diagonal_relaxed")
    lf = lf.filter((pl.col("timestamp") >= t0) & (pl.col("timestamp") < t1))
    if mmsi:
        lf = lf.filter(pl.col("mmsi").is_in([str(m) for m in mmsi]))
    df = lf.select("mmsi", "timestamp", "lat", "lon", "sog", "cog", "vessel_name", "vessel_length_m").collect()
    return standard(df, synthetic=False) if df.height else None


def read_synthetic(start: str, end: str) -> pl.DataFrame | None:
    """Bateaux test : uniquement les fichiers « reported » (ce que le détecteur a le droit de voir)."""
    files = sorted(SYNTHETIC.glob("boat_*_reported.parquet"))
    if not files:
        return None
    t0, t1 = period(start, end)
    df = pl.concat([standard(pl.read_parquet(f), synthetic=True) for f in files], how="vertical_relaxed")
    df = df.filter((pl.col("timestamp") >= t0) & (pl.col("timestamp") < t1))
    return df if df.height else None


def crossing_priority(start: str, end: str) -> dict:
    """Minutes en zone et en tampon par MMSI (07_crossing.py), pour choisir d'abord ces navires."""
    files = sorted(TRACKS.glob(f"crossing_tracks_{start}_{end}_*.parquet"))
    if not files:
        return {}
    tot = pl.read_parquet(files[-1])
    cols = [c for c in tot.columns if c.startswith("minutes_in_")]
    if not cols or "track_id" not in tot.columns:
        return {}
    agg = (tot.with_columns(pl.col("track_id").str.split("-").list.first().alias("mmsi"),
                            pl.sum_horizontal(cols).alias("m"))
           .group_by("mmsi").agg(pl.col("m").sum()))
    return dict(zip(agg["mmsi"].to_list(), agg["m"].to_list()))


# --- Trajets et espacement ------------------------------------------------------------------
def thin(t_s: np.ndarray, step_s: float = STEP_S) -> np.ndarray:
    """Positions gardées : au moins `step_s` secondes après la précédente gardée, plus TOUJOURS la
    dernière position avant un intervalle de plus de `step_s` (et donc la première après)."""
    n = len(t_s)
    keep = np.zeros(n, bool)
    if n == 0:
        return keep
    t = t_s.tolist()
    keep[0] = True
    last = t[0]
    for i in range(1, n):
        before_gap = i + 1 < n and t[i + 1] - t[i] > step_s
        if t[i] - last >= step_s or (before_gap and t[i] > last):
            keep[i] = True
            last = t[i]
    keep[n - 1] = keep[n - 1] or t[n - 1] > last
    return keep


def clean_name(v, mmsi: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 _\-.']", "", str(v or "")).strip()[:40]
    return name or f"MMSI {mmsi}"


def to_seatrace(df: pl.DataFrame, step_s=STEP_S, trip_gap_h=TRIP_GAP_H, min_points=MIN_POINTS):
    """Positions au format SeaTrace (pandas) et fiche de chaque bateau."""
    df = df.sort(["mmsi", "timestamp"]).unique(subset=["mmsi", "timestamp"], keep="first", maintain_order=True)
    rows, boats = [], {}
    for g in df.partition_by("mmsi", maintain_order=True):
        mmsi = g["mmsi"][0]
        t = g["timestamp"].dt.epoch("s").to_numpy().astype(float)
        trip = np.concatenate([[0], np.cumsum(np.diff(t) > trip_gap_h * 3600)])
        pdf = g.to_pandas()
        n_before = len(rows)
        for k in np.unique(trip):
            sel = np.flatnonzero(trip == k)
            sel = sel[thin(t[sel], step_s)]
            if len(sel) < min_points:
                continue
            seg = pdf.iloc[sel]
            ts = pd.to_datetime(seg["timestamp"], utc=True)
            sog = seg["sog"].astype(float)
            cog = seg["cog"].astype(float)
            rows.append(pd.DataFrame({
                "boat_id": mmsi, "trip_id": f"{mmsi}-{ts.iloc[0]:%Y%m%d%H%M}",
                "timestamp": ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lat": seg["lat"].round(6), "lon": seg["lon"].round(6),
                "sog_kn": sog.where((sog >= 0) & (sog < 100)).round(2),
                "cog_deg": cog.where((cog >= 0) & (cog < 360)).round(1),
                "accuracy_m": ACCURACY_M, "source": "ais", "synthetic": bool(g["synthetic"][0]),
            }))
        if len(rows) > n_before:
            scen = g["scenario"][0]
            names, length = g["name"].drop_nulls(), g["length_m"].drop_nulls()
            boats[mmsi] = {
                "type_bateau": BOAT_TYPE, "nom": clean_name(names[0] if names.len() else "", mmsi),
                "flotte": f"Kattegat map: test boat {scen}" if scen else "Kattegat map: DMA AIS (fishing, class A)",
                "mmsi": mmsi, "longueur_m": float(length[0]) if length.len() else None,
                "synthetique": bool(g["synthetic"][0]),
            }
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=COLUMNS)
    return out[COLUMNS], boats


def select(real: pd.DataFrame, priority: dict, budget: int) -> list:
    """Navires réels gardés dans la limite de lignes : d'abord ceux qui passent en zone ou en tampon
    (temps décroissant), puis ceux qui ont le plus long trou, un navire n'étant jamais coupé."""
    if real.empty or budget <= 0:
        return []
    t = pd.to_datetime(real["timestamp"], utc=True)
    gap = t.groupby(real["trip_id"]).diff().dt.total_seconds().groupby(real["boat_id"]).max().fillna(0)
    size = real.groupby("boat_id").size()
    order = sorted(size.index, key=lambda m: (-priority.get(m, 0.0), -gap.get(m, 0.0), m))
    kept, used = [], 0
    for m in order:
        if used + size[m] <= budget:
            kept.append(m)
            used += int(size[m])
    return kept


# --- Couches ------------------------------------------------------------------------------
def export_land() -> pathlib.Path | None:
    """Terres Natural Earth au 1:10 M découpées sur le Kattegat (précision ~1 km : suffisante au large,
    pas dans les passages étroits)."""
    import geopandas as gpd

    if not LAND_RAW.exists():
        import requests

        try:
            LAND_RAW.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(LAND_URL, timeout=120)
            r.raise_for_status()
            LAND_RAW.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"[warn] terres indisponibles ({e}) : {rel(LAND_OUT)} non régénéré")
            return LAND_OUT if LAND_OUT.exists() else None
    land = gpd.read_file(LAND_RAW, bbox=LAND_BBOX).clip(LAND_BBOX).explode(index_parts=False)
    land = land[land.geom_type == "Polygon"][["geometry"]]
    land["name"] = "Land (Natural Earth 1:10m)"
    LAND_OUT.parent.mkdir(parents=True, exist_ok=True)
    land.to_file(LAND_OUT, driver="GeoJSON", COORDINATE_PRECISION=5)
    print(f"[out ] {rel(LAND_OUT)} ({len(land)} polygones)")
    return LAND_OUT


def export_currents(start: str, end: str):
    """Courant horaire sur une grille d'environ 5 milles nautiques, au format CSV Copernicus (m/s)."""
    try:
        ctx = _load("11_context")
        src = ctx.load_sources()
    except (ImportError, OSError, ValueError) as e:
        print(f"[warn] courant indisponible ({type(e).__name__}: {e})")
        return None, None
    ds = src.get("currents")
    if ds is None:
        print("[info] aucun courant (ni Copernicus ni simulé) : lancer src/09_currents.py ou src/12_simulate_context.py")
        return None, None
    t0 = np.datetime64(start) - np.timedelta64(1, "h")
    t1 = np.datetime64(end) + np.timedelta64(25, "h")
    sub = ds.isel(latitude=slice(0, None, CUR_STEP[0]), longitude=slice(0, None, CUR_STEP[1]))
    sub = sub.sel(time=slice(t0, t1)).resample(time="1h").mean()
    df = sub[["u", "v"]].to_dataframe().reset_index().dropna(subset=["u", "v"])
    out = pd.DataFrame({
        "time": pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": df["latitude"].round(4), "longitude": df["longitude"].round(4),
        "uo": (df["u"] * KN_TO_MS).round(4), "vo": (df["v"] * KN_TO_MS).round(4),
    })
    OUT_CUR.mkdir(parents=True, exist_ok=True)
    path = OUT_CUR / f"currents_{start}_{end}.csv"
    out.to_csv(path, index=False)
    simulated = bool(src.get("simulated", {}).get("currents"))
    label = ("SeaTrace demonstration current (simulated, fictitious)" if simulated
             else "Copernicus Marine BALTICSEA_ANALYSISFORECAST_PHY_003_006 (hourly mean)")
    print(f"[out ] {rel(path)} ({len(out)} valeurs, {'simulé' if simulated else 'Copernicus'})")
    return path, label


def export_wind(start: str, end: str):
    """Vent à 10 m horaire (Open-Meteo, sinon Copernicus, sinon simulé : même priorité que la carte), en CSV :
    time, latitude, longitude, wind_speed_kn, wind_from_deg (direction d'où vient le vent)."""
    try:
        ctx = _load("11_context")
        src = ctx.load_sources()
    except (ImportError, OSError, ValueError) as e:
        print(f"[warn] vent indisponible ({type(e).__name__}: {e})")
        return None, None
    ds = src.get("wind")
    if ds is None:
        print("[info] aucun vent (ni Open-Meteo ni Copernicus ni simulé) : lancer src/10_meteo.py")
        return None, None
    t0 = np.datetime64(start) - np.timedelta64(1, "h")
    t1 = np.datetime64(end) + np.timedelta64(25, "h")
    sub = ds[["u", "v"]].sel(time=slice(t0, t1)).resample(time="1h").mean()
    df = sub.to_dataframe().reset_index().dropna(subset=["u", "v"])
    u, v = df["u"].to_numpy(), df["v"].to_numpy()  # nœuds, vers où va le vent
    out = pd.DataFrame({
        "time": pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latitude": df["latitude"].round(4), "longitude": df["longitude"].round(4),
        "wind_speed_kn": np.hypot(u, v).round(2),
        "wind_from_deg": ((np.degrees(np.arctan2(u, v)) + 180.0) % 360.0).round(1),
    })
    OUT_CUR.mkdir(parents=True, exist_ok=True)
    path = OUT_CUR / f"wind_{start}_{end}.csv"
    out.to_csv(path, index=False)
    simulated = bool(src.get("simulated", {}).get("wind"))
    lab = src.get("labels", {}).get("wind", "")
    label = ("SeaTrace demonstration wind (simulated, fictitious)" if simulated
             else "Open-Meteo historical weather API, 10 m wind (CC BY 4.0)" if "Open-Meteo" in lab
             else "Copernicus Marine WIND_GLO_PHY_L4_NRT_012_004 (hourly)")
    print(f"[out ] {rel(path)} ({len(out)} valeurs, {'simulé' if simulated else 'réel'})")
    return path, label


def export_context(start: str, end: str, current=True, wind=True) -> dict:
    """Tables de courant et de vent de la période, et leurs sources (context_<période>.json), relues par
    l'interface pour les jeux du Kattegat (flotte de la carte et bateaux test)."""
    layers = {}
    if current:
        cur, label = export_currents(start, end)
        if cur is not None:
            layers.update(courants=str(cur.relative_to(ROOT)), source_courants=label)
    if wind:
        w, label = export_wind(start, end)
        if w is not None:
            layers.update(vents=str(w.relative_to(ROOT)), source_vents=label)
    if layers:
        # une table non demandée (--no-current, --no-wind) garde son entrée précédente : on complète, on n'efface pas
        side = OUT_CUR / f"context_{start}_{end}.json"
        try:
            known = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        except ValueError:
            known = {}
        OUT_CUR.mkdir(parents=True, exist_ok=True)
        side.write_text(json.dumps({**known, **layers}, indent=2, ensure_ascii=False), encoding="utf-8")
    return layers


# --- Programme ----------------------------------------------------------------------------
def build(start: str, end: str, mmsi=None, max_rows=None, real=True, synthetic=True, current=True,
          wind=True) -> pathlib.Path:
    max_rows = max_rows or import_limit()
    parts = []
    syn = read_synthetic(start, end) if synthetic else None
    syn_pos, syn_boats = to_seatrace(syn) if syn is not None else (pd.DataFrame(columns=COLUMNS), {})
    real_df = read_real(start, end, mmsi) if real else None
    real_pos, real_boats = to_seatrace(real_df) if real_df is not None else (pd.DataFrame(columns=COLUMNS), {})
    if real and real_df is None:
        print("[info] aucune position AIS réelle pour cette période (data/processed/, src/01 et 02) : "
              "bateaux test seulement")
    kept = select(real_pos, crossing_priority(start, end), max_rows - len(syn_pos))
    parts = [p for p in (syn_pos, real_pos[real_pos["boat_id"].isin(kept)]) if len(p)]
    if not parts:
        raise SystemExit("Aucune position à exporter.")
    pos = pd.concat(parts, ignore_index=True)
    if len(pos) > max_rows:
        raise SystemExit(f"{len(pos)} lignes > limite de {max_rows} : réduire la sélection (--mmsi) ou la période.")
    boats = {m: b for m, b in {**syn_boats, **real_boats}.items() if m in set(pos["boat_id"])}

    layers = {"zones": "kattegat"}
    land = export_land()
    if land is not None:
        layers.update(terres=str(land.relative_to(ROOT)), source_terres="Natural Earth 1:10m land (public domain)")
    layers.update(export_context(start, end, current, wind))

    OUT_AIS.mkdir(parents=True, exist_ok=True)
    out = OUT_AIS / f"kattegat_{start}_{end}.csv"
    pos.to_csv(out, index=False)
    n_syn = sum(b["synthetique"] for b in boats.values())
    meta = {
        "nom_jeu": f"Kattegat map: {len(boats) - n_syn} real fishing vessels + {n_syn} test boats ({start} to {end})",
        "genere": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "periode": [start, end],
        "parametres": {"step_s": STEP_S, "trip_gap_h": TRIP_GAP_H, "min_points": MIN_POINTS,
                       "accuracy_m": ACCURACY_M, "max_rows": max_rows},
        "selection": {"test_boats": sorted(syn_boats), "real_vessels": kept,
                      "real_candidates": int(real_pos["boat_id"].nunique()) if len(real_pos) else 0},
        "bateaux": boats,
        "couches": layers,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[out ] {rel(out)} : {len(pos)} positions, {pos['trip_id'].nunique()} trajets, "
          f"{len(boats)} bateaux ({n_syn} bateaux test)")
    print(f"       jeu « ais-{out.stem} » dans l'interface : python serveur.py, puis http://127.0.0.1:8050/"
          f"?dataset=ais-{out.stem}&boat=<MMSI>")
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=DEFAULTS["start"])
    p.add_argument("--end", default=DEFAULTS["end"])
    p.add_argument("--mmsi", nargs="*", help="navires réels à exporter (par défaut : choix automatique)")
    p.add_argument("--max-rows", type=int, default=None, help="par défaut : limite d'import de l'interface")
    p.add_argument("--no-real", action="store_true")
    p.add_argument("--no-synthetic", action="store_true")
    p.add_argument("--no-current", action="store_true")
    p.add_argument("--no-wind", action="store_true")
    p.add_argument("--context-only", action="store_true",
                   help="n'écrire que les tables de courant et de vent (pas de positions)")
    a = p.parse_args()
    if a.context_only:
        export_context(a.start, a.end, not a.no_current, not a.no_wind)
        return
    build(a.start, a.end, a.mmsi, a.max_rows, real=not a.no_real, synthetic=not a.no_synthetic,
          current=not a.no_current, wind=not a.no_wind)


if __name__ == "__main__":
    main()
