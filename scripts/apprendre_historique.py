"""Apprend le « modèle appris » (lois de vitesse, de changement de cap, densité de trafic)
sur des historiques de trajets.

Exemples :
  python scripts/apprendre_historique.py                         # flotte synthétique (démo)
  python scripts/apprendre_historique.py --fichier data/ais/aisdk-2024-06-15.csv \
      --sortie data/modeles/appris_dma.npz
Puis, pour l'utiliser : modifier modeles.appris.fichier dans config/parametres.json
(ou le choisir dans l'interface).

Les positions repérées comme suspectes par la détection sont écartées avant l'apprentissage
(on n'apprend pas sur des données altérées), et seuls les segments continus sont utilisés.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import appris, config, detect, scenarios, schema  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fichier", help="CSV au format SeaTrace (ex. sortie de importer_ais.py)")
    ap.add_argument("--type-defaut", default="ais_autre", help="type de bateau si les métadonnées manquent")
    ap.add_argument("--sortie", default="data/modeles/appris_synthetique.npz")
    args = ap.parse_args()
    params = config.charger()
    if args.fichier:
        chemin = Path(args.fichier)
        res = schema.lire_csv_securise(chemin.read_bytes(), chemin.name,
                                       {**params["import"], "taille_max_octets": params["import"]["taille_max_ais_octets"]})
        df = res.donnees
        meta_f = chemin.with_suffix(".json")
        bateaux = json.loads(meta_f.read_text(encoding="utf-8")).get("bateaux", {}) if meta_f.exists() else {}
        source = f"fichier {schema.nom_affichable(chemin.name)}"
    else:
        flotte = scenarios.generer_flotte(params, n_bateaux=30, trajets_par_bateau=4)
        df = pd.concat([t["obs"] for t in flotte if t["jeu"] == "apprentissage"], ignore_index=True)
        bateaux = {t["boat_id"]: {"type_bateau": t["type_bateau"]} for t in flotte}
        source = "flotte synthétique (bateaux du jeu d'apprentissage uniquement)"
    # écarter les positions suspectes
    propres = []
    for (b, t), g in df.groupby(["boat_id", "trip_id"], sort=False):
        tb = bateaux.get(b, {}).get("type_bateau", args.type_defaut)
        ptype = params["types_bateau"].get(tb, params["types_bateau"][args.type_defaut])
        a = detect.analyser_trajet(g, ptype, params)
        propres.append(a.points)
    pts = pd.concat(propres, ignore_index=True)
    m = appris.apprendre(pts, dt_ref_s=params["modele"]["pas_temps_s"], nb_caps=params["modele"]["nb_caps"],
                         source=source)
    m.sauver(RACINE / args.sortie)
    print(json.dumps(m.description(), ensure_ascii=False, indent=2))
    print(f"Modèle écrit : {args.sortie}")


if __name__ == "__main__":
    main()
