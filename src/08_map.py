"""Étape 4 : carte Folium des trajets de pêche et des zones restreintes du Kattegat.

Paramètres ci-dessous. Les trajets (06) et le croisement (07) sont recalculés
seulement si ces paramètres changent ; sinon les fichiers existants sont relus.

Sorties :
  reports/map.html             carte principale (couches activables)
  reports/map_animation.html   trajets animés dans le temps (page séparée : le
                               plugin TimestampedGeoJson ne s'active pas via LayerControl)
"""
# --- Paramètres ------------------------------------------------------------------
PERIOD_START = "2026-09-15"  # période affichée (incluse, UTC)
PERIOD_END = "2026-09-17"
GAP_MINUTES = 30  # seuil de trou : au-delà, le trajet est coupé
SIMPLIFY_M = 30  # tolérance de simplification des lignes (m, EPSG:25832)
ANIMATION = True  # produire reports/map_animation.html
ANIM_STEP_MIN = 10  # une position animée toutes les N minutes par trajet
# ---------------------------------------------------------------------------------

import importlib.util  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

import folium  # noqa: E402
import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
from branca.element import Element  # noqa: E402
from folium.plugins import TimestampedGeoJson  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
REPORTS = ROOT / "reports"
TILES = "Esri.WorldGrayCanvas"  # fond clair et neutre, sans clé d'API
CENTER, ZOOM = (56.75, 11.5), 8

COLORS = {
    "zone": "#d62828",  # zones restreintes : rouge
    "buffer": "#f4a261",  # tampons : orange clair
    "ais_zone": "#2a78d6",  # zones AIS officielles : contour bleu
    "natura2000": "#2a9d8f",
    "helcom": "#7b5ea7",
    "track": "#3d5a80",  # trajets : bleu-gris discret
    "portion_zone": "#ff00a8",  # portions dans une zone : magenta vif
    "portion_buffer": "#ffb000",  # portions dans un tampon : jaune vif
}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt_min(m: float) -> str:
    if m is None or pd.isna(m) or m <= 0:
        return "0"
    return f"{m:.0f} min" if m < 120 else f"{m / 60:.1f} h"


def zones_layer(m, zones):
    z = zones[zones.layer == "zone"].copy()
    z["opacity"] = z.category.map({"Areas 1": 0.35, "Areas 2": 0.55, "Areas 3": 0.2})
    fg = folium.FeatureGroup("1. Zones restreintes (règlement 2022/952)", show=True)
    folium.GeoJson(
        z[["zone_id", "name", "country", "category", "restriction", "legal_ref", "area_km2", "opacity", "geometry"]],
        style_function=lambda f: {"color": COLORS["zone"], "weight": 1.5, "fillColor": COLORS["zone"],
                                  "fillOpacity": f["properties"]["opacity"]},
        tooltip=folium.GeoJsonTooltip(["zone_id", "name", "country", "category", "restriction", "legal_ref", "area_km2"],
                                      aliases=["Zone", "Nom", "Pays", "Catégorie", "Restriction", "Source", "km²"]),
    ).add_to(fg)
    fg.add_to(m)


def buffers_layer(m, zones):
    b = zones[zones.layer == "buffer"]
    fg = folium.FeatureGroup("2. Tampons 4 NM autour des zones danoises (calculés)", show=True)
    folium.GeoJson(
        b[["zone_id", "name", "restriction", "area_km2", "geometry"]],
        style_function=lambda f: {"color": COLORS["buffer"], "weight": 1, "fillColor": COLORS["buffer"],
                                  "fillOpacity": 0.25},
        tooltip=folium.GeoJsonTooltip(["zone_id", "name", "restriction", "area_km2"],
                                      aliases=["Tampon", "Nom", "Hypothèse", "km²"]),
    ).add_to(fg)
    fg.add_to(m)
    a = zones[zones.layer == "ais_zone"]
    fg = folium.FeatureGroup("2 bis. Zones AIS officielles (annexe V)", show=False)
    folium.GeoJson(
        a[["zone_id", "name", "restriction", "legal_ref", "geometry"]],
        style_function=lambda f: {"color": COLORS["ais_zone"], "weight": 1.5, "dashArray": "6 4", "fillOpacity": 0},
        tooltip=folium.GeoJsonTooltip(["zone_id", "name", "restriction", "legal_ref"],
                                      aliases=["Zone AIS", "Nom", "Obligation", "Source"]),
    ).add_to(fg)
    fg.add_to(m)


