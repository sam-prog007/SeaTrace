"""Étape 1b : filtrage géographique et nettoyage des fichiers AIS journaliers.

Lit chaque ZIP de data/raw/ en flux (pyarrow, blocs de 64 Mo, jamais le fichier
entier en mémoire), applique le nettoyage documenté, garde les positions du
rectangle Kattegat et écrit :

  data/interim/day=YYYY-MM-DD.parquet       positions filtrées du jour
  data/interim/static_YYYY-MM-DD.parquet    infos statiques par MMSI (toute la zone DK)
  data/interim/stats_YYYY-MM-DD.json        compteurs de nettoyage du jour

puis, à chaque exécution, assemble le jeu final, limité aux navires de pêche
(type AIS « Fishing ») et à leurs messages de classe A, trié par MMSI et par
temps (un trajet par navire) :

  data/processed/ais_kattegat_<YYYY-MM>.parquet/day=YYYY-MM-DD/part-0.parquet

Un jour déjà présent dans data/interim/ n'est pas retraité.

Usage : python src/02_filter.py --start 2026-09-15 --end 2026-09-17 [--delete-raw]
"""
import argparse
import collections
import datetime as dt
import json
import pathlib
import shutil
import zipfile

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

# Rectangle large du Kattegat (à affiner avec les polygones de 03_zones.py).
BBOX = {"lat_min": 55.0, "lat_max": 58.0, "lon_min": 10.0, "lon_max": 13.0}

# Colonnes lues en texte pour ne rien perdre (zéros de tête des MMSI de
# stations de base, IMO "Unknown", etc.).
STRING_COLS = ["# Timestamp", "MMSI", "IMO", "Callsign", "Name", "ETA"]

RENAME = {
    "# Timestamp": "timestamp_raw",
    "Type of mobile": "mobile_type",
    "MMSI": "mmsi",
    "Latitude": "lat",
    "Longitude": "lon",
    "Navigational status": "nav_status",
    "ROT": "rot",
    "SOG": "sog",
    "COG": "cog",
    "Heading": "heading",
    "IMO": "imo",
    "Callsign": "callsign",
    "Name": "name",
    "Ship type": "ship_type",
    "Cargo type": "cargo_type",
    "Width": "width",
    "Length": "length",
    "Type of position fixing device": "epfd",
    "Draught": "draught",
    "Destination": "destination",
    "ETA": "eta",
    "Data source type": "source_type",
    "A": "dim_a",
    "B": "dim_b",
    "C": "dim_c",
    "D": "dim_d",
}

# Valeurs de ship_type qui ne renseignent pas le type réel.
UNKNOWN_SHIP_TYPES = {"", "Undefined"}


def count_true(mask) -> int:
    return int(pc.sum(pc.cast(pc.fill_null(mask, False), pa.int64())).as_py() or 0)


def null_where(col, mask):
    return pc.if_else(pc.fill_null(mask, False), pa.scalar(None, col.type), col)


def clean_batch(b: pa.RecordBatch, c: collections.Counter) -> pa.Table:
    """Nettoie un bloc, met à jour les compteurs et renvoie les lignes du rectangle."""
    t = pa.Table.from_batches([b]).rename_columns([RENAME[n] for n in b.schema.names])
    c["rows_read"] += t.num_rows

    lat, lon = t["lat"], t["lon"]
    # Position non disponible (AIS : lat = 91, lon = 181) ou hors plage physique.
    bad_pos = pc.or_(
        pc.or_(pc.equal(lat, 91.0), pc.equal(lon, 181.0)),
        pc.or_(pc.greater(pc.abs(lat), 90.0), pc.greater(pc.abs(lon), 180.0)),
    )
    c["position_unavailable_set_null"] += count_true(bad_pos)
    c["position_missing_in_source"] += count_true(pc.or_(pc.is_null(lat), pc.is_null(lon)))
    t = t.set_column(t.schema.get_field_index("lat"), "lat", null_where(lat, bad_pos))
    t = t.set_column(t.schema.get_field_index("lon"), "lon", null_where(t["lon"], bad_pos))

    # SOG : 102.3 = non disponible ; > 102.2 n'est pas codable en AIS.
    sog = t["sog"]
    m = pc.greater(sog, 102.2)
    c["sog_unavailable_or_out_of_range_set_null"] += count_true(m)
    t = t.set_column(t.schema.get_field_index("sog"), "sog", null_where(sog, m))
    # COG : 360 = non disponible ; plage valide [0, 360).
    cog = t["cog"]
    m = pc.or_(pc.greater_equal(cog, 360.0), pc.less(cog, 0.0))
    c["cog_unavailable_or_out_of_range_set_null"] += count_true(m)
    t = t.set_column(t.schema.get_field_index("cog"), "cog", null_where(cog, m))
    # Heading : 511 = non disponible ; plage valide [0, 359].
    hdg = t["heading"]
    m = pc.or_(pc.greater(hdg, 359), pc.less(hdg, 0))
    c["heading_unavailable_or_out_of_range_set_null"] += count_true(m)
    t = t.set_column(t.schema.get_field_index("heading"), "heading", null_where(hdg, m))
    # ROT : -128 = non disponible (valeur brute) ; la DMA publie une valeur
    # décodée en °/min, on ne la modifie pas (voir README).

    lat, lon = t["lat"], t["lon"]
    in_box = pc.and_(
        pc.and_(pc.greater_equal(lat, BBOX["lat_min"]), pc.less_equal(lat, BBOX["lat_max"])),
        pc.and_(pc.greater_equal(lon, BBOX["lon_min"]), pc.less_equal(lon, BBOX["lon_max"])),
    )
    in_box = pc.fill_null(in_box, False)
    n_in = count_true(in_box)
    c["rows_in_bbox"] += n_in
    c["rows_null_position_dropped"] += count_true(pc.or_(pc.is_null(lat), pc.is_null(lon)))
    c["rows_outside_bbox_dropped"] += t.num_rows - n_in - count_true(
        pc.or_(pc.is_null(lat), pc.is_null(lon))
    )
    return t, t.filter(in_box)


