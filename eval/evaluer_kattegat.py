"""Bateaux test A, B, C de la carte du Kattegat passés dans l'algorithme complet, comme dans l'interface.

Prérequis : python src/13_seatrace_export.py   (écrit data/ais/kattegat_<début>_<fin>.csv) ; le résultat
            versionné est fait avec --no-real (bateaux test seuls, reproductible depuis le dépôt) et le
            courant simulé de src/12_simulate_context.py
Lancement : python eval/evaluer_kattegat.py [--modeles hmm appris particules pont boosting]

L'analyse et la reconstruction passent par l'API de l'interface (client de test Flask) : c'est exactement
le chemin d'un analyste. La vérité (data/synthetic/summary_truth.json et les fichiers de positions réelles)
n'est lue qu'ICI, après coup, pour noter les résultats ; l'algorithme ne la voit jamais.

Ce que l'on mesure :
  A  silence de 45 min commencé dans une zone où la pêche est interdite : la période est-elle trouvée,
     à la bonne heure ? probabilité de passage dans cette zone (attendue élevée : le navire y est quand il
     se tait) ; erreur entre la position moyenne reconstruite et la vraie position.
  B  positions décalées de 10 km pendant 60 min : alertes d'intégrité dans la fenêtre, part des positions
     décalées mises à l'écart, alertes hors de la fenêtre ; erreur de la reconstruction de la période écartée.
  C  trou innocent loin de toute zone : période trouvée ; probabilité de passage dans la zone restreinte la
     plus proche (attendue quasi nulle) ; erreur de position.
Sortie : eval/resultats/kattegat_bateaux_test.json. Trois cas ne font pas une validation : ce banc vérifie
que la chaîne carte -> algorithme fonctionne sur de vrais trajets, il ne mesure pas une performance.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import archive, config, detect, geo, pipeline, schema  # noqa: E402

SYN = RACINE / "data" / "synthetic"
ZONES = RACINE / "data" / "reference" / "kattegat_zones.geojson"
SORTIE = RACINE / "eval" / "resultats" / "kattegat_bateaux_test.json"
H = {"X-SeaTrace": "1"}


def client_interface(dossier_tmp: Path):
    from web.app import create_app
    archive.DOSSIER = dossier_tmp / "chemins"
    pipeline.CHEMIN_JOURNAL = dossier_tmp / "journal.jsonl"
    pipeline.DOSSIER_IMPORTS = dossier_tmp / "imports"
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def utc(s):
    return pd.Timestamp(s).tz_convert("UTC") if pd.Timestamp(s).tzinfo else pd.Timestamp(s, tz="UTC")


def chevauche(a0, a1, b0, b1) -> bool:
    return utc(a0) <= utc(b1) and utc(b0) <= utc(a1)


def vraies_positions(scenario: str) -> pd.DataFrame:
    """Positions réelles du bateau test (évaluation seulement)."""
    v = pd.read_parquet(SYN / f"boat_{scenario}_truth.parquet", columns=["timestamp", "lat", "lon"])
    v["timestamp"] = pd.to_datetime(v["timestamp"], utc=True)
    return v.sort_values("timestamp").reset_index(drop=True)


def erreur_position(run: dict, verite: pd.DataFrame) -> dict:
    """Distance (km) entre la position moyenne pondérée des chemins de chaque modèle et la vraie position,
    à chaque pas de la période (vraie position interpolée linéairement entre deux messages)."""
    t0 = pd.Timestamp(run["timestamps"][0]).timestamp()
    tv = (verite["timestamp"] - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds().to_numpy()
    o = (float(verite["lat"].mean()), float(verite["lon"].mean()))
    out = {}
    for m, r in run["models"].items():
        if not r["paths"]:
            continue
        # chaque modèle a son propre pas de temps : instants de ses chemins
        tq = t0 + np.arange(len(r["paths"][0]["lat"])) * float(r["dt_s"])
        xv, yv = geo.vers_km(np.interp(tq, tv, verite["lat"].to_numpy()), np.interp(tq, tv, verite["lon"].to_numpy()), o)
        w = np.array([c["weight"] or 0.0 for c in r["paths"]], float)
        if w.sum() <= 0:
            w = np.ones(len(w))
        w = w / w.sum()
        lat = np.array([c["lat"] for c in r["paths"]], float)
        lon = np.array([c["lon"] for c in r["paths"]], float)
        x, y = geo.vers_km(lat, lon, o)
        mx, my = (w[:, None] * x).sum(0), (w[:, None] * y).sum(0)
        d = np.hypot(mx - xv, my - yv)
        out[m] = {"moyenne_km": round(float(d.mean()), 2), "max_km": round(float(d.max()), 2)}
    return out


def positions(jeu: Path, params) -> pd.DataFrame:
    """Le jeu exporté, relu par l'import sécurisé de l'interface."""
    p_imp = {**params["import"], "taille_max_octets": params["import"]["taille_max_ais_octets"]}
    return schema.lire_csv_securise(jeu.read_bytes(), jeu.name, p_imp).donnees


def zone_restreinte_la_plus_proche(lat, lon, proposees) -> tuple[str, float]:
    """Zone restreinte (pas une zone AIS) la plus proche, parmi celles que l'interface propose pour ce trajet."""
    feats = [f for f in json.loads(ZONES.read_text(encoding="utf-8"))["features"]
             if f["properties"]["layer"] == "zone" and f["properties"]["zone_id"] in proposees]
    o = (float(lat), float(lon))
    best = (None, float("inf"))
    for f in feats:
        g = f["geometry"]
        anneaux = [g["coordinates"][0]] if g["type"] == "Polygon" else [p[0] for p in g["coordinates"]]
        for a in anneaux:
            x, y = geo.vers_km(np.array([p[1] for p in a]), np.array([p[0] for p in a]), o)
            d = float(geo.distance_a_polyligne(np.array([0.0]), np.array([0.0]), list(zip(x, y)))[0])
            if d < best[1]:
                best = (f["properties"]["zone_id"], d)
    return best


