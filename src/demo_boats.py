"""Trois trajets de démonstration, copiés de vrais trajets puis modifiés (usage interne).

Les données réelles ne sont jamais modifiées sur disque. Ce script écrit :
  data/processed/map_input_<période>.parquet   positions affichées par la carte : les
                                               positions réelles, où chaque trajet source est
                                               REMPLACÉ par sa copie modifiée (nombre de navires inchangé)
  data/private/demo_truth/boat_<A|B|C>.parquet trajets d'origine et fenêtres (ignoré par git)
  data/private/vessel_id_map.csv               display_id, mmsi_reel, modifie, scenario (ignoré par git)
  data/private/excluded_from_stats.csv         MMSI à exclure de toute statistique (sources et copies)

Scénarios :
  A  silence en zone : plus aucun message pendant WINDOW_A_MIN, fenêtre qui couvre toute la
     traversée d'une zone où le chalut de fond ou la pêche est interdit ; le navire
     disparaît avant d'entrer et réapparaît hors de toute zone restreinte.
  B  saut de position : pendant WINDOW_B_MIN, positions décalées de SHIFT_KM dans une
     direction constante (perpendiculaire à la route), puis vraies positions rétablies ;
     horodatages et rythme des messages inchangés.
  C  trou innocent : plus aucun message pendant WINDOW_C_MIN, loin (≥ C_MIN_DIST_NM) de toute
     zone restreinte et de leur tampon, là où la couverture de réception est la plus faible.

Identifiants des copies : MMSI de même format, avec les codes pays 217, 221 et 260, qui ne sont
attribués à aucun pays (table des MID de l'UIT) : aucun navire réel ne peut porter ces numéros.
Noms fictifs, absents de tous les noms vus dans les données AIS danoises de la période.

Usage : python src/demo_boats.py
"""
import json
import pathlib

import geopandas as gpd
import numpy as np
import polars as pl
import shapely
from pyproj import Transformer

# --- Paramètres --------------------------------------------------------------------------------
PERIOD = ("2026-09-15", "2026-09-17")
WINDOW_A_MIN = 45
WINDOW_B_MIN = 20
SHIFT_KM = 10.0
WINDOW_C_MIN = 45
C_MIN_DIST_NM = 10
MAX_NATURAL_GAP_MIN = 30
MIN_POINTS = 200
TRAWL_KN = (2.0, 5.0)  # vitesse de chalutage présumée
FAKE = {"A": ("221458300", "LYNGBORG"), "B": ("217602700", "ANNEGRET"), "C": ("260730500", "ÖSTERLYS")}
# -------------------------------------------------------------------------------------------------

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed" / "ais_kattegat_2026-09.parquet"
INTERIM = ROOT / "data" / "interim"
ZONES = ROOT / "data" / "reference" / "kattegat_zones.geojson"
LAND = ROOT / "data" / "raw" / "basemap" / "ne_10m_land.geojson"
PRIVATE = ROOT / "data" / "private"
MAP_INPUT = ROOT / "data" / "processed" / f"map_input_{PERIOD[0]}_{PERIOD[1]}.parquet"
COLS = ["mmsi", "timestamp", "lat", "lon", "sog", "cog", "heading", "nav_status",
        "vessel_ship_type", "vessel_length_m", "vessel_name"]
STRICT = ("Areas 1", "Areas 2")
FWD = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
INV = Transformer.from_crs("EPSG:25832", "EPSG:4326", always_xy=True)


def load_real() -> pl.DataFrame:
    return (pl.scan_parquet(str(PROCESSED / "**" / "*.parquet"), hive_partitioning=True)
            .select(COLS).collect().sort(["mmsi", "timestamp"]))


def candidates(df: pl.DataFrame) -> pl.DataFrame:
    """Chalutiers présumés sans trou naturel > MAX_NATURAL_GAP_MIN et avec assez de points."""
    return (df.with_columns(pl.col("timestamp").diff().over("mmsi").dt.total_minutes().alias("gap"))
            .group_by("mmsi").agg(
                pl.len().alias("n"), pl.col("gap").max().alias("max_gap"),
                ((pl.col("sog") >= TRAWL_KN[0]) & (pl.col("sog") <= TRAWL_KN[1])).sum().alias("n_trawl"),
                (pl.col("sog") >= 0.5).sum().alias("n_mov"))
            .with_columns((pl.col("n_trawl") / pl.col("n_mov")).alias("trawl_share"))
            .filter((pl.col("n") >= MIN_POINTS) & (pl.col("max_gap").fill_null(0) <= MAX_NATURAL_GAP_MIN)
                    & (pl.col("trawl_share") >= 0.4) & (pl.col("n_mov") >= MIN_POINTS)))


