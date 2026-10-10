"""Étape 2 bis : vérification croisée avec les sites Natura 2000 et les aires HELCOM.

Entrées (fichiers officiels déposés à la main, domaines bloqués dans
l'environnement de développement) :
  data/raw/protected/Natura2000_end2024*.gpkg   EEA, « Natura 2000 (vector) - version end 2024 »
                                                https://www.eea.europa.eu/data-and-maps/data/natura-12
  data/raw/protected/HELCOM_MPAs*.{zip,shp,gpkg} HELCOM, jeu « HELCOM MPAs »
                                                https://metadata.helcom.fi/geonetwork/srv/api/records/d27df8c0-de86-4d13-a06d-35a8f50b16fa

Seule l'emprise du Kattegat est lue (filtre bbox à la lecture). Sorties :
  data/reference/natura2000_kattegat.geojson, data/reference/helcom_mpa_kattegat.geojson
  reports/protected_sites_check.md
Une source absente est signalée, jamais remplacée.
"""
import pathlib

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "protected"
REF = ROOT / "data" / "reference"
ZONES = REF / "kattegat_zones.geojson"
REPORT = ROOT / "reports" / "protected_sites_check.md"
BBOX = (10.0, 55.0, 13.0, 58.0)
PROJ_CRS = "EPSG:25832"
MIN_SHARE = 0.5  # part minimale d'une zone couverte par un site pour la dire « dans un site »

SOURCES = {
    "natura2000": {"glob": "Natura2000_end2024*.gpkg", "out": REF / "natura2000_kattegat.geojson",
                   "label": "Natura 2000 (EEA, fin 2024)", "layer_hint": "NaturaSite_polygon",
                   "code": "SITECODE", "name": "SITENAME"},
    "helcom": {"glob": "HELCOM_MPA*", "out": REF / "helcom_mpa_kattegat.geojson",
               "label": "Aires protégées HELCOM", "layer_hint": None, "code": "MPA_ID", "name": "Name"},
}


def load_source(key: str) -> gpd.GeoDataFrame | None:
    s = SOURCES[key]
    if s["out"].exists():
        return gpd.read_file(s["out"])
    files = sorted(RAW.glob(s["glob"]))
    if not files:
        return None
    f = files[0]
    path = f"zip://{f}" if f.suffix == ".zip" else str(f)
    kw = {}
    if s["layer_hint"] and f.suffix == ".gpkg":
        import pyogrio
        layers = [name for name, _ in pyogrio.list_layers(f)]
        kw["layer"] = s["layer_hint"] if s["layer_hint"] in layers else layers[0]
    bb = gpd.GeoSeries([box(*BBOX)], crs="EPSG:4326")
    gdf = gpd.read_file(path, bbox=bb, **kw)  # geopandas reprojette la bbox dans le CRS du fichier
    if gdf.crs is None:
        raise SystemExit(f"{f.name} : système de coordonnées absent, impossible de vérifier.")
    gdf = gdf.to_crs("EPSG:4326").clip(BBOX)
    invalid = int((~gdf.is_valid).sum())
    if invalid:
        print(f"[warn] {f.name} : {invalid} géométries invalides, réparées par make_valid (signalé dans le rapport)")
        gdf["geometry"] = gdf.make_valid()
    gdf.attrs["invalid_repaired"] = invalid
    keep = [c for c in (s["code"], s["name"]) if c in gdf.columns]
    gdf = gdf[keep + ["geometry"]].assign(source_file=f.name)
    gdf.to_file(s["out"], driver="GeoJSON")
    return gdf


def main():
    zones = gpd.read_file(ZONES)
    reg = zones[zones.layer == "zone"].to_crs(PROJ_CRS)
    lines = ["# Vérification croisée : zones du règlement et sites protégés connus", ""]
    loaded = {}
    for key, s in SOURCES.items():
        g = load_source(key)
        if g is None:
            lines.append(f"- **{s['label']}** : fichier absent dans `data/raw/protected/` (`{s['glob']}`). "
                         "Vérification non faite.")
            print(f"[miss] {s['label']} : déposer {s['glob']} dans data/raw/protected/")
        else:
            loaded[key] = g.to_crs(PROJ_CRS)
            lines.append(f"- **{s['label']}** : {len(g)} sites dans le rectangle Kattegat "
                         f"(`{s["out"].name}`).")
    if loaded:
        rows = []
        for r in reg.itertuples():
            row = {"zone_id": r.zone_id, "category": r.category}
            in_any = False
            for key, g in loaded.items():
                inter = g[g.intersects(r.geometry)]
                share = inter.intersection(r.geometry).union_all().area / r.geometry.area if len(inter) else 0
                codes = ", ".join(sorted(inter[SOURCES[key]["code"]].astype(str))) if len(inter) else "-"
                row[f"{key} : part couverte"] = f"{share:.0%}"
                row[f"{key} : sites"] = codes
                in_any |= share >= MIN_SHARE
            row["dans un site connu"] = "oui" if in_any else "**NON**"
            rows.append(row)
        df = pd.DataFrame(rows)
        lines += ["", f"Une zone est « dans un site connu » si au moins {MIN_SHARE:.0%} de sa surface est couverte.", "",
                  "| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
        lines += ["| " + " | ".join(str(v) for v in t) + " |" for t in df.itertuples(index=False)]
        missing = df[df["dans un site connu"] != "oui"]
        print(f"[chk ] {len(missing)} zones du règlement hors de tout site protégé connu")
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print(f"[out ] {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
