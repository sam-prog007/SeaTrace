"""Boosting stochastique : prédiction DIRECTE de la probabilité de passage.

Contrairement aux quatre autres modèles, celui-ci ne simule aucun chemin. Il apprend, sur
des interruptions dont on connaît la vérité (jeu d'apprentissage synthétique, ou historiques
étiquetés d'un client), la relation entre des descripteurs de la situation et le fait que le
bateau a réellement traversé la zone.

Méthode : gradient boosting STOCHASTIQUE (Friedman 2002) — chaque arbre est ajusté sur un
sous-échantillon aléatoire des exemples (`sous_echantillon` < 1). On entraîne un petit
ENSEMBLE de modèles avec des graines différentes : la dispersion de leurs prédictions donne
une incertitude liée au caractère aléatoire de l'apprentissage (ce n'est pas un intervalle de
confiance complet).

Descripteurs (calculés sans regarder la vérité) : durée, distance entre les bornes, portée à
vitesse maximale, marge de détour nécessaire pour toucher la zone, part de la zone atteignable,
distances des bornes à la zone, interpolation linéaire, caps déclarés orientés vers la zone,
et — en « empilement » — les probabilités du HMM et du pont brownien quand elles sont fournies.

Sécurité : aucun modèle sérialisé (pickle) n'est chargé. Le modèle est ré-entraîné au
démarrage à partir d'un CSV de descripteurs versionné, ce qui est rapide et vérifiable.

Limites : il n'est valable que dans le domaine de ses exemples (ici : synthétiques, baie
fictive) ; hors de ce domaine, son résultat doit être lu comme indicatif. Il n'explique pas
« par où » le bateau est passé.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import KN_EN_KMH, Grille

DESCRIPTEURS = [
    "duree_min", "distance_km", "portee_km", "rapport_distance_portee", "marge_detour",
    "part_zone_atteignable", "dist_debut_zone_km", "dist_fin_zone_km", "dist_ligne_zone_km",
    "interpolation_traverse", "cap_debut_vers_zone", "cap_fin_depuis_zone", "vitesse_max_kn",
    "p_hmm", "p_pont",
]
MANQUANT = -1.0


def _cos_cap(cap_deg, vitesse_kn, ox, oy, cx, cy, sens=1.0):
    if cap_deg is None or vitesse_kn is None or not np.isfinite(cap_deg) or not np.isfinite(vitesse_kn) \
            or vitesse_kn < 1.5:
        return 0.0
    vx, vy = math.sin(math.radians(cap_deg)), math.cos(math.radians(cap_deg))
    dx, dy = sens * (cx - ox), sens * (cy - oy)
    d = math.hypot(dx, dy)
    return 0.0 if d < 1e-9 else (vx * dx + vy * dy) / d


def descripteurs(grille: Grille, zone_id, debut, fin, params_type, p_hmm=None, p_pont=None) -> dict:
    duree_h = (fin.t_s - debut.t_s) / 3600
    vmax = params_type["vitesse_max_kn"]
    portee = vmax * KN_EN_KMH * duree_h
    dist = math.hypot(fin.x - debut.x, fin.y - debut.y)
    gx, gy = np.meshgrid(grille.xc, grille.yc)
    zm = grille.zones[zone_id] & grille.mer if zone_id else np.zeros_like(grille.mer)
    if zm.any():
        zx, zy = gx[zm], gy[zm]
        da = np.hypot(zx - debut.x, zy - debut.y)
        db = np.hypot(zx - fin.x, zy - fin.y)
        detour = da + db
        marge = float((portee - detour.min()) / max(portee, 1e-9))
        part = float(np.mean(detour <= portee))
        # distance du segment [début, fin] à la zone
        vx, vy = fin.x - debut.x, fin.y - debut.y
        l2 = vx * vx + vy * vy
        tt = np.clip(((zx - debut.x) * vx + (zy - debut.y) * vy) / l2, 0, 1) if l2 > 0 else np.zeros_like(zx)
        dl = float(np.min(np.hypot(zx - (debut.x + tt * vx), zy - (debut.y + tt * vy))))
        cx, cy = float(zx.mean()), float(zy.mean())
        d_deb, d_fin = float(da.min()), float(db.min())
    else:
        marge, part, dl, d_deb, d_fin, cx, cy = -1.0, 0.0, 99.0, 99.0, 99.0, debut.x, debut.y
    return {
        "duree_min": duree_h * 60, "distance_km": dist, "portee_km": portee,
        "rapport_distance_portee": dist / max(portee, 1e-9), "marge_detour": marge,
        "part_zone_atteignable": part, "dist_debut_zone_km": d_deb, "dist_fin_zone_km": d_fin,
        "dist_ligne_zone_km": dl, "interpolation_traverse": float(dl <= grille.taille / 2),
        "cap_debut_vers_zone": _cos_cap(debut.cap_deg, debut.vitesse_kn, debut.x, debut.y, cx, cy),
        "cap_fin_depuis_zone": _cos_cap(fin.cap_deg, fin.vitesse_kn, fin.x, fin.y, cx, cy, sens=-1.0),
        "vitesse_max_kn": vmax,
        "p_hmm": MANQUANT if p_hmm is None else float(p_hmm),
        "p_pont": MANQUANT if p_pont is None else float(p_pont),
    }


HP_DEFAUT = {"n_estimators": 200, "max_depth": 3, "learning_rate": 0.05, "subsample": 0.7,
             "min_samples_leaf": 1, "max_features": None}


def hp_depuis_config(pb: dict) -> dict:
    return {"n_estimators": int(pb["n_arbres"]), "max_depth": int(pb["profondeur"]),
            "learning_rate": float(pb["taux_apprentissage"]), "subsample": float(pb["sous_echantillon"]),
            "min_samples_leaf": 1, "max_features": None}


def _nettoyer_hp(hp: dict) -> dict:
    out = dict(HP_DEFAUT)
    for k in out:
        if k in hp:
            out[k] = hp[k]
    mf = out["max_features"]
    out["max_features"] = None if mf in (None, "none", "None") else (mf if mf == "sqrt" else float(mf))
    return {"n_estimators": int(out["n_estimators"]), "max_depth": int(out["max_depth"]),
            "learning_rate": float(out["learning_rate"]), "subsample": float(out["subsample"]),
            "min_samples_leaf": int(out["min_samples_leaf"]), "max_features": out["max_features"]}


def _bateaux(donnees: pd.DataFrame):
    return donnees["trip_id"].astype(str).str.split("-").str[0].to_numpy()


def brier_cv(donnees: pd.DataFrame, hp: dict, graine=0, n_plis=5) -> float:
    """Score de Brier en validation croisée GROUPÉE PAR BATEAU (un bateau n'est jamais à la fois
    dans l'apprentissage et dans la validation d'un pli)."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import GroupKFold
    X = donnees[DESCRIPTEURS].to_numpy(float)
    y = donnees["verite_passage"].astype(int).to_numpy()
    g = _bateaux(donnees)
    hp = _nettoyer_hp(hp)
    erreurs = np.zeros(len(y))
    for tr, va in GroupKFold(n_splits=min(n_plis, len(set(g)))).split(X, y, g):
        if len(np.unique(y[tr])) < 2:
            erreurs[va] = (y[va] - y[tr].mean()) ** 2
            continue
        m = GradientBoostingClassifier(random_state=graine, **hp).fit(X[tr], y[tr])
        erreurs[va] = (m.predict_proba(X[va])[:, 1] - y[va]) ** 2
    return float(erreurs.mean())