def xy(d: pl.DataFrame):
    return FWD.transform(d["lon"].to_numpy(), d["lat"].to_numpy())


def runs(mask: np.ndarray):
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        if start is not None and (not v or i == len(mask) - 1):
            out.append((start, i - 1 if not v else i))
            start = None
    return out


def pick_a(df, cand, zones_all, zones_strict):
    best = None
    w = np.timedelta64(WINDOW_A_MIN * 60, "s")
    for m in cand["mmsi"]:
        d = df.filter(pl.col("mmsi") == m)
        x, y = xy(d)
        ins = shapely.contains_xy(zones_strict, x, y)
        if not ins.any():
            continue
        any_zone = shapely.contains_xy(zones_all, x, y)
        ts = d["timestamp"].to_numpy()
        sog = d["sog"].fill_null(0).to_numpy()
        for a, b in runs(ins):
            dur = (ts[b] - ts[a]) / np.timedelta64(1, "m")
            if not (8 <= dur <= WINDOW_A_MIN - 10):
                continue
            start = ts[a] - (w - (ts[b] - ts[a])) / 2  # fenêtre centrée sur la traversée
            end = start + w
            before, after = np.flatnonzero(ts < start), np.flatnonzero(ts >= end)
            if not len(before) or not len(after) or any_zone[before[-1]] or any_zone[after[0]]:
                continue
            if any_zone[after[0]:after[0] + 30].any():
                continue  # ne pas y revenir aussitôt
            trawl = float(((sog[a:b + 1] >= TRAWL_KN[0]) & (sog[a:b + 1] <= TRAWL_KN[1])).mean())
            score = trawl + dur / 100
            if best is None or score > best["score"]:
                best = {"mmsi": m, "start": start, "end": end, "score": score, "zone_run_min": round(dur, 1),
                        "trawl_share_in_zone": round(trawl, 2), "x": float(x[a:b + 1].mean()), "y": float(y[a:b + 1].mean())}
    return best


def pick_b(df, cand, avoid_xy, land):
    lengths = []
    for m in cand["mmsi"]:
        d = df.filter(pl.col("mmsi") == m)
        x, y = xy(d)
        if np.hypot(x.mean() - avoid_xy[0], y.mean() - avoid_xy[1]) < 50_000:
            continue
        lengths.append((float(np.hypot(np.diff(x), np.diff(y)).sum()), m))
    w = np.timedelta64(WINDOW_B_MIN * 60, "s")
    for _, m in sorted(lengths, reverse=True):
        d = df.filter(pl.col("mmsi") == m)
        ts, sog, x, y = d["timestamp"].to_numpy(), d["sog"].fill_null(0).to_numpy(), *xy(d)
        mid = ts[0] + (ts[-1] - ts[0]) / 2
        order = np.argsort(np.abs((ts - mid) / np.timedelta64(1, "s")))
        for i in order[::25]:
            j = np.searchsorted(ts, ts[i] + w)
            if j >= len(ts) - 10 or i < 10 or (sog[i:j] >= 2).mean() < 0.95:
                continue
            if (np.diff(ts[i:j]) / np.timedelta64(1, "s")).max(initial=0) > 120:
                continue
            course = np.degrees(np.arctan2(x[j] - x[i], y[j] - y[i]))
            for bearing in ((course + 90) % 360, (course - 90) % 360):
                dx, dy = SHIFT_KM * 1000 * np.sin(np.deg2rad(bearing)), SHIFT_KM * 1000 * np.cos(np.deg2rad(bearing))
                if not shapely.contains_xy(land, x[i:j] + dx, y[i:j] + dy).any():
                    return {"mmsi": m, "start": ts[i], "end": ts[j], "bearing": round(float(bearing), 1),
                            "x": float(x[i:j].mean()), "y": float(y[i:j].mean()), "track_km": round(_ / 1000)}
    return None


