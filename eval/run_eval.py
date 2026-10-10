"""Banc d'évaluation de SeaTrace (données SYNTHÉTIQUES).

Lancement :  python eval/run_eval.py           (complet, ~10 min)
             python eval/run_eval.py --rapide  (réduit, ~2 min)

Ce que l'on mesure, séparément :
1. Détection des manipulations : rappel par type, précision, fausses alertes sur
   trajets propres, temps d'analyse.
2. Reconstruction : erreur spatiale et couverture du trajet réel par les régions
   prédites (on cache de vraies portions de trajets complets).
3. Passage dans la zone : score de Brier, calibration, comparaison à des méthodes
   simples (interpolation linéaire, zone atteignable, taux de base).
4. Sensibilité à la durée de l'interruption, comparaison avec la variante sans cap.

Séparation des données : jeux distincts PAR BATEAU (apprentissage / réglage /
évaluation). Le réglage des paramètres du modèle se fait uniquement sur le jeu de
réglage. Le jeu d'évaluation contient ~35 % de trajets « hors habitudes » (points
aléatoires, boucles). Le générateur de trajets n'est pas le modèle de
reconstruction (inertie de cap continue, points de passage, mouillages).

Limite majeure : tout est synthétique. Ces chiffres ne valent PAS validation
opérationnelle ; ils montrent que la méthode se comporte correctement dans un
monde simulé et documentent ses faiblesses.
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

from seatrace import config, detect, geo, reconstruct as R, scenarios  # noqa: E402

SORTIE = RACINE / "eval" / "resultats"


def _xy_obs(obs):
    x, y = geo.vers_km(obs["lat"].to_numpy(), obs["lon"].to_numpy())
    return x, y


# ---------------------------------------------------------------------------
# 1. Détection
# ---------------------------------------------------------------------------

MANIPS = ["saut", "derive", "remplacement", "rejeu", "dedoublement", "suppression"]


def manipuler(obs, type_m, rng):
    n = len(obs)
    deb = int(rng.integers(int(n * 0.2), int(n * 0.6)))
    long = int(rng.integers(6, 16))
    if type_m == "saut":
        return scenarios.manip_saut(obs, rng, deb, long, float(rng.uniform(1.5, 6)))
    if type_m == "derive":
        return scenarios.manip_derive(obs, rng, deb, long + 10, float(rng.uniform(0.8, 2.5)))
    if type_m == "remplacement":
        x, y = _xy_obs(obs)
        fin = min(n - 1, deb + long)
        mx, my = (x[deb] + x[fin]) / 2, (y[deb] + y[fin]) / 2
        ang = rng.uniform(0, 2 * math.pi)
        via = (mx + 1.5 * math.cos(ang), my + 1.5 * math.sin(ang))
        return scenarios.manip_remplacement(obs, deb, fin - deb + 1, (x[deb], y[deb]), (x[fin], y[fin]), [via])
    if type_m == "rejeu":
        src = int(rng.integers(1, max(2, deb - long)))
        return scenarios.manip_rejeu(obs, src, long, deb)
    if type_m == "dedoublement":
        return scenarios.manip_dedoublement(obs, rng, deb, long, float(rng.uniform(1.5, 6)))
    if type_m == "suppression":
        return scenarios.manip_suppression(obs, deb, long + 10)
    raise ValueError(type_m)


def evaluer_detection(trajets, params, rng):
    lignes = []
    fausses = []
    temps = []
    for tr in trajets:
        pt = params["types_bateau"][tr["type_bateau"]]
        t0 = time.perf_counter()
        a = detect.analyser_trajet(tr["obs"], pt, params)
        temps.append(time.perf_counter() - t0)
        fausses.append(sum(1 for al in a.alertes if al.categorie == detect.INTEGRITE))
        for type_m in MANIPS:
            obs_m, (d0, d1) = manipuler(tr["obs"].reset_index(drop=True), type_m, rng)
            a = detect.analyser_trajet(obs_m, pt, params)
            marge = pd.Timedelta(minutes=2)
            integ = [al for al in a.alertes if al.categorie == detect.INTEGRITE]
            dispo = [al for al in a.alertes if al.categorie == detect.DISPONIBILITE]
            touche = [al for al in integ if al.debut <= d1 + marge and al.fin >= d0 - marge]
            touche_dispo = [al for al in dispo if al.debut <= d1 + marge and al.fin >= d0 - marge]
            lignes.append({
                "trip_id": tr["trip_id"], "manipulation": type_m,
                "detectee_integrite": bool(touche), "signalee_disponibilite": bool(touche_dispo),
                "alertes_integrite": len(integ), "alertes_integrite_vraies": len(touche),
            })
    df = pd.DataFrame(lignes)
    par_type = df.groupby("manipulation").agg(
        rappel_integrite=("detectee_integrite", "mean"),
        signalee_disponibilite=("signalee_disponibilite", "mean"), n=("trip_id", "size"))
    manip_df = df[df.manipulation != "suppression"]
    precision = manip_df["alertes_integrite_vraies"].sum() / max(1, manip_df["alertes_integrite"].sum() + sum(fausses))
    return {
        "trajets_propres": len(trajets),
        "fausses_alertes_integrite_par_trajet_propre": float(np.mean(fausses)),
        "part_trajets_propres_sans_alerte": float(np.mean([f == 0 for f in fausses])),
        "rappel_par_type": {k: round(float(v), 3) for k, v in par_type["rappel_integrite"].items()},
        "suppression_signalee_comme_interruption": round(float(par_type.loc["suppression", "signalee_disponibilite"]), 3),
        "rappel_global_hors_suppression": round(float(manip_df["detectee_integrite"].mean()), 3),
        "precision_alertes": round(float(precision), 3),
        "temps_analyse_median_s": round(float(np.median(temps)), 4),
    }


# ---------------------------------------------------------------------------
# 2-3. Reconstruction et passage
# ---------------------------------------------------------------------------

def tirer_interruptions(tr, zone, rng, n_par_trajet, duree_min=(10, 120)):
    obs = tr["obs"].reset_index(drop=True)
    vrai = tr["vrai"]
    t_obs = (obs["timestamp"] - vrai["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
    candidats = []
    for _ in range(40):
        if len(obs) < 8:
            break
        i0 = int(rng.integers(2, len(obs) - 4))
        d = rng.uniform(*duree_min) * 60
        i1 = int(np.searchsorted(t_obs, t_obs[i0] + d))
        if i1 >= len(obs) - 1 or i1 - i0 < 3:
            continue
        m = (vrai["t_s"] >= t_obs[i0]) & (vrai["t_s"] <= t_obs[i1])
        vx, vy = vrai.loc[m, "x"].to_numpy(), vrai.loc[m, "y"].to_numpy()
        pts = np.column_stack([vx, vy])
        dans = bool(zone.chemin().contains_points(pts).any())
        cx = np.mean([p[0] for p in zone.polygone])
        cy = np.mean([p[1] for p in zone.polygone])
        proche = float(np.min(np.hypot(vx - cx, vy - cy))) < 5.0
        candidats.append((i0, i1, dans, proche))
    if not candidats:
        return []
    # Échantillon ENRICHI en interruptions proches de la zone ou la traversant (sinon
    # presque aucun cas positif pour mesurer la calibration). Indiqué dans les résultats.
    dedans = [c for c in candidats if c[2]]
    proches = [c for c in candidats if c[3] and not c[2]]
    autres = [c for c in candidats if not c[3] and not c[2]]
    choix = []
    for k in range(n_par_trajet):
        if k % 3 == 0 and dedans:
            pool = dedans
        elif k % 3 == 1 and proches:
            pool = proches
        else:
            pool = autres or proches or dedans
        c = pool[int(rng.integers(0, len(pool)))]
        if c not in choix:
            choix.append(c)
    return choix


def evaluer_interruption(tr, i0, i1, grille, params, zone_id, variantes, habitudes=None):
    obs = tr["obs"].reset_index(drop=True)
    vrai = tr["vrai"]
    pt = params["types_bateau"][tr["type_bateau"]]
    pm = params["modele"]
    x, y = _xy_obs(obs)
    t_obs = (obs["timestamp"] - vrai["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
    sig = math.hypot(pm["incertitude_bornes_m"], 10.0) / 1000
    deb = R.Observation(x[i0], y[i0], sig, 0.0, cap_deg=obs["cog_deg"].iloc[i0], vitesse_kn=obs["sog_kn"].iloc[i0])
    fin = R.Observation(x[i1], y[i1], sig, t_obs[i1] - t_obs[i0], cap_deg=obs["cog_deg"].iloc[i1], vitesse_kn=obs["sog_kn"].iloc[i1])
    m = (vrai["t_s"] >= t_obs[i0]) & (vrai["t_s"] <= t_obs[i1])
    vx, vy, vt = vrai.loc[m, "x"].to_numpy(), vrai.loc[m, "y"].to_numpy(), vrai.loc[m, "t_s"].to_numpy() - t_obs[i0]
    zone = geo.geographie_demo().zone(zone_id)
    verite = bool(zone.chemin().contains_points(np.column_stack([vx, vy])).any())
    out = {"trip_id": tr["trip_id"], "type_bateau": tr["type_bateau"], "exotique": tr["exotique"],
           "duree_min": (t_obs[i1] - t_obs[i0]) / 60, "verite_passage": verite,
           "ref_interpolation": R.reference_interpolation(zone.chemin(), deb, fin),
           "ref_atteignable": R.reference_zone_atteignable(grille, zone_id, deb, fin, pt["vitesse_max_kn"])}
    for nom, kw in variantes.items():
        t0 = time.perf_counter()
        r = R.reconstruire(grille, deb, fin, pt, pm, zone_id, habitudes=habitudes if kw.get("habitudes") else None,
                           echantillonner=False, garder_marginales=kw.get("spatial", False),
                           **{k: v for k, v in kw.items() if k not in ("habitudes", "spatial")})
        out[f"p_{nom}"] = r.proba_passage
        out[f"temps_{nom}"] = time.perf_counter() - t0
        if kw.get("spatial") and r.marginales is not None:
            gx, gy = np.meshgrid(grille.xc, grille.yc)
            erreurs, couverts, err_interp, err_moy = [], [], [], []
            for k in range(1, r.n_pas):
                tk = k * r.dt_s
                tx, ty = np.interp(tk, vt, vx), np.interp(tk, vt, vy)
                mk = r.marginales[k].astype(float)
                if mk.sum() <= 0:
                    continue
                mk = mk / mk.sum()
                d = np.hypot(gx - tx, gy - ty)
                erreurs.append(float((mk * d).sum()))
                err_moy.append(float(math.hypot((mk * gx).sum() - tx, (mk * gy).sum() - ty)))
                ordre = np.sort(mk.ravel())[::-1]
                seuil = ordre[np.searchsorted(np.cumsum(ordre), 0.9)]
                i, j = grille.indice(tx, ty)
                couverts.append(bool(mk[i, j] >= seuil))
                f = tk / (fin.t_s)
                err_interp.append(float(math.hypot(deb.x + f * (fin.x - deb.x) - tx, deb.y + f * (fin.y - deb.y) - ty)))
            out[f"erreur_moyenne_km_{nom}"] = float(np.mean(erreurs)) if erreurs else np.nan
            out[f"erreur_position_moyenne_km_{nom}"] = float(np.mean(err_moy)) if err_moy else np.nan
            out[f"couverture90_{nom}"] = float(np.mean(couverts)) if couverts else np.nan
            out["erreur_interpolation_km"] = float(np.mean(err_interp)) if err_interp else np.nan
    return out


def brier(p, y):
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def calibration(p, y, bornes=(0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0001)):
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    lignes = []
    for a, b in zip(bornes[:-1], bornes[1:]):
        m = (p >= a) & (p < b)
        if m.sum():
            lignes.append({"intervalle": f"[{a:.1f}, {min(b, 1):.1f}]", "n": int(m.sum()),
                           "proba_moyenne": round(float(p[m].mean()), 3),
                           "frequence_observee": round(float(y[m].mean()), 3)})
    return lignes


def carte_habitudes(trajets, grille):
    """Densité de passage apprise sur le jeu d'APPRENTISSAGE uniquement (positions observées)."""
    from scipy.ndimage import gaussian_filter
    h = np.zeros((grille.ny, grille.nx))
    for tr in trajets:
        x, y = _xy_obs(tr["obs"])
        for xi, yi in zip(x, y):
            i, j = grille.indice(xi, yi)
            h[i, j] += 1
    h = gaussian_filter(h, 3.0)
    h = h / h[grille.mer].mean()
    return np.clip(h, 0.05, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapide", action="store_true")
    args = ap.parse_args()
    params = config.charger()
    rng = np.random.default_rng(11)
    SORTIE.mkdir(parents=True, exist_ok=True)
    t_total = time.perf_counter()

    n_bateaux = 20 if args.rapide else 30
    print("Génération de la flotte synthétique…", flush=True)
    trajets = scenarios.generer_flotte(params, n_bateaux=n_bateaux, trajets_par_bateau=4)
    jeux = {j: [t for t in trajets if t["jeu"] == j] for j in ("apprentissage", "reglage", "evaluation")}
    bateaux = {j: sorted({t["boat_id"] for t in v}) for j, v in jeux.items()}
    assert not (set(bateaux["evaluation"]) & set(bateaux["reglage"]) | set(bateaux["evaluation"]) & set(bateaux["apprentissage"]))

    g = geo.geographie_demo()
    grille = geo.construire_grille(g, params["modele"]["taille_case_km"])
    zone = g.zone("Z1")

    # Taux de base (climatologie) appris sur le jeu d'apprentissage
    base = []
    for tr in jeux["apprentissage"]:
        for i0, i1, dans, _ in tirer_interruptions(tr, zone, rng, 3):
            base.append(dans)
    taux_base = float(np.mean(base)) if base else 0.0
    habitudes = carte_habitudes(jeux["apprentissage"], grille)
    np.save(SORTIE / "habitudes_apprentissage.npy", habitudes)

    # --- Détection ------------------------------------------------------------
    print("Évaluation de la détection…", flush=True)
    res_det = evaluer_detection(jeux["evaluation"], params, rng)

    # --- Réglage du modèle sur le jeu de réglage -------------------------------
    print("Réglage sur le jeu de réglage…", flush=True)
    grille_reglage = [(p, d) for p in (0.5, 0.7, 0.85) for d in (0.4, 0.7, 1.0, 1.5)]
    if args.rapide:
        grille_reglage = [(0.5, 0.4), (0.7, 0.4), (0.85, 0.4), (0.7, 0.25), (0.7, 0.7)]
    gaps_reglage = []
    for tr in jeux["reglage"]:
        for i0, i1, _, _ in tirer_interruptions(tr, zone, rng, 2 if args.rapide else 3):
            gaps_reglage.append((tr, i0, i1))
    variantes_reglage = {f"pers{p}_disp{d}": {"persistance": p, "dispersion": d} for p, d in grille_reglage}
    lignes_reglage = [evaluer_interruption(tr, i0, i1, grille, params, "Z1", variantes_reglage)
                      for tr, i0, i1 in gaps_reglage]
    dfr = pd.DataFrame(lignes_reglage)
    scores_reglage = {nom: brier(dfr[f"p_{nom}"], dfr["verite_passage"]) for nom in variantes_reglage}
    meilleur = min(scores_reglage, key=scores_reglage.get)
    p_best, d_best = grille_reglage[list(variantes_reglage).index(meilleur)]
    print(f"  meilleur réglage : persistance={p_best}, dispersion={d_best}", flush=True)

    # --- Évaluation sur le jeu d'évaluation ------------------------------------
    print("Évaluation de la reconstruction…", flush=True)
    variantes = {
        "modele": {"persistance": p_best, "dispersion": d_best, "spatial": True},
        "isotrope": {"nb_caps": 1, "dispersion": 0.6},
        "habitudes": {"persistance": p_best, "dispersion": d_best, "habitudes": True},
    }
    params_hab = config.copie(params)
    lignes = []
    for tr in jeux["evaluation"]:
        for i0, i1, _, _ in tirer_interruptions(tr, zone, rng, 2 if args.rapide else 4):
            ligne = evaluer_interruption(tr, i0, i1, grille, params, "Z1",
                                         {k: v for k, v in variantes.items() if k != "habitudes"})
            params_hab["modele"]["poids_habitudes"] = 0.5
            l2 = evaluer_interruption(tr, i0, i1, grille, params_hab, "Z1",
                                      {"habitudes": variantes["habitudes"]}, habitudes=habitudes)
            ligne["p_habitudes"] = l2["p_habitudes"]
            lignes.append(ligne)
    df = pd.DataFrame(lignes)
    df.to_csv(SORTIE / "interruptions_evaluation.csv", index=False)
    y = df["verite_passage"].astype(float)

    def bloc(col):
        return {"brier": round(brier(df[col], y), 4)}

    tranches = pd.cut(df["duree_min"], [0, 30, 60, 90, 130])
    par_duree = []
    for tr_, sous in df.groupby(tranches, observed=True):
        par_duree.append({"duree": str(tr_), "n": int(len(sous)),
                          "brier_modele": round(brier(sous["p_modele"], sous["verite_passage"]), 4),
                          "erreur_moyenne_km": round(float(sous["erreur_moyenne_km_modele"].mean()), 3),
                          "couverture90": round(float(sous["couverture90_modele"].mean()), 3)})
    exo = df[df["exotique"]]
    resultats = {
        "avertissement": "Données entièrement synthétiques : ces résultats ne constituent pas une validation opérationnelle.",
        "generation": {"bateaux": n_bateaux, "trajets": len(trajets),
                       "bateaux_par_jeu": {k: len(v) for k, v in bateaux.items()},
                       "part_trajets_hors_habitudes_evaluation": round(float(np.mean([t["exotique"] for t in jeux["evaluation"]])), 3)},
        "detection": res_det,
        "reglage": {"criteres": "score de Brier sur le jeu de réglage",
                    "scores": {k: round(v, 4) for k, v in scores_reglage.items()},
                    "persistance_retenue": p_best, "dispersion_retenue": d_best,
                    "interruptions_reglage": len(dfr)},
        "passage": {
            "interruptions_evaluation": int(len(df)),
            "part_passages_reels": round(float(y.mean()), 3),
            "note_echantillon": "échantillon enrichi : ~1/3 des interruptions tirées parmi celles où le trajet réel traverse la zone",
            "modele": bloc("p_modele"),
            "modele_sans_cap_isotrope": bloc("p_isotrope"),
            "modele_avec_habitudes_beta0.5": bloc("p_habitudes"),
            "reference_interpolation_lineaire": bloc("ref_interpolation"),
            "reference_zone_atteignable": bloc("ref_atteignable"),
            "reference_taux_de_base": {"brier": round(brier(np.full(len(df), taux_base), y), 4),
                                       "taux_base_apprentissage": round(taux_base, 3)},
            "calibration_modele": calibration(df["p_modele"], y),
            "brier_trajets_hors_habitudes": round(brier(exo["p_modele"], exo["verite_passage"]), 4) if len(exo) else None,
            "n_trajets_hors_habitudes": int(len(exo)),
        },
        "reconstruction": {
            "definition": ("erreur_attendue = distance moyenne entre la position réelle et la loi a posteriori ; "
                           "erreur_position_moyenne = distance entre la position réelle et la position moyenne "
                           "estimée ; couverture = part des instants où la position réelle est dans la région "
                           "à 90 % de probabilité (valeur visée : ~90 %)"),
            "erreur_attendue_km_modele": round(float(df["erreur_moyenne_km_modele"].mean()), 3),
            "erreur_position_moyenne_km_modele": round(float(df["erreur_position_moyenne_km_modele"].mean()), 3),
            "erreur_moyenne_km_interpolation": round(float(df["erreur_interpolation_km"].mean()), 3),
            "couverture_region_90pct": round(float(df["couverture90_modele"].mean()), 3),
            "par_duree": par_duree,
        },
        "temps_calcul": {
            "reconstruction_mediane_s": round(float(df["temps_modele"].median()), 3),
            "reconstruction_max_s": round(float(df["temps_modele"].max()), 3),
            "evaluation_totale_s": round(time.perf_counter() - t_total, 1),
        },
    }
    params_retenus = {"persistance_cap": p_best, "dispersion_relative": d_best}
    (SORTIE / "metriques.json").write_text(json.dumps(resultats, ensure_ascii=False, indent=2), encoding="utf-8")
    (SORTIE / "parametres_regles.json").write_text(json.dumps(params_retenus, indent=2), encoding="utf-8")
    _figure_calibration(df, SORTIE / "calibration.png")
    print(json.dumps(resultats, ensure_ascii=False, indent=2))


def _figure_calibration(df, chemin):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5, 5))
    for col, lib, mk in (("p_modele", "Modèle à cap (réglé)", "o"), ("p_isotrope", "Variante sans cap", "s")):
        cal = calibration(df[col], df["verite_passage"].astype(float))
        ax.plot([c["proba_moyenne"] for c in cal], [c["frequence_observee"] for c in cal], mk + "-", label=lib)
    ax.plot([0, 1], [0, 1], "k--", lw=1, label="Calibration parfaite")
    ax.set_xlabel("Probabilité de passage estimée")
    ax.set_ylabel("Fréquence observée de passage")
    ax.set_title("Calibration (données synthétiques)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(chemin, dpi=110)


if __name__ == "__main__":
    main()
