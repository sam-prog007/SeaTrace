"""Étape 3 : rapport de qualité des données AIS du Kattegat.

Lit data/processed/ais_kattegat_<YYYY-MM>.parquet, les compteurs de
data/interim/stats_*.json et, s'ils existent, les polygones de
data/reference/. Écrit :

  reports/data_quality.md
  reports/coverage_map.png

Usage : python src/04_report.py --month 2026-09
"""
import argparse
import datetime as dt
import json
import pathlib

import geopandas as gpd
import matplotlib
import matplotlib.ticker
import numpy as np
import polars as pl
import requests
from pyproj import Transformer

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LogNorm  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
REF = ROOT / "data" / "reference"
REPORTS = ROOT / "reports"
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_land.geojson"
LAND = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"

CELL_M = 2000  # taille de cellule de la carte de couverture
PROJ_CRS = "EPSG:25832"
VIEW = (9.8, 54.9, 13.2, 58.1)  # lon_min, lat_min, lon_max, lat_max
# Rampe séquentielle bleue (clair = peu de positions, foncé = beaucoup).
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
FIXED_STATIONS = ["Base Station", "AtoN"]
LENGTH_BINS = [(None, 12, "< 12 m"), (12, 15, "12-15 m"), (15, 18, "15-18 m"), (18, 24, "18-24 m"),
               (24, 40, "24-40 m"), (40, None, ">= 40 m")]


def md_table(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.iter_rows():
        lines.append("| " + " | ".join(f"{v:,}" if isinstance(v, int) else str(v) for v in row) + " |")
    return "\n".join(lines)


def fmt_s(s) -> str:
    if s is None:
        return "-"
    s = float(s)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.1f} min"
    return f"{s / 3600:.1f} h"


def ensure_land() -> gpd.GeoDataFrame | None:
    if not LAND.exists():
        try:
            LAND.parent.mkdir(parents=True, exist_ok=True)
            r = requests.get(LAND_URL, timeout=120)
            r.raise_for_status()
            LAND.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"[warn] fond de carte indisponible : {e}")
            return None
    return gpd.read_file(LAND, bbox=VIEW).clip(VIEW)


def load_zones():
    z = REF / "kattegat_zones_buffers.geojson"
    return gpd.read_file(z) if z.exists() else None


