"""Évaluation de l'algorithme SeaTrace appliqué à la carte du Kattegat (src/13_reconstruct.py).

Seul endroit qui connaît les trajets de démonstration (src/demo_boats.py, constante FAKE) et qui lit
data/private/ (vérité terrain, liste d'exclusion). Rien de ce fichier ne va dans la carte publique.

1. Bateaux de démonstration A, B, C : le résultat attendu est celui décrit dans src/demo_boats.py
   (A silence pendant la traversée d'une zone restreinte, B saut de position de 10 km pendant 20 min,
   C trou de 45 min loin des zones et tampons). Si data/private/demo_truth/ existe, la position réelle
   pendant la fenêtre est aussi comparée au contour à 90 % et au passage réel en zone.
2. Flotte : niveaux d'alerte et périodes, en EXCLUANT les MMSI de data/private/excluded_from_stats.csv
   (sources des copies et copies). Sans ce fichier, seules les trois copies sont exclues, et c'est écrit.

Sortie : eval/resultats/carte_kattegat.json

Usage : python eval/evaluer_carte_kattegat.py
"""
import ast
import collections
import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from seatrace import carte_kattegat as K  # noqa: E402

PRIVATE = ROOT / "data" / "private"
OUT = ROOT / "eval" / "resultats" / "carte_kattegat.json"
EXPECTED = {
    "A": "silence of 45 min while crossing a restricted area: the gap should be reconstructed with a high "
         "probability of passage through a restricted area",
    "B": "positions shifted by 10 km for 20 min with unchanged timestamps: integrity alert (position jump) "
         "and the shifted block set aside, then reconstructed",
    "C": "gap of 45 min far from any zone and buffer: low probability of passage (negative control)",
}


def demo_ids() -> dict:
    """FAKE est une constante littérale en tête de src/demo_boats.py : la lire sans importer geopandas & co."""
    src = (ROOT / "src" / "demo_boats.py").read_text(encoding="utf-8")
    line = next(x for x in src.splitlines() if x.startswith("FAKE = "))
    return {k: v[0] for k, v in ast.literal_eval(line.split("=", 1)[1].strip()).items()}


def excluded() -> tuple[set, str]:
    f = PRIVATE / "excluded_from_stats.csv"
    if f.exists():
        df = pd.read_csv(f, dtype=str)
        col = "mmsi" if "mmsi" in df.columns else df.columns[0]
        return set(df[col].str.strip()), str(f.relative_to(ROOT))
    return set(demo_ids().values()), "data/private/excluded_from_stats.csv absent: only the three copies are excluded"


def truth_check(x: str, gaps: list) -> dict | None:
    """Real positions during the modified window (internal file, absent from git)."""
    f = PRIVATE / "demo_truth" / f"boat_{x}.parquet"
    if not f.exists():
        return None
    t = pd.read_parquet(f)
    t = t[t["in_window"]] if "in_window" in t.columns else t
    if t.empty or not gaps or not {"lat", "lon"} <= set(t.columns):
        return None
    from matplotlib.path import Path as MPath
    g = max(gaps, key=lambda g: g["min"])
    inside = None
    if g.get("c90"):
        pts = np.column_stack([t["lat"], t["lon"]])
        inside = float(np.mean(np.logical_or.reduce([MPath(np.array(r)).contains_points(pts) for r in g["c90"]])))
    return {"window_positions": int(len(t)), "share_inside_90pc_contour": inside}


def main():
    a = json.loads(K.chemins(ROOT)["analysis"].read_text(encoding="utf-8"))
    V = a["vessels"]
    ids = demo_ids()
    demo = {}
    for x, mmsi in ids.items():
        v = V.get(mmsi, {"gaps": [], "alerts": [], "lvl": 0, "pz": None, "pb": None})
        gaps = [g for g in v["gaps"]]
        alerts = sorted({al["type"] for al in v["alerts"]})
        if x == "A":
            ok = any(g["cause"] == "interruption" and (g.get("pz") or 0) >= 0.5 for g in gaps)
        elif x == "B":
            ok = "saut_position" in alerts and any(g["cause"] != "interruption" and g["status"] == "ok" for g in gaps)
        else:
            ok = any(g["cause"] == "interruption" for g in gaps) and all((g.get("pz") or 0) < 0.1 for g in gaps)
        demo[x] = {"mmsi": mmsi, "expected": EXPECTED[x], "level": v["lvl"], "alerts": alerts,
                   "periods": [{k: g.get(k) for k in ("start", "min", "cause", "status", "pz", "pb", "cov", "low_cov",
                                                      "unexplained", "km")} for g in gaps],
                   "as_expected": bool(ok), "flagged": v["lvl"] >= 2, "truth": truth_check(x, gaps)}

    excl, excl_note = excluded()
    fleet_ids = set(K.navires(K.donnees_carte(K.chemins(ROOT)["map"]))) or set(V)
    in_stats = fleet_ids - excl
    kept = {m: V[m] for m in in_stats if m in V}
    levels = collections.Counter(V[m]["lvl"] if m in V else 0 for m in in_stats)
    periods = [g for v in kept.values() for g in v["gaps"]]
    fleet = {
        "vessels_in_stats": len(in_stats),
        "excluded": excl_note,
        "levels": {"review": levels[3], "check": levels[2], "gaps_only": levels[1], "none": levels[0]},
        "periods": len(periods),
        "status": dict(collections.Counter(g["status"] for g in periods)),
        "p_zone_ge_0.5": sum((g.get("pz") or 0) >= 0.5 for g in periods),
        "p_zone_ge_0.1": sum((g.get("pz") or 0) >= 0.1 for g in periods),
        "unexplained_silences": sum(bool(g.get("unexplained")) for g in periods),
        "integrity_alerts": dict(collections.Counter(al["type"] for v in kept.values() for al in v["alerts"])),
        "flagged_vessels_other_than_copies": sorted(m for m, v in kept.items() if v["lvl"] >= 2),
    }
    res = {"computed_from": a["computed"], "source": a["source"], "timing_s": a["timing_s"], "demo_boats": demo,
           "fleet_excluding_listed_mmsi": fleet,
           "reading": ("Expected behaviour on the three copies is checked from their description; with the internal "
                       "files present, the real positions during the window are compared with the 90 % contour. "
                       "Fleet figures exclude the listed MMSI. A flagged vessel is a review priority, never an offence.")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for x, d in demo.items():
        print(f"{x} {d['mmsi']} niveau {d['level']} alertes {d['alerts']} attendu={'oui' if d['as_expected'] else 'NON'} "
              f"périodes {[(p['min'], p['cause'], p['status'], p['pz']) for p in d['periods']]}")
    print("flotte :", json.dumps(fleet, ensure_ascii=False))
    print(f"[out ] {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
