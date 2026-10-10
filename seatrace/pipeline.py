"""Assemblage des briques : chargement, analyse d'un trajet, préparation d'une reconstruction.

Ce module ne lit JAMAIS les fichiers d'étiquettes (data/etiquettes) : ils ne
servent qu'à l'évaluation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, detect, geo, reconstruct as R, report, schema

RACINE = config.RACINE
DOSSIER_DEMO = RACINE / "data" / "demo"
DOSSIER_IMPORTS = RACINE / "data" / "imports"
DOSSIER_RAPPORTS = RACINE / "data" / "rapports"
CHEMIN_JOURNAL = RACINE / "data" / "journal" / "journal.jsonl"
CHEMIN_METRIQUES = RACINE / "eval" / "resultats" / "metriques.json"

EXPLICATIONS_ALTERNATIVES = {
    "saut_position": ["erreur ponctuelle de positionnement satellite (multi-trajets près des côtes)",
                      "brouillage ou leurrage du signal satellite reçu par le traceur",
                      "données de deux appareils mélangées sous un même identifiant",
                      "altération des données après leur collecte"],
    "incoherence_vitesse_cap": ["capteur de vitesse ou de cap défaillant",
                                "conversion d'unités erronée lors d'un export",
                                "positions modifiées sans les champs de vitesse/cap"],
    "derive_cumulee": ["dérive lente d'un récepteur satellite", "courant fort non pris en compte",
                       "décalage progressif introduit dans les positions"],
    "identite_dedoublee": ["deux traceurs configurés avec le même identifiant",
                           "réaffectation d'un traceur à un autre bateau",
                           "flux injecté usurpant l'identifiant"],
    "rejeu": ["erreur de synchronisation ou de mise en cache d'un logiciel de collecte",
              "réinjection volontaire de données anciennes"],
    "doublon": ["double envoi par le traceur ou par la passerelle de collecte"],
    "interruption": ["zone sans couverture réseau ou satellite", "batterie ou alimentation coupée",
                     "défaillance matérielle du traceur", "suppression volontaire de positions"],
    "passage": ["passage effectivement réalisé (avec ou sans autorisation inconnue de nous)",
                "limites de zone ou horaires d'application différents de ceux utilisés",
                "observations de début/fin erronées", "hypothèses de vitesse ou de cap inadaptées"],
}


@dataclass
class Jeu:
    positions: pd.DataFrame
    bateaux: dict
    routes: dict
    rf: pd.DataFrame
    empreinte: str
    origine: str
    import_info: dict


def charger_demo(params) -> Jeu:
    octets = (DOSSIER_DEMO / "positions_demo.csv").read_bytes()
    res = schema.lire_csv_securise(octets, "positions_demo.csv", params["import"])
    bateaux = json.loads((DOSSIER_DEMO / "bateaux_demo.json").read_text(encoding="utf-8"))
    routes = json.loads((DOSSIER_DEMO / "routes_declarees.json").read_text(encoding="utf-8"))
    rf_path = DOSSIER_DEMO / "observations_rf_simulees.csv"
    rf = pd.read_csv(rf_path) if rf_path.exists() else pd.DataFrame()
    if len(rf):
        rf["timestamp"] = pd.to_datetime(rf["timestamp"], utc=True)
    return Jeu(res.donnees, bateaux, routes, rf, res.empreinte, "demonstration_synthetique",
               {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                "avertissements": res.avertissements, "nom": "positions_demo.csv"})


def geographie_pour(points: pd.DataFrame, params):
    demo = geo.geographie_demo()
    if geo.dans_bornes(demo, points["x"], points["y"]):
        return demo
    return geo.geographie_generique(points["x"], points["y"], params["modele"]["marge_grille_km"])


def grille_pour(geographie, points, params):
    taille = params["modele"]["taille_case_km"]
    if geographie.fictive:
        return geo.construire_grille(geographie, taille)
    # Géographie inconnue : grille limitée, taille de case adaptée (au plus ~150 x 150 cases)
    xmin, xmax, ymin, ymax = geographie.bornes
    taille = max(taille, (xmax - xmin) / 150, (ymax - ymin) / 150)
    return geo.construire_grille(geographie, taille)


def ecart_trajet_prevu(points: pd.DataFrame, route_xy) -> dict | None:
    if not route_xy or len(route_xy) < 2:
        return None
    fiables = points[points["statut"] == "observee"]
    if fiables.empty:
        return None
    d = geo.distance_a_polyligne(fiables["x"], fiables["y"], route_xy)
    return {"ecart_max_km": round(float(d.max()), 2), "ecart_moyen_km": round(float(d.mean()), 2),
            "part_points_a_plus_de_1km_pct": round(100 * float((d > 1.0).mean()), 1)}


def observations_bornes(analyse: detect.Analyse, periode: detect.Periode, params):
    pts = analyse.points
    pm = params["modele"]
    acc = params["detection"]["precision_position_defaut_m"]

    def obs(i, t0):
        a = pts["accuracy_m"].iloc[i]
        a = acc if not np.isfinite(a) else a
        return R.Observation(
            x=float(pts["x"].iloc[i]), y=float(pts["y"].iloc[i]),
            sigma_km=math.hypot(pm["incertitude_bornes_m"], a) / 1000.0,
            t_s=float(pts["t_s"].iloc[i] - t0), source=str(pts["source"].iloc[i]),
            cap_deg=float(pts["cog_deg"].iloc[i]) if np.isfinite(pts["cog_deg"].iloc[i]) else None,
            vitesse_kn=float(pts["sog_kn"].iloc[i]) if np.isfinite(pts["sog_kn"].iloc[i]) else None,
        )

    t0 = float(pts["t_s"].iloc[periode.i_avant])
    return obs(periode.i_avant, t0), obs(periode.i_apres, t0)


def observations_rf(rf: pd.DataFrame, trip_id: str, debut_ts, fin_ts):
    if rf is None or rf.empty:
        return []
    sel = rf[(rf["trip_id"] == trip_id) & (rf["timestamp"] > debut_ts) & (rf["timestamp"] < fin_ts)]
    out = []
    for _, o in sel.iterrows():
        x, y = geo.vers_km(o["lat"], o["lon"])
        out.append(R.Observation(
            x=float(x), y=float(y), sigma_km=float(o["precision_m"]) / 1000.0,
            t_s=float((o["timestamp"] - debut_ts).total_seconds()),
            p_attribution=float(o["proba_attribution"]), source="rf_simulee", statut="simulee",
            libelle=f"{o['obs_id']} (observation RF simulée)",
        ))
    return out


def metriques_evaluation():
    if CHEMIN_METRIQUES.exists():
        return json.loads(CHEMIN_METRIQUES.read_text(encoding="utf-8"))
    return None


def contexte_rapport(jeu, dossier, bateau, meta, type_b, params, trip, analyse, ecart, periode, zone_id,
                     resultat, externes, decision, geographie):
    pts = analyse.points
    rec = None
    if periode is not None and resultat is not None:
        z = geographie.zone(zone_id) if zone_id else None
        rec = {
            "periode": {"identifiant": periode.identifiant, "debut": f"{periode.debut:%Y-%m-%d %H:%M:%S} UTC",
                        "fin": f"{periode.fin:%Y-%m-%d %H:%M:%S} UTC", "duree_min": periode.duree_min,
                        "cause": periode.cause, "fiabilite_bornes": periode.fiabilite_bornes},
            "zone": {"identifiant": zone_id, "nom": z.nom if z else "—", "description": z.description if z else "—"},
            "proba_passage": resultat.proba_passage, "statut": resultat.statut, "raisons": resultat.raisons,
            "lecture": resultat.lecture,
            "controle_mc": resultat.controle_mc, "hypotheses": resultat.hypotheses,
            "sensibilite": [{"variation": l["variation"],
                             "proba_passage": "impossible" if l["proba_passage"] is None else f"{100 * l['proba_passage']:.0f} %"}
                            for l in resultat.sensibilite],
            "alternatives": EXPLICATIONS_ALTERNATIVES["passage"],
            "observations_externes": ", ".join(o.libelle for o in externes) or "aucune",
        }
    return {
        "dossier": dossier,
        "bateau": {"boat_id": bateau, "nom": (meta or {}).get("nom", "—"),
                   "type": params["types_bateau"][type_b]["libelle"]},
        "trip_id": trip,
        "periode_donnees": f"{pts['timestamp'].iloc[0]:%Y-%m-%d %H:%M} → {pts['timestamp'].iloc[-1]:%H:%M} UTC",
        "sources": ", ".join(sorted(pts["source"].unique())) + (" + observations RF simulées" if externes else ""),
        "empreinte_fichier": jeu.empreinte,
        "mention_synthetique": ("bateaux, trajets, géographie et zone sont fictifs ; manipulations simulées en laboratoire."
                                if bool(pts["synthetic"].any()) or jeu.origine == "demonstration_synthetique" else ""),
        "qualite": analyse.qualite,
        "score_anomalie": analyse.score_anomalie,
        "anomalies": [{"identifiant": a.identifiant, "libelle": a.libelle, "categorie": a.categorie,
                       "debut": f"{a.debut:%H:%M:%S}", "fin": f"{a.fin:%H:%M:%S}", "explication": a.explication,
                       "valeurs": a.valeurs, "alternatives": EXPLICATIONS_ALTERNATIVES.get(a.type, [])}
                      for a in analyse.alertes],
        "ecart_trajet_prevu": ecart,
        "reconstruction": rec,
        "versions": config.versions(params),
        "decision_humaine": decision,
        "limites": report.LIMITES_GENERALES,
    }
