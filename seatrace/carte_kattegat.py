"""Kattegat fishing-fleet map (reports/map.html) <-> SeaTrace algorithm.

Same positions for the batch script (src/13_reconstruct.py) and the Flask dashboard:
  1. full resolution, when the pipeline outputs are present:
     data/processed/tracks/points_<period>_gap30_tol30.parquet (src/06_tracks.py);
  2. otherwise, what the published map carries: the positions of reports/map_animation.html (first
     message of every 10-minute slot, real timestamps) plus the exact boundaries of every signal gap of
     more than 30 minutes (DATA.gaps of reports/map.html). Coarser, but enough to reconstruct the gaps and
     to see position jumps of several kilometres.

Geography: Natural Earth land (data/raw/basemap/ne_10m_land.geojson, else the clipped copy
data/reference/kattegat_land.geojson), restricted areas (layer "zone") and 4-NM buffers (layer "buffer")
of data/reference/kattegat_zones.geojson. The buffers are a project hypothesis, not a legal boundary.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, detect, geo, pipeline, sources

PERIODE = ("2026-09-15", "2026-09-17")
ECART_MIN = 30  # signal gap threshold of the map (minutes)
ZONE_ALL, BUFFER_ALL = "RESTRICTED", "BUFFER"  # union masks added to every grid


def _racine(racine=None) -> Path:
    return Path(racine or config.RACINE)


def chemins(racine=None) -> dict:
    r = _racine(racine)
    tag = f"{PERIODE[0]}_{PERIODE[1]}_gap{ECART_MIN}_tol30"
    return {
        "points": r / "data" / "processed" / "tracks" / f"points_{tag}.parquet",
        "map": r / "reports" / "map.html",
        "animation": r / "reports" / "map_animation.html",
        "zones": r / "data" / "reference" / "kattegat_zones.geojson",
        "land": [r / "data" / "raw" / "basemap" / "ne_10m_land.geojson",
                 r / "data" / "reference" / "kattegat_land.geojson"],
        "analysis": r / "data" / "processed" / f"reconstructions_{PERIODE[0]}_{PERIODE[1]}.json",
        "context": r / "data" / "processed" / "seatrace" / f"context_{PERIODE[0]}_{PERIODE[1]}.json",
    }


def contexte(racine=None) -> tuple[dict, list]:
    """Current and wind tables of the map period, written by src/13_seatrace_export.py --context-only from the
    same sources as the map's context layers (real data when downloaded, otherwise the simulation, whose label
    says so). Returns ({"courants": table, "source_courants": label, "vents": ..., "source_vents": ...},
    warnings); empty when the export has not been run. Tables: sources.lire_courants / lire_vents format."""
    r = _racine(racine)
    side = chemins(r)["context"]
    if not side.exists():
        return {}, []
    try:
        spec = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, ["Current/wind side file unreadable: no current or wind loaded."]
    data = (r / "data").resolve()
    layers, warnings = {}, []
    for key, read in (("courants", sources.lire_courants), ("vents", sources.lire_vents)):
        v = spec.get(key)
        if not isinstance(v, str) or not v:
            continue
        f = (r / v).resolve()
        if data not in f.parents or f.suffix.lower() != ".csv" or not f.is_file():
            warnings.append(f"{key}: table not found or outside data/ ({Path(v).name}).")
            continue
        try:
            layers[key] = read(f.read_bytes())
        except ValueError as exc:
            warnings.append(f"{key}: table refused ({str(exc)[:120]}).")
            continue
        layers[f"source_{key}"] = re.sub(r"[^\w .,:;()'/+\-]", "", str(spec.get(f"source_{key}") or f.name))[:120]
    return layers, warnings


def derive_locale(couches: dict, gr: geo.Grille, origine, debut_ts, fin_ts, k_vent: float):
    """Current and wind averaged over a reconstruction window and its gap (± 30 min), and the drift given to the
    model: current + k_vent x wind (km/h, east and north). Missing table or no value nearby: (0, 0)."""
    bornes = (gr.x0, gr.x0 + gr.nx * gr.taille, gr.y0, gr.y0 + gr.ny * gr.taille)

    def moyenne(cle):
        if cle not in couches:
            return (0.0, 0.0), 0
        r = pipeline.courant_du_jeu(couches[cle], bornes, origine, debut_ts, fin_ts)
        return ((0.0, 0.0), 0) if r is None else ((r[0], r[1]), r[2])

    (cu, cv), nc = moyenne("courants")
    (wu, wv), nw = moyenne("vents")
    du = float(np.clip(cu + k_vent * wu, -15, 15))
    dv = float(np.clip(cv + k_vent * wv, -15, 15))
    return {"courant": (cu, cv), "vent": (wu, wv), "derive": (du, dv), "n_courant": nc, "n_vent": nw}


def disponible(racine=None) -> bool:
    c = chemins(racine)
    return c["points"].exists() or (c["map"].exists() and c["animation"].exists())


# --- Reading the published map ---------------------------------------------------------------------

def donnees_carte(map_html) -> dict:
    """The DATA object embedded in reports/map.html (<script type="application/json" id="st-data">)."""
    t = Path(map_html).read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="st-data">(.*?)</script>', t, re.S)
    if not m:
        raise ValueError(f"{map_html}: no embedded data block (st-data).")
    return json.loads(m.group(1).replace("<\\/", "</"))


def positions_animation(anim_html) -> pd.DataFrame:
    """Positions of the animated map: one per vessel and 10-minute slot, with their real timestamps."""
    t = Path(anim_html).read_text(encoding="utf-8")
    i = t.find('{"type": "FeatureCollection"')
    while i >= 0:
        fc, _ = json.JSONDecoder().raw_decode(t, i)
        if fc.get("features") and "times" in fc["features"][0].get("properties", {}):
            break
        i = t.find('{"type": "FeatureCollection"', i + 1)
    else:
        raise ValueError(f"{anim_html}: no timestamped tracks found.")
    rows = []
    for f in fc["features"]:
        mmsi = f["properties"]["popup"].rsplit("MMSI", 1)[-1].strip()
        for (lon, lat), ts in zip(f["geometry"]["coordinates"], f["properties"]["times"]):
            rows.append((mmsi, ts, lat, lon))
    df = pd.DataFrame(rows, columns=["mmsi", "timestamp", "lat", "lon"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def navires(data: dict) -> dict:
    """mmsi -> {name, flag, length_m} from the track properties of the map."""
    out = {}
    for f in data["tracks"]["features"]:
        p = f["properties"]
        out.setdefault(p["mmsi"], {"name": p.get("name") or "", "flag": p.get("flag"), "length_m": p.get("len")})
    return out


def _decimer(df: pd.DataFrame, pas_s: float) -> pd.DataFrame:
    """At most one position per pas_s per vessel, always keeping both ends of every gap longer than pas_s."""
    df = df.sort_values(["mmsi", "timestamp"]).reset_index(drop=True)
    t = (df["timestamp"] - df["timestamp"].min()).dt.total_seconds().to_numpy()
    same = (df["mmsi"].to_numpy()[1:] == df["mmsi"].to_numpy()[:-1])
    dt_next = np.append(np.where(same, np.diff(t), np.inf), np.inf)
    bucket = np.floor(t / pas_s)
    first = np.ones(len(df), dtype=bool)
    first[1:] = ~same | (bucket[1:] != bucket[:-1])
    return df[first | (dt_next > pas_s)].reset_index(drop=True)


def charger_positions(racine=None, pas_min_s: float = 60.0):
    """(positions mmsi/timestamp/lat/lon/sog/cog, known gaps {mmsi: [...]}, vessels, source description)."""
    c = chemins(racine)
    data = donnees_carte(c["map"]) if c["map"].exists() else {"tracks": {"features": []}, "gaps": {}}
    if c["points"].exists():
        df = pd.read_parquet(c["points"], columns=["mmsi", "timestamp", "lat", "lon", "sog", "cog"])
        df["mmsi"] = df["mmsi"].astype(str)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = _decimer(df, pas_min_s)
        source = {"kind": "full", "label": "AIS positions of the pipeline (src/06_tracks.py), one per minute at most",
                  "step_s": pas_min_s}
    else:
        df = positions_animation(c["animation"])
        bornes = []
        for mmsi, gs in data.get("gaps", {}).items():
            for g in gs:
                bornes += [(mmsi, g[4], g[0], g[1]), (mmsi, g[5], g[2], g[3])]
        b = pd.DataFrame(bornes, columns=["mmsi", "timestamp", "lat", "lon"])
        b["timestamp"] = pd.to_datetime(b["timestamp"], utc=True)
        df = (pd.concat([df, b], ignore_index=True).sort_values(["mmsi", "timestamp"])
              .drop_duplicates(["mmsi", "timestamp"], keep="last").reset_index(drop=True))
        df["sog"], df["cog"] = np.nan, np.nan
        source = {"kind": "published_map",
                  "label": "positions of the published map (one per 10 min) and exact boundaries of its signal gaps",
                  "step_s": 600.0}
    return df, data.get("gaps", {}), navires(data), source


def params_detection(params, pas_s: float):
    """Detection settings for these positions: a gap is a silence of more than ECART_MIN minutes, as on the map."""
    p = config.copie(params)
    p["detection"]["intervalle_attendu_s"] = int(round(pas_s))
    p["detection"]["facteur_interruption"] = ECART_MIN * 60.0 / pas_s
    return p


def garder_periode(p: detect.Periode, mmsi: str, connus: dict, source_kind: str) -> bool:
    """With the published-map positions (one per 10 min), a spacing of more than 30 min can be an artefact of the
    sampling: a pure interruption is kept only if the map lists it as a signal gap (exact boundaries)."""
    if source_kind != "published_map" or p.cause != "interruption":
        return True
    t0 = pd.Timestamp(p.debut).timestamp()
    return any(abs(pd.Timestamp(g[4]).timestamp() - t0) <= 90 for g in connus.get(mmsi, []))


def jeu_seatrace(df: pd.DataFrame, vessels: dict) -> tuple[pd.DataFrame, dict]:
    """Positions in the SeaTrace import format (one trip per vessel over the period) and boat metadata."""
    out = pd.DataFrame({
        "boat_id": df["mmsi"], "trip_id": df["mmsi"], "timestamp": df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lat": df["lat"].round(6), "lon": df["lon"].round(6),
        "sog_kn": df["sog"], "cog_deg": df["cog"], "source": "ais", "accuracy_m": 10.0,
    })
    boats = {m: {"nom": (v.get("name") or "").strip(), "type_bateau": "ais_peche"} for m, v in vessels.items()}
    return out, boats


# --- Geography ---------------------------------------------------------------------------------------

def _anneaux(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    return []


def couches(racine=None) -> dict:
    """Land rings and zone rings in lon/lat, read once."""
    c = chemins(racine)
    land = next((p for p in c["land"] if p.exists()), None)
    terres = []
    if land is not None:
        for f in json.loads(land.read_text(encoding="utf-8"))["features"]:
            for ring in _anneaux(f["geometry"]):
                lon = np.array([p[0] for p in ring])
                lat = np.array([p[1] for p in ring])
                if lon.max() < 9.0 or lon.min() > 13.5 or lat.max() < 54.8 or lat.min() > 58.6:
                    continue
                terres.append(np.column_stack([lon, lat]))
    zones = []
    for f in json.loads(c["zones"].read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        if p.get("layer") not in ("zone", "buffer"):
            continue
        for ring in _anneaux(f["geometry"]):
            zones.append({"id": p["zone_id"], "layer": p["layer"], "name": p.get("name") or p["zone_id"],
                          "category": p.get("category"), "ring": np.array(ring, dtype=float)[:, :2]})
    return {"land": terres, "zones": zones, "land_source": str(land.name) if land else None}


def geographie(cch: dict, origine, bornes_km) -> geo.Geographie:
    """Geography of one window: land and zones projected around `origine`, kept if they touch the window."""
    xmin, xmax, ymin, ymax = bornes_km

    def proj(ring):
        x, y = geo.vers_km(ring[:, 1], ring[:, 0], origine)
        if x.max() < xmin or x.min() > xmax or y.max() < ymin or y.min() > ymax:
            return None
        return list(zip(x.tolist(), y.tolist()))

    g = geo.Geographie(nom="Kattegat", bornes=bornes_km, fictive=False, origine=tuple(origine),
                       sources=[f"land: Natural Earth 1:10m ({cch['land_source']})",
                                "zones: Delegated Regulation (EU) 2022/952 and 4-NM buffers (project hypothesis)"])
    g.terres = [("land", p) for p in (proj(r) for r in cch["land"]) if p]
    seen = {}
    for z in cch["zones"]:
        p = proj(z["ring"])
        if not p:
            continue
        seen[z["id"]] = seen.get(z["id"], 0) + 1
        ident = z["id"] if seen[z["id"]] == 1 else f"{z['id']}#{seen[z['id']]}"
        g.zones.append(geo.Zone(ident, z["name"], z["layer"], z.get("category") or "", p, fictive=False,
                                source="kattegat_zones.geojson"))
    return g


def grille(g: geo.Geographie, debut, fin, ptype, params) -> geo.Grille:
    """Window grid (pipeline.grille_fenetre) plus the union masks of all restricted areas and all buffers."""
    gr = pipeline.grille_fenetre(g, debut, fin, ptype, params)
    vide = np.zeros((gr.ny, gr.nx), dtype=bool)
    gr.zones[ZONE_ALL] = np.logical_or.reduce([gr.zones[z.identifiant] for z in g.zones if z.nature == "zone"] + [vide])
    gr.zones[BUFFER_ALL] = np.logical_or.reduce([gr.zones[z.identifiant] for z in g.zones if z.nature == "buffer"]
                                                + [vide])
    return gr


def bornes_fenetre(debut, fin, ptype):
    """Same window as pipeline.grille_fenetre, to project the geography before building the grid."""
    duree_h = max(fin.t_s - debut.t_s, 1.0) / 3600.0
    marge = ptype["vitesse_max_kn"] * geo.KN_EN_KMH * duree_h / 2.0 + 2.0
    return (min(debut.x, fin.x) - marge, max(debut.x, fin.x) + marge,
            min(debut.y, fin.y) - marge, max(debut.y, fin.y) + marge)


def observations_locales(analyse: detect.Analyse, periode: detect.Periode, params, origine):
    """Boundary observations of a period, re-projected around `origine` (the start of the gap)."""
    a, b = pipeline.observations_bornes(analyse, periode, params)
    pts = analyse.points
    for o, i in ((a, periode.i_avant), (b, periode.i_apres)):
        x, y = geo.vers_km(np.array([pts["lat"].iloc[i]]), np.array([pts["lon"].iloc[i]]), origine)
        o.x, o.y = float(x[0]), float(y[0])
    return a, b


def distance_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742.0 * math.asin(math.sqrt(a))
