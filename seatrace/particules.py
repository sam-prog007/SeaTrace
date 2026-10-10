"""Méthode à particules avec lissage : filtre particulaire guidé + lissage FFBSi.

Modèle de déplacement (continu, pas de grille) :
  état s = (x, y, vx, vy) en km et km/h ;
  vitesse : processus autorégressif (Ornstein-Uhlenbeck discret), persistance rho par pas :
      v_k = rho * v_{k-1} + sqrt(1 - rho²) * sigma_v * eps ;
  position : x_k = x_{k-1} + (v_k + courant) * dt + bruit (sigma_p) ;
  contraintes : vitesse <= vitesse maximale du type, positions en mer (grille), dans la zone d'étude.

Conditionnement sur l'observation APRÈS l'interruption (pont) :
  un filtre « naïf » ne verrait la fin qu'au dernier pas et dégénérerait. On guide donc les
  particules par une fonction de torsion psi_k(s) = loi gaussienne approchée de la position
  finale sachant l'état courant (calcul exact sans contraintes, variance gonflée). Les poids
  incrémentaux psi_k / psi_{k-1} corrigent exactement ce guidage (SMC « tordu »), et la
  portée maximale coupe les particules qui ne peuvent plus atteindre la fin.

Lissage : simulation arrière (FFBSi, Godsill, Doucet & West 2004). Pour chaque chemin lissé,
on remonte le temps en choisissant la particule précédente avec une probabilité
proportionnelle à son poids filtré (non tordu) x densité de transition. Les chemins lissés
sont des tirages de la loi a posteriori du trajet complet ; leur proportion qui traverse la
zone estime la probabilité de passage.

Interprétation : identique au HMM — probabilité sous hypothèses, jamais une probabilité de fraude.
"""

from __future__ import annotations

import math
import time

import numpy as np

from . import modeles as MD
from .geo import KN_EN_KMH, Grille


def _poids_cap_final(vx, vy, cap_deg, vitesse_kn, kappa=4.0):
    if cap_deg is None or vitesse_kn is None or not np.isfinite(cap_deg) or not np.isfinite(vitesse_kn) \
            or vitesse_kn < 1.5:
        return np.zeros(len(vx))
    theta = math.radians(90.0 - cap_deg)
    ang = np.arctan2(vy, vx)
    w = np.exp(kappa * (np.cos(ang - theta) - 1.0))
    # mélange : le cap déclaré peut être faux
    return np.log(0.8 * w + 0.2 * math.exp(-kappa))


def _coefficients_guidage(n, dt_h, rho, sigma_v, sigma_p, sigma_fin, inflation):
    """Pour m pas restants : coefficient de la vitesse courante dans la moyenne et variance (par axe)."""
    coef = np.zeros(n + 1)
    var = np.zeros(n + 1)
    for m in range(n + 1):
        if m == 0:
            coef[m], var[m] = 0.0, sigma_fin ** 2
            continue
        coef[m] = dt_h * sum(rho ** j for j in range(1, m + 1))
        s = sum(((1 - rho ** l) / (1 - rho)) ** 2 if rho < 1 else l ** 2 for l in range(1, m + 1))
        var[m] = (dt_h ** 2 * (1 - rho ** 2) * sigma_v ** 2 * s + m * sigma_p ** 2) * inflation + sigma_fin ** 2
    return coef, var


