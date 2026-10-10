"""Reconstruction probabiliste d'un segment sans positions fiables.

Modèle retenu : modèle de Markov caché (HMM) sur grille, à CAP PERSISTANT,
lissé avant/arrière et conditionné sur l'observation AVANT et l'observation
APRÈS l'interruption.

État caché à chaque pas de temps (~2 min) :
  - la case de la grille (cases de 200 m) ;
  - le cap, discrétisé en H directions (8 par défaut) ;
  - un drapeau « est déjà passé dans la zone étudiée ».

Transition :
  1. le cap est conservé avec une probabilité « persistance », sinon il tourne
     (surtout vers les directions voisines) ;
  2. le bateau avance dans la direction du cap, avec une vitesse tirée dans une
     loi large (arrêts possibles), plafonnée à la vitesse maximale du type ;
  3. les cases de terre ont une probabilité nulle ; la zone étudiée n'est PAS
     exclue (sinon on ne pourrait pas estimer si le bateau l'a traversée).

Pourquoi le cap : une première version sans cap (marche aléatoire isotrope)
donnait des résultats trop tranchés en faveur du chemin le plus court, parce
qu'un déplacement aléatoire sans inertie « diffuse » au lieu d'avancer. Cette
version est conservée (nb_caps = 1) comme point de comparaison dans l'évaluation.

Observations : positions avant/après (avec incertitude), cap déclaré au début et
à la fin s'il est disponible, éventuelles observations externes pondérées par
une probabilité d'attribution (jamais attribuées automatiquement au bateau).

Probabilité de passage : grâce au drapeau, P(au moins une entrée dans la zone)
est obtenue exactement (sous les hypothèses et à la résolution de la grille)
par la passe avant. Les trajets dessinés sont tirés exactement dans la loi a
posteriori ; leur proportion sert de contrôle Monte Carlo.

Interprétation : « sous ces hypothèses, probabilité estimée que le trajet réel
ait traversé la zone ». Ce n'est jamais une probabilité de fraude.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import fftconvolve

from .geo import KN_EN_KMH, Grille

PAS_MAX = 90


@dataclass
class Observation:
    x: float
    y: float
    sigma_km: float
    t_s: float  # secondes depuis l'observation de début
    p_attribution: float = 1.0
    source: str = "tracker"
    statut: str = "observee"  # observee, declaree, simulee, estimee
    cap_deg: float | None = None  # cap déclaré (0 = nord, sens horaire)
    vitesse_kn: float | None = None
    libelle: str = ""


@dataclass
class Resultat:
    proba_passage: float | None
    statut: str  # "etayee", "fragile", "insuffisamment_etayee"
    raisons: list
    controle_mc: dict
    occupation: np.ndarray | None
    marginales: np.ndarray | None = None
    trajets: list = field(default_factory=list)  # [(xs, ys, passe_zone)]
    log_probas: list = field(default_factory=list)  # log P(chemin tiré | observations), sur la grille
    hypotheses: dict = field(default_factory=dict)
    sensibilite: list = field(default_factory=list)
    temps_calcul_s: float = 0.0
    n_pas: int = 0
    dt_s: float = 0.0
    log_vraisemblance: float = float("nan")
    lecture: str = ""


def lecture_probabilite(p):
    """Lecture prudente et fixe de la probabilité (pas un verdict)."""
    if p is None:
        return "Pas d'estimation défendable."
    if p >= 0.9:
        return "Passage très probable sous les hypothèses retenues (ce n'est pas une preuve d'infraction)."
    if p <= 0.1:
        return "Passage peu probable sous les hypothèses retenues (ce n'est pas une preuve d'absence)."
    return "Plusieurs chemins restent plausibles : l'outil ne permet pas de conclure sur le passage."


def _echec(message, **kw):
    return Resultat(None, "insuffisamment_etayee", [message], {}, None, **kw)


# ---------------------------------------------------------------------------
# Noyaux de déplacement
# ---------------------------------------------------------------------------

def noyaux(taille_km, dt_s, vmax_kn, vtyp_kn, dispersion, nb_caps, part_arret, graine=0,
           courant_kmh=(0.0, 0.0), tirer_vitesses=None):
    """Loi du déplacement en un pas, pour chaque cap. Retourne (liste de noyaux, rayon en cases).

    courant_kmh : dérive (est, nord) ajoutée à chaque déplacement (courant de surface).
    tirer_vitesses(rng, m) : loi de vitesse apprise (nœuds) à la place de la loi paramétrique.
    """
    km_s = KN_EN_KMH / 3600.0
    ddx = float(courant_kmh[0]) * dt_s / 3600.0
    ddy = float(courant_kmh[1]) * dt_s / 3600.0
    rmax = vmax_kn * km_s * dt_s + math.hypot(ddx, ddy)
    r = int(math.ceil((rmax + taille_km) / taille_km))
    taille_noyau = 2 * r + 1
    if nb_caps == 1:
        sigma = max(dispersion * vtyp_kn * km_s * dt_s, 0.35 * taille_km)
        off = np.arange(-r, r + 1) * taille_km
        gx, gy = np.meshgrid(off, off)
        dist = np.hypot(gx, gy)
        k = np.exp(-0.5 * (dist / sigma) ** 2)
        k[dist > rmax + 0.5 * taille_km] = 0.0
        return [k / k.sum()], r
    rng = np.random.default_rng(graine)
    m = 40000
    sorties = []
    for h in range(nb_caps):
        theta0 = 2 * math.pi * h / nb_caps
        ang = theta0 + rng.uniform(-math.pi / nb_caps, math.pi / nb_caps, m)
        # Vitesse : arrêt / très lent avec probabilité part_arret, sinon loi normale
        # large autour de la vitesse typique, tronquée à [0, vmax].
        if tirer_vitesses is not None:
            v = np.clip(tirer_vitesses(rng, m), 0, vmax_kn)
        else:
            v = rng.normal(vtyp_kn, dispersion * vtyp_kn, m)
            lent = rng.random(m) < part_arret
            v[lent] = rng.uniform(0, 0.5, lent.sum())
            v = np.clip(np.abs(v), 0, vmax_kn)
        d = v * km_s * dt_s
        # position de départ uniforme dans la case (lissage de la discrétisation)
        dx = d * np.cos(ang) + ddx + rng.uniform(-0.5, 0.5, m) * taille_km
        dy = d * np.sin(ang) + ddy + rng.uniform(-0.5, 0.5, m) * taille_km
        bj = np.clip(np.round(dx / taille_km).astype(int) + r, 0, taille_noyau - 1)
        bi = np.clip(np.round(dy / taille_km).astype(int) + r, 0, taille_noyau - 1)
        k = np.zeros((taille_noyau, taille_noyau))
        np.add.at(k, (bi, bj), 1.0)
        sorties.append(k / k.sum())
    return sorties, r


def matrice_caps(nb_caps, persistance):
    if nb_caps == 1:
        return np.ones((1, 1))
    M = np.zeros((nb_caps, nb_caps))
    reste = 1.0 - persistance
    for h in range(nb_caps):
        M[h, h] = persistance
        M[h, (h + 1) % nb_caps] += reste * 0.4
        M[h, (h - 1) % nb_caps] += reste * 0.4
        autres = [g for g in range(nb_caps) if g not in (h, (h + 1) % nb_caps, (h - 1) % nb_caps)]
        for g in autres:
            M[h, g] += reste * 0.2 / len(autres)
    return M / M.sum(axis=1, keepdims=True)


def poids_cap(nb_caps, cap_deg, vitesse_kn, kappa=4.0):
    """Information apportée par un cap déclaré (0 = nord, sens horaire)."""
    if nb_caps == 1 or cap_deg is None or not np.isfinite(cap_deg) or \
            vitesse_kn is None or not np.isfinite(vitesse_kn) or vitesse_kn < 1.5:
        return np.ones(nb_caps)
    theta = math.radians(90.0 - cap_deg)
    th = 2 * np.pi * np.arange(nb_caps) / nb_caps
    w = np.exp(kappa * np.cos(th - theta))
    # mélange avec un uniforme : le cap déclaré peut être faux ou bruité
    w = 0.8 * w / w.sum() + 0.2 / nb_caps
    return w * nb_caps


def _conv(a, k):
    out = fftconvolve(a, k, mode="same")
    np.maximum(out, 0.0, out=out)
    return out


def _conv_lot(a, k):
    """Convolutions 2D par lot : a (2, H, ny, nx), k (1, H, kh, kw)."""
    out = fftconvolve(a, k, mode="same", axes=(-2, -1))
    np.maximum(out, 0.0, out=out)
    return out


def _gauss(grille: Grille, x, y, sigma_km):
    gx, gy = np.meshgrid(grille.xc, grille.yc)
    s = max(sigma_km, 0.5 * grille.taille)
    return np.exp(-0.5 * ((gx - x) ** 2 + (gy - y) ** 2) / s ** 2)


def _vraisemblance_obs(grille, obs: Observation):
    g = _gauss(grille, obs.x, obs.y, obs.sigma_km)
    p = float(np.clip(obs.p_attribution, 0.0, 1.0))
    # Avec probabilité (1 - p), l'observation ne concerne pas ce bateau et
    # n'apporte aucune information (terme constant).
    return p * g + (1.0 - p)


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------

def reconstruire(grille: Grille, debut: Observation, fin: Observation, params_type: dict,
                 params_modele: dict, zone_id: str | None = None, externes=None,
                 habitudes: np.ndarray | None = None, n_trajets: int | None = None,
                 graine: int = 0, garder_marginales: bool = False,
                 dispersion: float | None = None, facteur_vitesse: float = 1.0,
                 persistance: float | None = None, nb_caps: int | None = None,
                 echantillonner: bool = True, courant_kmh=(0.0, 0.0), appris=None) -> Resultat:
    """appris : ModeleAppris (seatrace/appris.py) — remplace la loi de vitesse, la matrice de
    changement de cap et l'a priori spatial par ceux estimés sur des historiques."""
    t_debut = time.perf_counter()
    externes = externes or []
    raisons = []
    pm = params_modele
    duree = fin.t_s - debut.t_s
    if duree <= 0:
        return _echec("Intervalle de durée nulle ou négative.")
    if duree / 3600 > pm["duree_max_interruption_h"]:
        return _echec(f"Interruption de {duree / 3600:.1f} h, au-delà du maximum retenu "
                      f"({pm['duree_max_interruption_h']} h) : estimation insuffisamment étayée.")
    distance = math.hypot(fin.x - debut.x, fin.y - debut.y)
    vmax_test = params_type["vitesse_max_kn"] * facteur_vitesse
    portee = vmax_test * KN_EN_KMH * duree / 3600
    if distance - 3 * (debut.sigma_km + fin.sigma_km) > portee:
        return _echec(f"Observations incompatibles : {distance:.1f} km à parcourir en {duree / 60:.0f} min, soit plus "
                      f"que la portée maximale de {portee:.1f} km à {vmax_test:.0f} nœuds. Cela peut indiquer une "
                      "donnée altérée, une erreur d'horodatage ou une hypothèse de vitesse inadaptée.")
    n = int(np.clip(round(duree / pm["pas_temps_s"]), 1, PAS_MAX))
    dt = duree / n
    disp = pm["dispersion_relative"] if dispersion is None else dispersion
    pers = pm["persistance_cap"] if persistance is None else persistance
    H = int(pm["nb_caps"] if nb_caps is None else nb_caps)
    vmax = params_type["vitesse_max_kn"] * facteur_vitesse
    vtyp = min(params_type["vitesse_typique_kn"], vmax)
    tirer_v = None
    if appris is not None:
        H = appris.nb_caps
        tirer_v = appris.tireur_vitesses(vtyp)
    K, r = noyaux(grille.taille, dt, vmax, vtyp, disp, H, pm["part_arret"], courant_kmh=courant_kmh,
                  tirer_vitesses=tirer_v)
    Kf = [np.flip(k, (0, 1)) for k in K]
    Kpile = np.stack(K)[None]
    Kfpile = np.stack(Kf)[None]
    M = appris.matrice_caps_dt(dt) if appris is not None else matrice_caps(H, pers)

    mer = grille.mer.astype(float)
    if habitudes is not None and pm.get("poids_habitudes", 0) > 0:
        mer = mer * np.power(np.clip(habitudes, 1e-3, None), pm["poids_habitudes"])
    if appris is not None:
        prior = appris.densite_sur_grille(grille)
        if prior is not None:
            mer = mer * np.power(prior, appris.poids_densite)
    zone = grille.zones[zone_id].astype(float) if zone_id else np.zeros_like(mer)
    hors = 1.0 - zone
    xmax = grille.x0 + grille.nx * grille.taille
    ymax = grille.y0 + grille.ny * grille.taille
    for o, nom in ((debut, "début"), (fin, "fin")):
        if not (grille.x0 <= o.x <= xmax and grille.y0 <= o.y <= ymax):
            return _echec(f"L'observation de {nom} est hors de la zone d'étude.")
        i, j = grille.indice(o.x, o.y)
        if not grille.mer[i, j]:
            raisons.append(f"L'observation de {nom} tombe sur une case de terre (côte proche ou "
                           "position douteuse) ; elle est rattachée aux cases de mer voisines.")

    obs_pas = {}
    for o in externes:
        k = int(round((o.t_s - debut.t_s) / dt))
        if 0 < k < n:
            obs_pas.setdefault(k, []).append(_vraisemblance_obs(grille, o))
        else:
            raisons.append(f"Observation externe {o.libelle or ''} hors de l'intervalle : ignorée.")
    L_fin = _gauss(grille, fin.x, fin.y, fin.sigma_km)
    cap_fin = poids_cap(H, fin.cap_deg, fin.vitesse_kn)
    cap_deb = poids_cap(H, debut.cap_deg, debut.vitesse_kn)

    # --- Passe avant ------------------------------------------------------------
    g0 = _gauss(grille, debut.x, debut.y, debut.sigma_km) * mer
    if g0.sum() == 0:
        return _echec("Aucune case de mer près de l'observation de début.")
    A = np.zeros((n + 1, 2, H) + mer.shape, dtype=np.float32)
    init = cap_deb[:, None, None] / cap_deb.sum() * g0[None]
    A[0, 0] = init * hors
    A[0, 1] = init * zone
    A[0] /= A[0].sum()
    logZ = 0.0
    for k in range(1, n + 1):
        mix = np.einsum("hg,fhyx->fgyx", M, A[k - 1].astype(float))
        B = _conv_lot(mix, Kpile) * mer
        nv1 = B[1] + B[0] * zone
        nv0 = B[0] * hors
        for L in obs_pas.get(k, []):
            nv0 = nv0 * L
            nv1 = nv1 * L
        if k == n:
            nv0 = nv0 * L_fin * cap_fin[:, None, None]
            nv1 = nv1 * L_fin * cap_fin[:, None, None]
        s = nv0.sum() + nv1.sum()
        if s <= 0 or not np.isfinite(s):
            return _echec("Les observations de début et de fin sont incompatibles avec les hypothèses "
                          f"(vitesse maximale {vmax:.0f} nœuds, terres) : aucun trajet possible. "
                          "Cela peut indiquer une donnée altérée ou une hypothèse de vitesse trop basse.",
                          n_pas=n, dt_s=dt)
        logZ += math.log(s)
        A[k, 0] = nv0 / s
        A[k, 1] = nv1 / s

    # Contrôle de compatibilité : la masse finale doit se trouver près de l'observation de fin
    gx, gy = np.meshgrid(grille.xc, grille.yc)
    pf = A[n].sum(axis=(0, 1))
    ecart_fin = float((pf * np.hypot(gx - fin.x, gy - fin.y)).sum() / max(pf.sum(), 1e-300))
    if ecart_fin > 3 * max(fin.sigma_km, grille.taille) + grille.taille:
        return _echec("Les trajets compatibles avec les hypothèses n'atteignent pas l'observation de fin "
                      f"(écart moyen {ecart_fin:.1f} km) : observations incompatibles avec le modèle "
                      "(terres, vitesse maximale) ou donnée altérée.", n_pas=n, dt_s=dt)
    if logZ < math.log(pm["seuil_vraisemblance_min"]):
        raisons.append("Le trajet requis est très improbable sous les hypothèses (vitesse proche du "
                       "maximum ou détour important) : résultat à interpréter avec prudence.")
    p_passage = float(A[n, 1].sum()) if zone_id else None

    # --- Passe arrière : probabilités de présence (corridors) -------------------
    occupation = np.zeros_like(mer)
    marg = np.zeros((n + 1,) + mer.shape, dtype=np.float32) if garder_marginales else None
    beta = np.ones((2, H) + mer.shape)
    for k in range(n, -1, -1):
        m = (A[k] * beta).sum(axis=(0, 1))
        sm = m.sum()
        if sm > 0:
            m = m / sm
        occupation += m
        if marg is not None:
            marg[k] = m
        if k == 0:
            break
        obs_k = L_fin.copy() if k == n else np.ones_like(mer)
        for L in obs_pas.get(k, []):
            obs_k = obs_k * L
        c = mer * obs_k
        capw = cap_fin if k == n else np.ones(H)
        G0 = c[None] * (hors[None] * beta[0] + zone[None] * beta[1]) * capw[:, None, None]
        G1 = c[None] * beta[1] * capw[:, None, None]
        D = _conv_lot(np.stack([G0, G1]), Kfpile)
        beta = np.einsum("hg,fgyx->fhyx", M, D)
        beta /= max(beta.max(), 1e-300)
    occupation /= (n + 1)

    # --- Trajets tirés dans la loi a posteriori --------------------------------
    trajets, ctrl, log_probas = [], {}, []
    if echantillonner:
        rng = np.random.default_rng(graine)
        n_tir = n_trajets or int(pm["nb_trajets_dessines"])
        ny, nx = mer.shape
        passages = 0
        coherents = 0
        fin_flat = A[n].astype(float).ravel()
        fin_flat = fin_flat / fin_flat.sum()
        for _ in range(n_tir):
            idx = rng.choice(len(fin_flat), p=fin_flat)
            logp = math.log(max(fin_flat[idx], 1e-300))
            f, reste = divmod(idx, H * ny * nx)
            h, cell = divmod(reste, ny * nx)
            ci, cj = divmod(cell, nx)
            chemin = [(ci, cj)]
            passe = bool(f)
            for k in range(n - 1, -1, -1):
                i0, i1 = max(0, ci - r), min(ny, ci + r + 1)
                j0, j1 = max(0, cj - r), min(nx, cj + r + 1)
                kw = Kf[h][r - (ci - i0): r + (i1 - ci), r - (cj - j0): r + (j1 - cj)]
                w = A[k][:, :, i0:i1, j0:j1].astype(float) * M[:, h][None, :, None, None] * kw[None, None]
                if f == 0:
                    w[1] = 0.0
                elif zone[ci, cj] == 0:
                    w[0] = 0.0
                sw = w.sum()
                if sw <= 0:
                    break
                pw = (w / sw).ravel()
                sel = rng.choice(w.size, p=pw)
                logp += math.log(max(pw[sel], 1e-300))
                f, reste = divmod(sel, H * (i1 - i0) * (j1 - j0))
                h, loc = divmod(reste, (i1 - i0) * (j1 - j0))
                di, dj = divmod(loc, j1 - j0)
                ci, cj = i0 + di, j0 + dj
                chemin.append((ci, cj))
            chemin.reverse()
            xs = np.array([grille.x0 + (c[1] + 0.5) * grille.taille for c in chemin])
            ys = np.array([grille.y0 + (c[0] + 0.5) * grille.taille for c in chemin])
            trajets.append((xs, ys, passe))
            log_probas.append(logp)
            passages += passe
            geom = any(zone[c] > 0 for c in chemin) if zone_id else False
            coherents += (geom == passe)
        if zone_id:
            p_mc = passages / n_tir
            ctrl = {"n_trajets": n_tir, "proportion_passage": p_mc,
                    "erreur_type": math.sqrt(max(p_mc * (1 - p_mc), 1.0 / n_tir) / n_tir),
                    "coherence_drapeau_geometrie": coherents / n_tir}

    hyp = {
        "pas_de_temps_s": round(dt, 1),
        "nombre_de_pas": n,
        "taille_case_km": grille.taille,
        "nombre_de_caps": H,
        "persistance_cap": pers if H > 1 else None,
        "vitesse_max_kn": round(vmax, 1),
        "vitesse_typique_kn": round(vtyp, 1),
        "dispersion_vitesse_relative": disp,
        "part_arret": pm["part_arret"],
        "incertitude_debut_km": round(debut.sigma_km, 3),
        "incertitude_fin_km": round(fin.sigma_km, 3),
        "cap_declare_debut_utilise": bool(np.ptp(cap_deb) > 0),
        "cap_declare_fin_utilise": bool(np.ptp(cap_fin) > 0),
        "terres_infranchissables": bool((~grille.mer).any()),
        "zone_exclue_du_modele": False,
        "observations_externes": len(externes),
        "habitudes": bool(habitudes is not None and pm.get("poids_habitudes", 0) > 0),
        "meteo_courants": bool(courant_kmh[0] or courant_kmh[1]),
        "courant_kmh": [float(courant_kmh[0]), float(courant_kmh[1])],
        "parametres_appris": None if appris is None else appris.description(),
    }
    return Resultat(
        proba_passage=p_passage, statut="etayee", raisons=raisons, controle_mc=ctrl,
        occupation=occupation, marginales=marg, trajets=trajets, log_probas=log_probas, hypotheses=hyp,
        temps_calcul_s=time.perf_counter() - t_debut, n_pas=n, dt_s=dt, log_vraisemblance=logZ,
    )


