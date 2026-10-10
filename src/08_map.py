"""Étape 4 : carte Folium des trajets de pêche et des zones restreintes du Kattegat.

Paramètres ci-dessous. Les trajets (06) et le croisement (07) sont recalculés
seulement si ces paramètres changent ; sinon les fichiers existants sont relus.

La carte est générée par Folium (fond, zones, sélecteur de couches). Un panneau
latéral en JavaScript, intégré au même fichier HTML, ajoute :
  - le choix d'un navire à étudier (recherche par nom ou MMSI) et sa fiche ;
  - des filtres (passage en zone, longueur, pavillon, statut « en pêche »,
    jours, navires immobiles) ;
  - une légende et un glossaire des termes employés.

Sorties :
  reports/map.html             carte principale
  reports/map_animation.html   trajets animés dans le temps (page séparée : le
                               plugin TimestampedGeoJson ne s'active pas via LayerControl)
"""
# --- Paramètres ------------------------------------------------------------------
PERIOD_START = "2026-09-15"  # période affichée (incluse, UTC)
PERIOD_END = "2026-09-17"
GAP_MINUTES = 30  # seuil de trou : au-delà, le trajet est coupé
SIMPLIFY_M = 30  # tolérance de simplification des lignes (m, EPSG:25832)
STILL_KN = 0.5  # vitesse moyenne sous laquelle un trajet est dit « quasi immobile »
ANIMATION = True  # produire reports/map_animation.html
ANIM_STEP_MIN = 10  # une position animée toutes les N minutes par trajet
POINT_STEP_S = 60  # positions AIS dessinées : au plus une par minute et par trajet
POINT_MIN_MOVE_M = 15  # navire immobile : un point toutes les 10 min suffit (points superposés)
# ---------------------------------------------------------------------------------

import datetime as dt  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

import folium  # noqa: E402
import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
from branca.element import Element  # noqa: E402
from folium.plugins import TimestampedGeoJson  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import mapping  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
REPORTS = ROOT / "reports"
PRIVATE = ROOT / "data" / "private"  # ignoré par git
TILES = "Esri.WorldGrayCanvas"  # fond clair et neutre, sans clé d'API
CENTER, ZOOM = (56.75, 11.5), 8

COLORS = {
    "a1": "#d62828",  # engins de fond interdits : rouge
    "a2": "#8b0000",  # pêche interdite : rouge foncé
    "a3": "#d62828",  # engins limités : rouge clair, contour pointillé
    "buffer": "#f4a261",  # tampons : orange clair
    "ais_zone": "#2a78d6",  # zones AIS officielles : contour bleu
    "natura2000": "#2a9d8f",
    "helcom": "#7b5ea7",
    "track": "#3d5a80",  # trajets : bleu-gris
    "selected": "#0b2545",  # navire étudié : bleu nuit
    "other": "#9aa0a6",  # autres navires en arrière-plan : gris
    "portion_zone": "#e6007e",  # passage dans une zone : magenta vif
    "portion_buffer": "#f59e0b",  # passage dans un tampon : ambre vif
}

# Restrictions en langage courant (règlement délégué (UE) 2022/952, art. 3).
CATEGORY = {
    "Areas 1": {"key": "a1", "short": "Engins de fond interdits",
                "rule": "Interdit : pêcher avec des engins traînés sur le fond (chaluts de fond, sennes, dragues). "
                        "Ces engins doivent rester arrimés et rangés à bord. Les autres engins (filets fixes, "
                        "casiers, lignes, chaluts pélagiques) ne sont pas visés par ce texte."},
    "Areas 2": {"key": "a2", "short": "Pêche interdite",
                "rule": "Interdit : toute opération de pêche, y compris la pêche de loisir."},
    "Areas 3": {"key": "a3", "short": "Pêche limitée à certains engins",
                "rule": "Seuls autorisés : lignes et cannes, chaluts pélagiques (en pleine eau), casiers à "
                        "crustacés, et filets maillants si le navire est suivi par caméras à bord. "
                        "Les mêmes règles s'appliquent à la pêche de loisir."},
}
COUNTRY = {"DK": "Danemark", "SE": "Suède"}

# Pays du pavillon d'après les 3 premiers chiffres du MMSI (MID, table de l'UIT).
FLAGS = {
    "205": "Belgique", "211": "Allemagne", "218": "Allemagne", "219": "Danemark", "220": "Danemark",
    "224": "Espagne", "225": "Espagne", "227": "France", "228": "France", "230": "Finlande",
    "231": "Îles Féroé", "232": "Royaume-Uni", "233": "Royaume-Uni", "234": "Royaume-Uni",
    "235": "Royaume-Uni", "244": "Pays-Bas", "245": "Pays-Bas", "246": "Pays-Bas", "250": "Irlande",
    "251": "Islande", "257": "Norvège", "258": "Norvège", "259": "Norvège", "261": "Pologne",
    # 217, 221, 260 : codes non attribués, utilisés par les trajets de démonstration (src/demo_boats.py)
    "217": "Allemagne", "221": "Danemark", "260": "Suède",
    "265": "Suède", "266": "Suède", "273": "Russie", "275": "Lettonie", "276": "Estonie", "277": "Lituanie",
}
LEN_CLASSES = [  # (clé, libellé, min inclus, max exclu)
    ("lt12", "moins de 12 m", None, 12), ("12_15", "12 à 15 m", 12, 15), ("15_24", "15 à 24 m", 15, 24),
    ("24_40", "24 à 40 m", 24, 40), ("ge40", "40 m et plus", 40, None), ("unk", "longueur inconnue", None, None),
]


# Contexte (courant, vent) : classes de vitesse en nœuds et rampes de couleur séquentielles.
CUR_BINS = [0.25, 0.5, 1.0, 1.5]
CUR_COLORS = ["#9ec5f4", "#5598e7", "#256abf", "#184f95", "#0d366b"]
WIND_BINS = [5, 10, 15, 20, 25]
WIND_COLORS = ["#d9c8f5", "#b79be8", "#9670d6", "#7448bf", "#56309c", "#3a1d72"]
COMPASS = ["nord", "nord-nord-est", "nord-est", "est-nord-est", "est", "est-sud-est", "sud-est", "sud-sud-est", "sud",
           "sud-sud-ouest", "sud-ouest", "ouest-sud-ouest", "ouest", "ouest-nord-ouest", "nord-ouest", "nord-nord-ouest"]
