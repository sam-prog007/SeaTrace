"""Banc de COMPARAISON des cinq modèles (données SYNTHÉTIQUES).

Lancement :  python eval/comparer_modeles.py            (~8 min)
             python eval/comparer_modeles.py --rapide   (~3 min)

Étapes (jeux séparés PAR BATEAU, comme eval/run_eval.py) :
1. apprentissage : le modèle appris est estimé sur les trajets du jeu d'apprentissage ;
2. le boosting est entraîné sur des interruptions des jeux d'apprentissage et de réglage
   (vérité connue) -> data/modeles/boosting_entrainement.csv ;
3. réglage : poids de la densité de trafic du modèle appris (score de Brier, jeu de réglage) ;
4. évaluation sur les bateaux jamais vus : pour chaque modèle, score de Brier, log-loss,
   calibration, erreur spatiale (distance moyenne position réelle / chemins tirés), couverture
   de la région à 90 %, temps de calcul -> eval/resultats/comparatif.json et comparatif.png.

Limite majeure : tout est synthétique, la baie est fictive et le générateur, bien que distinct
des modèles, reste une simplification. Ces chiffres ne valent PAS validation opérationnelle.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import appris as AP, boosting as BO, comparatif as CP, config, geo, reconstruct as R  # noqa: E402
from seatrace import particules as PF, pont_brownien as BB, scenarios  # noqa: E402

sys.path.insert(0, str(RACINE / "eval"))
from run_eval import brier, tirer_interruptions  # noqa: E402
from run_eval import calibration as _calibration_fr  # noqa: E402


def calibration(p, y, bornes=(0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001)):
    """Calibration bins with English keys."""
    return [{"bin": c["intervalle"], "n": c["n"], "mean_probability": c["proba_moyenne"],
             "observed_frequency": c["frequence_observee"]} for c in _calibration_fr(p, y, bornes)]

SORTIE = RACINE / "eval" / "resultats"
MODELES = ["hmm", "appris", "particules", "pont", "boosting"]


def bootstrap_brier(df, modeles, n=2000, graine=0):
    """IC à 95 % du score de Brier de chaque modèle et de l'écart au meilleur (rééchantillonnage des
    interruptions, mêmes tirages pour tous les modèles : comparaison appariée)."""
    rng = np.random.default_rng(graine)
    y = df["verite_passage"].astype(float).to_numpy()
    P = {m: df[f"p_{m}"].fillna(0.5).to_numpy(float) for m in modeles}
    idx = rng.integers(0, len(y), (n, len(y)))
    scores = {m: ((P[m][idx] - y[idx]) ** 2).mean(axis=1) for m in modeles}
    meilleur = min(modeles, key=lambda m: float(np.mean((P[m] - y) ** 2)))
    out = {}
    for m in modeles:
        diff = scores[m] - scores[meilleur]
        out[m] = {"brier_ci95": [round(float(np.percentile(scores[m], 2.5)), 4),
                                 round(float(np.percentile(scores[m], 97.5)), 4)],
                  "gap_to_best_ci95": [round(float(np.percentile(diff, 2.5)), 4),
                                       round(float(np.percentile(diff, 97.5)), 4)]}
    return meilleur, out


def bornes(tr, i0, i1, params):
    obs = tr["obs"].reset_index(drop=True)
    vrai = tr["vrai"]
    x, y = geo.vers_km(obs["lat"].to_numpy(), obs["lon"].to_numpy())
    t_obs = (obs["timestamp"] - vrai["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
    sig = math.hypot(params["modele"]["incertitude_bornes_m"], 10.0) / 1000
    deb = R.Observation(x[i0], y[i0], sig, 0.0, cap_deg=obs["cog_deg"].iloc[i0], vitesse_kn=obs["sog_kn"].iloc[i0])
    fin = R.Observation(x[i1], y[i1], sig, t_obs[i1] - t_obs[i0], cap_deg=obs["cog_deg"].iloc[i1],
                        vitesse_kn=obs["sog_kn"].iloc[i1])
    m = (vrai["t_s"] >= t_obs[i0]) & (vrai["t_s"] <= t_obs[i1])
    verite = (vrai.loc[m, "x"].to_numpy(), vrai.loc[m, "y"].to_numpy(), vrai.loc[m, "t_s"].to_numpy() - t_obs[i0])
    # positions fiables hors interruption (pour le pont brownien)
    garde = np.r_[0:i0 + 1, i1:len(obs)]
    fiables = pd.DataFrame({"x": x[garde], "y": y[garde], "t_s": t_obs[garde], "sigma_km": sig})
    return deb, fin, verite, fiables


def metriques_spatiales(chemins, verite, dt_s, n_pas):
    """Erreur attendue (km) et couverture de la région à 90 % à partir des chemins tirés."""
    if not chemins:
        return np.nan, np.nan
    vx, vy, vt = verite
    xs = np.array([c.xs for c in chemins])
    ys = np.array([c.ys for c in chemins])
    w = np.array([c.poids for c in chemins])
    w = w / w.sum()
    erreurs, couverts = [], []
    for k in range(1, n_pas):
        tx, ty = np.interp(k * dt_s, vt, vx), np.interp(k * dt_s, vt, vy)
        d = np.hypot(xs[:, k] - tx, ys[:, k] - ty)
        erreurs.append(float((w * d).sum()))
        mx, my = (w * xs[:, k]).sum(), (w * ys[:, k]).sum()
        r = np.hypot(xs[:, k] - mx, ys[:, k] - my)
        o = np.argsort(r)
        q90 = r[o][np.searchsorted(np.cumsum(w[o]), 0.9)]
        couverts.append(bool(math.hypot(tx - mx, ty - my) <= q90 + 0.1))
    return float(np.mean(erreurs)), float(np.mean(couverts))


def zone_verite(zone, verite):
    vx, vy, _ = verite
    return bool(zone.chemin().contains_points(np.column_stack([vx, vy])).any())


def lignes_boosting(trajets, grille, params, zone, rng, n_par_trajet):
    lignes = []
    for tr in trajets:
        pt = params["types_bateau"][tr["type_bateau"]]
        for i0, i1, _, _ in tirer_interruptions(tr, zone, rng, n_par_trajet):
            deb, fin, verite, fiables = bornes(tr, i0, i1, params)
            rh = R.reconstruire(grille, deb, fin, pt, params["modele"], "Z1", echantillonner=False)
            s2, info = BB.estimer_sigma_m(fiables["x"], fiables["y"], fiables["t_s"], fiables["sigma_km"],
                                          fin.t_s - deb.t_s)
            rb = BB.reconstruire_pont(grille, deb, fin, pt, params, "Z1", s2, info, n_chemins=150)
            d = BO.descripteurs(grille, "Z1", deb, fin, pt, rh.proba_passage, rb.proba_passage)
            d.update({"verite_passage": zone_verite(zone, verite), "trip_id": tr["trip_id"], "jeu": tr["jeu"]})
            lignes.append(d)
    return pd.DataFrame(lignes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapide", action="store_true")
    args = ap.parse_args()
    params = config.charger()
    rng = np.random.default_rng(23)
    SORTIE.mkdir(parents=True, exist_ok=True)
    t_total = time.perf_counter()
    flotte = scenarios.generer_flotte(params, n_bateaux=30, trajets_par_bateau=4)
    jeux = {j: [t for t in flotte if t["jeu"] == j] for j in ("apprentissage", "reglage", "evaluation")}
    g = geo.geographie_demo()
    grille = geo.construire_grille(g, params["modele"]["taille_case_km"])
    zone = g.zone("Z1")

    print("1. Modèle appris (jeu d'apprentissage)…", flush=True)
    obs = pd.concat([t["obs"] for t in jeux["apprentissage"]], ignore_index=True)
    modele_appris = AP.apprendre(obs, dt_ref_s=params["modele"]["pas_temps_s"], nb_caps=params["modele"]["nb_caps"],
                                 source="flotte synthétique (jeu d'apprentissage)")
    modele_appris.sauver(RACINE / params["modeles"]["appris"]["fichier"])

    print("2. Données d'entraînement du boosting (apprentissage + réglage)…", flush=True)
    n_b = 3 if args.rapide else 5
    df_b = lignes_boosting(jeux["apprentissage"] + jeux["reglage"], grille, params, zone, rng, n_b)
    chemin_b = RACINE / params["modeles"]["boosting"]["fichier_entrainement"]
    df_b.to_csv(chemin_b, index=False)
    BO._charger.cache_clear()
    print(f"   {len(df_b)} examples, {df_b['verite_passage'].mean():.0%} real crossings", flush=True)

    print("2b. Optuna tuning of the boosting hyperparameters (grouped CV on the training boats)…", flush=True)
    n_trials = 15 if args.rapide else int(params["modeles"]["boosting"]["essais_optuna"])
    reglage = BO.regler(df_b, n_essais=n_trials, graine=0, hp_reference=BO.hp_depuis_config(params["modeles"]["boosting"]))
    f_reglage = RACINE / params["modeles"]["boosting"]["fichier_reglage"]
    f_reglage.write_text(json.dumps(reglage, ensure_ascii=False, indent=2), encoding="utf-8")
    BO._charger.cache_clear()
    print(f"   CV Brier: reference {reglage['reference_cv_brier']} -> tuned {reglage['best_cv_brier']} "
          f"({reglage['n_trials']} trials)", flush=True)

    print("3. Réglage du poids de densité (jeu de réglage)…", flush=True)
    gaps_r = [(tr, i0, i1) for tr in jeux["reglage"] for i0, i1, _, _ in tirer_interruptions(tr, zone, rng, 2)]
    scores_densite = {}
    for beta in (0.0, 0.02, 0.05, 0.1, 0.2):
        modele_appris.poids_densite = beta
        ps, ys = [], []
        for tr, i0, i1 in gaps_r:
            deb, fin, verite, _ = bornes(tr, i0, i1, params)
            r = R.reconstruire(grille, deb, fin, params["types_bateau"][tr["type_bateau"]], params["modele"], "Z1",
                               echantillonner=False, appris=modele_appris)
            if r.proba_passage is not None:
                ps.append(r.proba_passage)
                ys.append(zone_verite(zone, verite))
        scores_densite[beta] = round(brier(ps, ys), 4)
    beta_best = min(scores_densite, key=scores_densite.get)
    modele_appris.poids_densite = beta_best
    print(f"   poids de densité retenu : {beta_best} ({scores_densite})", flush=True)

    print("4. Évaluation des cinq modèles (bateaux jamais vus)…", flush=True)
    ens = BO.ensemble(params, RACINE)
    lignes = []
    n_e = 2 if args.rapide else 4
    for tr in jeux["evaluation"]:
        pt = params["types_bateau"][tr["type_bateau"]]
        for i0, i1, _, _ in tirer_interruptions(tr, zone, rng, n_e):
            deb, fin, verite, fiables = bornes(tr, i0, i1, params)
            ligne = {"trip_id": tr["trip_id"], "type_bateau": tr["type_bateau"], "exotique": tr["exotique"],
                     "duree_min": (fin.t_s - deb.t_s) / 60, "verite_passage": zone_verite(zone, verite)}
            res = {}
            t0 = time.perf_counter()
            r = R.reconstruire(grille, deb, fin, pt, params["modele"], "Z1", graine=1, n_trajets=100)
            res["hmm"] = (CP.MD.depuis_hmm("hmm", r, grille, "Z1"), time.perf_counter() - t0)
            t0 = time.perf_counter()
            r = R.reconstruire(grille, deb, fin, pt, params["modele"], "Z1", graine=1, n_trajets=100,
                               appris=modele_appris)
            res["appris"] = (CP.MD.depuis_hmm("appris", r, grille, "Z1"), time.perf_counter() - t0)
            t0 = time.perf_counter()
            res["particules"] = (PF.reconstruire_particules(grille, deb, fin, pt, params, "Z1", graine=1,
                                                            n_particules=2000, n_lisses=100),
                                 time.perf_counter() - t0)
            t0 = time.perf_counter()
            s2, info = BB.estimer_sigma_m(fiables["x"], fiables["y"], fiables["t_s"], fiables["sigma_km"],
                                          fin.t_s - deb.t_s)
            res["pont"] = (BB.reconstruire_pont(grille, deb, fin, pt, params, "Z1", s2, info, graine=1,
                                                n_chemins=150), time.perf_counter() - t0)
            t0 = time.perf_counter()
            d = BO.descripteurs(grille, "Z1", deb, fin, pt, res["hmm"][0].proba_passage, res["pont"][0].proba_passage)
            pb = ens.predire(d)[0] if ens else None
            ligne["p_boosting"], ligne["temps_boosting"] = pb, time.perf_counter() - t0
            for m in ("hmm", "appris", "particules", "pont"):
                rm, tm = res[m]
                ligne[f"p_{m}"] = rm.proba_passage
                ligne[f"temps_{m}"] = tm
                e, c = metriques_spatiales(rm.chemins, verite, rm.dt_s, rm.n_pas) if rm.statut == "ok" else (np.nan, np.nan)
                ligne[f"erreur_km_{m}"] = e
                ligne[f"couverture90_{m}"] = c
            ligne["ref_interpolation"] = R.reference_interpolation(zone.chemin(), deb, fin)
            lignes.append(ligne)
    df = pd.DataFrame(lignes)
    df.to_csv(SORTIE / "comparatif_interruptions.csv", index=False)
    y = df["verite_passage"].astype(float)
    taux_base = float(df_b["verite_passage"].mean())

    def bloc(m):
        p = df[f"p_{m}"]
        ok = p.notna()
        out = {"n_processed": int(ok.sum()), "failures": int((~ok).sum()),
               "brier": round(brier(p[ok], y[ok]), 4) if ok.any() else None,
               # Brier counting a failure as "don't know" (0.5): penalises models that often refuse
               "brier_failure_0_5": round(brier(p.fillna(0.5), y), 4),
               "calibration": calibration(p[ok], y[ok]) if ok.any() else [],
               "median_time_s": round(float(df[f"temps_{m}"].median()), 3)}
        if f"erreur_km_{m}" in df:
            out["spatial_error_km"] = round(float(df[f"erreur_km_{m}"].mean()), 3)
            out["coverage_90"] = round(float(df[f"couverture90_{m}"].mean()), 3)
        return out

    meilleur, boot = bootstrap_brier(df, MODELES)
    resultats = {
        "warning": "Entirely synthetic data (fictitious bay): these results are not an operational validation.",
        "n_interruptions": int(len(df)), "real_crossing_share": round(float(y.mean()), 3),
        "sample_note": "sample enriched in gaps close to the zone (as in eval/run_eval.py)",
        "models": {m: bloc(m) for m in MODELES},
        "references": {"linear_interpolation": {"brier": round(brier(df["ref_interpolation"], y), 4)},
                       "base_rate": {"brier": round(brier(np.full(len(df), taux_base), y), 4),
                                     "rate": round(taux_base, 3)}},
        "density_tuning": {"criterion": "Brier on the tuning set", "scores_by_density_weight": scores_densite,
                           "selected_density_weight": beta_best},
        "boosting": {"training_examples": int(len(df_b)), "importances": ens.importances if ens else None,
                     "hyperparameters": ens.hp if ens else None, "tuning": reglage},
        "bootstrap": {"best": meilleur, "by_model": boot,
                      "note": "paired bootstrap on brier_failure_0_5; a gap to the best whose interval contains 0 "
                              "is not significant on this sample"},
        "total_time_s": round(time.perf_counter() - t_total, 1),
    }
    (SORTIE / "comparatif.json").write_text(json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    _figure(df, SORTIE / "comparatif.png")
    print(json.dumps({m: {k: v for k, v in b.items() if k != "calibration"} for m, b in resultats["models"].items()},
                     ensure_ascii=False, indent=2))
    print(f"density weight to copy into config/parametres.json: {beta_best}")


COLOURS = {"hmm": "#6e3ca0", "appris": "#1e78b4", "particules": "#c86414", "pont": "#288c5a", "boosting": "#7f8c8d"}


def _figure(df, chemin):
    """Calibration plot with the SAME colours as the web interface."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5.5, 5))
    y = df["verite_passage"].astype(float)
    for m, mk in zip(MODELES, "osd^v"):
        p = df[f"p_{m}"]
        ok = p.notna()
        cal = calibration(p[ok], y[ok])
        ax.plot([c["mean_probability"] for c in cal], [c["observed_frequency"] for c in cal], mk + "-",
                color=COLOURS[m], label=m)
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="perfect calibration")
    ax.set_xlabel("Estimated crossing probability")
    ax.set_ylabel("Observed crossing frequency")
    ax.set_title("Calibration of the five models (synthetic)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(chemin, dpi=110)


if __name__ == "__main__":
    main()