def analyse_sensibilite(grille, debut, fin, params_type, params_modele, zone_id, externes=None,
                        habitudes=None):
    """Recalcule la probabilité de passage en faisant varier les hypothèses principales."""
    pm = params_modele
    lignes = []

    def run(libelle, **kw):
        r = reconstruire(grille, debut if "debut" not in kw else kw.pop("debut"),
                         fin if "fin" not in kw else kw.pop("fin"), params_type, pm, zone_id,
                         externes, habitudes, echantillonner=False, **kw)
        lignes.append({"variation": libelle, "proba_passage": r.proba_passage})

    run("hypothèses de référence")
    for disp in pm["sensibilite_dispersion"]:
        if disp != pm["dispersion_relative"]:
            run(f"dispersion de vitesse {disp}", dispersion=disp)
    for fv in pm["sensibilite_vitesse"]:
        if fv != 1.0:
            run(f"vitesse max × {fv}", facteur_vitesse=fv)
    for pers in pm["sensibilite_persistance"]:
        if pers != pm["persistance_cap"]:
            run(f"persistance du cap {pers}", persistance=pers)
    for facteur in (3.0,):
        run(f"incertitude des bornes × {facteur:g}",
            debut=Observation(debut.x, debut.y, debut.sigma_km * facteur, debut.t_s,
                              cap_deg=debut.cap_deg, vitesse_kn=debut.vitesse_kn),
            fin=Observation(fin.x, fin.y, fin.sigma_km * facteur, fin.t_s,
                            cap_deg=fin.cap_deg, vitesse_kn=fin.vitesse_kn))
    run("sans cap (marche aléatoire isotrope)", nb_caps=1)
    return lignes