def protected_layers(m) -> list[str]:
    missing = []
    for key, label, color, code, name in [
        ("natura2000_kattegat", "3. Sites Natura 2000 (EEA fin 2024)", COLORS["natura2000"], "SITECODE", "SITENAME"),
        ("helcom_mpa_kattegat", "4. Aires protégées HELCOM", COLORS["helcom"], "MPA_ID", "Name"),
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


def tracks_layer(m, tracks):
    t = tracks.copy()
    t["sog"] = t.sog_mean_kn.map(lambda v: f"{v:.1f} nd" if pd.notna(v) else "-")
    t["longueur"] = t.length_m.map(lambda v: f"{v:.0f} m" if pd.notna(v) else "inconnue")
    t["dans_zone"] = t.minutes_in_zone.map(fmt_min)
    t["dans_tampon"] = t.minutes_in_buffer.map(fmt_min)
    t["periode"] = t.start.dt.strftime("%d/%m %H:%M") + " → " + t.end.dt.strftime("%d/%m %H:%M")
    fg = folium.FeatureGroup("5. Trajets des navires de pêche (classe A)", show=True)
    folium.GeoJson(
        t[["track_id", "mmsi", "name", "sog", "longueur", "periode", "n_points", "dans_zone", "dans_tampon", "geometry"]],
        style_function=lambda f: {"color": COLORS["track"], "weight": 1.5, "opacity": 0.6},
        highlight_function=lambda f: {"weight": 4, "opacity": 1},
        tooltip=folium.GeoJsonTooltip(
            ["mmsi", "name", "sog", "longueur", "periode", "n_points", "dans_zone", "dans_tampon"],
            aliases=["MMSI", "Nom", "Vitesse moyenne", "Longueur", "Période", "Points", "Durée dans une zone",
                     "Durée dans un tampon"]),
    ).add_to(fg)
    fg.add_to(m)


def portions_layer(m, portions, per_zone):
    det = (per_zone.group_by(["track_id", "layer"])
           .agg(pl.col("zone_id").unique().sort().str.join(", ").alias("zones"),
                pl.col("sog_mean_in_kn").mean().alias("sog_in"),
                pl.col("share_engaged_fishing_in").mean().alias("engaged")).to_pandas())
    p = portions.merge(det, on=["track_id", "layer"], how="left")
    p["mmsi"] = p.track_id.str.split("-").str[0]
    p["type"] = p.layer.map({"zone": "zone restreinte", "buffer": "tampon 4 NM"})
    p["sog"] = p.sog_in.map(lambda v: f"{v:.1f} nd" if pd.notna(v) else "-")
    p["engaged_txt"] = p.engaged.map(lambda v: f"{v:.0%}" if pd.notna(v) else "-")
    fg = folium.FeatureGroup("6. Portions de trajet dans une zone ou un tampon", show=True)
    folium.GeoJson(
        p[["track_id", "mmsi", "type", "zones", "duree", "sog", "engaged_txt", "layer", "geometry"]],
        style_function=lambda f: {"color": COLORS["portion_zone"] if f["properties"]["layer"] == "zone"
                                  else COLORS["portion_buffer"], "weight": 3.5, "opacity": 0.95},
        tooltip=folium.GeoJsonTooltip(
            ["mmsi", "type", "zones", "duree", "sog", "engaged_txt"],
            aliases=["MMSI", "Portion dans", "Zones", "Durée dans cette couche", "Vitesse moyenne dedans",
                     "Messages « Engaged in fishing »"]),
    ).add_to(fg)
    fg.add_to(m)


def legend(m, missing: list[str], meta: dict, n_tracks_in: int):
    items = [
        (COLORS["zone"], "fill", "Zone restreinte (2022/952)"),
        (COLORS["buffer"], "fill", "Tampon 4 NM (calculé)"),
        (COLORS["ais_zone"], "dash", "Zone AIS officielle (annexe V)"),
        (COLORS["natura2000"], "fill", "Natura 2000"),
        (COLORS["helcom"], "fill", "Aire HELCOM"),
        (COLORS["track"], "line", "Trajet de pêche (classe A)"),
        (COLORS["portion_zone"], "line", "Portion dans une zone"),
        (COLORS["portion_buffer"], "line", "Portion dans un tampon"),
    ]

    def swatch(c, kind):
        if kind == "fill":
            return f'<span style="display:inline-block;width:14px;height:10px;background:{c}66;border:1.5px solid {c}"></span>'
        if kind == "dash":
            return f'<span style="display:inline-block;width:16px;border-top:2px dashed {c};vertical-align:middle"></span>'
        return f'<span style="display:inline-block;width:16px;border-top:3px solid {c};vertical-align:middle"></span>'

    absent = {"Natura 2000": "Natura 2000", "Aire HELCOM": "HELCOM"}
    items = [it for it in items if not any(absent.get(it[2], "@") in x for x in missing)]
    rows = "".join(f"<div>{swatch(c, k)}&nbsp;{label}</div>" for c, k, label in items)
    miss = ("<div style='margin-top:6px;color:#9a3412'>Non disponible : " + "; ".join(missing) + "</div>") if missing else ""
    anim = "<div style='margin-top:4px'><a href='map_animation.html' target='_blank'>Trajets animés (page séparée)</a></div>" if ANIMATION else ""
    html = f"""
    <div style="position:fixed;bottom:28px;left:10px;z-index:9999;background:#fff;padding:8px 10px;
         border:1px solid #c9c7c0;border-radius:4px;font:12px/1.5 system-ui,sans-serif;color:#1f1f1d;max-width:290px">
      <b>SeaTrace - Kattegat</b><br>
      {PERIOD_START} → {PERIOD_END} (UTC) · trou &gt; {GAP_MINUTES} min · simplification {SIMPLIFY_M} m<br>
      {meta['segments_kept']} trajets, {meta['vessels']} navires · {n_tracks_in} trajets avec une portion dans une zone ou un tampon
      <div style="margin-top:6px">{rows}</div>{miss}{anim}
      <div style="margin-top:6px;color:#55534e">Une portion dans une zone n'est pas une infraction :
      un navire en transit, engins relevés, est en règle.</div>
    </div>"""
    m.get_root().html.add_child(Element(html))


def animation(tracks_points: pl.DataFrame, zones: gpd.GeoDataFrame, out: pathlib.Path):
    """Positions échantillonnées toutes les ANIM_STEP_MIN minutes, une ligne par trajet."""
    s = (tracks_points.with_columns(pl.col("timestamp").dt.truncate(f"{ANIM_STEP_MIN}m").alias("bucket"))
         .group_by(["track_id", "bucket"], maintain_order=True).first()
         .sort(["track_id", "timestamp"]))
    feats = []
    for (tid,), g in s.group_by(["track_id"], maintain_order=True):
        if g.height < 2:
            continue
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(x, 5), round(y, 5)] for x, y in zip(g["lon"], g["lat"])]},
                      "properties": {"times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in g["timestamp"]],
                                     "popup": f"MMSI {tid.split('-')[0]}",
                                     "style": {"color": COLORS["track"], "weight": 2, "opacity": 0.8}}})
    m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=TILES, prefer_canvas=True, control_scale=True)
    for layer, color, op in [("buffer", COLORS["buffer"], 0.2), ("zone", COLORS["zone"], 0.35)]:
        folium.GeoJson(zones[zones.layer == layer][["zone_id", "geometry"]],
                       style_function=lambda f, c=color, o=op: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": o},
                       tooltip=folium.GeoJsonTooltip(["zone_id"], aliases=["Zone"])).add_to(m)
    TimestampedGeoJson({"type": "FeatureCollection", "features": feats}, period=f"PT{ANIM_STEP_MIN}M",
                       duration="PT2H", add_last_point=True, auto_play=False, loop=False,
                       date_options="YYYY-MM-DD HH:mm", transition_time=100).add_to(m)
    m.save(out)
    return len(feats), int(s.height)