SOURCES_TXT = {
    "currents": "Courants : E.U. Copernicus Marine Service Information, produit "
                "BALTICSEA_ANALYSISFORECAST_PHY_003_006 (DOI 10.48670/moi-00010).",
    "wind_om": "Vent : Open-Meteo.com (CC BY 4.0), archive météo.",
    "wind_cm": "Vent : E.U. Copernicus Marine Service Information, produit WIND_GLO_PHY_L4_NRT_012_004 "
               "(DOI 10.48670/moi-00305).",
    "waves": "Vagues : Open-Meteo.com (CC BY 4.0), API marine, modèles de vagues du Deutscher Wetterdienst (DWD).",
    "currents_sim": "Courant : modèle simplifié SeaTrace.",
    "wind_sim": "Vent : modèle simplifié SeaTrace.",
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flag_of(mmsi: str) -> str:
    return FLAGS.get(mmsi[:3], f"inconnu (MID {mmsi[:3]})")


def len_class(v) -> str:
    if v is None or pd.isna(v):
        return "unk"
    for key, _, lo, hi in LEN_CLASSES[:-1]:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return key
    return "unk"


def zone_label(zone_id: str, name: str) -> str:
    """Nom lisible d'une zone, d'un tampon ou d'une zone AIS."""
    if zone_id.startswith("DK00VA247-"):
        return f"Kims Top og den Kinesiske Mur (Natura 2000), sous-zone {zone_id.split('-')[-1]}"
    if zone_id.startswith("DK-MSFD-"):
        return f"Zone MSFD {zone_id.split('-')[-1]}"
    if zone_id.endswith("-A2"):
        return f"{name} – partie où la pêche est interdite"
    if zone_id.endswith("-A3"):
        return f"{name} – partie à engins limités"
    if zone_id.startswith("BUF4NM-"):
        target = zone_id.removeprefix("BUF4NM-")
        target = "Kims Top" if target == "DK00VA247" else "zone " + target.replace("-", " ")
        return f"Tampon de 4 milles autour de {target}"
    return name


def rnd(c, nd=5):
    """Arrondit récursivement des coordonnées GeoJSON (~1 m) pour alléger le fichier."""
    if isinstance(c, (list, tuple)):
        if c and isinstance(c[0], (int, float)):
            return [round(v, nd) for v in c]
        return [rnd(x, nd) for x in c]
    return c


def feature(geom, props: dict) -> dict:
    g = mapping(geom)
    return {"type": "Feature", "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])},
            "properties": props}


def fc(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


def zone_tooltip(title: str, badge: str, country: str, rule: str, source: str) -> str:
    return (f"<div class='st-tip'><b>{title}</b><br><span class='st-badge'>{badge}</span> · {country}"
            f"<div class='st-rule'>{rule}</div><div class='st-src'>{source}</div></div>")


def zones_layers(m, zones: gpd.GeoDataFrame):
    """Une couche par type de restriction, pour pouvoir les afficher séparément."""
    z = zones[zones.layer == "zone"]
    for cat, label in [("Areas 1", "Zones où les engins de fond sont interdits (DK)"),
                       ("Areas 2", "Zones où la pêche est interdite (SE)"),
                       ("Areas 3", "Zones où seuls certains engins sont permis (SE)")]:
        info = CATEGORY[cat]
        feats = [feature(r.geometry, {"tip": zone_tooltip(
            zone_label(r.zone_id, r.name), info["short"], COUNTRY.get(r.country, r.country), info["rule"],
            f"Règlement délégué (UE) 2022/952, {r.legal_ref} · {r.area_km2:.1f} km²")})
            for r in z[z.category == cat].itertuples()]
        color = COLORS[info["key"]]
        style = {"color": color, "weight": 1.5, "fillColor": color,
                 "fillOpacity": {"a1": 0.35, "a2": 0.55, "a3": 0.15}[info["key"]]}
        if info["key"] == "a3":
            style["dashArray"] = "5 4"
        fg = folium.FeatureGroup(label, show=True)
        folium.GeoJson(fc(feats), style_function=lambda f, s=style: s,
                       tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
        fg.add_to(m)

    b = zones[zones.layer == "buffer"]
    feats = [feature(r.geometry, {"tip": zone_tooltip(
        zone_label(r.zone_id, r.name), "Tampon de 4 milles (hypothèse du projet)", "Danemark",
        "Bande de 4 milles nautiques (7,4 km) autour des zones danoises. Hypothèse de travail : les chalutiers "
        "de fond doivent y avoir un AIS de classe A. Ce n'est pas un tracé juridique : les zones AIS "
        "officielles sont dans l'annexe V (couche bleue).",
        f"Calcul SeaTrace en EPSG:25832 · {r.area_km2:.0f} km²")}) for r in b.itertuples()]
    fg = folium.FeatureGroup("Tampons de 4 milles autour des zones danoises (hypothèse)", show=True)
    folium.GeoJson(fc(feats), style_function=lambda f: {"color": COLORS["buffer"], "weight": 1,
                                                        "fillColor": COLORS["buffer"], "fillOpacity": 0.22},
                   tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
    fg.add_to(m)

    a = zones[zones.layer == "ais_zone"]
    feats = [feature(r.geometry, {"tip": zone_tooltip(
        r.name, "Zone AIS obligatoire (officielle)", COUNTRY.get(r.country, r.country), r.restriction
        .replace("AIS obligatoire pour les navires utilisant des engins de fond mobiles (art. 5(b))",
                 "AIS obligatoire pour les navires qui pêchent avec des engins traînés sur le fond.")
        .replace("AIS obligatoire pour tout navire de pêche présent (art. 5(c))",
                 "AIS obligatoire pour tout navire de pêche présent dans la zone."),
        f"Règlement délégué (UE) 2022/952, {r.legal_ref}")}) for r in a.itertuples()]
    fg = folium.FeatureGroup("Zones où l'AIS est obligatoire (annexe V, officielles)", show=False)
    folium.GeoJson(fc(feats), style_function=lambda f: {"color": COLORS["ais_zone"], "weight": 1.5,
                                                        "dashArray": "6 4", "fillOpacity": 0},
                   tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
    fg.add_to(m)


def protected_layers(m) -> list[str]:
    missing = []
    for key, label, color, code, name in [
        ("natura2000_kattegat", "Sites Natura 2000 (EEA, fin 2024)", COLORS["natura2000"], "SITECODE", "SITENAME"),
        ("helcom_mpa_kattegat", "Aires protégées HELCOM", COLORS["helcom"], "MPA_ID", "Name"),
    ]:
        f = REF / f"{key}.geojson"
        if not f.exists():
            missing.append(label)
            continue
        g = gpd.read_file(f).to_crs("EPSG:4326")
        g["geometry"] = g.simplify(0.0003)
        fields = [c for c in (code, name) if c in g.columns]
        fg = folium.FeatureGroup(label, show=False)
        folium.GeoJson(g[fields + ["geometry"]],
                       style_function=lambda f, c=color: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": 0.15},
                       tooltip=folium.GeoJsonTooltip(fields) if fields else None).add_to(fg)
        fg.add_to(m)
    return missing


def compass(deg: float) -> str:
    return COMPASS[int(((deg % 360) + 11.25) // 22.5) % 16]


def toward(deg: float) -> str:
    c = compass(deg)
    return f"vers l'{c}" if c[0] in "eo" else f"vers le {c}"


def from_(deg: float) -> str:
    c = compass(deg)
    return f"de l'{c}" if c[0] in "eo" else f"du {c}"


def bin_color(v: float, bins: list, colors: list) -> str:
    return colors[int(np.searchsorted(bins, v, side="right"))]


def fmt_kn(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") + " nd"


def arrows(rows: list, length_m: float, kind: str, day: str, simulated: bool = False) -> dict:
    """Flèches de longueur fixe, orientées vers où va le courant ou le vent, couleur selon la vitesse moyenne."""
    fwd = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
    inv = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)
    feats = []
    d = f"{day[8:10]}/{day[5:7]}"
    for la, lo, u, v, ms, mx in rows:
        n = float(np.hypot(u, v))
        if n == 0:
            continue
        x, y = fwd.transform(lo, la)
        dx, dy = u / n, v / n
        tail, head = (x - dx * length_m / 2, y - dy * length_m / 2), (x + dx * length_m / 2, y + dy * length_m / 2)
        wings = []
        for a in (np.deg2rad(25), -np.deg2rad(25)):
            bx, by = -dx * np.cos(a) + dy * np.sin(a), -dx * np.sin(a) - dy * np.cos(a)
            wings.append((head[0] + bx * length_m * 0.35, head[1] + by * length_m * 0.35))
        lines = [[tail, head], [head, wings[0]], [head, wings[1]]]
        coords = [[[round(c, 5) for c in inv.transform(px, py)] for px, py in ln] for ln in lines]
        to_deg = (np.degrees(np.arctan2(u, v)) + 360) % 360
        steady = n >= 0.3 * ms if ms > 0 else False
        if kind == "currents":
            tip = (f"<b>Courant le {d}</b> (moyenne de la journée)<br>Vitesse moyenne {fmt_kn(ms)}, maximum {fmt_kn(mx)}<br>"
                   + (f"Va {toward(to_deg)} ({to_deg:.0f}°)" if steady else
                      "Direction variable dans la journée (marée) : la flèche indique seulement la dérive moyenne"))
            color = bin_color(ms, CUR_BINS, CUR_COLORS)
        else:
            frm = (to_deg + 180) % 360
            tip = (f"<b>Vent le {d}</b> (moyenne de la journée)<br>Vitesse moyenne {fmt_kn(ms)}, maximum horaire {fmt_kn(mx)}<br>"
                   + (f"Vient {from_(frm)} ({frm:.0f}°)" if steady else "Direction variable dans la journée"))
            color = bin_color(ms, WIND_BINS, WIND_COLORS)
        feats.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": coords},
                      "properties": {"c": color, "tip": f"<div class='st-tip'>{tip}</div>"}})
    return fc(feats)


def context_layers(m, ctx: dict | None):
    """Une couche par jour et par variable, masquées par défaut."""
    if not ctx:
        return
    for kind, label, length in [("currents", "Courant", 4000), ("wind", "Vent", 9000)]:
        sim = bool(ctx.get("simulated", {}).get(kind))
        for day, rows in ctx["grids"].get(kind, {}).items():
            name = f"{label} moyen du {day[8:10]}/{day[5:7]} (flèches)"
            fg = folium.FeatureGroup(name, show=False)
            folium.GeoJson(arrows(rows, length, kind, day, sim),
                           style_function=lambda f: {"color": f["properties"]["c"], "weight": 2, "opacity": 0.9},
                           tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
            fg.add_to(m)


def portion_ctx(r) -> dict | None:
    if r is None:
        return None
    g = lambda k, nd=1: None if r.get(k) is None or pd.isna(r.get(k)) else round(float(r[k]), nd)  # noqa: E731
    return {"sog": g("sog_mean_kn"), "cur": g("cur_mean_kn", 2), "curMax": g("cur_max_kn", 2),
            "ang": g("angle_mean_deg", 0), "water": g("water_speed_est_kn"), "wind": g("wind_mean_kn"),
            "windFrom": g("wind_from_deg", 0), "wave": g("wave_mean_m", 2), "waveMax": g("wave_max_m", 2),
            "curShare": g("share_current_available", 2)}


def dots_and_gaps(pts: pl.DataFrame) -> tuple[dict, dict]:
    """Positions AIS par trajet (au plus une par POINT_STEP_S, en degrés × 1e4, codées en différences)
    et trous de signal > GAP_MINUTES par navire."""
    p = (pts.with_columns(pl.col("timestamp").dt.truncate(f"{POINT_STEP_S}s").alias("b"))
         .group_by(["track_id", "b"], maintain_order=True).first().sort(["track_id", "timestamp"]))
    dots, min2 = {}, POINT_MIN_MOVE_M ** 2
    for (tid,), g in p.group_by(["track_id"], maintain_order=True):
        x, y = g["x"].to_numpy(), g["y"].to_numpy()
        t = g["timestamp"].dt.epoch("s").to_numpy()
        keep = [0]
        for i in range(1, len(x)):
            j = keep[-1]
            if (x[i] - x[j]) ** 2 + (y[i] - y[j]) ** 2 >= min2 or t[i] - t[j] >= 600:
                keep.append(i)
        la = np.round(g["lat"].to_numpy()[keep] * 1e4).astype(np.int64)
        lo = np.round(g["lon"].to_numpy()[keep] * 1e4).astype(np.int64)
        arr = np.empty(2 * len(keep), dtype=np.int64)
        arr[0::2], arr[1::2] = np.diff(la, prepend=0), np.diff(lo, prepend=0)
        dots[tid] = arr.tolist()
    s = (pts.select("mmsi", "timestamp", "lat", "lon").sort(["mmsi", "timestamp"])
         .with_columns(pl.col("timestamp").diff().over("mmsi").dt.total_seconds().alias("gap_s"),
                       pl.col("lat").shift().over("mmsi").alias("lat0"), pl.col("lon").shift().over("mmsi").alias("lon0"),
                       pl.col("timestamp").shift().over("mmsi").alias("t0"))
         .filter(pl.col("gap_s") > GAP_MINUTES * 60))
    gaps = {}
    for r in s.iter_rows(named=True):
        gaps.setdefault(r["mmsi"], []).append([round(r["lat0"], 5), round(r["lon0"], 5), round(r["lat"], 5), round(r["lon"], 5),
                                               r["t0"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                                               r["timestamp"].strftime("%Y-%m-%dT%H:%M:%SZ"), round(r["gap_s"] / 60)])
    return dots, gaps


def default_favorites() -> list[str]:
    f = PRIVATE / "vessel_id_map.csv"
    if not f.exists():
        return []
    m = pl.read_csv(f, schema_overrides={"display_id": pl.String, "mmsi_reel": pl.String})
    return sorted(m.filter(pl.col("modifie"))["display_id"].to_list())


def build_data(tracks: gpd.GeoDataFrame, per_zone: pl.DataFrame, portions: gpd.GeoDataFrame, meta: dict,
               ctx: dict | None = None, pctx: pd.DataFrame | None = None, dots: dict | None = None,
               gaps: dict | None = None, favorites: list | None = None) -> dict:
    """Données du panneau : trajets, passages en zone, navires, période."""
    pz = per_zone.to_pandas()
    pz["label"] = [zone_label(z, n) for z, n in zip(pz.zone_id, pz.name)]
    pz["short"] = [CATEGORY[c]["short"] if c in CATEGORY else "Tampon de 4 milles" for c in pz.category]
    zones_by_track = {
        tid: [{"n": r.label, "k": r.layer, "c": r.short, "m": round(float(r.minutes_in), 1)}
              for r in g.sort_values("minutes_in", ascending=False).itertuples()]
        for tid, g in pz.groupby("track_id")
    }
    track_feats = []
    for r in tracks.itertuples():
        track_feats.append(feature(r.geometry, {
            "tid": r.track_id, "mmsi": r.mmsi, "name": r.name.strip() or None if isinstance(r.name, str) else None,
            "flag": flag_of(r.mmsi), "len": None if pd.isna(r.length_m) else int(r.length_m),
            "lc": len_class(r.length_m), "sog": None if pd.isna(r.sog_mean_kn) else round(float(r.sog_mean_kn), 2),
            "start": r.start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": r.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n": int(r.n_points), "km": round(float(r.length_km), 1),
            "eng": round(float(r.share_engaged_fishing), 3),
            "mz": round(float(r.minutes_in_zone), 1), "mb": round(float(r.minutes_in_buffer), 1),
            "zones": zones_by_track.get(r.track_id, []),
        }))
    pc = {} if pctx is None else {(r["track_id"], r["layer"]): r for r in pctx.to_dict("records")}
    portion_feats = [feature(r.geometry, {"tid": r.track_id, "k": r.layer, "m": round(float(r.minutes or 0), 1),
                                          "ctx": portion_ctx(pc.get((r.track_id, r.layer)))})
                     for r in portions.itertuples() if not r.geometry.is_empty]
    d0 = dt.date.fromisoformat(PERIOD_START)
    days = [(d0 + dt.timedelta(days=i)).isoformat()
            for i in range((dt.date.fromisoformat(PERIOD_END) - d0).days + 1)]
    return {
        "tracks": fc(track_feats), "portions": fc(portion_feats), "days": days,
        "lenClasses": [{"k": k, "label": lab} for k, lab, _, _ in LEN_CLASSES],
        "flags": sorted({f["properties"]["flag"] for f in track_feats}),
        "still": STILL_KN, "gap": GAP_MINUTES, "simplify": SIMPLIFY_M,
        "nTracks": meta["segments_kept"], "nVessels": meta["vessels"],
        "colors": {k: COLORS[k] for k in ("track", "selected", "other", "portion_zone", "portion_buffer")},
        "animation": ANIMATION,
        "dots": dots or {}, "gaps": gaps or {}, "favDefault": favorites or [], "pointStep": POINT_STEP_S,
        "context": None if not ctx else {
            "available": ctx["available"], "summary": ctx["summary"], "days": ctx["days"],
            "sources": [SOURCES_TXT[k] for k in
                        ([("currents_sim" if ctx.get("simulated", {}).get("currents") else "currents")]
                         if ctx["available"]["currents"] else [])
                        + ([("wind_sim" if ctx.get("simulated", {}).get("wind") else
                             "wind_om" if "Open-Meteo" in ctx["labels"].get("wind", "") else "wind_cm")]
                           if ctx["available"]["wind"] else [])
                        + (["waves"] if ctx["available"]["waves"] else [])],
            "curScale": [CUR_BINS, CUR_COLORS], "windScale": [WIND_BINS, WIND_COLORS],
            "excluded": ctx.get("openmeteo_points_excluded", {}), "snapKm": ctx.get("max_snap_km"),
        },
    }


PANEL_CSS = """
<style>
#st-panel{position:absolute;top:10px;left:10px;z-index:1000;width:350px;max-width:calc(100vw - 20px);
  max-height:calc(100% - 20px);overflow:auto;background:#fff;color:#1f1f1d;border:1px solid #c9c7c0;
  border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.15);font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
#st-panel.collapsed{width:auto}#st-panel.collapsed .st-body{display:none}
.st-head{position:sticky;top:0;background:#0b2545;color:#fff;padding:9px 12px;display:flex;align-items:center;gap:8px;z-index:2}
.st-head h1{font-size:15px;margin:0;flex:1;font-weight:600}
.st-head button{background:rgba(255,255,255,.15);color:#fff;border:0;border-radius:4px;padding:4px 8px;cursor:pointer;font:inherit;font-size:12px}
.st-head button:hover{background:rgba(255,255,255,.3)}
.st-body{padding:4px 12px 12px}
.st-sec{border-top:1px solid #e6e4df;padding:10px 0 4px}.st-sec:first-child{border-top:0}
.st-sec h2{font-size:13px;margin:0 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#55534e}
.st-row{margin:6px 0}.st-row label.t{display:block;font-weight:600;margin-bottom:3px}
.st-row select,.st-row input[type=search]{width:100%;box-sizing:border-box;padding:5px 6px;border:1px solid #b9b7b0;border-radius:4px;font:inherit;background:#fff}
.st-checks{display:flex;flex-wrap:wrap;gap:2px 12px}.st-checks label{cursor:pointer}#st-lens label{white-space:nowrap}
.leaflet-control-layers{font:12.5px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1f1d}
.leaflet-control-layers-list::before{content:"Couches affichées";display:block;font-weight:700;margin-bottom:4px}
.st-help{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:50%;background:#e6e4df;color:#55534e;font-size:11px;font-weight:700;cursor:help;margin-left:4px}
.st-hint{color:#55534e;font-size:12px;margin-top:2px}
.st-count{background:#f3f2ef;border-radius:4px;padding:6px 8px;margin-top:8px}
.st-btn{border:1px solid #b9b7b0;background:#fff;border-radius:4px;padding:4px 10px;cursor:pointer;font:inherit}
.st-btn:hover{background:#f3f2ef}.st-btn.primary{background:#0b2545;color:#fff;border-color:#0b2545}
.st-card{border:1px solid #d9d7d0;border-radius:6px;padding:8px 10px;margin-top:8px;background:#fbfaf8}
.st-card h3{margin:0 0 4px;font-size:15px}
.st-tbl{border-collapse:collapse;width:100%;font-size:12.5px}.st-tbl th,.st-tbl td{border-bottom:1px solid #e6e4df;padding:3px 4px;text-align:left}
.st-tbl th{color:#55534e;font-weight:600}.st-na{color:#9a3412}
.st-scale{display:flex;gap:0;margin:4px 0 2px}.st-scale span{flex:1;height:8px}.st-scale-l{display:flex;justify-content:space-between;font-size:11px;color:#55534e}
.st-ctxtip{margin-top:4px;padding-top:4px;border-top:1px solid #e6e4df}
.st-card table{border-collapse:collapse;width:100%}.st-card td{padding:2px 0;vertical-align:top}
.st-card td:first-child{color:#55534e;width:46%;padding-right:6px}
.st-zl{margin:4px 0 0;padding-left:16px}.st-zl li{margin:2px 0}
.st-warn{color:#7c2d12;background:#fff4e5;border-radius:4px;padding:6px 8px;margin-top:6px;font-size:12px}
.st-leg div{margin:3px 0;display:flex;align-items:center;gap:8px}
.st-sw{display:inline-block;width:22px;height:12px;flex:none}
.st-tip{max-width:330px;min-width:230px;white-space:normal;font:12.5px/1.4 system-ui,sans-serif}
.st-badge{display:inline-block;background:#f3f2ef;border-radius:3px;padding:0 5px;font-weight:600}
.st-rule{margin-top:4px}.st-src{margin-top:4px;color:#6b6962;font-size:11.5px}
#st-exp{position:absolute;z-index:1000;left:372px;right:64px;bottom:26px;background:#fff;border:1px solid #c9c7c0;
  border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.15);font:13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1f1d}
@media (max-width:900px){#st-exp{left:10px;right:10px}}
.st-exp-head{display:flex;align-items:center;gap:8px;padding:6px 10px;background:#0b2545;color:#fff;border-radius:6px 6px 0 0;cursor:pointer}
.st-exp-head b{font-size:14px}.st-exp-head span{flex:1;opacity:.85;font-size:12px}
.st-exp-head button{background:rgba(255,255,255,.15);color:#fff;border:0;border-radius:4px;padding:3px 8px;cursor:pointer;font:inherit;font-size:12px}
#st-exp.collapsed .st-exp-body{display:none}#st-exp.collapsed .st-exp-head{border-radius:6px}
.st-exp-ctrl{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;padding:6px 10px;border-bottom:1px solid #e6e4df}
.st-exp-ctrl input[type=search]{padding:4px 6px;border:1px solid #b9b7b0;border-radius:4px;font:inherit;width:220px}
.st-exp-ctrl select{padding:3px 4px;border:1px solid #b9b7b0;border-radius:4px;font:inherit}
.st-exp-table{max-height:30vh;overflow:auto}
.st-exp-table table{border-collapse:collapse;width:100%}
.st-exp-table th{position:sticky;top:0;background:#f3f2ef;text-align:left;font-weight:600;color:#55534e;padding:4px 6px;font-size:12px}
.st-exp-table td{padding:3px 6px;border-bottom:1px solid #eeece7;white-space:nowrap}
.st-exp-table tr{cursor:pointer}.st-exp-table tbody tr:hover{background:#f6f8fc}
.st-exp-table tr.sel{background:#e3ecfa}.st-exp-table tr.fav-sep td{background:#fbfaf8;color:#55534e;font-size:11.5px;cursor:default}
.st-star{border:0;background:none;cursor:pointer;font-size:16px;line-height:1;color:#b9b7b0;padding:0 2px}
.st-star.on{color:#d4a017}
#st-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:2000}
#st-modal.open{display:flex;align-items:center;justify-content:center}
#st-modal .box{background:#fff;max-width:720px;width:calc(100vw - 32px);max-height:calc(100vh - 48px);overflow:auto;border-radius:8px;padding:16px 20px;font:14px/1.5 system-ui,sans-serif;color:#1f1f1d}
#st-modal h2{font-size:18px;margin:0 0 8px}#st-modal h3{font-size:14px;margin:14px 0 4px;color:#0b2545;text-transform:uppercase;letter-spacing:.03em}
#st-modal dl{margin:0}#st-modal dt{font-weight:700;margin-top:8px}#st-modal dd{margin:2px 0 0 0;color:#33312c}
.leaflet-popup-content{margin:10px 12px;font:13px/1.45 system-ui,sans-serif}
</style>
"""

GLOSSARY = """
<div id="st-exp">
 <div class="st-exp-head" id="st-exp-head"><b>Explorer les navires</b><span id="st-exp-n"></span>
  <button id="st-exp-toggle" type="button">Réduire</button></div>
 <div class="st-exp-body">
  <div class="st-exp-ctrl">
   <input type="search" id="st-exp-q" placeholder="Chercher un MMSI ou un nom…" aria-label="Chercher un navire">
   <label>Trier par <select id="st-exp-sort">
     <option value="mz">durée en zone</option><option value="len">longueur</option>
     <option value="gaps">nombre de trous</option><option value="mmsi">MMSI</option></select></label>
   <label><input type="checkbox" id="st-exp-fav"> Favoris seulement</label>
   <button class="st-btn" id="st-exp-reset" type="button">Réinitialiser les favoris</button>
  </div>
  <div class="st-exp-table"><table>
   <thead><tr><th title="Favori">★</th><th>MMSI</th><th>Nom</th><th>Longueur</th><th>Vitesse moy.</th>
    <th title="Périodes de plus de 30 min sans message AIS">Trous &gt; 30 min</th><th>Passe en zone</th><th>Durée en zone</th></tr></thead>
   <tbody id="st-exp-rows"></tbody></table></div>
 </div>
</div>
<div id="st-modal" role="dialog" aria-modal="true" aria-labelledby="st-gl-title"><div class="box">
<div style="display:flex;align-items:center"><h2 id="st-gl-title" style="flex:1">Comprendre les termes de la carte</h2>
<button class="st-btn" id="st-gl-close">Fermer</button></div>

<h3>Les navires et leur signal</h3><dl>
<dt>AIS (Automatic Identification System)</dt><dd>Balise radio à bord qui émet automatiquement la position, la vitesse,
le cap et l'identité du navire. Les positions de cette carte ont été reçues par les stations côtières danoises
(données publiques de l'administration danoise).</dd>
<dt>MMSI</dt><dd>Numéro d'identification radio du navire, à 9 chiffres. Les 3 premiers chiffres (le « MID ») indiquent
le pays du pavillon : 219 et 220 = Danemark, 265 et 266 = Suède, etc.</dd>
<dt>Pavillon</dt><dd>Pays d'immatriculation du navire, déduit ici du MMSI (table de l'Union internationale des
télécommunications). Ce n'est pas vérifié dans un registre.</dd>
<dt>Classe A / classe B</dt><dd>Deux catégories d'émetteurs AIS. La classe A est l'équipement professionnel : elle émet
plus souvent et plus d'informations. Dans nos données, un navire en classe A envoie un message toutes les 10 s
(médiane), contre 57 s en classe B. Dans l'UE, l'AIS est obligatoire pour les navires de pêche de plus de 15 m.
<b>Cette carte ne montre que des messages de classe A.</b></dd>
<dt>Navire de pêche</dt><dd>Navire dont le type déclaré dans son AIS est « Fishing » (code AIS 30). C'est une
déclaration de l'équipage, non vérifiée dans un registre.</dd>
<dt>Longueur</dt><dd>Longueur déclarée dans l'AIS (en mètres). Parfois absente ou mal saisie.</dd>
<dt>Vitesse moyenne (nœuds)</dt><dd>Moyenne des vitesses envoyées par le navire. 1 nœud = 1,852 km/h.
En dessous de 0,5 nœud en moyenne, le navire est quasi immobile : il est probablement à quai ou au mouillage.</dd>
<dt>Statut « en pêche » déclaré</dt><dd>Statut de navigation choisi à la main par l'équipage (« Engaged in fishing »).
C'est purement déclaratif et souvent laissé activé, y compris au port. Ce n'est pas une preuve d'activité de pêche.</dd>
</dl>

<h3>Les trajets</h3><dl>
<dt>Trajet</dt><dd>Suite des positions d'un même navire. Le trajet est coupé quand le navire n'envoie plus de message
pendant plus de 30 minutes : un navire peut donc avoir plusieurs trajets.</dd>
<dt>Trou de signal</dt><dd>Période de plus de 30 minutes sans message. Causes possibles : navire hors de portée
des antennes, AIS éteint, navire sorti de la zone étudiée.</dd>
<dt>Simplification à 30 m</dt><dd>Pour alléger la carte, les lignes sont redessinées avec moins de points ; l'écart
avec le trajet réel ne dépasse pas 30 m. Les calculs de durée, eux, utilisent toutes les positions.</dd>
</dl>

<h3>Les zones</h3><dl>
<dt>Règlement délégué (UE) 2022/952</dt><dd>Texte européen qui fixe des mesures de protection des fonds marins dans
le Kattegat, côté danois et côté suédois. Les zones de la carte reprennent ses coordonnées officielles.</dd>
<dt>Engins de fond (« engins de fond mobiles »)</dt><dd>Engins de pêche traînés sur le fond : chaluts de fond
(à panneaux, à perche, jumeaux, bœufs), chaluts à langoustines ou à crevettes, sennes (danoise, écossaise, de bateau)
et dragues.</dd>
<dt>Zone où les engins de fond sont interdits (rouge)</dt><dd>Zones danoises (« Areas 1 » dans le règlement) : pêcher
avec des engins de fond y est interdit, et ces engins doivent rester rangés à bord. Les autres engins ne sont pas
visés.</dd>
<dt>Zone où la pêche est interdite (rouge foncé)</dt><dd>Zones suédoises (« Areas 2 ») : toute pêche y est interdite,
y compris la pêche de loisir.</dd>
<dt>Zone où seuls certains engins sont permis (rouge clair, pointillés)</dt><dd>Zones suédoises (« Areas 3 ») : seuls
les lignes, les chaluts pélagiques, les casiers à crustacés et les filets maillants sous surveillance par caméras
y sont autorisés.</dd>
<dt>Zone MSFD</dt><dd>Zone désignée au titre de la directive-cadre « stratégie pour le milieu marin » (en anglais
Marine Strategy Framework Directive, directive 2008/56/CE), pour protéger les fonds marins. Le Danemark en a six
dans le Kattegat (A à F).</dd>
<dt>Natura 2000 · Kims Top og den Kinesiske Mur</dt><dd>Natura 2000 est le réseau européen de sites naturels
protégés. Kims Top og den Kinesiske Mur est un site danois (code DK00VA247), découpé ici en sous-zones.</dd>
<dt>Tampon de 4 milles (orange)</dt><dd>Bande de 4 milles nautiques (7,4 km) autour des zones danoises, <b>calculée par
le projet</b>. Hypothèse : les chalutiers de fond doivent y avoir un AIS de classe A. Les zones où l'AIS est
légalement obligatoire sont celles de l'annexe V (couche bleue, à activer en haut à droite).
1 mille nautique = 1,852 km.</dd>
<dt>Zone où l'AIS est obligatoire (bleu, pointillés)</dt><dd>Zones officielles de l'annexe V du règlement.
Côté danois, l'AIS est obligatoire pour les navires qui pêchent avec des engins de fond ; côté suédois, pour tout
navire de pêche présent.</dd>
</dl>

<h3>Les calculs</h3><dl>
<dt>Passage dans une zone (magenta) / dans un tampon (ambre)</dt><dd>Partie d'un trajet située à l'intérieur d'une
zone restreinte ou d'un tampon.</dd>
<dt>Temps dans une zone</dt><dd>Durée estimée en supposant que le navire avance à vitesse constante entre deux messages.
Si les zones se chevauchent, le même temps n'est compté qu'une fois dans le total.</dd>
</dl>

<h3>Courant, vent et vagues (contexte)</h3><dl>
<dt>Courant de surface</dt><dd>Vitesse et direction de l'eau près de la surface (grille d'environ 1 mille nautique,
une valeur toutes les 15 minutes). Ce sont des valeurs modélisées, pas des mesures. Elles incluent la marée, qui fait
varier le courant au fil de la journée.</dd>
<dt>Direction du courant</dt><dd>Direction <b>vers laquelle</b> va l'eau (0° = nord, 90° = est).</dd>
<dt>Direction du vent</dt><dd>Direction <b>d'où vient</b> le vent, selon l'usage météorologique : un vent d'ouest
souffle de l'ouest vers l'est. Sur la carte, les flèches pointent dans le sens où le vent souffle.</dd>
<dt>Flèches du jour</dt><dd>Moyenne des vecteurs sur la journée (UTC). Avec la marée, le courant change de sens
plusieurs fois par jour : la flèche montre la dérive moyenne, et l'infobulle donne la vitesse moyenne et la vitesse
maximale.</dd>
<dt>Vagues</dt><dd>Hauteur des vagues modélisée par le service météorologique allemand (DWD) et fournie par Open-Meteo,
sur une grille de 0,25°. Près des côtes et dans les ports, la valeur est celle du large le plus proche.</dd>
<dt>Angle cap de route / courant</dt><dd>Écart entre la route suivie par le navire (cap de route, ou « COG ») et la
direction du courant : 0° = courant dans le dos, 90° = courant de travers, 180° = courant de face. Il n'est calculé
que si le navire avance (au moins 0,5 nœud).</dd>
<dt>Vitesse par rapport à l'eau (estimation)</dt><dd>Vitesse sur le fond (donnée par l'AIS) moins le courant modélisé.
C'est une <b>estimation</b>, pas une mesure : elle dépend de la précision du modèle de courant.</dd>
</dl>

<h3>Ce que la carte ne dit pas</h3><dl>
<dd>• <b>Un passage dans une zone n'est pas une infraction.</b> Un navire en transit, engins relevés, est en règle,
et l'AIS ne dit pas quel engin est à bord.</dd>
<dd>• Les petits navires (moins de 15 m) ne sont pas tenus d'avoir l'AIS : leur absence de la carte est normale.</dd>
<dd>• Les zones plus anciennes du règlement 2017/118 ne sont pas encore affichées. Les sites Natura 2000 et HELCOM
ne le sont pas non plus (données non récupérées).</dd>
<dd>• Période : 3 jours seulement (15 au 17 septembre 2026, heures en temps universel, UTC).</dd>
</dl></div></div>
"""

PANEL_HTML = """
<div id="st-panel">
 <div class="st-head"><h1>SeaTrace · Kattegat</h1>
  <button id="st-gl-open" title="Définitions des termes employés">? Comprendre les termes</button>
  <button id="st-collapse" title="Réduire ou agrandir le panneau">Réduire</button></div>
 <div class="st-body">
  <div class="st-sec">
   <div class="st-hint">Trajets AIS des navires de pêche (classe A) du <b id="st-period"></b>, et zones où la pêche est
   restreinte. Survolez une zone ou un trajet pour l'expliquer ; cliquez sur un trajet pour sa fiche.</div>
  </div>
  <div class="st-sec"><h2>Étudier un navire</h2>
   <div class="st-row"><input type="search" id="st-search" placeholder="Chercher par nom ou par MMSI…" aria-label="Chercher un navire"></div>
   <div class="st-row"><select id="st-vessel" aria-label="Navire à étudier"></select></div>
   <div class="st-row st-checks"><label><input type="checkbox" id="st-others" checked> Garder les autres navires en gris</label></div>
   <div id="st-card"></div>
  </div>
  <div class="st-sec"><h2>Filtres</h2>
   <div class="st-row"><label class="t" for="st-passage">Passage dans une zone
     <span class="st-help" title="Zone restreinte : zone du règlement 2022/952 (rouge). Tampon : bande de 4 milles autour des zones danoises (orange).">?</span></label>
    <select id="st-passage">
     <option value="all">Tous les navires</option>
     <option value="zone">Passés dans une zone restreinte</option>
     <option value="any">Passés dans une zone ou un tampon</option>
     <option value="buffer">Passés seulement dans un tampon</option>
     <option value="none">Jamais passés dans une zone ni un tampon</option>
    </select></div>
   <div class="st-row"><label class="t">Longueur déclarée
     <span class="st-help" title="Longueur déclarée dans l'AIS. Dans l'UE, l'AIS est obligatoire pour les navires de pêche de plus de 15 m.">?</span></label>
    <div class="st-checks" id="st-lens"></div></div>
   <div class="st-row"><label class="t" for="st-flag">Pavillon
     <span class="st-help" title="Pays du pavillon, déduit des 3 premiers chiffres du MMSI.">?</span></label>
    <select id="st-flag"><option value="all">Tous les pavillons</option></select></div>
   <div class="st-row"><label class="t" for="st-fishing">Statut « en pêche » déclaré
     <span class="st-help" title="Statut choisi à la main par l'équipage (Engaged in fishing). Déclaratif : souvent laissé activé, même au port.">?</span></label>
    <select id="st-fishing">
     <option value="all">Indifférent</option>
     <option value="yes">A déclaré être en pêche au moins une fois</option>
     <option value="no">N'a jamais déclaré être en pêche</option>
    </select></div>
   <div class="st-row"><label class="t">Jours (UTC)
     <span class="st-help" title="Un trajet qui chevauche plusieurs jours est affiché en entier.">?</span></label>
    <div style="display:flex;gap:6px;align-items:center"><span>du</span><select id="st-d0"></select><span>au</span><select id="st-d1"></select></div></div>
   <div class="st-row st-checks"><label><input type="checkbox" id="st-still"> Masquer les trajets quasi immobiles
     (vitesse moyenne &lt; <span id="st-still-kn"></span> nœud, navire probablement à quai)</label></div>
   <div class="st-row"><button class="st-btn" id="st-reset">Tout réinitialiser</button></div>
   <div class="st-count" id="st-count"></div>
  </div>
  <div class="st-sec"><h2>Conditions de mer (contexte)</h2><div id="st-ctx"></div></div>
  <div class="st-sec"><h2>Légende</h2><div class="st-leg" id="st-legend"></div>
   <div class="st-hint" style="margin-top:6px">Les couches s'affichent ou se masquent en haut à droite.</div>
   <div class="st-warn"><b>À lire :</b> un passage dans une zone n'est pas une infraction. Un navire en transit, engins
   relevés, est en règle.</div>
   <div class="st-hint" id="st-anim" style="margin-top:6px"></div>
  </div>
 </div>
</div>
"""

PANEL_JS = """
<script>
document.addEventListener("DOMContentLoaded", function () {
const DATA = JSON.parse(document.getElementById("st-data").textContent);
const map = __MAP__, fgTracks = __FG_TRACKS__, fgPortions = __FG_PORTIONS__, lc = __LC__;
const C = DATA.colors;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtMin = m => { if (!m || m < 0.5) return "aucun"; const t = Math.round(m); if (t < 60) return t + " min";
  const h = Math.floor(t / 60), r = t % 60; return h + " h" + (r ? " " + String(r).padStart(2, "0") : ""); };
const fmtDate = iso => { const d = new Date(iso); const p = n => String(n).padStart(2, "0");
  return p(d.getUTCDate()) + "/" + p(d.getUTCMonth() + 1) + " à " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()); };
const fmtDay = s => { const [y, m, d] = s.split("-"); return d + "/" + m + "/" + y; };
const pct = x => Math.round(100 * x) + " %";
const nameOf = p => p.name || "(sans nom)";
const lenTxt = v => v == null ? "inconnue" : v + " m";
const kindTxt = {zone: "une zone restreinte", buffer: "un tampon de 4 milles"};

const tracks = DATA.tracks.features;
const portionsByTid = {};
DATA.portions.features.forEach(f => (portionsByTid[f.properties.tid] ||= []).push(f));

// --- Commandes ---------------------------------------------------------------
$("st-period").textContent = fmtDay(DATA.days[0]) + " au " + fmtDay(DATA.days[DATA.days.length - 1]);
$("st-still-kn").textContent = String(DATA.still).replace(".", ",");
DATA.lenClasses.forEach(c => { $("st-lens").insertAdjacentHTML("beforeend",
  `<label><input type="checkbox" value="${c.k}" checked> ${c.label}</label>`); });
DATA.flags.forEach(f => $("st-flag").insertAdjacentHTML("beforeend", `<option>${esc(f)}</option>`));
DATA.days.forEach((d, i) => { ["st-d0", "st-d1"].forEach(id => $(id).insertAdjacentHTML("beforeend",
  `<option value="${i}">${fmtDay(d)}</option>`)); });
$("st-d1").value = DATA.days.length - 1;
if (DATA.animation) $("st-anim").innerHTML = '<a href="map_animation.html" target="_blank">Voir les trajets animés dans le temps</a> (page séparée).';

const legend = [
  ["fill", "#d62828", 0.35, "", "Zone où les engins de fond sont interdits (DK)"],
  ["fill", "#8b0000", 0.55, "", "Zone où la pêche est interdite (SE)"],
  ["fill", "#d62828", 0.15, "dash", "Zone où seuls certains engins sont permis (SE)"],
  ["fill", "#f4a261", 0.22, "", "Tampon de 4 milles (hypothèse du projet)"],
  ["fill", "#2a78d6", 0, "dash", "Zone où l'AIS est obligatoire (officielle, masquée par défaut)"],
  ["line", C.track, 1, "", "Trajet d'un navire de pêche"],
  ["line", C.selected, 1, "thick", "Navire étudié"],
  ["line", C.other, 1, "", "Autres navires (quand un navire est étudié)"],
  ["line", C.portion_zone, 1, "thick", "Passage dans une zone restreinte"],
  ["line", C.portion_buffer, 1, "thick", "Passage dans un tampon de 4 milles"],
];
legend.forEach(([t, c, o, s, label]) => {
  const hex = Math.round(o * 255).toString(16).padStart(2, "0");
  const sw = t === "fill"
    ? `<span class="st-sw" style="background:${c}${hex};border:1.5px ${s === "dash" ? "dashed" : "solid"} ${c}"></span>`
    : `<span class="st-sw" style="height:0;border-top:${s === "thick" ? 4 : 2}px solid ${c}"></span>`;
  $("st-legend").insertAdjacentHTML("beforeend", `<div>${sw}<span>${esc(label)}</span></div>`);
});

// --- État et filtres ----------------------------------------------------------
const state = {};
function readState() {
  state.vessel = $("st-vessel").value;
  state.others = $("st-others").checked;
  state.passage = $("st-passage").value;
  state.flag = $("st-flag").value;
  state.fishing = $("st-fishing").value;
  state.lens = new Set([...$("st-lens").querySelectorAll("input:checked")].map(i => i.value));
  state.still = $("st-still").checked;
  let a = +$("st-d0").value, b = +$("st-d1").value; if (a > b) [a, b] = [b, a];
  state.t0 = Date.parse(DATA.days[a] + "T00:00:00Z"); state.t1 = Date.parse(DATA.days[b] + "T00:00:00Z") + 864e5;
  state.search = $("st-search").value.trim().toLowerCase();
}
const inDays = f => Date.parse(f.properties.end) >= state.t0 && Date.parse(f.properties.start) < state.t1;
const notStill = f => !state.still || f.properties.sog == null || f.properties.sog >= DATA.still;

function aggregate(list) {
  const by = {};
  list.forEach(f => { const p = f.properties;
    const v = by[p.mmsi] ||= {mmsi: p.mmsi, name: p.name, flag: p.flag, len: p.len, lc: p.lc, tracks: [],
      mz: 0, mb: 0, n: 0, sogW: 0, sogN: 0, engW: 0, km: 0, start: p.start, end: p.end, zones: {}};
    v.tracks.push(f); v.mz += p.mz; v.mb += p.mb; v.n += p.n; v.km += p.km;
    if (p.sog != null) { v.sogW += p.sog * p.n; v.sogN += p.n; }
    v.engW += p.eng * p.n;
    if (p.start < v.start) v.start = p.start; if (p.end > v.end) v.end = p.end;
    p.zones.forEach(z => { const k = z.n; (v.zones[k] ||= {n: z.n, k: z.k, c: z.c, m: 0}).m += z.m; });
  });
  Object.values(by).forEach(v => { v.sog = v.sogN ? v.sogW / v.sogN : null; v.eng = v.n ? v.engW / v.n : 0;
    v.status = v.mz > 0 ? "zone" : v.mb > 0 ? "buffer" : "none"; });
  return by;
}
function vesselOk(v) {
  if (state.flag !== "all" && v.flag !== state.flag) return false;
  if (!state.lens.has(v.lc)) return false;
  if (state.fishing === "yes" && !(v.eng > 0)) return false;
  if (state.fishing === "no" && v.eng > 0) return false;
  const s = state.passage;
  if (s === "zone" && v.status !== "zone") return false;
  if (s === "any" && v.status === "none") return false;
  if (s === "buffer" && v.status !== "buffer") return false;
  if (s === "none" && v.status !== "none") return false;
  return true;
}

// --- Dessin -------------------------------------------------------------------
// Même moteur de dessin que les zones (un second calque bloquerait leurs infobulles),
// avec une tolérance de clic élargie pour attraper les lignes fines.
const renderer = map.getRenderer(L.polyline([[0, 0], [0, 0]]));
renderer.options.tolerance = 6;
function trackPopup(f) {
  const p = f.properties;
  const zl = p.zones.length ? "<ul class='st-zl'>" + p.zones.map(z =>
    `<li>${esc(z.n)}${z.k === "zone" ? ` <i>(${esc(z.c)})</i>` : ""} : ${fmtMin(z.m)}</li>`).join("") + "</ul>" : " aucune";
  return `<b style="font-size:14px">${esc(nameOf(p))}</b><br>MMSI ${p.mmsi} · ${esc(p.flag)} · ${lenTxt(p.len)}
   <table style="margin-top:4px">
   <tr><td>Trajet</td><td>du ${fmtDate(p.start)} au ${fmtDate(p.end)} UTC</td></tr>
   <tr><td>Vitesse moyenne</td><td>${p.sog == null ? "inconnue" : String(p.sog.toFixed(1)).replace(".", ",") + " nœud"}${p.sog != null && p.sog < DATA.still ? " (quasi immobile)" : ""}</td></tr>
   <tr><td>Statut « en pêche »</td><td>${pct(p.eng)} des messages</td></tr>
   <tr><td>Temps en zone restreinte</td><td>${fmtMin(p.mz)}</td></tr>
   <tr><td>Temps en tampon</td><td>${fmtMin(p.mb)}</td></tr></table>
   <div>Zones traversées :${zl}</div>
   <div style="margin-top:6px"><button class="st-btn primary" data-study="${p.mmsi}">Étudier ce navire</button></div>`;
}
function draw(list, mode) {
  const style = mode === "selected" ? {color: C.selected, weight: 3, opacity: 0.9}
              : mode === "other" ? {color: C.other, weight: 1, opacity: 0.3}
              : {color: C.track, weight: 1, opacity: 0.4};
  const layer = L.geoJSON({type: "FeatureCollection", features: list}, {renderer, style: () => style,
    onEachFeature: (f, l) => {
      l.bindTooltip(`<b>${esc(nameOf(f.properties))}</b> · MMSI ${f.properties.mmsi}<br><span style="color:#55534e">Cliquer pour la fiche</span>`, {sticky: true});
      l.bindPopup(() => trackPopup(f), {maxWidth: 340});
      if (mode !== "other") l.on("mouseover", () => l.setStyle({weight: style.weight + 2}))
                             .on("mouseout", () => l.setStyle({weight: style.weight}));
    }});
  fgTracks.addLayer(layer);
}
// --- Contexte : courant, vent, vagues -------------------------------------------------
const NA = '<span class="st-na">non disponible</span>';
const kn = v => v == null ? NA : String(v.toFixed(1)).replace(".", ",") + " nd";
const mt = v => v == null ? NA : String(v.toFixed(1)).replace(".", ",") + " m";
const COMPASS = ["nord","nord-nord-est","nord-est","est-nord-est","est","est-sud-est","sud-est","sud-sud-est","sud",
  "sud-sud-ouest","sud-ouest","ouest-sud-ouest","ouest","ouest-nord-ouest","nord-ouest","nord-nord-ouest"];
const card = d => COMPASS[Math.floor((((d % 360) + 360) % 360 + 11.25) / 22.5) % 16];
const fromTxt = d => { const c = card(d); return /^[eo]/.test(c) ? "de l'" + c : "du " + c; };
function angleTxt(a) { if (a == null) return NA;
  return Math.round(a) + "° (" + (a < 45 ? "courant plutôt dans le dos" : a > 135 ? "courant plutôt de face" : "courant plutôt de travers") + ")"; }
function ctxTip(c) {
  if (!DATA.context) return "";
  if (!c) return `<div class="st-ctxtip">Conditions : ${NA}</div>`;
  const av = DATA.context.available, still = c.sog != null && c.sog < DATA.still;
  const sc = "", sw = "";
  return `<div class="st-ctxtip"><b>Conditions pendant ce passage</b> (moyennes)<br>
    Vitesse du navire (sur le fond) : ${kn(c.sog)}<br>
    Courant${sc} : ${av.currents ? kn(c.cur) + (c.curMax != null ? " (max " + kn(c.curMax) + ")" : "") : NA}<br>
    Angle cap de route / courant : ${av.currents ? (still ? "non calculé (navire quasi immobile)" : angleTxt(c.ang)) : NA}<br>
    Vitesse par rapport à l'eau : ${av.currents && c.water != null ? "≈ " + kn(c.water) + " <i>(estimation)</i>" : NA}<br>
    Vent${sw} : ${av.wind && c.wind != null ? kn(c.wind) + (c.windFrom != null ? ", venant " + fromTxt(c.windFrom) : "") : NA}<br>
    Vagues : ${av.waves && c.wave != null ? mt(c.wave) + (c.waveMax != null ? " (max " + mt(c.waveMax) + ")" : "") : NA}
    ${still ? '<br><span style="color:#55534e">Navire quasi immobile, probablement à quai : vent et vagues sont les valeurs du large le plus proche.</span>' : ""}</div>`;
}
function renderContext() {
  const C2 = DATA.context, el = $("st-ctx");
  if (!C2) { el.innerHTML = `<div class="st-hint">Aucune donnée de contexte : lancer src/09, 10 et 11.</div>`; return; }
  const av = C2.available;
  let rows = C2.days.map(d => { const r = C2.summary[d] || {};
    return `<tr><td>${fmtDay(d).slice(0, 5)}</td>
      <td>${av.wind ? kn(r.wind_mean_kn) + " / " + kn(r.wind_max_kn) : NA}</td>
      <td>${av.waves ? mt(r.wave_max_m) : NA}</td>
      <td>${av.currents ? kn(r.cur_mean_kn) : NA}</td></tr>`; }).join("");
  const scale = (sc, unit) => `<div class="st-scale">${sc[1].map(c => `<span style="background:${c}"></span>`).join("")}</div>
    <div class="st-scale-l"><span>&lt; ${String(sc[0][0]).replace(".", ",")}</span>${sc[0].slice(1).map(b => `<span>${String(b).replace(".", ",")}</span>`).join("")}<span>${unit}</span></div>`;
  const miss = [!av.currents && "courant", !av.wind && "vent", !av.waves && "vagues"].filter(Boolean);
  const tag = k => "";
  el.innerHTML = `<div class="st-hint">Contexte pour interpréter les trajets, pas pour détecter des infractions.
      Valeurs modélisées, en temps universel (UTC).</div>
    <table class="st-tbl" style="margin-top:6px"><tr><th>Jour</th><th>Vent moyen / max${tag("wind")}</th><th>Vagues max</th><th>Courant moyen${tag("currents")}</th></tr>${rows}</table>
    ${miss.length ? `<div class="st-warn">Non disponible pour l'instant : ${miss.join(", ")}.</div>` : ""}
    ${av.currents ? `<div style="margin-top:6px"><b>Flèches de courant</b> (vitesse moyenne du jour)${scale(C2.curScale, "nœuds")}</div>` : ""}
    ${av.wind ? `<div style="margin-top:6px"><b>Flèches de vent</b> (vitesse moyenne du jour)${scale(C2.windScale, "nœuds")}</div>` : ""}
    ${(av.currents || av.wind) ? `<div class="st-hint">Couches « Courant » et « Vent » par jour : à activer en haut à droite.</div>` : ""}
    <div class="st-hint" style="margin-top:6px"><b>Sources :</b><br>${C2.sources.map(esc).join("<br>")}</div>`;
}
renderContext();

// --- Positions AIS : un point par message (au plus un par minute), dessinés sur un canevas ---------
const DOTS = {};
for (const [tid, arr] of Object.entries(DATA.dots)) {
  const n = arr.length / 2, mx = new Float64Array(n), my = new Float64Array(n);
  let la = 0, lo = 0;
  for (let i = 0; i < n; i++) {
    la += arr[2 * i]; lo += arr[2 * i + 1];
    const s = Math.sin(la / 1e4 * Math.PI / 180);
    mx[i] = (lo / 1e4 + 180) / 360; my[i] = 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
  }
  DOTS[tid] = {mx, my};
}
const dotSize = z => z <= 8 ? 1.6 : z <= 10 ? 2.2 : z <= 12 ? 3 : 4;
const DotLayer = L.Layer.extend({
  initialize() { this.groups = []; },
  onAdd(m) {
    this._c = L.DomUtil.create("canvas", "leaflet-zoom-hide");
    this._c.style.pointerEvents = "none";
    m.getPanes().overlayPane.appendChild(this._c);
    m.on("moveend zoomend resize viewreset", this.redraw, this);
    this.redraw();
  },
  onRemove(m) { L.DomUtil.remove(this._c); m.off("moveend zoomend resize viewreset", this.redraw, this); },
  setGroups(g) { this.groups = g; if (this._map) this.redraw(); },
  redraw() {
    const m = this._map; if (!m) return;
    const size = m.getSize(), c = this._c, dpr = window.devicePixelRatio || 1;
    L.DomUtil.setPosition(c, m.containerPointToLayerPoint([0, 0]));
    c.width = size.x * dpr; c.height = size.y * dpr; c.style.width = size.x + "px"; c.style.height = size.y + "px";
    const ctx = c.getContext("2d"); ctx.scale(dpr, dpr);
    const z = m.getZoom(), scale = 256 * Math.pow(2, z), o = m.getPixelBounds().min;
    for (const g of this.groups) {
      ctx.fillStyle = g.color; ctx.globalAlpha = g.alpha;
      const r = dotSize(z) + (g.bigger || 0), h = r / 2;
      for (const tid of g.tids) {
        const d = DOTS[tid]; if (!d) continue;
        for (let i = 0; i < d.mx.length; i++) {
          const x = d.mx[i] * scale - o.x, y = d.my[i] * scale - o.y;
          if (x < -r || y < -r || x > size.x + r || y > size.y + r) continue;
          ctx.fillRect(x - h, y - h, r, r);
        }
      }
    }
  }
});
const dotLayer = new DotLayer();
dotLayer.addTo(map);
lc.addOverlay(dotLayer, "Positions AIS (un point par message, au plus un par minute)");
const tidsOf = list => list.map(f => f.properties.tid);

function drawGaps(mmsi) {
  (DATA.gaps[mmsi] || []).filter(g => Date.parse(g[5]) >= state.t0 && Date.parse(g[4]) < state.t1).forEach(g => {
    const tip = `<b>Trou de signal</b> : ${fmtMin(g[6])} sans message AIS<br>du ${fmtDate(g[4])} au ${fmtDate(g[5])} UTC`;
    fgTracks.addLayer(L.polyline([[g[0], g[1]], [g[2], g[3]]], {renderer, color: C.selected, weight: 2, opacity: 0.8,
      dashArray: "4 6"}).bindTooltip(tip, {sticky: true}));
    [[g[0], g[1]], [g[2], g[3]]].forEach(p => fgTracks.addLayer(L.circleMarker(p, {renderer, radius: 5, color: C.selected,
      weight: 2, fillColor: "#fff", fillOpacity: 1}).bindTooltip(tip)));
  });
}

function drawPortions(list) {
  const feats = list.flatMap(f => portionsByTid[f.properties.tid] || []);
  const byTid = Object.fromEntries(list.map(f => [f.properties.tid, f.properties]));
  fgPortions.addLayer(L.geoJSON({type: "FeatureCollection", features: feats}, {renderer,
    style: f => ({color: f.properties.k === "zone" ? C.portion_zone : C.portion_buffer, weight: 4, opacity: 0.95}),
    onEachFeature: (f, l) => { const t = byTid[f.properties.tid];
      const zs = t.zones.filter(z => z.k === f.properties.k).map(z => `${esc(z.n)} : ${fmtMin(z.m)}`).join("<br>");
      l.bindTooltip(`<div class="st-tip"><b>${esc(nameOf(t))}</b> · passage dans ${kindTxt[f.properties.k]}<br>Temps total : ${fmtMin(f.properties.m)}<br>${zs}${ctxTip(f.properties.ctx)}</div>`, {sticky: true});
      l.bindPopup(() => trackPopup({properties: t}), {maxWidth: 340}); }}));
}

function vesselCard(v) {
  if (!v) return "";
  const zones = Object.values(v.zones).sort((a, b) => b.m - a.m);
  const rz = zones.filter(z => z.k === "zone"), bz = zones.filter(z => z.k === "buffer");
  const zl = (rz.length ? "<div style='margin-top:4px'>Zones restreintes :</div><ul class='st-zl'>" + rz.map(z =>
      `<li><b>${esc(z.n)}</b> : ${fmtMin(z.m)}<br><span style="color:#55534e">${esc(z.c)}</span></li>`).join("") + "</ul>" : "")
    + (bz.length ? "<div style='margin-top:4px'>Tampons de 4 milles :</div><ul class='st-zl'>" + bz.map(z =>
      `<li>${esc(z.n.replace("Tampon de 4 milles autour de ", "autour de "))} : ${fmtMin(z.m)}</li>`).join("") + "</ul>" : "")
    || "<div class='st-hint'>Aucune zone ni tampon traversé sur la période.</div>";
  const still = v.sog != null && v.sog < DATA.still;
  return `<div class="st-card"><h3>${esc(nameOf(v))}</h3>
   <table>
    <tr><td>MMSI</td><td>${v.mmsi}</td></tr>
    <tr><td>Pavillon (d'après le MMSI)</td><td>${esc(v.flag)}</td></tr>
    <tr><td>Longueur déclarée</td><td>${lenTxt(v.len)}</td></tr>
    <tr><td>Observé</td><td>du ${fmtDate(v.start)} au ${fmtDate(v.end)} UTC</td></tr>
    <tr><td>Trajets</td><td>${v.tracks.length} (coupés après ${DATA.gap} min sans signal)</td></tr>
    <tr><td>Distance parcourue</td><td>${Math.round(v.km)} km</td></tr>
    <tr><td>Vitesse moyenne</td><td>${v.sog == null ? "inconnue" : v.sog.toFixed(1).replace(".", ",") + " nœud"}</td></tr>
    <tr><td>Statut « en pêche » déclaré</td><td>${pct(v.eng)} des messages</td></tr>
    <tr><td>Temps en zone restreinte</td><td><b>${fmtMin(v.mz)}</b></td></tr>
    <tr><td>Temps en tampon de 4 milles</td><td><b>${fmtMin(v.mb)}</b></td></tr>
   </table>
   <div style="margin-top:6px"><b>Zones traversées</b>${zl}</div>
   ${zones.filter(z => z.k === "buffer").reduce((a, z) => a + z.m, 0) > v.mb + 1 ? `<div class="st-hint">Les tampons se chevauchent : un même moment peut compter pour plusieurs tampons. Le temps total en tampon ci-dessus ne le compte qu'une fois.</div>` : ""}
   ${still ? `<div class="st-warn">Vitesse moyenne très faible : le navire est probablement resté à quai ou au mouillage. Son « trajet » est alors surtout le bruit du GPS.</div>` : ""}
   ${v.eng > 0.9 && still ? `<div class="st-warn">Le statut « en pêche » reste activé alors que le navire est quasi immobile : ce statut est déclaratif.</div>` : ""}
   <div style="margin-top:8px;display:flex;gap:6px"><button class="st-btn" id="st-zoom">Centrer la carte</button>
   <button class="st-btn" id="st-clear">Revenir à tous les navires</button></div></div>`;
}

let lastFit = null;
function fit(features) {
  if (!features.length) return;
  const b = L.geoJSON({type: "FeatureCollection", features}).getBounds();
  if (!b.isValid()) return;
  const panel = $("st-panel"); const w = panel.classList.contains("collapsed") ? 0 : panel.offsetWidth;
  const exp = $("st-exp"), eh = exp ? exp.offsetHeight + 30 : 30;
  map.fitBounds(b, {paddingTopLeft: [w + 30, 30], paddingBottomRight: [80, eh], maxZoom: 12});
}

function fillVesselSelect(vessels) {
  const sel = $("st-vessel"), cur = state.vessel;
  const groups = {zone: "Passés dans une zone restreinte", buffer: "Passés seulement dans un tampon", none: "Sans passage dans une zone ni un tampon"};
  let html = `<option value="">— Tous les navires (${vessels.length}) —</option>`;
  for (const g of ["zone", "buffer", "none"]) {
    const vs = vessels.filter(v => v.status === g).filter(v => !state.search ||
      (v.name || "").toLowerCase().includes(state.search) || v.mmsi.includes(state.search))
      .sort((a, b) => (b.mz + b.mb) - (a.mz + a.mb) || nameOf(a).localeCompare(nameOf(b)));
    if (!vs.length) continue;
    html += `<optgroup label="${groups[g]} (${vs.length})">` + vs.map(v =>
      `<option value="${v.mmsi}">${esc(nameOf(v))} · ${lenTxt(v.len)} · ${esc(v.flag)}${g !== "none" ? " · " + fmtMin(g === "zone" ? v.mz : v.mb) : ""}</option>`).join("") + "</optgroup>";
  }
  sel.innerHTML = html;
  if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
  else if (cur) { sel.insertAdjacentHTML("afterbegin", `<option value="${cur}">${cur} (hors filtres)</option>`); sel.value = cur; }
}

function apply(opts = {}) {
  readState();
  fgTracks.clearLayers(); fgPortions.clearLayers();
  const dayTracks = tracks.filter(inDays);
  const shown = dayTracks.filter(notStill);
  const vessels = aggregate(shown);
  const okVessels = Object.values(vessels).filter(vesselOk);
  const okSet = new Set(okVessels.map(v => v.mmsi));
  fillVesselSelect(okVessels);
  let visible;
  if (state.vessel) {
    const mine = dayTracks.filter(f => f.properties.mmsi === state.vessel);
    if (state.others) draw(shown.filter(f => okSet.has(f.properties.mmsi) && f.properties.mmsi !== state.vessel), "other");
    draw(mine, "selected"); drawPortions(mine); drawGaps(state.vessel); visible = mine;
    dotLayer.setGroups([
      ...(state.others ? [{tids: tidsOf(shown.filter(f => okSet.has(f.properties.mmsi) && f.properties.mmsi !== state.vessel)),
                           color: C.other, alpha: 0.35}] : []),
      {tids: tidsOf(mine), color: C.selected, alpha: 1, bigger: 1.5}]);
    $("st-card").innerHTML = vesselCard(aggregate(mine)[state.vessel]);
    $("st-zoom") && ($("st-zoom").onclick = () => fit(mine));
    $("st-clear") && ($("st-clear").onclick = () => { $("st-vessel").value = ""; apply({fit: true}); });
    if (opts.fit) fit(mine);
  } else {
    visible = shown.filter(f => okSet.has(f.properties.mmsi));
    draw(visible, "normal"); drawPortions(visible);
    dotLayer.setGroups([{tids: tidsOf(visible), color: C.track, alpha: 0.85}]);
    $("st-card").innerHTML = "";
    if (opts.fit) fit(visible);
  }
  renderExplorer();
  const nInZone = okVessels.filter(v => v.status === "zone").length;
  const nStill = dayTracks.length - shown.length;
  $("st-count").innerHTML = state.vessel
    ? `Navire étudié : <b>${$("st-vessel").selectedOptions[0]?.text.split(" · ")[0] || state.vessel}</b>, ${visible.length} trajet(s).`
    : `<b>${okVessels.length}</b> navires et <b>${visible.length}</b> trajets affichés, sur ${DATA.nVessels} navires et ${DATA.nTracks} trajets.<br>
       Dont ${nInZone} navire(s) passés dans une zone restreinte.` + (state.still ? `<br>${nStill} trajet(s) quasi immobiles masqués.` : "");
}

// --- Événements -----------------------------------------------------------------
$("st-vessel").addEventListener("change", () => apply({fit: true}));
$("st-search").addEventListener("input", () => apply());
["st-others", "st-passage", "st-flag", "st-fishing", "st-d0", "st-d1", "st-still"].forEach(id =>
  $(id).addEventListener("change", () => apply()));
$("st-lens").addEventListener("change", () => apply());
$("st-reset").addEventListener("click", () => {
  $("st-vessel").value = ""; $("st-search").value = ""; $("st-others").checked = true;
  ["st-passage", "st-flag", "st-fishing"].forEach(id => $(id).selectedIndex = 0);
  $("st-lens").querySelectorAll("input").forEach(i => i.checked = true);
  $("st-d0").value = 0; $("st-d1").value = DATA.days.length - 1; $("st-still").checked = false;
  apply({fit: true});
});
function selectVessel(mmsi, openPopup = true) {
  map.closePopup(); $("st-search").value = ""; $("st-vessel").value = ""; apply();
  const sel = $("st-vessel");
  if (![...sel.options].some(o => o.value === mmsi)) sel.insertAdjacentHTML("afterbegin", `<option value="${mmsi}">${mmsi}</option>`);
  sel.value = mmsi; apply({fit: true});
  if (openPopup) {
    const f = tracks.filter(t => t.properties.mmsi === mmsi && inDays(t)).sort((a, b) => b.properties.n - a.properties.n)[0];
    if (f) { const cs = f.geometry.coordinates, mid = cs[Math.floor(cs.length / 2)];
      const lcW = (document.querySelector(".leaflet-control-layers") || {offsetWidth: 0}).offsetWidth;
      setTimeout(() => L.popup({maxWidth: 340, autoPanPaddingTopLeft: [$("st-panel").offsetWidth + 20, 20],
        autoPanPaddingBottomRight: [lcW + 20, $("st-exp").offsetHeight + 30]})
        .setLatLng([mid[1], mid[0]]).setContent(trackPopup(f)).openOn(map), 350); }
  }
}
map.on("popupopen", e => { const b = e.popup.getElement().querySelector("[data-study]");
  if (b) b.addEventListener("click", () => selectVessel(b.dataset.study, false)); });

// --- Explorateur et favoris ---------------------------------------------------------------
const FAV_KEY = "seatrace.favoris.v1";
let favMem = null;
function loadFav() {
  try { const s = localStorage.getItem(FAV_KEY); if (s) return new Set(JSON.parse(s)); } catch (e) { /* mémoire */ }
  return new Set(favMem || DATA.favDefault);
}
function saveFav(set) {
  favMem = [...set];
  try { localStorage.setItem(FAV_KEY, JSON.stringify(favMem)); } catch (e) { /* stockage indisponible : mémoire */ }
}
let favs = loadFav();
const ALL = aggregate(tracks);
const nGaps = m => (DATA.gaps[m] || []).length;
function renderExplorer() {
  const q = $("st-exp-q").value.trim().toLowerCase(), key = $("st-exp-sort").value, favOnly = $("st-exp-fav").checked;
  let vs = Object.values(ALL).filter(v => !q || v.mmsi.includes(q) || (v.name || "").toLowerCase().includes(q));
  if (favOnly) vs = vs.filter(v => favs.has(v.mmsi));
  const val = {mz: v => v.mz, len: v => v.len ?? -1, gaps: v => nGaps(v.mmsi), mmsi: v => -Number(v.mmsi)};
  vs.sort((a, b) => (favs.has(b.mmsi) - favs.has(a.mmsi)) || (val[key](b) - val[key](a)) || a.mmsi.localeCompare(b.mmsi));
  const nf = vs.filter(v => favs.has(v.mmsi)).length;
  const row = v => `<tr data-mmsi="${v.mmsi}" class="${state.vessel === v.mmsi ? "sel" : ""}">
    <td><button class="st-star ${favs.has(v.mmsi) ? "on" : ""}" data-star="${v.mmsi}" aria-pressed="${favs.has(v.mmsi)}"
      title="${favs.has(v.mmsi) ? "Retirer des favoris" : "Ajouter aux favoris"}">${favs.has(v.mmsi) ? "★" : "☆"}</button></td>
    <td>${v.mmsi}</td><td>${esc(nameOf(v))}</td><td>${lenTxt(v.len)}</td>
    <td>${v.sog == null ? "–" : v.sog.toFixed(1).replace(".", ",") + " nd"}</td><td>${nGaps(v.mmsi)}</td>
    <td>${v.mz > 0 ? "oui" : "–"}</td><td>${v.mz > 0 ? fmtMin(v.mz) : "–"}</td></tr>`;
  let html = "";
  if (nf) html += `<tr class="fav-sep"><td colspan="8">Favoris (${nf})</td></tr>` + vs.slice(0, nf).map(row).join("");
  if (vs.length > nf) html += (nf ? `<tr class="fav-sep"><td colspan="8">Autres navires (${vs.length - nf})</td></tr>` : "")
    + vs.slice(nf).map(row).join("");
  $("st-exp-rows").innerHTML = html;
  $("st-exp-n").textContent = `${Object.keys(ALL).length} navires · ${favs.size} favori(s)`;
}
$("st-exp-rows").addEventListener("click", e => {
  const star = e.target.closest("[data-star]");
  if (star) { const m = star.dataset.star; favs.has(m) ? favs.delete(m) : favs.add(m); saveFav(favs); renderExplorer(); return; }
  const tr = e.target.closest("tr[data-mmsi]"); if (tr) selectVessel(tr.dataset.mmsi, true);
});
["st-exp-q", "st-exp-sort", "st-exp-fav"].forEach(id => $(id).addEventListener(id === "st-exp-q" ? "input" : "change", renderExplorer));
$("st-exp-reset").addEventListener("click", () => { favs = new Set(DATA.favDefault); saveFav(favs); renderExplorer(); });
$("st-exp-head").addEventListener("click", e => { const x = $("st-exp"); x.classList.toggle("collapsed");
  $("st-exp-toggle").textContent = x.classList.contains("collapsed") ? "Ouvrir" : "Réduire"; });
L.DomEvent.disableClickPropagation($("st-exp")); L.DomEvent.disableScrollPropagation($("st-exp"));
$("st-collapse").addEventListener("click", () => { const p = $("st-panel"); p.classList.toggle("collapsed");
  $("st-collapse").textContent = p.classList.contains("collapsed") ? "Ouvrir" : "Réduire"; });
$("st-gl-open").addEventListener("click", () => $("st-modal").classList.add("open"));
$("st-gl-close").addEventListener("click", () => $("st-modal").classList.remove("open"));
$("st-modal").addEventListener("click", e => { if (e.target.id === "st-modal") e.target.classList.remove("open"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") $("st-modal").classList.remove("open"); });
L.DomEvent.disableClickPropagation($("st-panel")); L.DomEvent.disableScrollPropagation($("st-panel"));
L.control.zoom({position: "bottomright", zoomInTitle: "Zoomer", zoomOutTitle: "Dézoomer"}).addTo(map);
L.control.scale({position: "bottomright", imperial: false}).addTo(map);

apply({fit: true});
});
</script>
"""


def add_panel(m, data: dict, fg_tracks, fg_portions, lc):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    js = (PANEL_JS.replace("__MAP__", m.get_name()).replace("__FG_TRACKS__", fg_tracks.get_name())
          .replace("__FG_PORTIONS__", fg_portions.get_name()).replace("__LC__", lc.get_name()))
    html = (PANEL_CSS + PANEL_HTML + GLOSSARY
            + f'<script type="application/json" id="st-data">{payload}</script>' + js)
    # Le contenu passe par Jinja (branca) : on le protège tel quel.
    m.get_root().html.add_child(Element("{% raw %}" + html + "{% endraw %}"))


def animation(tracks_points: pl.DataFrame, zones: gpd.GeoDataFrame, names: dict, out: pathlib.Path):
    """Positions échantillonnées toutes les ANIM_STEP_MIN minutes, une ligne par trajet."""
    s = (tracks_points.with_columns(pl.col("timestamp").dt.truncate(f"{ANIM_STEP_MIN}m").alias("bucket"))
         .group_by(["track_id", "bucket"], maintain_order=True).first()
         .sort(["track_id", "timestamp"]))
    feats = []
    for (tid,), g in s.group_by(["track_id"], maintain_order=True):
        if g.height < 2:
            continue
        mmsi = tid.split("-")[0]
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(x, 5), round(y, 5)] for x, y in zip(g["lon"], g["lat"])]},
                      "properties": {"times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in g["timestamp"]],
                                     "popup": f"{names.get(mmsi) or '(sans nom)'} · MMSI {mmsi}",
                                     "style": {"color": COLORS["track"], "weight": 2, "opacity": 0.8}}})
    m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=TILES, prefer_canvas=True, control_scale=True)
    for layer, color, op in [("buffer", COLORS["buffer"], 0.2), ("zone", COLORS["a1"], 0.35)]:
        zz = zones[zones.layer == layer]
        folium.GeoJson(fc([feature(r.geometry, {"n": zone_label(r.zone_id, r.name)}) for r in zz.itertuples()]),
                       style_function=lambda f, c=color, o=op: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": o},
                       tooltip=folium.GeoJsonTooltip(["n"], labels=False)).add_to(m)
    TimestampedGeoJson({"type": "FeatureCollection", "features": feats}, period=f"PT{ANIM_STEP_MIN}M",
                       duration="PT2H", add_last_point=True, auto_play=False, loop=False,
                       date_options="YYYY-MM-DD HH:mm", transition_time=100).add_to(m)
    m.get_root().html.add_child(Element(
        "<div style='position:fixed;top:10px;left:50px;z-index:1000;background:#fff;padding:6px 10px;"
        "border:1px solid #c9c7c0;border-radius:4px;font:13px system-ui,sans-serif'>"
        "<b>Trajets animés</b> · utilisez le curseur en bas pour avancer dans le temps (UTC) · "
        "<a href='map.html'>retour à la carte principale</a></div>"))
    m.save(out)
    return len(feats), int(s.height)


def main(ctx_mod=None, reports: pathlib.Path = REPORTS, animation_on: bool = ANIMATION):
    """ctx_mod et reports ne servent qu'aux tests (contexte simulé, sortie ailleurs)."""
    crossing_mod = _load("07_crossing")
    tracks_mod = crossing_mod.tracks_mod
    crossing_mod.crossing(PERIOD_START, PERIOD_END, GAP_MINUTES, SIMPLIFY_M)
    tag = tracks_mod.tag(PERIOD_START, PERIOD_END, GAP_MINUTES, SIMPLIFY_M)
    d = tracks_mod.OUT_DIR
    meta = json.loads((d / f"tracks_{tag}.json").read_text())
    tracks = gpd.read_parquet(d / f"tracks_{tag}.parquet")
    tot = pd.read_parquet(d / f"crossing_tracks_{tag}.parquet")
    tracks = tracks.merge(tot, on="track_id", how="left").fillna({"minutes_in_zone": 0, "minutes_in_buffer": 0})
    per_zone = pl.read_parquet(d / f"crossing_{tag}.parquet")
    portions = gpd.read_parquet(d / f"portions_{tag}.parquet")
    dur = tot.melt(id_vars="track_id", var_name="layer", value_name="minutes")
    dur["layer"] = dur.layer.str.replace("minutes_in_", "")
    portions = portions.merge(dur, on=["track_id", "layer"], how="left")

    ctx_mod = ctx_mod or _load("11_context")
    ctx_path = ctx_mod.build_context(PERIOD_START, PERIOD_END, GAP_MINUTES, SIMPLIFY_M)
    ctx = json.loads(ctx_path.read_text())
    pctx = pd.read_parquet(ctx_mod.OUT_DIR / f"portion_context_{tag}.parquet")

    zones = gpd.read_file(REF / "kattegat_zones.geojson")
    if zones.crs is None or zones.crs.to_epsg() != 4326:
        raise SystemExit(f"kattegat_zones.geojson : CRS inattendu {zones.crs}")
    if not zones.is_valid.all():
        raise SystemExit("kattegat_zones.geojson contient des géométries invalides : relancer 03_zones.py.")

    pts = pl.read_parquet(d / f"points_{tag}.parquet")
    dots, gaps = dots_and_gaps(pts)
    data = build_data(tracks, per_zone, portions, meta, ctx, pctx, dots, gaps, default_favorites())
    print(f"[info] {sum(len(v) for v in dots.values()) // 2} positions AIS dessinées, "
          f"{sum(len(v) for v in gaps.values())} trous de plus de {GAP_MINUTES} min")

    for internal in (False, True):
        if internal and not (PRIVATE / "demo_truth").exists():
            continue
        m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=None, prefer_canvas=True,
                       zoom_control=False, control_scale=False)
        folium.TileLayer(TILES, name="Fond de carte gris clair (Esri)").add_to(m)
        zones_layers(m, zones)
        missing = protected_layers(m)
        context_layers(m, ctx)
        fg_tracks = folium.FeatureGroup("Trajets des navires de pêche", show=True).add_to(m)
        fg_portions = folium.FeatureGroup("Passages dans une zone ou un tampon", show=True).add_to(m)
        if internal:
            truth_layer(m)
        lc = folium.LayerControl(collapsed=False, position="topright")
        add_panel(m, data, fg_tracks, fg_portions, lc)
        lc.add_to(m)
        out_dir = reports / "internal" if internal else reports
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / ("map_with_truth.html" if internal else "map.html")
        m.save(out)
        print(f"[out ] {out} ({out.stat().st_size / 1e6:.2f} Mo)")
    if missing:
        print("[miss] couches absentes :", "; ".join(missing))
    if animation_on:
        names = {k: v for k, v in zip(tracks.mmsi, tracks.name) if isinstance(v, str)}
        aout = reports / "map_animation.html"
        n, k = animation(pts, zones, names, aout)
        print(f"[out ] {aout.relative_to(ROOT)} ({aout.stat().st_size / 1e6:.1f} Mo, {n} trajets, {k} positions)")