def qualifier(res: Resultat, sensibilite: list, fiabilite_bornes: str, calibre: bool,
              duree_min: float):
    """Statut défendable de l'estimation, selon des critères explicites."""
    if res.proba_passage is None:
        return res
    raisons = list(res.raisons)
    # La variante isotrope sert de comparaison, pas de critère.
    sens = [l for l in sensibilite if "isotrope" not in l["variation"]]
    vals = [l["proba_passage"] for l in sens if l["proba_passage"] is not None]
    impossibles = [l for l in sens if l["proba_passage"] is None]
    etendue = (max(vals) - min(vals)) if vals else 1.0
    statut = "etayee"
    if fiabilite_bornes == "reduite":
        statut = "fragile"
        raisons.append("Les observations qui encadrent la période sont proches de données suspectes.")
    if etendue > 0.25:
        statut = "fragile"
        raisons.append(f"Le résultat varie de {min(vals):.0%} à {max(vals):.0%} selon les hypothèses.")
    if duree_min > 120:
        statut = "fragile"
        raisons.append("Interruption longue (plus de 2 h).")
    if not calibre:
        if statut == "etayee":
            statut = "fragile"
        raisons.append("Modèle non réglé pour ce type de bateau (aucune donnée de réglage).")
    if etendue > 0.5:
        statut = "insuffisamment_etayee"
        raisons.append("Des hypothèses raisonnables mènent à des conclusions opposées : "
                       "estimation insuffisamment étayée.")
    if impossibles:
        statut = "insuffisamment_etayee"
        raisons.append("Certaines hypothèses raisonnables rendent le trajet impossible.")
    res.statut = statut
    res.raisons = raisons
    res.sensibilite = sensibilite
    res.lecture = lecture_probabilite(res.proba_passage)
    if statut == "insuffisamment_etayee":
        res.lecture = "Estimation insuffisamment étayée : l'outil ne peut pas conclure."
    return res


