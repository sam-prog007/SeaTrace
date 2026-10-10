"""Pont brownien (Brownian Bridge Movement Model, Horne et al. 2007).

Modèle : entre deux observations a (instant 0) et b (instant T), la position à l'instant t
suit une loi normale de moyenne a + (t/T)(b - a) (interpolation linéaire) et de variance
    sigma²(t) = T α(1 - α) sigma_m² + (1 - α)² δ_a² + α² δ_b²,   α = t / T,
où sigma_m² est la « variance de mobilité » du bateau (km²/h) et δ l'incertitude des bornes.

Estimation de sigma_m² : par maximum de vraisemblance sur les positions FIABLES du même
trajet (méthode de Horne : chaque position d'indice impair est prédite par le pont entre ses
deux voisines). Le pas entre voisines est choisi à l'échelle de l'interruption étudiée (une
mobilité mesurée sur 1 min ne dit rien d'une absence de 1 h).

Chemins : ponts browniens tirés exactement ; les chemins qui traversent une terre, sortent de
la zone d'étude ou dépassent la vitesse maximale (avec marge) sont rejetés. Ce rejet
conditionne le pont à « rester en mer » ; le taux d'acceptation est publié. Un taux très bas
signale que le pont (qui suppose un mouvement sans inertie autour de la ligne droite) est
inadapté à la configuration — par exemple une île sur la ligne directe.

Forces : très peu d'hypothèses, calcul rapide, référence classique en écologie du mouvement.
Limites : pas d'inertie ni de cap ; variance identique dans toutes les directions ; la
contrainte des terres n'est prise en compte que par rejet.
"""

from __future__ import annotations

import math
import time

import numpy as np

from . import modeles as MD
from .geo import KN_EN_KMH, Grille, densifier

BORNES_SIGMA = (1e-3, 1e3)


def _log_vraisemblance(s2, triplets):
    T, a, d2, da2, db2, dm2 = triplets
    var = T * a * (1 - a) * s2 + (1 - a) ** 2 * da2 + a ** 2 * db2 + dm2
    return float(np.sum(-np.log(var) - 0.5 * d2 / var))