def main():
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
    portions["duree"] = portions.minutes.map(fmt_min)

    zones = gpd.read_file(REF / "kattegat_zones.geojson")
    if zones.crs is None or zones.crs.to_epsg() != 4326:
        raise SystemExit(f"kattegat_zones.geojson : CRS inattendu {zones.crs}")
    if not zones.is_valid.all():
        raise SystemExit("kattegat_zones.geojson contient des géométries invalides : relancer 03_zones.py.")

    m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=None, prefer_canvas=True, control_scale=True)
    folium.TileLayer(TILES, name="Fond de carte gris clair (Esri)").add_to(m)
    zones_layer(m, zones)
    buffers_layer(m, zones)
    missing = protected_layers(m)
    tracks_layer(m, tracks)
    portions_layer(m, portions, per_zone)
    n_in = int(((tracks.minutes_in_zone > 0) | (tracks.minutes_in_buffer > 0)).sum())
    legend(m, missing, meta, n_in)
    folium.LayerControl(collapsed=False).add_to(m)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "map.html"
    m.save(out)
    print(f"[out ] {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} Mo)")
    if missing:
        print("[miss] couches absentes :", "; ".join(missing))
    if ANIMATION:
        pts = pl.read_parquet(d / f"points_{tag}.parquet")
        aout = REPORTS / "map_animation.html"
        n, k = animation(pts, zones, aout)
        print(f"[out ] {aout.relative_to(ROOT)} ({aout.stat().st_size / 1e6:.1f} Mo, {n} trajets, {k} positions)")


if __name__ == "__main__":
    main()