def static_info(t: pa.Table) -> pl.DataFrame:
    """Infos statiques (type, dimensions, nom) par MMSI, toutes positions confondues."""
    df = pl.from_arrow(t.select(["mmsi", "mobile_type", "ship_type", "length", "width", "name", "imo", "callsign"]))
    return df.group_by(df.columns).agg(pl.len().alias("n"))


def process_day(zip_path: pathlib.Path, day: str) -> None:
    out = INTERIM_DIR / f"day={day}.parquet"
    if out.exists():
        print(f"[skip] {day} déjà filtré")
        return
    c = collections.Counter()
    kept, statics = [], []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        assert len(names) == 1, f"ZIP inattendu : {names}"
        with z.open(names[0]) as f:
            reader = pcsv.open_csv(
                f,
                read_options=pcsv.ReadOptions(block_size=64 << 20),
                convert_options=pcsv.ConvertOptions(
                    column_types={k: pa.string() for k in STRING_COLS},
                    strings_can_be_null=False,
                ),
            )
            header = reader.schema.names
            unexpected = set(header) - set(RENAME)
            assert not unexpected, f"Colonnes inattendues : {unexpected}"
            for b in reader:
                full, sub = clean_batch(b, c)
                kept.append(sub)
                statics.append(static_info(full))

    df = pl.from_arrow(pa.concat_tables(kept))
    n_before = df.height
    df = df.unique(maintain_order=True)
    c["exact_duplicates_dropped"] = n_before - df.height
    df = df.with_columns(
        pl.col("timestamp_raw").str.strptime(pl.Datetime("us", "UTC"), "%d/%m/%Y %H:%M:%S").alias("timestamp"),
        pl.col("mmsi").str.contains(r"^\d{9}$").alias("mmsi_is_9_digits"),
        (pl.col("mobile_type") == "Class A").alias("is_class_a"),
    )
    c["mmsi_not_9_digits_flagged"] = int((~df["mmsi_is_9_digits"]).sum())
    c["rows_written"] = df.height
    ts = df["timestamp"]
    c_out = dict(c)
    c_out.update(
        day=day,
        source_file=zip_path.name,
        timestamp_min=str(ts.min()),
        timestamp_max=str(ts.max()),
        rows_timestamp_outside_day=int((ts.dt.date() != dt.date.fromisoformat(day)).sum()),
    )
    st = pl.concat(statics).group_by(["mmsi", "mobile_type", "ship_type", "length", "width", "name", "imo", "callsign"]).agg(pl.col("n").sum())
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    st.write_parquet(INTERIM_DIR / f"static_{day}.parquet")
    (INTERIM_DIR / f"stats_{day}.json").write_text(json.dumps(c_out, indent=2))
    tmp = out.with_suffix(".parquet.part")
    df.drop("timestamp_raw").write_parquet(tmp)
    tmp.rename(out)
    print(f"[done] {day} : {c['rows_read']:,} lus, {df.height:,} écrits")


