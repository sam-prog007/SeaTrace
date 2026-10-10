"""Lancement des cinq modèles sur une même période, segmentation commune et comparatif.

Les modèles :
  hmm         HMM sur grille à cap persistant (V1, paramètres réglés)
  appris      même machinerie, lois estimées sur des historiques (AIS ou client)
  particules  filtre particulaire guidé + lissage FFBSi (espace continu)
  pont        pont brownien (variance de mobilité estimée sur le trajet)
  boosting    boosting stochastique : probabilité prédite directement (pas de chemins)

Règle de prudence : SeaTrace ne fait PAS la moyenne des modèles pour fabriquer un chiffre
unique. Il montre chaque estimation, leur écart, et signale quand des modèles raisonnables
divergent (l'outil ne permet alors pas de conclure).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from . import appris as AP
from . import boosting as BO
from . import config
from . import modeles as MD
from . import particules as PF
from . import pont_brownien as BB
from . import reconstruct as R
from . import segmentation as SG

MODELES = ["hmm", "appris", "particules", "pont", "boosting"]


@dataclass
class Comparaison:
    resultats: dict
    segmentation: dict | None
    synthese: dict
    n_pas: int
    dt_s: float
    temps_total_s: float
    hypotheses_communes: dict = field(default_factory=dict)


def charger_appris(params):
    pa = params["modeles"]["appris"]
    chemin = (config.RACINE / pa["fichier"]).resolve()
    if not chemin.exists() or config.RACINE.resolve() not in chemin.parents:
        return None
    return AP.ModeleAppris.charger(chemin, poids_densite=float(pa["poids_densite"]))


def _hmm(modele, grille, debut, fin, ptype, params, zone_id, externes, periode, graine, courant, appris,
         sensibilite):
    pm = params["modele"]
    r = R.reconstruire(grille, debut, fin, ptype, pm, zone_id, externes, graine=graine, courant_kmh=courant,
                       appris=appris)
    if r.proba_passage is not None and sensibilite and modele == "hmm":
        sens = R.analyse_sensibilite(grille, debut, fin, ptype, pm, zone_id, externes)
        r = R.qualifier(r, sens, periode.fiabilite_bornes if periode else "normale", calibre=True,
                        duree_min=periode.duree_min if periode else (fin.t_s - debut.t_s) / 60)
    return MD.depuis_hmm(modele, r, grille, zone_id)


def _boosting(grille, debut, fin, ptype, params, zone_id, p_hmm, p_pont):
    t0 = time.perf_counter()
    if not zone_id:
        return MD.echec("boosting", "Aucune zone choisie : le boosting prédit uniquement une probabilité de passage.")
    ens = BO.ensemble(params, config.RACINE)
    if ens is None:
        return MD.echec("boosting", "Données d'entraînement absentes : lancer `python eval/comparer_modeles.py`.")
    d = BO.descripteurs(grille, zone_id, debut, fin, ptype, p_hmm, p_pont)
    p, bas, haut, hors = ens.predire(d)
    raisons = ["Modèle entraîné sur des interruptions SYNTHÉTIQUES de la baie fictive : hors de ce domaine, "
               "résultat indicatif."]
    if hors:
        raisons.append("Situation hors du domaine d'entraînement pour : " + ", ".join(hors) + ".")
    return MD.ResultatModele(
        "boosting", p, "ok", raisons, [], None, time.perf_counter() - t0, intervalle=(bas, haut),
        diagnostics={"descripteurs": {k: round(v, 4) for k, v in d.items()},
                     "importances": ens.importances, "exemples_entrainement": ens.n,
                     "taux_positifs_entrainement": round(ens.taux_positifs, 3),
                     "dispersion_ensemble": [round(bas, 4), round(haut, 4)], "hors_domaine": hors},
        hypotheses={"modele": "gradient boosting stochastique (ensemble de 7 graines)",
                    "arbres": params["modeles"]["boosting"]["n_arbres"],
                    "profondeur": params["modeles"]["boosting"]["profondeur"],
                    "sous_echantillon": params["modeles"]["boosting"]["sous_echantillon"],
                    "empilement": "utilise les probabilités du HMM et du pont brownien comme descripteurs"},
    )


def lecture_commune(probas: dict, seuil):
    vals = [p for p in probas.values() if p is not None]
    if not vals:
        return "aucune", "Aucun modèle ne fournit d'estimation défendable."
    etendue = max(vals) - min(vals)
    if etendue > seuil:
        return "divergence", (f"Les modèles divergent (de {min(vals):.0%} à {max(vals):.0%}) : leurs hypothèses "
                              "mènent à des conclusions différentes ; l'outil ne permet pas de conclure.")
    if min(vals) >= 0.9:
        return "convergence_haute", "Tous les modèles jugent le passage très probable (ce n'est pas une preuve d'infraction)."
    if max(vals) <= 0.1:
        return "convergence_basse", "Tous les modèles jugent le passage peu probable (ce n'est pas une preuve d'absence)."
    return "incertain", "Les modèles concordent sur une situation incertaine : plusieurs chemins restent plausibles."


def reconstruire_tous(grille, debut, fin, ptype, params, zone_id=None, externes=None, periode=None,
                      points_fiables=None, modeles=None, graine=7, sensibilite=True) -> Comparaison:
    t0 = time.perf_counter()
    modeles = [m for m in (modeles or MODELES) if m in MODELES]
    externes = externes or []
    courant = tuple(params["modeles"].get("courant_kmh", [0.0, 0.0]))
    res = {}
    for m in modeles:
        try:
            if m == "hmm":
                res[m] = _hmm("hmm", grille, debut, fin, ptype, params, zone_id, externes, periode, graine, courant,
                              None, sensibilite)
            elif m == "appris":
                ap = charger_appris(params)
                res[m] = (MD.echec("appris", "Aucun modèle appris disponible : lancer "
                                             "`python scripts/apprendre_historique.py`.") if ap is None else
                          _hmm("appris", grille, debut, fin, ptype, params, zone_id, externes, periode, graine,
                               courant, ap, False))
            elif m == "particules":
                res[m] = PF.reconstruire_particules(grille, debut, fin, ptype, params, zone_id, externes, graine,
                                                    courant)
            elif m == "pont":
                s2, info = None, None
                if points_fiables is not None and len(points_fiables) > 0:
                    s2, info = BB.estimer_sigma_m(points_fiables["x"], points_fiables["y"], points_fiables["t_s"],
                                                  points_fiables["sigma_km"], fin.t_s - debut.t_s)
                res[m] = BB.reconstruire_pont(grille, debut, fin, ptype, params, zone_id, s2, info, externes, graine)
        except Exception as exc:  # jamais de résultat rassurant en cas de panne
            res[m] = MD.echec(m, f"Erreur de calcul ({type(exc).__name__}) : aucun résultat pour ce modèle.")
    if "boosting" in modeles:
        try:
            ph = res.get("hmm")
            pp = res.get("pont")
            res["boosting"] = _boosting(grille, debut, fin, ptype, params, zone_id,
                                        ph.proba_passage if ph else None, pp.proba_passage if pp else None)
        except Exception as exc:
            res["boosting"] = MD.echec("boosting", f"Erreur de calcul ({type(exc).__name__}).")

    seg = None
    chemins = {m: r.chemins for m, r in res.items() if r.statut == "ok" and r.chemins}
    if chemins:
        try:
            seg = SG.segmenter(chemins, debut, fin, params, graine)
        except Exception as exc:
            seg = {"erreur": f"Segmentation impossible ({type(exc).__name__})."}
    probas = {m: r.proba_passage for m, r in res.items() if r.statut == "ok"}
    code, texte = lecture_commune(probas, float(params["modeles"]["seuil_divergence_modeles"])) if zone_id else \
        ("sans_zone", "Aucune zone choisie : corridors et familles de chemins uniquement.")
    vals = [p for p in probas.values() if p is not None]
    synthese = {
        "lecture": texte, "code": code,
        "proba_min": min(vals) if vals else None, "proba_max": max(vals) if vals else None,
        "modeles_en_echec": [m for m, r in res.items() if r.statut != "ok"],
        "rappel": "Les probabilités ne sont PAS moyennées : chaque modèle repose sur des hypothèses différentes. "
                  "Une probabilité de passage n'est pas une probabilité de fraude.",
    }
    n_pas = next((r.n_pas for r in res.values() if r.n_pas), 0)
    dt = next((r.dt_s for r in res.values() if r.dt_s), 0.0)
    return Comparaison(res, seg, synthese, n_pas, dt, time.perf_counter() - t0,
                       {"courant_kmh": list(courant), "zone": zone_id, "observations_externes": len(externes)})
