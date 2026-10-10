"""Étape 2 : polygones des zones de pêche restreintes du Kattegat.

Source juridique : règlement délégué (UE) 2022/952 de la Commission
(https://eur-lex.europa.eu/eli/reg_del/2022/952/oj).

Entrée attendue : data/reference/source/zones_coordinates.csv, transcription
des tableaux de coordonnées des annexes, une ligne par sommet :

  zone_id,zone_name,country,zone_type,restriction,source,vertex,lat,lon

- zone_type : MSFD, NATURA2000, ... (le tampon de 4 NM ne s'applique qu'aux
  zones country=DK et zone_type=MSFD)
- lat/lon : degrés décimaux ou DMS tels qu'écrits dans l'annexe
  (ex. 56°55,23′N, 11°45′30″E) ; l'ordre des sommets est celui de l'annexe.
- source : référence précise (annexe, point, page).

Le script ne devine aucune coordonnée : sans ce fichier, il s'arrête.

Sorties :
  data/reference/kattegat_zones.geojson           zones, WGS84
  data/reference/kattegat_zones_buffers.geojson   zones + anneaux de 4 NM, WGS84
  reports/zones_check.png                         contrôle visuel
"""
import pathlib
import re
import sys

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.validation import explain_validity

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "reference" / "source" / "zones_coordinates.csv"
OUT = ROOT / "data" / "reference" / "kattegat_zones.geojson"
OUT_BUF = ROOT / "data" / "reference" / "kattegat_zones_buffers.geojson"
PNG = ROOT / "reports" / "zones_check.png"

PROJ_CRS = "EPSG:25832"  # ETRS89 / UTM 32N, en mètres
BUFFER_M = 4 * 1852  # 4 milles nautiques = 7 408 m
KATTEGAT = {"lat": (55.0, 58.0), "lon": (10.0, 13.0)}

DMS = re.compile(r"^\s*(\d+)\s*°\s*(?:(\d+(?:[.,]\d+)?)\s*[′']\s*)?(?:(\d+(?:[.,]\d+)?)\s*[″\"]\s*)?([NSEW])\s*$")


def to_decimal(v) -> float:
    s = str(v).strip()
    try:
        return float(s.replace(",", "."))
    except ValueError:
        pass
    m = DMS.match(s)
    if not m:
        raise ValueError(f"Coordonnée illisible : {s!r}")
    deg, minutes, seconds, hemi = m.groups()
    x = int(deg) + float((minutes or "0").replace(",", ".")) / 60 + float((seconds or "0").replace(",", ".")) / 3600
    return -x if hemi in "SW" else x


def build_polygons(df: pd.DataFrame) -> gpd.GeoDataFrame:
    rows, problems = [], []
    attrs = ["zone_id", "zone_name", "country", "zone_type", "restriction", "source"]
    for zid, g in df.sort_values(["zone_id", "vertex"]).groupby("zone_id", sort=False):
        meta = g[attrs].drop_duplicates()
        if len(meta) != 1:
            problems.append(f"{zid}: attributs incohérents entre sommets")
        coords = list(zip(g["lon_dd"], g["lat_dd"]))  # shapely attend (x=lon, y=lat)
        closed_in_source = coords[0] == coords[-1]
        if not closed_in_source:
            coords.append(coords[0])
        poly = Polygon(coords)
        out_of_box = [
            (la, lo) for lo, la in coords
            if not (KATTEGAT["lat"][0] <= la <= KATTEGAT["lat"][1] and KATTEGAT["lon"][0] <= lo <= KATTEGAT["lon"][1])
        ]
        if out_of_box:
            problems.append(f"{zid}: sommets hors du Kattegat {out_of_box} (inversion lat/lon ?)")
        if not poly.is_valid:
            problems.append(f"{zid}: géométrie invalide ({explain_validity(poly)})")
        rows.append({
            **meta.iloc[0].to_dict(),
            "n_vertices": len(g),
            "closed_in_source": closed_in_source,
            "orientation_ccw": poly.exterior.is_ccw,
            "is_valid": poly.is_valid,
            "geometry": poly,
        })
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    gdf["area_km2"] = gdf.to_crs(PROJ_CRS).area / 1e6
    if problems:
        print("PROBLÈMES À VÉRIFIER DANS LE TEXTE SOURCE :")
        for p in problems:
            print("  -", p)
        sys.exit("Arrêt : corriger la transcription plutôt que deviner.")
    return gdf


def buffers(zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    dk = zones[(zones.country == "DK") & (zones.zone_type == "MSFD")].to_crs(PROJ_CRS)
    if dk.empty:
        return dk.to_crs("EPSG:4326")
    zones_union = zones.to_crs(PROJ_CRS).union_all()
    ring = dk.buffer(BUFFER_M).union_all().difference(zones_union)
    buf = gpd.GeoDataFrame(
        [{
            "zone_id": "DK_MSFD_BUFFER_4NM",
            "zone_name": "Tampon de 4 NM autour des zones MSFD danoises",
            "country": "DK",
            "zone_type": "BUFFER",
            "restriction": "Chalutiers de fond : AIS de classe A obligatoire",
            "source": f"Calcul : tampon {BUFFER_M} m en {PROJ_CRS}, moins les zones elles-mêmes",
            "geometry": ring,
        }],
        crs=PROJ_CRS,
    )
    buf["area_km2"] = buf.area / 1e6
    return buf.to_crs("EPSG:4326")


def plot(zones, buf):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 8))
    land = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"
    if land.exists():
        gpd.read_file(land, bbox=(9.5, 54.5, 13.5, 58.5)).clip((9.5, 54.5, 13.5, 58.5)).plot(ax=ax, color="#ddd", edgecolor="#999", lw=0.4)
    if not buf.empty:
        buf.plot(ax=ax, color="#f4a261", alpha=0.4)
    zones.plot(ax=ax, column="country", alpha=0.6, edgecolor="k", legend=True)
    for _, r in zones.iterrows():
        p = r.geometry.representative_point()
        ax.annotate(r.zone_id, (p.x, p.y), fontsize=7, ha="center")
    ax.set_xlim(9.5, 13.5)
    ax.set_ylim(54.5, 58.5)
    ax.set_title("Zones restreintes Kattegat (contrôle visuel)")
    PNG.parent.mkdir(exist_ok=True)
    fig.savefig(PNG, dpi=130, bbox_inches="tight")


def main():
    if not SRC.exists():
        sys.exit(
            f"{SRC.relative_to(ROOT)} absent.\n"
            "Transcrire les coordonnées des annexes du règlement délégué (UE) 2022/952 "
            "(voir l'en-tête de ce script). Aucune coordonnée n'est inventée."
        )
    df = pd.read_csv(SRC, dtype=str)
    df["vertex"] = df["vertex"].astype(int)
    df["lat_dd"] = df["lat"].map(to_decimal)
    df["lon_dd"] = df["lon"].map(to_decimal)
    zones = build_polygons(df)
    buf = buffers(zones)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    zones.to_file(OUT, driver="GeoJSON")
    pd.concat([zones, buf], ignore_index=True).pipe(gpd.GeoDataFrame, crs="EPSG:4326").to_file(OUT_BUF, driver="GeoJSON")
    plot(zones, buf)
    print(zones.drop(columns="geometry").to_string())
    print(f"[out ] {OUT.relative_to(ROOT)}, {OUT_BUF.relative_to(ROOT)}, {PNG.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