def resolve_vessels() -> pl.DataFrame:
    """Type de navire, longueur et classe retenus par MMSI sur toute la période.

    Le ship_type n'est renseigné que sur une partie des messages (messages
    statiques) : on retient le libellé connu le plus fréquent pour chaque MMSI,
    et on garde la trace des MMSI qui en déclarent plusieurs.
    """
    st = pl.concat([pl.read_parquet(p) for p in sorted(INTERIM_DIR.glob("static_*.parquet"))])
    known = st.filter(~pl.col("ship_type").is_in(list(UNKNOWN_SHIP_TYPES)))
    ship = (
        known.group_by(["mmsi", "ship_type"]).agg(pl.col("n").sum())
        .sort(["mmsi", "n", "ship_type"], descending=[False, True, False])
        .group_by("mmsi", maintain_order=True)
        .agg(
            pl.col("ship_type").first().alias("vessel_ship_type"),
            pl.col("ship_type").n_unique().alias("vessel_ship_type_n_distinct"),
        )
    )
    length = (
        st.filter(pl.col("length").is_not_null())
        .group_by(["mmsi", "length"]).agg(pl.col("n").sum())
        .sort(["mmsi", "n", "length"], descending=[False, True, True])
        .group_by("mmsi", maintain_order=True)
        .agg(pl.col("length").first().alias("vessel_length_m"))
    )
    name = (
        st.filter(pl.col("name") != "")
        .group_by(["mmsi", "name"]).agg(pl.col("n").sum())
        .sort(["mmsi", "n", "name"], descending=[False, True, False])
        .group_by("mmsi", maintain_order=True)
        .agg(pl.col("name").first().alias("vessel_name"))
    )
    return ship.join(length, on="mmsi", how="full", coalesce=True).join(name, on="mmsi", how="full", coalesce=True)


def enrich(df: pl.DataFrame, vessels: pl.DataFrame) -> pl.DataFrame:
    """Ajoute les infos navire résolues et is_fishing_candidate."""
    return df.join(vessels, on="mmsi", how="left").with_columns(
        # Type AIS 30 = « Fishing » (la DMA publie le libellé décodé, pas le code).
        (pl.col("vessel_ship_type") == "Fishing").fill_null(False).alias("is_fishing_candidate"),
    )


def build_processed(days: list[str]) -> None:
    """Jeu final : navires de pêche, messages de classe A uniquement.

    Les positions de tous les navires restent dans data/interim/day=*.parquet
    (carte de couverture et comptages du rapport).
    """
    vessels = resolve_vessels()
    vessels.write_parquet(INTERIM_DIR / "vessels.parquet")
    months = sorted({d[:7] for d in days})
    for month in months:
        dest = PROCESSED_DIR / f"ais_kattegat_{month}.parquet"
        for d in [d for d in days if d.startswith(month)]:
            df = enrich(pl.read_parquet(INTERIM_DIR / f"day={d}.parquet"), vessels)
            sel = df.filter(pl.col("is_fishing_candidate") & pl.col("is_class_a"))
            counts = {
                "day": d,
                "rows_all_vessels": df.height,
                "rows_fishing_any_class": int(df["is_fishing_candidate"].sum()),
                "rows_fishing_class_a_kept": sel.height,
                "mmsi_fishing_class_a_kept": sel["mmsi"].n_unique(),
            }
            (INTERIM_DIR / f"selection_{d}.json").write_text(json.dumps(counts, indent=2))
            part = dest / f"day={d}"
            if part.exists():
                shutil.rmtree(part)
            part.mkdir(parents=True)
            # Trié par navire puis par temps : chaque MMSI forme son trajet.
            sel.sort(["mmsi", "timestamp"]).write_parquet(part / "part-0.parquet")
        print(f"[out ] {dest.relative_to(ROOT)}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, type=dt.date.fromisoformat)
    p.add_argument("--end", required=True, type=dt.date.fromisoformat)
    p.add_argument("--delete-raw", action="store_true", help="supprimer le ZIP après filtrage")
    a = p.parse_args()
    days = []
    d = a.start
    while d <= a.end:
        day = d.isoformat()
        z = RAW_DIR / f"aisdk-{day}.zip"
        if (INTERIM_DIR / f"day={day}.parquet").exists():
            print(f"[skip] {day} déjà filtré")
        elif z.exists():
            process_day(z, day)
        else:
            raise SystemExit(f"{z} absent : lancer d'abord src/01_download.py")
        if a.delete_raw and z.exists():
            z.unlink()
        days.append(day)
        d += dt.timedelta(days=1)
    build_processed(days)


if __name__ == "__main__":
    main()