def coverage_counts():
    p = (pl.scan_parquet(str(INTERIM / "day=*.parquet"))
         .filter(~pl.col("mobile_type").is_in(["Base Station", "AtoN"])).select("lon", "lat").collect())
    x, y = FWD.transform(p["lon"].to_numpy(), p["lat"].to_numpy())
    return pl.DataFrame({"cx": (x // 2000).astype(np.int64), "cy": (y // 2000).astype(np.int64)}).group_by(["cx", "cy"]).len()


def pick_c(df, cand, avoid, far_from):
    cov = coverage_counts()
    w = np.timedelta64(WINDOW_C_MIN * 60, "s")
    best = None
    for m in cand["mmsi"]:
        d = df.filter(pl.col("mmsi") == m)
        x, y = xy(d)
        if shapely.LineString(np.column_stack([x, y])).distance(far_from) < C_MIN_DIST_NM * 1852:
            continue
        if min(np.hypot(x.mean() - ax, y.mean() - ay) for ax, ay in avoid) < 40_000:
            continue
        ts = d["timestamp"].to_numpy()
        cells = pl.DataFrame({"cx": (x // 2000).astype(np.int64), "cy": (y // 2000).astype(np.int64)})
        counts = cells.join(cov, on=["cx", "cy"], how="left")["len"].fill_null(0).to_numpy()
        for i in range(30, len(ts), 20):
            j = np.searchsorted(ts, ts[i] + w)
            if j >= len(ts) - 30:
                break
            c = float(np.median(counts[i:j]))
            if best is None or c < best["coverage_median"]:
                best = {"mmsi": m, "start": ts[i], "end": ts[j], "coverage_median": c,
                        "coverage_track_median": float(np.median(counts)),
                        "dist_zone_buffer_nm": round(shapely.LineString(np.column_stack([x, y])).distance(far_from) / 1852, 1),
                        "x": float(x[i:j].mean()), "y": float(y[i:j].mean())}
    return best


def write_notes(notes: dict) -> None:
    """reports/internal/demo_notes.md (ignoré par git) : ce que l'équipe doit savoir pour la démo."""
    titles = {"A": "silence en zone", "B": "saut de position", "C": "trou innocent"}
    params = {"A": f"fenêtre {WINDOW_A_MIN} min, centrée sur la traversée", "B": f"fenêtre {WINDOW_B_MIN} min, décalage {SHIFT_KM:g} km",
              "C": f"fenêtre {WINDOW_C_MIN} min, distance minimale {C_MIN_DIST_NM} milles nautiques aux zones et tampons"}
    lines = ["# Notes internes de démonstration (ne pas diffuser)", "",
             "Carte publique : reports/map.html. Carte avec vérité terrain : reports/internal/map_with_truth.html.",
             "Ces trois navires sont aussi les favoris par défaut de l'explorateur.", ""]
    for x, n in notes.items():
        lines += [f"## {x} : {titles[x]}", "",
                  f"- Identifiant affiché : **{n['display_id']}** (nom affiché : {n['display_name']})",
                  f"- Trajet source : MMSI réel {n['source_mmsi']} ({n['source_name']})",
                  f"- Paramètres : {params[x]}" + (f", cap {n['bearing']}°" if "bearing" in n else ""),
                  f"- Fenêtre : {n['window_start']} → {n['window_end']} ({n['messages_in_window']} messages d'origine)",
                  *[f"- {k} : {v}" for k, v in n.items() if k in ("zone_run_min", "trawl_share_in_zone", "track_km",
                                                                  "coverage_median", "coverage_track_median", "dist_zone_buffer_nm")],
                  "- Position réelle pendant la fenêtre (lat, lon, heure UTC), échantillon :",
                  *[f"  - {p[0]}, {p[1]} à {p[2]}" for p in n["true_positions"]], ""]
    out = ROOT / "reports" / "internal" / "demo_notes.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    df = load_real()
    cand = candidates(df)
    z = gpd.read_file(ZONES).to_crs("EPSG:25832")
    zones_all = z[z.layer == "zone"].union_all()
    zones_strict = z[(z.layer == "zone") & z.category.isin(STRICT)].union_all()
    zone_buf = z[z.layer.isin(["zone", "buffer"])].union_all()
    land = gpd.read_file(LAND, bbox=(9.5, 54.5, 13.5, 58.5)).clip((9.5, 54.5, 13.5, 58.5)).to_crs("EPSG:25832").make_valid().union_all()
    print(f"[info] {cand.height} trajets candidats (chalutiers présumés, sans trou > {MAX_NATURAL_GAP_MIN} min, au moins {MIN_POINTS} points)")

    a = pick_a(df, cand, zones_all, zones_strict)
    b = pick_b(df, cand.filter(pl.col("mmsi") != a["mmsi"]), (a["x"], a["y"]), land)
    c = pick_c(df, cand.filter(~pl.col("mmsi").is_in([a["mmsi"], b["mmsi"]])), [(a["x"], a["y"]), (b["x"], b["y"])], zone_buf)
    if not (a and b and c):
        raise SystemExit("Aucun trajet ne satisfait toutes les contraintes : à examiner.")
    picks = {"A": a, "B": b, "C": c}

    observed_names = set(pl.concat([pl.read_parquet(f, columns=["name"]) for f in INTERIM.glob("static_*.parquet")])["name"]
                         .str.strip_chars().str.to_uppercase().to_list())
    for x_, (mm, nm) in FAKE.items():
        assert nm.upper() not in observed_names, f"nom {nm} déjà porté par un navire réel"
        assert mm not in set(df["mmsi"].unique().to_list())

    (PRIVATE / "demo_truth").mkdir(parents=True, exist_ok=True)
    copies, notes = [], {}
    for x_, p in picks.items():
        src = df.filter(pl.col("mmsi") == p["mmsi"])
        ts = src["timestamp"].to_numpy()
        in_win = (ts >= p["start"]) & (ts < p["end"])
        fake_mmsi, fake_name = FAKE[x_]
        cp = src.with_columns(pl.lit(fake_mmsi).alias("mmsi"), pl.lit(fake_name).alias("vessel_name"))
        truth = src.with_columns(pl.Series("in_window", in_win))
        if x_ == "B":
            x, y = xy(src)
            dx = SHIFT_KM * 1000 * np.sin(np.deg2rad(p["bearing"]))
            dy = SHIFT_KM * 1000 * np.cos(np.deg2rad(p["bearing"]))
            lo, la = INV.transform(x + dx, y + dy)
            cp = cp.with_columns(pl.Series("lat", np.where(in_win, la, src["lat"].to_numpy())),
                                 pl.Series("lon", np.where(in_win, lo, src["lon"].to_numpy())))
            truth = truth.with_columns(pl.Series("shown_lat", cp["lat"]), pl.Series("shown_lon", cp["lon"]))
        else:
            cp = cp.filter(pl.Series(~in_win))
        truth.write_parquet(PRIVATE / "demo_truth" / f"boat_{x_}.parquet")
        copies.append(cp)
        hidden = src.filter(pl.Series(in_win))
        notes[x_] = {"display_id": fake_mmsi, "display_name": fake_name, "source_mmsi": p["mmsi"],
                     "source_name": src["vessel_name"][0], "window_start": str(p["start"])[:19] + "Z",
                     "window_end": str(p["end"])[:19] + "Z", "messages_in_window": hidden.height,
                     "true_positions": [[round(r[0], 5), round(r[1], 5), str(r[2])[:19] + "Z"] for r in
                                        hidden.select("lat", "lon", "timestamp").gather_every(max(1, hidden.height // 8)).rows()],
                     **{k: v for k, v in p.items() if k not in ("mmsi", "start", "end", "x", "y", "score")}}

    sources = [p["mmsi"] for p in picks.values()]
    out = pl.concat([df.filter(~pl.col("mmsi").is_in(sources)), *[c_.select(COLS) for c_ in copies]]).sort(["mmsi", "timestamp"])
    out.write_parquet(MAP_INPUT)

    rows = [{"display_id": m, "mmsi_reel": m, "modifie": False, "scenario": ""} for m in out["mmsi"].unique().sort()
            if m not in {v[0] for v in FAKE.values()}]
    rows += [{"display_id": n["display_id"], "mmsi_reel": n["source_mmsi"], "modifie": True, "scenario": x_} for x_, n in notes.items()]
    pl.DataFrame(rows).sort("display_id").write_csv(PRIVATE / "vessel_id_map.csv")
    pl.DataFrame({"mmsi": sources + [v[0] for v in FAKE.values()]}).write_csv(PRIVATE / "excluded_from_stats.csv")
    (PRIVATE / "demo_notes.json").write_text(json.dumps(notes, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_notes(notes)
    print(f"[out ] {MAP_INPUT.relative_to(ROOT)} : {out['mmsi'].n_unique()} navires (réel : {df['mmsi'].n_unique()})")
    for x_, n in notes.items():
        print(f"[{x_}] affiché {n['display_id']} <- source {n['source_mmsi']} ; fenêtre {n['window_start']} -> {n['window_end']} ; "
              + ", ".join(f"{k}={v}" for k, v in n.items() if k in ("zone_run_min", "trawl_share_in_zone", "bearing", "track_km",
                                                                   "coverage_median", "coverage_track_median", "dist_zone_buffer_nm")))


if __name__ == "__main__":
    main()