def reconstruire(client, did, trip, periode, zone, modeles):
    r = client.post("/api/reconstruct", json={"dataset": did, "trip": trip, "period": periode, "zone": zone,
                                              "models": modeles, "current": "dataset"}, headers=H)
    if r.status_code == 400 and "no current table" in (r.get_json() or {}).get("error", ""):
        r = client.post("/api/reconstruct", json={"dataset": did, "trip": trip, "period": periode, "zone": zone,
                                                  "models": modeles}, headers=H)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def resume_run(run):
    return {"grille_case_km": run["context"]["grid_cell_km"], "courant_kmh": run["context"]["current_kmh"],
            "source_courant": run["context"]["current_source"], "lecture": run["synthesis"]["reading"],
            "probabilites": {m: r["crossing_probability"] for m, r in run["models"].items()},
            "echecs": {m: r["reasons"] for m, r in run["models"].items() if r["status"] != "ok"},
            "temps_s": run["total_time_s"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debut", default="2026-09-15")
    ap.add_argument("--fin", default="2026-09-17")
    ap.add_argument("--modeles", nargs="*", default=["hmm", "appris", "particules", "pont", "boosting"])
    a = ap.parse_args()
    jeu = RACINE / "data" / "ais" / f"kattegat_{a.debut}_{a.fin}.csv"
    if not jeu.exists():
        raise SystemExit(f"{jeu.relative_to(RACINE)} absent : lancer d'abord python src/13_seatrace_export.py")
    did = f"ais-{jeu.stem}"
    verite = {b["scenario"]: b for b in json.loads((SYN / "summary_truth.json").read_text(encoding="utf-8"))["boats"]}
    params = config.charger()
    meta = json.loads(jeu.with_suffix(".json").read_text(encoding="utf-8")) if jeu.with_suffix(".json").exists() else {}
    resultats = {"jeu": str(jeu.relative_to(RACINE)), "nom_jeu": meta.get("nom_jeu"), "selection": meta.get("selection"),
                 "couches": meta.get("couches"), "modeles": a.modeles, "bateaux": {}}

    with tempfile.TemporaryDirectory() as tmp:
        client = client_interface(Path(tmp))
        trajets = client.get(f"/api/datasets/{did}/trips").get_json()["trips"]
        for x, v in sorted(verite.items()):
            trip = next((t["trip_id"] for t in trajets if t["boat_id"] == v["mmsi"]), None)
            if trip is None:
                resultats["bateaux"][x] = {"erreur": "bateau absent du jeu"}
                continue
            an = client.get(f"/api/datasets/{did}/trips/{trip}").get_json()
            integ = [al for al in an["alerts"] if al["category"] == "integrity"]
            res = {"mmsi": v["mmsi"], "scenario": v["type"], "trajet": trip, "evenement": [v["start"], v["end"]],
                   "periodes": len(an["periods"]), "alertes_integrite": len(integ),
                   "alertes_integrite_hors_evenement": sum(not chevauche(al["start_utc"], al["end_utc"], v["start"], v["end"])
                                                           for al in integ)}
            per = [p for p in an["periods"] if chevauche(p["start_utc"], p["end_utc"], v["start"], v["end"])]
            res["periode_trouvee"] = bool(per)
            if per:
                p = max(per, key=lambda q: q["duration_min"])
                res["periode"] = {"id": p["id"], "debut": p["start_utc"], "fin": p["end_utc"], "duree_min": p["duration_min"],
                                  "cause": p["cause"], "ecart_debut_s": round((utc(p["start_utc"]) - utc(v["start"])).total_seconds()),
                                  "ecart_fin_s": round((utc(p["end_utc"]) - utc(v["end"])).total_seconds())}
                zone = v["zone_ids"][0] if x == "A" else None  # B : pas de zone, on regarde le trajet reconstruit
                if x == "C":
                    t = positions(jeu, params)
                    t = t[(t["trip_id"] == trip) & (t["timestamp"] >= utc(p["start_utc"]))
                          & (t["timestamp"] <= utc(p["end_utc"]))]
                    proposees = {z["id"].split("#")[0] for z in an["geography"]["zones"]}
                    zone, dist = zone_restreinte_la_plus_proche(t["lat"].mean(), t["lon"].mean(), proposees)
                    vmax = params["types_bateau"]["ais_peche"]["vitesse_max_kn"]
                    res["portee_max_km"] = round(vmax * geo.KN_EN_KMH * p["duration_min"] / 60, 1)
                    res["zone_restreinte_la_plus_proche_km"] = (round(dist, 1) if zone else
                                                                v.get("min_distance_to_restricted_zone_km"))
                    if zone is None:
                        res["note"] = ("aucune zone restreinte dans l'emprise du trajet : l'interface n'en propose pas, "
                                       "le passage est impossible (zone au-delà de la portée maximale)")
                res["zone"] = zone
                run = reconstruire(client, did, trip, p["id"], zone, a.modeles)
                res["reconstruction"] = resume_run(run)
                res["reconstruction"]["erreur_position"] = erreur_position(run, vraies_positions(x))
            if x == "B":
                # niveau des positions : même fonction que l'interface (detect.analyser_trajet)
                traj = positions(jeu, params)
                t = traj[traj["trip_id"] == trip]
                det = detect.analyser_trajet(t, params["types_bateau"]["ais_peche"], params, pipeline.origine_pour(traj))
                pts = det.points
                dans = (pts["timestamp"] >= utc(v["start"])) & (pts["timestamp"] < utc(v["end"]))
                res["positions_decalees"] = int(dans.sum())
                res["part_decalees_ecartees"] = round(float((pts.loc[dans, "statut"] == "suspecte").mean()), 3)
                res["positions_ecartees_hors_fenetre"] = int(((pts["statut"] == "suspecte") & ~dans).sum())
                res["types_alertes"] = sorted({al["type"] for al in integ})
            resultats["bateaux"][x] = res
            r = res.get("reconstruction", {})
            print(f"{x} ({v['type']}) : période trouvée={res['periode_trouvee']} "
                  f"{res.get('periode', {}).get('duree_min', '—')} min, zone={res.get('zone')}, "
                  f"P(passage)={r.get('probabilites')}, erreur moyenne km="
                  f"{ {m: e['moyenne_km'] for m, e in r.get('erreur_position', {}).items()} }")
            if x == "B":
                print(f"   alertes {res['types_alertes']}, positions décalées écartées {res['part_decalees_ecartees']:.0%}, "
                      f"écartées hors fenêtre {res['positions_ecartees_hors_fenetre']}")
    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(json.dumps(resultats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> {SORTIE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
