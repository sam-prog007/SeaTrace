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
        return MD.echec("boosting", "No zone chosen: boosting only predicts a crossing probability.")
    ens = BO.ensemble(params, config.RACINE)
    _, reglage = BO.hyperparametres(params, config.RACINE)
    if ens is None:
        return MD.echec("boosting", "Training data missing: run `python eval/comparer_modeles.py`.")
    d = BO.descripteurs(grille, zone_id, debut, fin, ptype, p_hmm, p_pont)
    p, bas, haut, hors = ens.predire(d)
    raisons = ["Model trained on SYNTHETIC gaps in the fictitious bay: outside that domain, "
               "the result is only indicative."]
    if hors:
        raisons.append("Situation outside the training domain for: " + ", ".join(hors) + ".")
    return MD.ResultatModele(
        "boosting", p, "ok", raisons, [], None, time.perf_counter() - t0, intervalle=(bas, haut),
        diagnostics={"descriptors": {k: round(v, 4) for k, v in d.items()},
                     "importances": ens.importances, "training_examples": ens.n,
                     "training_positive_rate": round(ens.taux_positifs, 3),
                     "ensemble_spread": [round(bas, 4), round(haut, 4)], "out_of_domain": hors,
                     "tuning": None if reglage is None else {"best_cv_brier": reglage["best_cv_brier"],
                                                              "reference_cv_brier": reglage["reference_cv_brier"],
                                                              "trials": reglage["n_trials"]}},
        hypotheses={"model": "stochastic gradient boosting (ensemble of 7 seeds)",
                    "trees": ens.hp["n_estimators"],
                    "depth": ens.hp["max_depth"], "learning_rate": round(ens.hp["learning_rate"], 4),
                    "subsample": round(ens.hp["subsample"], 3), "min_samples_leaf": ens.hp["min_samples_leaf"],
                    "max_features": ens.hp["max_features"],
                    "hyperparameters": "tuned with Optuna (grouped cross-validation)" if reglage else "defaults from the config file",
                    "stacking": "uses the HMM and Brownian bridge probabilities as descriptors"},
    )


def lecture_commune(probas: dict, seuil):
    vals = [p for p in probas.values() if p is not None]
    if not vals:
        return "aucune", "No model provides a defensible estimate."
    etendue = max(vals) - min(vals)
    if etendue > seuil:
        return "divergence", (f"The models diverge (from {min(vals):.0%} to {max(vals):.0%}): their assumptions "
                              "lead to different conclusions; the tool cannot conclude.")
    if min(vals) >= 0.9:
        return "convergence_haute", "All models consider the crossing very likely (this is not proof of an offence)."
    if max(vals) <= 0.1:
        return "convergence_basse", "All models consider the crossing unlikely (this is not proof of absence)."
    return "incertain", "The models agree that the situation is uncertain: several paths remain plausible."


def notes_comparatif(res: dict, probas: dict, seuil: float) -> list:
    """Explications en clair de ce qui distingue les modèles dans CE calcul (règles fixes, pas de texte généré)."""
    notes = []
    for m, r in res.items():
        if r.statut != "ok":
            if m == "pont":
                notes.append("The Brownian bridge produced no result: it has no inertia, so when land lies on the "
                             "direct line between the two observations almost every random bridge is rejected. "
                             "Only the models with inertia (grid HMM, particles) are relevant here.")
            else:
                notes.append(f"{r.libelle} produced no result: {' '.join(r.raisons)}")
    ref = [probas[m] for m in ("hmm", "particules") if m in probas]
    if "appris" in probas and ref and abs(probas["appris"] - float(np.mean(ref))) > seuil:
        notes.append("The learned model departs from the HMM and the particle filter. It was fitted on historical "
                     "tracks, so it favours the routes boats usually take; a path that is physically plausible "
                     "but unusual gets less weight. A gap here says the trip differs from the history, which is "
                     "not suspicious in itself.")
    if "boosting" in res and res["boosting"].statut == "ok":
        d = res["boosting"].diagnostics
        if d.get("out_of_domain"):
            notes.append("Boosting is extrapolating (descriptors outside its training range): ignore its figure.")
        elif d.get("importances", {}).get("p_hmm", 0) > 0.5:
            notes.append("Boosting leans mostly on the HMM probability (stacking): it re-calibrates the HMM rather "
                         "than adding independent evidence, so its agreement with the HMM is not a second opinion.")
    vals = list(probas.values())
    if len(vals) >= 2 and max(vals) - min(vals) <= seuil:
        notes.append("The models rest on different assumptions yet land close together: the answer does not hinge "
                     "on the modelling choice.")
    return notes


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
                res[m] = (MD.echec("appris", "No learned model available: run "
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
            res[m] = MD.echec(m, f"Computation error ({type(exc).__name__}): no result for this model.")
    if "boosting" in modeles:
        try:
            ph = res.get("hmm")
            pp = res.get("pont")
            res["boosting"] = _boosting(grille, debut, fin, ptype, params, zone_id,
                                        ph.proba_passage if ph else None, pp.proba_passage if pp else None)
        except Exception as exc:
            res["boosting"] = MD.echec("boosting", f"Computation error ({type(exc).__name__}).")

    seg = None
    chemins = {m: r.chemins for m, r in res.items() if r.statut == "ok" and r.chemins}
    if chemins:
        try:
            seg = SG.segmenter(chemins, debut, fin, params, graine)
        except Exception as exc:
            seg = {"error": f"Segmentation impossible ({type(exc).__name__})."}
    probas = {m: r.proba_passage for m, r in res.items() if r.statut == "ok"}
    code, texte = lecture_commune(probas, float(params["modeles"]["seuil_divergence_modeles"])) if zone_id else \
        ("sans_zone", "No zone chosen: corridors and path families only.")
    vals = [p for p in probas.values() if p is not None]
    synthese = {
        "lecture": texte, "code": code,
        "proba_min": min(vals) if vals else None, "proba_max": max(vals) if vals else None,
        "modeles_en_echec": [m for m, r in res.items() if r.statut != "ok"],
        "rappel": "Probabilities are NOT averaged: each model rests on different assumptions. "
                  "A crossing probability is not a probability of fraud.",
        "notes": notes_comparatif(res, probas, float(params["modeles"]["seuil_divergence_modeles"])),
    }
    n_pas = next((r.n_pas for r in res.values() if r.n_pas), 0)
    dt = next((r.dt_s for r in res.values() if r.dt_s), 0.0)
    return Comparaison(res, seg, synthese, n_pas, dt, time.perf_counter() - t0,
                       {"courant_kmh": list(courant), "zone": zone_id, "observations_externes": len(externes)})