def regler(donnees: pd.DataFrame, n_essais=40, graine=0, hp_reference=None) -> dict:
    """Recherche des hyperparamètres avec Optuna (TPE) : minimise le Brier en validation croisée
    groupée par bateau. Optuna n'est nécessaire QUE pour cette étape : le résultat est écrit dans un
    fichier JSON relu ensuite sans Optuna."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objectif(trial):
        hp = {"n_estimators": trial.suggest_int("n_estimators", 50, 400, step=25),
              "max_depth": trial.suggest_int("max_depth", 1, 5),
              "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
              "subsample": trial.suggest_float("subsample", 0.4, 1.0),
              "min_samples_leaf": trial.suggest_int("min_samples_leaf", 1, 20),
              "max_features": trial.suggest_categorical("max_features", ["sqrt", "0.5", "1.0"])}
        return brier_cv(donnees, hp, graine)

    reference = _nettoyer_hp(hp_reference or HP_DEFAUT)
    etude = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=graine))
    etude.optimize(objectif, n_trials=int(n_essais))
    meilleurs = _nettoyer_hp(etude.best_params)
    essais, record = [], np.inf
    for t in etude.trials:
        record = min(record, t.value)
        essais.append({"number": t.number, "value": round(float(t.value), 5), "best_so_far": round(float(record), 5)})
    try:
        importance = {k: round(float(v), 3) for k, v in optuna.importance.get_param_importances(etude).items()}
    except Exception:  # trop peu d'essais pour estimer l'importance
        importance = {}
    base = brier_cv(donnees, reference, graine)
    return {"best_params": meilleurs, "best_cv_brier": round(float(etude.best_value), 5),
            "reference_params": reference, "reference_cv_brier": round(float(base), 5),
            "n_trials": len(etude.trials), "n_examples": int(len(donnees)),
            "n_boats": int(len(set(_bateaux(donnees)))), "param_importance": importance, "trials": essais,
            "cv": "5-fold, grouped by boat", "metric": "Brier score", "sampler": "TPE (seed %d)" % graine,
            "optuna_version": optuna.__version__}


class EnsembleBoosting:
    def __init__(self, donnees: pd.DataFrame, hp: dict, n_modeles=7):
        from sklearn.ensemble import GradientBoostingClassifier
        X = donnees[DESCRIPTEURS].to_numpy(float)
        y = donnees["verite_passage"].astype(int).to_numpy()
        if len(np.unique(y)) < 2:
            raise ValueError("The boosting training data contain a single class.")
        self.hp = _nettoyer_hp(hp)
        self.n = len(y)
        self.taux_positifs = float(y.mean())
        self.modeles = [GradientBoostingClassifier(random_state=graine, **self.hp).fit(X, y)
                        for graine in range(n_modeles)]
        imp = np.mean([m.feature_importances_ for m in self.modeles], axis=0)
        self.importances = dict(sorted(zip(DESCRIPTEURS, imp.round(4).tolist()), key=lambda kv: -kv[1]))
        # Domaine d'entraînement : bornes des descripteurs (pour signaler l'extrapolation)
        self.bornes = {c: (float(donnees[c].min()), float(donnees[c].max())) for c in DESCRIPTEURS}

    def predire(self, d: dict):
        x = np.array([[d[c] for c in DESCRIPTEURS]], float)
        ps = np.array([m.predict_proba(x)[0, 1] for m in self.modeles])
        hors = [c for c in DESCRIPTEURS if not (self.bornes[c][0] <= d[c] <= self.bornes[c][1])]
        return float(ps.mean()), float(ps.min()), float(ps.max()), hors


def hyperparametres(params, racine: Path):
    """Hyperparamètres réglés par Optuna si le fichier existe, sinon valeurs de config/parametres.json.
    Retourne (hp, résumé du réglage ou None)."""
    import json
    pb = params["modeles"]["boosting"]
    f = (racine / pb["fichier_reglage"]).resolve()
    if f.exists() and racine.resolve() in f.parents:
        r = json.loads(f.read_text(encoding="utf-8"))
        return _nettoyer_hp(r["best_params"]), r
    return hp_depuis_config(pb), None


@lru_cache(maxsize=4)
def _charger(chemin: str, cle_hp: str):
    import json
    return EnsembleBoosting(pd.read_csv(chemin), json.loads(cle_hp))


def ensemble(params, racine: Path):
    import json
    pb = params["modeles"]["boosting"]
    chemin = (racine / pb["fichier_entrainement"]).resolve()
    if not chemin.exists():
        return None
    hp, _ = hyperparametres(params, racine)
    return _charger(str(chemin), json.dumps(hp, sort_keys=True))