def reconstruire_particules(grille: Grille, debut, fin, params_type, params, zone_id=None, externes=None,
                            graine=0, courant_kmh=(0.0, 0.0), n_particules=None, n_lisses=None,
                            facteur_vitesse=1.0, persistance=None):
    t0 = time.perf_counter()
    pm = params["modele"]
    pp = params["modeles"]["particules"]
    err = MD.controles_prealables("particules", grille, debut, fin, params_type, pm, facteur_vitesse)
    if err:
        return err
    externes = externes or []
    rng = np.random.default_rng(graine)
    T = fin.t_s - debut.t_s
    n, dt = MD.nombre_de_pas(T, pm["pas_temps_s"])
    dt_h = dt / 3600.0
    N = int(n_particules or pp["n_particules"])
    M = int(n_lisses or pp["n_lisses"])
    rho = float(pp["persistance_vitesse"] if persistance is None else persistance)
    sig_p = float(pp["bruit_position_km"])
    vmax = params_type["vitesse_max_kn"] * facteur_vitesse * KN_EN_KMH
    vtyp = min(params_type["vitesse_typique_kn"] * KN_EN_KMH, vmax)
    sig_v = vtyp / math.sqrt(math.pi / 2)  # vitesse moyenne de la loi de Rayleigh = vtyp
    cx, cy = float(courant_kmh[0]), float(courant_kmh[1])
    sig_fin = max(fin.sigma_km, 0.5 * grille.taille)
    coef, var = _coefficients_guidage(n, dt_h, rho, sig_v, sig_p, sig_fin, float(pp["inflation_guidage"]))
    q_v = (1 - rho ** 2) * sig_v ** 2

    obs_pas = {}
    raisons = []
    for o in externes:
        k = int(round((o.t_s - debut.t_s) / dt))
        if 0 < k < n:
            obs_pas.setdefault(k, []).append(o)
        else:
            raisons.append(f"External observation {o.libelle or ''} outside the interval: ignored.")

    def log_psi(k, X):
        m = n - k
        mx = X[:, 0] + coef[m] * X[:, 2] + cx * m * dt_h
        my = X[:, 1] + coef[m] * X[:, 3] + cy * m * dt_h
        d2 = (mx - fin.x) ** 2 + (my - fin.y) ** 2
        lp = -0.5 * d2 / var[m] - math.log(var[m])
        # portée : impossible d'atteindre la fin en m pas à vitesse max (+ courant)
        reste = math.hypot(cx, cy) * m * dt_h + vmax * m * dt_h + 3 * sig_fin + grille.taille
        dist = np.hypot(X[:, 0] - fin.x, X[:, 1] - fin.y)
        lp[dist > reste] = -np.inf
        return lp

    # --- Initialisation ----------------------------------------------------------
    X = np.zeros((N, 4))
    X[:, 0] = debut.x + rng.normal(0, debut.sigma_km, N)
    X[:, 1] = debut.y + rng.normal(0, debut.sigma_km, N)
    if debut.cap_deg is not None and debut.vitesse_kn is not None and np.isfinite(debut.cap_deg) \
            and np.isfinite(debut.vitesse_kn) and debut.vitesse_kn >= 1.5:
        th = math.radians(debut.cap_deg)
        v0 = min(debut.vitesse_kn * KN_EN_KMH, vmax)
        X[:, 2] = v0 * math.sin(th) + rng.normal(0, 0.3 * sig_v, N)
        X[:, 3] = v0 * math.cos(th) + rng.normal(0, 0.3 * sig_v, N)
    else:
        X[:, 2] = rng.normal(0, sig_v, N)
        X[:, 3] = rng.normal(0, sig_v, N)
    valide = grille.en_mer(X[:, 0], X[:, 1]) & (np.hypot(X[:, 2], X[:, 3]) <= vmax)
    if not valide.any():
        # début sur une case de terre (côte) : on tolère, comme le HMM, en rattachant à la mer voisine
        raisons.append("The start observation falls on a land cell; initial particles tolerated.")
        valide = np.hypot(X[:, 2], X[:, 3]) <= vmax
    lpsi = log_psi(0, X)
    logw = np.where(valide, lpsi, -np.inf)

    hist_X = np.zeros((n + 1, N, 4))
    hist_lw = np.zeros((n + 1, N))  # log poids filtrés NON tordus (pour le lissage)
    ess_min = float(N)
    n_reech = 0
    logZ = 0.0

    def normaliser(lw):
        mx = np.max(lw)
        if not np.isfinite(mx):
            return None, -np.inf
        w = np.exp(lw - mx)
        s = w.sum()
        return w / s, mx + math.log(s)

    W, lz = normaliser(logw)
    if W is None:
        return MD.echec("particules", "No valid initial particle (start outside the sea or incompatible).",
                        n_pas=n, dt_s=dt)
    logZ += lz - math.log(N)
    hist_X[0] = X
    hist_lw[0] = np.log(np.maximum(W, 1e-300)) - np.where(np.isfinite(lpsi), lpsi, 0.0)
    hist_lw[0][~np.isfinite(logw)] = -np.inf

    for k in range(1, n + 1):
        ess = 1.0 / np.sum(W ** 2)
        ess_min = min(ess_min, ess)
        if ess < N / 2:
            # rééchantillonnage systématique
            u = (rng.random() + np.arange(N)) / N
            idx = np.minimum(np.searchsorted(np.cumsum(W), u), N - 1)
            X = X[idx]
            lpsi = lpsi[idx]
            W = np.full(N, 1.0 / N)
            n_reech += 1
        Xn = np.empty_like(X)
        Xn[:, 2] = rho * X[:, 2] + math.sqrt(q_v) * rng.normal(size=N)
        Xn[:, 3] = rho * X[:, 3] + math.sqrt(q_v) * rng.normal(size=N)
        Xn[:, 0] = X[:, 0] + (Xn[:, 2] + cx) * dt_h + sig_p * rng.normal(size=N)
        Xn[:, 1] = X[:, 1] + (Xn[:, 3] + cy) * dt_h + sig_p * rng.normal(size=N)
        mx_, my_ = 0.5 * (X[:, 0] + Xn[:, 0]), 0.5 * (X[:, 1] + Xn[:, 1])
        ok = (np.hypot(Xn[:, 2], Xn[:, 3]) <= vmax) & grille.en_mer(Xn[:, 0], Xn[:, 1]) & grille.en_mer(mx_, my_)
        if k == n:
            lpsi_n = (-0.5 * ((Xn[:, 0] - fin.x) ** 2 + (Xn[:, 1] - fin.y) ** 2) / sig_fin ** 2
                      - math.log(sig_fin ** 2) + _poids_cap_final(Xn[:, 2], Xn[:, 3], fin.cap_deg, fin.vitesse_kn))
        else:
            lpsi_n = log_psi(k, Xn)
        lobs = np.zeros(N)
        for o in obs_pas.get(k, []):
            lobs += np.log(MD.vraisemblance_externe(o, Xn[:, 0], Xn[:, 1]))
        with np.errstate(invalid="ignore"):
            inc = lpsi_n + lobs - lpsi
        inc[~ok | ~np.isfinite(lpsi)] = -np.inf
        logw = np.log(np.maximum(W, 1e-300)) + inc
        Wn, lz = normaliser(logw)
        if Wn is None:
            return MD.echec("particules", "No particle links the start and end observations under "
                                          f"the assumptions (max speed {vmax / KN_EN_KMH:.0f} knots, land): "
                                          "impossible path or altered data.", n_pas=n, dt_s=dt,
                            temps_calcul_s=time.perf_counter() - t0)
        logZ += lz
        X, W, lpsi = Xn, Wn, lpsi_n
        hist_X[k] = X
        if k == n:
            hist_lw[k] = np.log(np.maximum(W, 1e-300))
        else:
            hist_lw[k] = np.log(np.maximum(W, 1e-300)) - np.where(np.isfinite(lpsi), lpsi, 0.0)
        hist_lw[k][W <= 0] = -np.inf

    ess_final = 1.0 / np.sum(W ** 2)
    # --- Lissage : simulation arrière (FFBSi) ----------------------------------------
    chem = np.zeros((M, n + 1, 4))
    Wn = np.exp(hist_lw[n] - np.max(hist_lw[n]))
    Wn /= Wn.sum()
    idx = rng.choice(N, size=M, p=Wn)
    chem[:, n] = hist_X[n][idx]
    for k in range(n - 1, -1, -1):
        Xk = hist_X[k]
        suiv = chem[:, k + 1]
        dvx = suiv[:, None, 2] - rho * Xk[None, :, 2]
        dvy = suiv[:, None, 3] - rho * Xk[None, :, 3]
        dpx = suiv[:, None, 0] - Xk[None, :, 0] - (suiv[:, None, 2] + cx) * dt_h
        dpy = suiv[:, None, 1] - Xk[None, :, 1] - (suiv[:, None, 3] + cy) * dt_h
        lt = (hist_lw[k][None, :] - 0.5 * (dvx ** 2 + dvy ** 2) / q_v
              - 0.5 * (dpx ** 2 + dpy ** 2) / sig_p ** 2)
        mx = lt.max(axis=1, keepdims=True)
        mx[~np.isfinite(mx)] = 0.0
        p = np.exp(lt - mx)
        s = p.sum(axis=1, keepdims=True)
        vide = (s[:, 0] <= 0)
        s[vide] = 1.0
        c = np.cumsum(p / s, axis=1)
        u = rng.random((M, 1))
        j = np.minimum((c < u).sum(axis=1), N - 1)
        if vide.any():
            j[vide] = rng.choice(N, size=int(vide.sum()), p=np.exp(hist_lw[k] - hist_lw[k].max())
                                 / np.exp(hist_lw[k] - hist_lw[k].max()).sum())
        chem[:, k] = Xk[j]

    xs, ys = chem[:, :, 0], chem[:, :, 1]
    passe = MD.passages(grille, zone_id, xs, ys)
    p = float(passe.mean()) if zone_id else None
    part = MD.temps_en_zone(grille, zone_id, xs, ys) if zone_id else None
    chemins = [MD.Chemin("particules", f"particules-{i + 1:04d}", xs[i], ys[i], 1.0 / M,
                         bool(passe[i]) if zone_id else None) for i in range(M)]
    vit = np.hypot(chem[:, :, 2], chem[:, :, 3]) / KN_EN_KMH
    if ess_min < 0.02 * N:
        raisons.append(f"Particle degeneracy (minimum effective size {ess_min:.0f} out of {N}): "
                       "less reliable result, increase the number of particles.")
    return MD.ResultatModele(
        "particules", p, "ok", raisons, chemins, MD.occupation_chemins(grille, xs, ys),
        time.perf_counter() - t0, n, dt, erreur_mc=MD.erreur_type(p, M) if p is not None else None,
        diagnostics={"time_in_zone_share": None if part is None else round(part, 4),
                     "expected_minutes_in_zone": None if part is None else round(part * n * dt / 60, 1),
                     "particles": N, "smoothed_paths": M, "min_effective_size": round(ess_min, 1),
                     "final_effective_size": round(float(ess_final), 1), "resamplings": n_reech,
                     "estimated_log_likelihood": round(logZ, 3),
                     "median_path_speed_kn": round(float(np.median(vit)), 2)},
        hypotheses={"model": "autoregressive speed (discrete Ornstein-Uhlenbeck), continuous space",
                    "time_step_s": round(dt, 1), "number_of_steps": n, "speed_persistence": rho,
                    "typical_speed_kn": round(vtyp / KN_EN_KMH, 1), "max_speed_kn": round(vmax / KN_EN_KMH, 1),
                    "position_noise_km": sig_p, "guidance": "Gaussian twisting towards the end observation",
                    "smoothing": "FFBSi (backward simulation)", "land_impassable": bool((~grille.mer).any()),
                    "current_kmh": [cx, cy], "external_observations": len(externes)},
    )