def cleaning_section(month: str) -> tuple[str, dict]:
    stats = [json.loads(p.read_text()) for p in sorted(INTERIM.glob(f"stats_{month}-*.json"))]
    keys = ["rows_read", "position_unavailable_set_null", "sog_unavailable_or_out_of_range_set_null",
            "cog_unavailable_or_out_of_range_set_null", "heading_unavailable_or_out_of_range_set_null",
            "rows_null_position_dropped", "rows_outside_bbox_dropped", "rows_in_bbox",
            "exact_duplicates_dropped", "mmsi_not_9_digits_flagged", "rows_written",
            "rows_timestamp_outside_day"]
    action = {
        "rows_read": "lignes lues (fichier complet, tout le Danemark)",
        "position_unavailable_set_null": "lat = 91 / lon = 181 ou hors plage -> lat/lon mis à null",
        "sog_unavailable_or_out_of_range_set_null": "SOG > 102,2 nd (102,3 = non dispo) -> null",
        "cog_unavailable_or_out_of_range_set_null": "COG >= 360 -> null",
        "heading_unavailable_or_out_of_range_set_null": "Heading > 359 (511 = non dispo) -> null",
        "rows_null_position_dropped": "sans position -> retirées (non localisables)",
        "rows_outside_bbox_dropped": "hors rectangle 55-58 N / 10-13 E -> retirées",
        "rows_in_bbox": "dans le rectangle",
        "exact_duplicates_dropped": "doublons exacts (toutes colonnes) dans le rectangle -> retirés",
        "mmsi_not_9_digits_flagged": "MMSI sans 9 chiffres -> gardées, mmsi_is_9_digits = false",
        "rows_written": "lignes écrites dans le Parquet",
        "rows_timestamp_outside_day": "horodatage hors du jour du fichier",
    }
    rows = []
    for k in keys:
        rows.append({"catégorie": k, "traitement": action[k], **{s["day"]: s.get(k, 0) for s in stats},
                     "total": sum(s.get(k, 0) for s in stats)})
    days = [s["day"] for s in stats]
    dup = sum(s["exact_duplicates_dropped"] for s in stats) / sum(s["rows_in_bbox"] for s in stats)
    return md_table(pl.DataFrame(rows)), {"days": days, "files": [s["source_file"] for s in stats], "dup_share": dup}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--month", required=True, help="YYYY-MM")
    a = p.parse_args()
    REPORTS.mkdir(exist_ok=True)
    src = PROCESSED / f"ais_kattegat_{a.month}.parquet"
    lf = pl.scan_parquet(str(src / "**" / "*.parquet"), hive_partitioning=True)

    clean_md, meta = cleaning_section(a.month)

    # --- Volumes ---------------------------------------------------------------
    by_type = (lf.group_by("mobile_type").agg(pl.len().alias("messages"), pl.col("mmsi").n_unique().alias("mmsi_uniques"))
               .sort("messages", descending=True).collect())
    tot = lf.select(pl.len().alias("messages"), pl.col("mmsi").n_unique().alias("mmsi_uniques")).collect().row(0, named=True)
    cla = lf.filter(pl.col("is_class_a")).select(pl.len().alias("messages"), pl.col("mmsi").n_unique().alias("mmsi_uniques")).collect().row(0, named=True)
    mixed = lf.group_by("mmsi").agg(pl.col("mobile_type").n_unique().alias("k")).filter(pl.col("k") > 1).select(pl.len()).collect().item()

    # --- Navires candidats pêche -------------------------------------------------
    vessels = (lf.group_by("mmsi").agg(
        pl.col("is_fishing_candidate").first(), pl.col("vessel_length_m").first(),
        pl.col("vessel_ship_type").first(), pl.col("vessel_ship_type_n_distinct").first(),
        pl.col("is_class_a").any().alias("any_class_a"), pl.col("is_class_a").all().alias("all_class_a"),
        pl.len().alias("n_msg"), (pl.col("nav_status") == "Engaged in fishing").sum().alias("n_engaged_fishing"),
    ).collect())
    fish = vessels.filter(pl.col("is_fishing_candidate"))
    rows = []
    for lo, hi, label in LENGTH_BINS:
        m = pl.lit(True)
        if lo is not None:
            m = m & (pl.col("vessel_length_m") >= lo)
        if hi is not None:
            m = m & (pl.col("vessel_length_m") < hi)
        sub = fish.filter(m)
        rows.append({"longueur": label, "navires": sub.height,
                     "dont classe A": sub.filter(pl.col("any_class_a")).height,
                     "dont classe B uniquement": sub.filter(~pl.col("any_class_a")).height})
    sub = fish.filter(pl.col("vessel_length_m").is_null())
    rows.append({"longueur": "inconnue", "navires": sub.height,
                 "dont classe A": sub.filter(pl.col("any_class_a")).height,
                 "dont classe B uniquement": sub.filter(~pl.col("any_class_a")).height})
    fish_len = pl.DataFrame(rows)
    engaged_not_fishing = vessels.filter(~pl.col("is_fishing_candidate") & (pl.col("n_engaged_fishing") > 0))
    no_type = vessels.filter(pl.col("vessel_ship_type").is_null()).height
    multi_type = vessels.filter(pl.col("vessel_ship_type_n_distinct") > 1).height

    # --- Carte de couverture ---------------------------------------------------
    pos = lf.filter(~pl.col("mobile_type").is_in(FIXED_STATIONS)).select("lon", "lat").collect()
    tr = Transformer.from_crs("EPSG:4326", PROJ_CRS, always_xy=True)
    x, y = tr.transform(pos["lon"].to_numpy(), pos["lat"].to_numpy())
    (x0, y0), (x1, y1) = [tr.transform(VIEW[0], VIEW[1]), tr.transform(VIEW[2], VIEW[3])]
    # Le rectangle en degrés n'est pas un rectangle en UTM : on prend l'enveloppe.
    xs, ys = tr.transform([VIEW[0], VIEW[0], VIEW[2], VIEW[2]], [VIEW[1], VIEW[3], VIEW[1], VIEW[3]])
    xb = np.arange(np.floor(min(xs) / CELL_M) * CELL_M, max(xs) + CELL_M, CELL_M)
    yb = np.arange(np.floor(min(ys) / CELL_M) * CELL_M, max(ys) + CELL_M, CELL_M)
    H, _, _ = np.histogram2d(x, y, bins=[xb, yb])
    n_cells_pos = int((H > 0).sum())
    q = np.percentile(H[H > 0], [50, 90, 99])

    fig, ax = plt.subplots(figsize=(8, 9))
    land = ensure_land()
    land_proj = land.to_crs(PROJ_CRS) if land is not None else None
    cmap = LinearSegmentedColormap.from_list("blues", BLUES)
    Hm = np.ma.masked_equal(H.T, 0)
    mesh = ax.pcolormesh(xb, yb, Hm, cmap=cmap, norm=LogNorm(vmin=1, vmax=H.max()), shading="flat")
    if land_proj is not None:
        land_proj.plot(ax=ax, color="#e8e6e1", edgecolor="#9a988f", lw=0.4, zorder=2)
    zones = load_zones()
    if zones is not None:
        zp = zones.to_crs(PROJ_CRS)
        zp[zp.zone_type == "BUFFER"].boundary.plot(ax=ax, color="#c2410c", lw=1, ls="--", zorder=3)
        zp[zp.zone_type != "BUFFER"].boundary.plot(ax=ax, color="#c2410c", lw=1.6, zorder=3)
    # Rectangle de filtrage, en pointillés gris.
    bx, by = tr.transform([10, 13, 13, 10, 10], [55, 55, 58, 58, 55])
    ax.plot(bx, by, color="#55534e", lw=0.8, ls=":", zorder=3)
    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_aspect("equal")
    km = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:.0f}")
    ax.xaxis.set_major_formatter(km)
    ax.yaxis.set_major_formatter(km)
    ax.set_xlabel("x (km, EPSG:25832)", color="#55534e")
    ax.set_ylabel("y (km, EPSG:25832)", color="#55534e")
    ax.tick_params(colors="#55534e", labelsize=7)
    for s in ax.spines.values():
        s.set_color("#c9c7c0")
    title = f"Positions AIS reçues par cellule de {CELL_M / 1000:.0f} km - {meta['days'][0]} au {meta['days'][-1]}"
    ax.set_title(title, fontsize=10, loc="left")
    cb = fig.colorbar(mesh, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label("positions par cellule (échelle log)", fontsize=8)
    cb.ax.tick_params(labelsize=7)
    note = "Zones : " + ("trait plein = zones, tirets = tampon 4 NM" if zones is not None else "non disponibles (voir Limites)")
    ax.text(0.01, 0.01, note + " ; pointillés = rectangle de filtrage", transform=ax.transAxes, fontsize=7, color="#55534e")
    png = REPORTS / "coverage_map.png"
    fig.savefig(png, dpi=140, bbox_inches="tight")
    plt.close(fig)

    # --- Intervalles entre messages par navire -----------------------------------
    gaps = (lf.filter(~pl.col("mobile_type").is_in(FIXED_STATIONS) & pl.col("mmsi_is_9_digits"))
            .sort(["mmsi", "timestamp"])
            .with_columns(pl.col("timestamp").diff().over("mmsi").dt.total_seconds().alias("dt_s"))
            .filter(pl.col("dt_s").is_not_null())
            .select("mmsi", "dt_s", "is_class_a", "is_fishing_candidate", "mobile_type")
            .collect())

    def gap_stats(df: pl.DataFrame, label: str) -> dict:
        d = df["dt_s"]
        return {
            "groupe": label, "intervalles": df.height, "navires": df["mmsi"].n_unique(),
            "= 0 s": f"{(d == 0).mean():.1%}",
            "médiane": fmt_s(d.median()), "p90": fmt_s(d.quantile(0.90)), "p99": fmt_s(d.quantile(0.99)),
            "p99,9": fmt_s(d.quantile(0.999)), "max": fmt_s(d.max()),
            "> 10 min": f"{(d > 600).mean():.2%}", "> 1 h": f"{(d > 3600).mean():.3%}",
        }

    gap_rows = [
        gap_stats(gaps, "tous (mobiles)"),
        gap_stats(gaps.filter(pl.col("mobile_type") == "Class A"), "classe A"),
        gap_stats(gaps.filter(pl.col("mobile_type") == "Class B"), "classe B"),
        gap_stats(gaps.filter(pl.col("is_fishing_candidate") & (pl.col("mobile_type") == "Class A")), "pêche, classe A"),
        gap_stats(gaps.filter(pl.col("is_fishing_candidate") & (pl.col("mobile_type") == "Class B")), "pêche, classe B"),
    ]
    per_vessel = gaps.group_by("mmsi").agg(pl.col("dt_s").median().alias("med"), pl.col("dt_s").max().alias("mx"))
    pv = per_vessel.select(pl.col("med").median().alias("médiane des médianes par navire (s)"),
                           pl.col("mx").median().alias("médiane des plus grands trous par navire (s)"),
                           (pl.col("mx") > 3600).sum().alias("navires avec au moins un trou > 1 h"))

    # --- Rectangle affiné d'après les polygones ----------------------------------
    if zones is not None:
        b = zones.total_bounds
        refine = (f"Enveloppe des zones et tampons : {b[1]:.3f}-{b[3]:.3f} N, {b[0]:.3f}-{b[2]:.3f} E. "
                  "Rectangle affiné proposé : cette enveloppe plus une marge de 0,1°.")
    else:
        refine = ("Non fait : les polygones ne sont pas disponibles (accès à EUR-Lex bloqué, voir Limites). "
                  "Le rectangle large 55-58 N / 10-13 E est conservé ; il déborde sur le Skagerrak oriental, "
                  "l'Øresund nord et le Grand Belt.")

    md = f"""# Rapport de qualité des données AIS - Kattegat

Généré le {dt.date.today().isoformat()} par `src/04_report.py --month {a.month}`.

- **Source** : Danish Emergency Management Agency / dépôt `aisdata.ais.dk` (bucket S3 public `s3.eu-central-1.amazonaws.com/aisdata.ais.dk`).
- **Fichiers** : {", ".join(meta["files"])}.
- **Période couverte** : {meta["days"][0]} au {meta["days"][-1]} ({len(meta["days"])} jours, pas le mois complet).
- **Rectangle** : 55,0-58,0 N, 10,0-13,0 E.
- **Jeu produit** : `data/processed/ais_kattegat_{a.month}.parquet/day=YYYY-MM-DD/`.

## 1. Nettoyage (compté, rien de retiré en silence)

{clean_md}

Les doublons exacts sont nombreux ({meta['dup_share']:.0%} des lignes du rectangle) : vraisemblablement le même message reçu par plusieurs stations côtières et non fusionné par la source (non vérifié auprès de la DMA).

Les valeurs « non disponible » 102,3 (SOG), 360 (COG) et 511 (Heading) sont **absentes du CSV source** : la source semble déjà les publier vides. On ne trouve que lat = 91 (avec lon = 0) et des valeurs hors plage (sur le 15/09 : SOG jusqu'à 168 nd, Heading jusqu'à 510), mises à null ci-dessus.

## 2. Messages et navires

| | messages | MMSI uniques |
|---|---|---|
| total | {tot["messages"]:,} | {tot["mmsi_uniques"]:,} |
| classe A (`Type of mobile` = « Class A ») | {cla["messages"]:,} | {cla["mmsi_uniques"]:,} |

Par type d'émetteur :

{md_table(by_type)}

{mixed} MMSI apparaissent sous plusieurs types d'émetteur (ex. A et B) : `is_class_a` est calculé **par message**, pas par navire.

## 3. Navires candidats pêche

`is_fishing_candidate` = le type de navire le plus fréquent déclaré par le MMSI (messages statiques, sur toute la période et tout le fichier) vaut « Fishing », libellé de la DMA pour le code AIS 30.

- **{fish.height} navires candidats pêche** dans le rectangle, dont {fish.filter(pl.col("any_class_a")).height} avec au moins un message de classe A et {fish.filter(~pl.col("any_class_a")).height} uniquement en classe B.
- {no_type} MMSI n'ont aucun type déclaré sur la période (ni pêche ni autre) ; {multi_type} MMSI déclarent plusieurs types.
- {fish.filter(pl.col("vessel_length_m") < 12).height} candidats pêche déclarent moins de 12 m, dont certains en classe A : longueur AIS à vérifier (champ souvent mal saisi).
- {engaged_not_fishing.height} MMSI non classés pêche émettent pourtant le statut « Engaged in fishing ».

Répartition par longueur déclarée (champ AIS `Length`, valeur la plus fréquente par MMSI) :

{md_table(fish_len)}

Rappel : l'AIS n'est obligatoire que pour les navires de pêche de plus de 15 m. Les navires plus petits sont donc en partie invisibles ; leur absence n'est pas une anomalie.

## 4. Carte de couverture

![Couverture]({png.name})

Positions des émetteurs mobiles (stations de base et balises AtoN exclues), après dédoublonnage, comptées par cellule de {CELL_M} m en EPSG:25832.

- {n_cells_pos:,} cellules avec au moins une position.
- Positions par cellule non vide : médiane {q[0]:.0f}, p90 {q[1]:.0f}, p99 {q[2]:.0f}.
- La densité mesure **trafic × réception** : une cellule vide peut être une zone sans navire autant qu'une zone mal couverte.

Rectangle affiné : {refine}

## 5. Intervalles entre messages par navire

Différence entre deux messages successifs d'un même MMSI, sur les messages dédoublonnés du rectangle, MMSI à 9 chiffres, stations fixes exclues.

{md_table(pl.DataFrame(gap_rows))}

{md_table(pv)}

Un grand intervalle peut venir d'un trou de signal, d'un AIS éteint, d'un navire **sorti du rectangle** puis revenu, ou d'un navire au port. Ces cas ne sont pas encore distingués. Les intervalles nuls correspondent à deux messages non identiques avec le même horodatage à la seconde près (quasi-doublons, gardés).

## 6. Limites

**Vérifié dans les données ou la source :**
- Le schéma : 26 colonnes, en-tête lu dans le CSV et conforme au README du dépôt (`!_README_information_CSV_files.txt`). Séparateur décimal : point.
- Les horodatages sont tous dans le jour du fichier (format `JJ/MM/AAAA HH:MM:SS`).
- Le type de navire est un libellé texte (« Fishing »), pas un code numérique, et il est souvent « Undefined » sur les messages de position. D'où la résolution par MMSI.
- Les MMSI de stations de base n'ont que 7 chiffres dans la source (zéros de tête absents). Ils sont lus en texte et signalés, pas corrigés.
- Le tampon de 4 NM (7 408 m) est calculé en EPSG:25832, et le calcul a été testé sur un carré de test.

**Supposé, non vérifié :**
- Les horodatages sont en UTC : le README dit seulement « Timestamp from the AIS basestation ».
- « Fishing » correspond au code AIS 30, d'après la table ITU-R M.1371 ; la table de correspondance de la DMA n'a pas été vue.
- `aisdata.ais.dk` est l'hébergement officiel actuel. La page dma.dk n'était pas accessible depuis l'environnement ; le dépôt porte le nom aisdata.ais.dk et sa page d'index y renvoie.
- Les doublons exacts viennent de la réception multi-stations.
- La longueur AIS déclarée est juste ; elle n'a pas été comparée à un registre.

**Non fait :**
- **Polygones des zones restreintes** : eur-lex.europa.eu, EMODnet et EEA sont bloqués par la politique réseau de l'environnement. `src/03_zones.py` est prêt mais attend la transcription des annexes ; aucune coordonnée n'a été inventée. La carte n'a donc pas de polygones superposés.
- La période ne couvre que 3 jours au lieu d'un mois (choix validé) : les statistiques de longueur et d'intervalles sont indicatives.
- Pas de contrôle de cohérence cinématique (sauts de position, vitesses implicites) : c'est le travail du détecteur, hors périmètre.
"""
    (REPORTS / "data_quality.md").write_text(md)
    print(f"[out ] reports/data_quality.md, {png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