# ---------------------------------------------------------------------------
# Méthodes de référence simples (comparaison)
# ---------------------------------------------------------------------------

def reference_interpolation(zone_chemin, debut: Observation, fin: Observation, n=200):
    """Ligne droite entre les deux observations : 1 si elle coupe la zone, 0 sinon.
    Limite : ignore les terres et toute autre route possible."""
    xs = np.linspace(debut.x, fin.x, n)
    ys = np.linspace(debut.y, fin.y, n)
    return float(zone_chemin.contains_points(np.column_stack([xs, ys])).any())


def reference_zone_atteignable(grille: Grille, zone_id, debut: Observation, fin: Observation, vmax_kn):
    """Ellipse des points atteignables à vitesse max : 1 si elle touche la zone, 0 sinon.
    Limite : dit seulement « possible » ou « impossible », sans aucune nuance."""
    duree_h = (fin.t_s - debut.t_s) / 3600
    portee = vmax_kn * KN_EN_KMH * duree_h
    gx, gy = np.meshgrid(grille.xc, grille.yc)
    somme = np.hypot(gx - debut.x, gy - debut.y) + np.hypot(gx - fin.x, gy - fin.y)
    atteignable = (somme <= portee + grille.taille) & grille.mer
    return float((atteignable & grille.zones[zone_id]).any())