def truth_layer(m):
    """Carte interne seulement : positions d'origine pendant les fenêtres modifiées."""
    notes = json.loads((PRIVATE / "demo_notes.json").read_text())
    fg = folium.FeatureGroup("Vérité terrain (interne)", show=True)
    for x, n in notes.items():
        t = pl.read_parquet(PRIVATE / "demo_truth" / f"boat_{x}.parquet").filter(pl.col("in_window"))
        coords = list(zip(t["lat"].to_list(), t["lon"].to_list()))
        folium.PolyLine(coords, color="#c1121f", weight=4, opacity=0.9, dash_array="6 4",
                        tooltip=f"Trajet modifié ({x}) : positions réelles d'origine").add_to(fg)
        lat, lon = coords[len(coords) // 2]
        folium.Marker([lat, lon], icon=folium.DivIcon(html=(
            f"<div style='background:#c1121f;color:#fff;font:600 12px system-ui;padding:2px 6px;border-radius:4px;"
            f"white-space:nowrap'>Trajet modifié ({x}) · {n['display_id']}</div>"))).add_to(fg)
    fg.add_to(m)
    m.get_root().html.add_child(Element(
        "<div style='position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:1500;background:#c1121f;"
        "color:#fff;padding:6px 12px;border-radius:4px;font:600 13px system-ui'>Carte interne : contient les trajets "
        "modifiés et leur vérité terrain. Ne pas diffuser.</div>"))


if __name__ == "__main__":
    main()
