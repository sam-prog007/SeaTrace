"""Comparatif des cinq modèles en ligne de commande (démo de secours, sans interface).

Exemples :
  python scripts/comparer.py --trajet S6-ambigu --zone Z1
  python scripts/comparer.py --trajet S6-ambigu --zone Z1 --rf --modeles hmm particules pont
Écrit tous les chemins (avec leur probabilité) et un rapport HTML dans data/chemins/<identifiant>/.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import archive, comparatif as CP, config, detect, pipeline, report  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trajet", required=True)
    ap.add_argument("--zone", default=None)
    ap.add_argument("--periode", default=None, help="P1, P2… (défaut : la plus longue)")
    ap.add_argument("--rf", action="store_true", help="inclure les observations RF simulées de la démo")
    ap.add_argument("--modeles", nargs="*", default=CP.MODELES, choices=CP.MODELES)
    args = ap.parse_args()
    params = config.charger()
    jeu = pipeline.charger_demo(params)
    traj = jeu.positions[jeu.positions["trip_id"] == args.trajet]
    if traj.empty:
        sys.exit(f"Trajet introuvable : {args.trajet}")
    b = traj["boat_id"].iloc[0]
    tb = jeu.bateaux[b]["type_bateau"]
    ptype = params["types_bateau"][tb]
    a = detect.analyser_trajet(traj, ptype, params)
    g = pipeline.geographie_pour(a.points, params)
    grille = pipeline.grille_pour(g, a.points, params)
    if not a.periodes:
        sys.exit("Aucune période à reconstruire sur ce trajet.")
    per = next((p for p in a.periodes if p.identifiant == args.periode), None) or max(a.periodes, key=lambda p: p.duree_min)
    deb, fin = pipeline.observations_bornes(a, per, params)
    externes = pipeline.observations_rf(jeu.rf, args.trajet, per.debut, per.fin) if args.rf else []
    pts = a.points[a.points["statut"] == "observee"]
    fiables = pd.DataFrame({"x": pts["x"], "y": pts["y"], "t_s": pts["t_s"],
                            "sigma_km": np.hypot(params["modele"]["incertitude_bornes_m"],
                                                 pts["accuracy_m"].fillna(15.0)) / 1000})
    comp = CP.reconstruire_tous(grille, deb, fin, ptype, params, args.zone, externes, per, fiables, args.modeles)
    print(f"Period {per.identifiant}: {per.debut:%H:%M} -> {per.fin:%H:%M} ({per.duree_min} min), zone {args.zone}")
    for m, r in comp.resultats.items():
        p = "failed" if r.statut != "ok" else ("—" if r.proba_passage is None else f"{100 * r.proba_passage:5.1f} %")
        print(f"  {m:<11} {p:>8}  {len(r.chemins):>4} paths  {r.temps_calcul_s:5.2f} s  {' '.join(r.raisons)[:110]}")
    seg = comp.segmentation or {}
    for f in seg.get("families", []):
        print(f"  {f['id']} ({f['description']}): " +
              ", ".join(f"{m} {100 * v:.0f} %" for m, v in f["probability_by_model"].items()))
    print("Reading:", comp.synthese["lecture"])
    for note in comp.synthese.get("notes", []):
        print("  -", note)
    context = {"dataset": "demo", "dataset_name": "Demonstration (synthetic)", "file_sha256": jeu.empreinte,
               "boat_id": b, "trip_id": args.trajet, "boat_type": tb, "period": per.identifiant,
               "start": f"{per.debut:%Y-%m-%dT%H:%M:%SZ}", "end": f"{per.fin:%Y-%m-%dT%H:%M:%SZ}",
               "duration_min": per.duree_min, "cause": per.cause, "boundary_reliability": per.fiabilite_bornes,
               "zone_name": g.zone(args.zone).nom if args.zone else None,
               "external_observations": [o.libelle for o in externes], "versions": config.versions(params),
               "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "synthetic": True}
    run = archive.comparaison_vers_dict(comp, grille, per.debut, args.zone, context)
    run["run_id"] = archive.nouveau_run_id(json.dumps(context, sort_keys=True))
    d = archive.sauver(run)
    (d / "report.html").write_text(report.comparison_report_html(run), encoding="utf-8")
    print(f"Paths and report: {d.relative_to(RACINE)}/")


if __name__ == "__main__":
    main()
