"""Analyse en ligne de commande (sans interface) : utile pour la démo de secours et les tests.

Exemples :
  python scripts/analyser.py --trajet S6-ambigu --zone Z1
  python scripts/analyser.py --trajet S6-ambigu --zone Z1 --rf
  python scripts/analyser.py --fichier mes_positions.csv --trajet T1 --type semi_rigide_location
"""

import argparse
import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import audit, config, detect, pipeline, reconstruct as R, report, schema  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Analyse SeaTrace d'un trajet (traitement local).")
    ap.add_argument("--fichier", help="CSV de positions (sinon : données de démonstration)")
    ap.add_argument("--trajet", required=True)
    ap.add_argument("--type", help="type de bateau si absent des métadonnées")
    ap.add_argument("--zone", default=None)
    ap.add_argument("--periode", default=None, help="identifiant de période (P1, P2…) ; défaut : la plus longue")
    ap.add_argument("--rf", action="store_true", help="inclure les observations RF simulées de la démo")
    ap.add_argument("--sortie", default=str(pipeline.DOSSIER_RAPPORTS))
    args = ap.parse_args()

    params = config.charger()
    if args.fichier:
        octets = Path(args.fichier).read_bytes()
        res = schema.lire_csv_securise(octets, args.fichier, params["import"])
        jeu = pipeline.Jeu(res.donnees, {}, {}, None, res.empreinte, "import_utilisateur", {})
    else:
        jeu = pipeline.charger_demo(params)
    traj = jeu.positions[jeu.positions["trip_id"] == args.trajet]
    if traj.empty:
        sys.exit(f"Trajet introuvable : {args.trajet}")
    bateau = traj["boat_id"].iloc[0]
    meta = jeu.bateaux.get(bateau)
    type_b = (meta or {}).get("type_bateau") or args.type
    if type_b not in params["types_bateau"]:
        sys.exit("Type de bateau inconnu : préciser --type " + "|".join(params["types_bateau"]))
    ptype = params["types_bateau"][type_b]
    analyse = detect.analyser_trajet(traj, ptype, params)
    pts = analyse.points
    geographie = pipeline.geographie_pour(pts, params)
    grille = pipeline.grille_pour(geographie, pts, params)
    route = jeu.routes.get(args.trajet, {}).get("route_declaree_xy")
    ecart = pipeline.ecart_trajet_prevu(pts, route)
    print(f"Score d'anomalie : {analyse.score_anomalie}/100 · qualité : {analyse.qualite}")
    for a in analyse.alertes:
        print(f"  [{a.categorie}] {a.libelle} {a.debut:%H:%M}-{a.fin:%H:%M} : {a.explication}")
    periode, resultat, externes = None, None, []
    if analyse.periodes:
        periode = next((p for p in analyse.periodes if p.identifiant == args.periode), None) or \
            max(analyse.periodes, key=lambda p: p.duree_min)
        deb, fin = pipeline.observations_bornes(analyse, periode, params)
        if args.rf:
            externes = pipeline.observations_rf(jeu.rf, args.trajet, periode.debut, periode.fin)
        resultat = R.reconstruire(grille, deb, fin, ptype, params["modele"], args.zone, externes, graine=7)
        if resultat.proba_passage is not None:
            sens = R.analyse_sensibilite(grille, deb, fin, ptype, params["modele"], args.zone, externes)
            resultat = R.qualifier(resultat, sens, periode.fiabilite_bornes, True, periode.duree_min)
        print(f"Période {periode.identifiant} ({periode.duree_min} min) · zone {args.zone} · "
              f"passage = {resultat.proba_passage} · statut = {resultat.statut}")
        for x in resultat.raisons:
            print("   -", x)
    dossier = f"ST-{args.trajet}-{jeu.empreinte[:8]}"
    ctx = pipeline.contexte_rapport(jeu, dossier, bateau, meta, type_b, params, args.trajet, analyse, ecart,
                                    periode, args.zone, resultat, externes, None, geographie)
    rap = report.construire(ctx)
    rap["empreinte_rapport"] = report.empreinte_json(rap)
    png = report.carte_png(pts, geographie, route, resultat, grille, args.zone, externes)
    sortie = Path(args.sortie)
    sortie.mkdir(parents=True, exist_ok=True)
    nom = f"seatrace_{dossier}"
    (sortie / f"{nom}.html").write_text(report.vers_html(rap, png), encoding="utf-8")
    (sortie / f"{nom}.json").write_text(json.dumps(rap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    audit.Journal(pipeline.CHEMIN_JOURNAL).ajouter("export_cli", {"dossier": dossier, "empreinte_rapport": rap["empreinte_rapport"][:16]})
    print(f"Rapport : {sortie / (nom + '.html')}")


if __name__ == "__main__":
    main()