def estimer_sigma_m(xs, ys, t_s, sigma_km, duree_cible_s, min_triplets=5):
    """sigma_m² (km²/h) par maximum de vraisemblance (recherche sur grille logarithmique).

    Retourne (sigma_m2 ou None, informations)."""
    xs, ys, t = np.asarray(xs, float), np.asarray(ys, float), np.asarray(t_s, float)
    sig = np.broadcast_to(np.asarray(sigma_km, float), xs.shape)
    n = len(xs)
    if n < 2 * min_triplets + 1:
        return None, {"reason": "too few reliable positions"}
    dt_med = float(np.median(np.diff(t))) if n > 1 else 60.0
    lag = int(max(1, round(duree_cible_s / 2 / max(dt_med, 1.0))))
    lag = int(min(lag, max(1, (n - 1) // (2 * min_triplets))))
    i0 = np.arange(0, n - 2 * lag, 2 * lag)
    if len(i0) < min_triplets:
        return None, {"reason": "too few triplets"}
    i1, i2 = i0 + lag, i0 + 2 * lag
    T = (t[i2] - t[i0]) / 3600.0
    ok = T > 0
    a = np.where(ok, (t[i1] - t[i0]) / 3600.0 / np.where(ok, T, 1), 0.5)
    mx = xs[i0] + a * (xs[i2] - xs[i0])
    my = ys[i0] + a * (ys[i2] - ys[i0])
    # vraisemblance bivariée isotrope = produit de deux lois normales (une par axe)
    d2 = (xs[i1] - mx) ** 2 + (ys[i1] - my) ** 2
    trip = (T[ok], a[ok], d2[ok], sig[i0][ok] ** 2, sig[i2][ok] ** 2, sig[i1][ok] ** 2)
    grille = np.logspace(math.log10(BORNES_SIGMA[0]), math.log10(BORNES_SIGMA[1]), 241)
    lls = np.array([_log_vraisemblance(s2, trip) for s2 in grille])
    best = float(grille[int(np.argmax(lls))])
    return best, {"triplets": int(ok.sum()), "spacing_between_neighbours_s": round(float(np.median(t[i2] - t[i0])) / 2, 1),
                  "sigma_m2_km2_h": round(best, 4)}


def reconstruire_pont(grille: Grille, debut, fin, params_type, params, zone_id=None, sigma_m2=None,
                      info_sigma=None, externes=None, graine=0, n_chemins=None, facteur_vitesse=1.0):
    t0 = time.perf_counter()
    pm = params["modele"]
    pb = params["modeles"]["pont_brownien"]
    err = MD.controles_prealables("pont", grille, debut, fin, params_type, pm, facteur_vitesse)
    if err:
        return err
    rng = np.random.default_rng(graine)
    raisons = []
    T = fin.t_s - debut.t_s
    n, dt = MD.nombre_de_pas(T, pm["pas_temps_s"])
    T_h = T / 3600.0
    defaut = sigma_m2 is None
    if defaut:
        sigma_m2 = float(pb["sigma_m_defaut_km2_h"])
        raisons.append(f"Mobility variance cannot be estimated on this trip: default value "
                       f"{sigma_m2} km²/h used.")
    N = int(n_chemins or pb["n_chemins"])
    vmax = params_type["vitesse_max_kn"] * facteur_vitesse * KN_EN_KMH * float(pb["marge_vitesse"])
    t = np.linspace(0.0, T_h, n + 1)
    alpha = t / T_h

    # Observations externes : intégrées par rééchantillonnage pondéré des ponts acceptés
    externes = externes or []
    acceptes_x, acceptes_y = [], []
    essais = 0
    max_essais = N * int(pb["essais_max_facteur"])
    lot = max(N, 256)
    while sum(len(a) for a in acceptes_x) < N and essais < max_essais:
        m = min(lot, max_essais - essais)
        essais += m
        ax = debut.x + rng.normal(0, debut.sigma_km, m)
        ay = debut.y + rng.normal(0, debut.sigma_km, m)
        bx = fin.x + rng.normal(0, fin.sigma_km, m)
        by = fin.y + rng.normal(0, fin.sigma_km, m)
        inc = rng.normal(0, math.sqrt(sigma_m2 * T_h / n), (2, m, n))
        W = np.concatenate([np.zeros((2, m, 1)), np.cumsum(inc, axis=2)], axis=2)
        B = W - alpha[None, None, :] * W[:, :, -1:]
        xs = ax[:, None] + alpha[None] * (bx - ax)[:, None] + B[0]
        ys = ay[:, None] + alpha[None] * (by - ay)[:, None] + B[1]
        dx, dy = densifier(xs[:, 1:-1] if n > 1 else xs, ys[:, 1:-1] if n > 1 else ys, 3)
        en_mer = grille.en_mer(dx, dy).all(axis=1) if dx.shape[1] else np.ones(m, bool)
        v = np.hypot(np.diff(xs, axis=1), np.diff(ys, axis=1)) / (T_h / n)
        ok = en_mer & (v.max(axis=1) <= vmax)
        acceptes_x.append(xs[ok])
        acceptes_y.append(ys[ok])
    xs = np.concatenate(acceptes_x) if acceptes_x else np.zeros((0, n + 1))
    ys = np.concatenate(acceptes_y) if acceptes_y else np.zeros((0, n + 1))
    taux = len(xs) / max(essais, 1)
    if len(xs) < max(20, N // 10):
        return MD.echec("pont", f"The Brownian bridge finds almost no compatible path at sea "
                                f"({len(xs)} accepted out of {essais} draws, {100 * taux:.2f} %): the model, with no "
                                "inertia, is unsuitable here (obstacle on the direct line or speed limit).",
                        n_pas=n, dt_s=dt, temps_calcul_s=time.perf_counter() - t0,
                        diagnostics={"acceptance_rate": taux, "sigma_m2_km2_h": sigma_m2})
    xs, ys = xs[:N], ys[:N]
    poids = np.full(len(xs), 1.0 / len(xs))
    if externes:
        lw = np.zeros(len(xs))
        for o in externes:
            k = int(round((o.t_s - debut.t_s) / dt))
            if 0 < k < n:
                lw += np.log(MD.vraisemblance_externe(o, xs[:, k], ys[:, k]))
        w = np.exp(lw - lw.max())
        poids = w / w.sum()
        # rééchantillonnage : chemins équipondérés tirés selon les observations externes
        idx = rng.choice(len(xs), size=len(xs), p=poids)
        xs, ys = xs[idx], ys[idx]
        poids = np.full(len(xs), 1.0 / len(xs))
    if taux < 0.05:
        raisons.append(f"Low acceptance rate ({100 * taux:.1f} %): the direct line is constrained by "
                       "land or by speed; interpret the result with caution.")
    passe = MD.passages(grille, zone_id, xs, ys)
    p = float(passe.mean()) if zone_id else None
    chemins = [MD.Chemin("pont", f"pont-{i + 1:04d}", xs[i], ys[i], float(poids[i]),
                         bool(passe[i]) if zone_id else None) for i in range(len(xs))]
    sd_mid = math.sqrt(T_h * 0.25 * sigma_m2)
    return MD.ResultatModele(
        "pont", p, "ok", raisons, chemins, MD.occupation_chemins(grille, xs, ys), time.perf_counter() - t0, n, dt,
        erreur_mc=MD.erreur_type(p, len(xs)) if p is not None else None,
        diagnostics={"sigma_m2_km2_h": round(sigma_m2, 4), "sigma_m_estimated": not defaut,
                     "estimation": info_sigma or {}, "acceptance_rate": round(taux, 4), "draws": essais,
                     "std_dev_mid_path_km": round(sd_mid, 3)},
        hypotheses={"model": "isotropic Brownian bridge (no inertia)", "time_step_s": round(dt, 1),
                    "number_of_steps": n, "mobility_variance_km2_h": round(sigma_m2, 4),
                    "land_constraint": "paths crossing land are rejected",
                    "rejection_max_speed_kn": round(vmax / KN_EN_KMH, 1),
                    "external_observations": len(externes),
                    "current": "no effect (the bridge is pinned at both ends)"},
    )
