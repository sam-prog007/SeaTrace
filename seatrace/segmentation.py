"""Segmentation des chemins en FAMILLES par analyse en composantes principales (ACP).

Idée : des milliers de chemins tirés racontent souvent deux ou trois histoires seulement
(« par le nord de l'île », « par le sud, en traversant la zone », « mouillage puis retour »).
On les regroupe pour donner à l'analyste une probabilité PAR FAMILLE, plus lisible qu'un nuage.

Méthode :
1. chaque chemin est rééchantillonné en `nb_points` instants intérieurs (les extrémités sont
   fixées par les observations) et exprimé dans le repère de la ligne directe début → fin :
   avancement le long de la ligne et écart latéral (gauche > 0) ;
2. ACP pondérée sur l'ensemble des chemins de tous les modèles (chaque modèle pèse autant),
   en gardant les composantes qui expliquent 90 % de la variance (5 au plus) ;
3. nombre de familles choisi par le critère BIC d'un mélange gaussien (un nuage continu sans
   structure reste UNE famille : on ne fabrique pas d'histoires artificielles), confirmé par le
   score de silhouette ; les chemins sont ensuite affectés par k-moyennes pondérées ;
4. pour chaque famille et chaque modèle : probabilité = somme des poids des chemins du modèle
   dans la famille. Les familles étant communes, les modèles sont directement comparables.
"""

from __future__ import annotations

import math

import numpy as np

CARDINAUX = ["est", "nord-est", "nord", "nord-ouest", "ouest", "sud-ouest", "sud", "sud-est"]


def _repere(debut, fin):
    ux, uy = fin.x - debut.x, fin.y - debut.y
    L = math.hypot(ux, uy)
    if L < 1e-6:
        ux, uy, L = 1.0, 0.0, 1.0
    ux, uy = ux / L, uy / L
    return ux, uy, -uy, ux, L


def vecteurs(chemins, debut, fin, nb_points):
    ux, uy, nx_, ny_, _ = _repere(debut, fin)
    f = np.linspace(0, 1, nb_points + 2)[1:-1]
    out = np.zeros((len(chemins), 2 * nb_points))
    for i, c in enumerate(chemins):
        k = np.linspace(0, 1, len(c.xs))
        x = np.interp(f, k, c.xs) - debut.x
        y = np.interp(f, k, c.ys) - debut.y
        out[i, :nb_points] = x * ux + y * uy
        out[i, nb_points:] = x * nx_ + y * ny_
    return out


def _cardinal(dx, dy):
    a = math.degrees(math.atan2(dy, dx)) % 360
    return CARDINAUX[int(((a + 22.5) % 360) // 45)]


def segmenter(chemins_par_modele: dict, debut, fin, params, graine=0):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.mixture import GaussianMixture

    ps = params["modeles"]["segmentation"]
    K = int(ps["nb_points"])
    tous, poids, modeles = [], [], []
    actifs = {m: cs for m, cs in chemins_par_modele.items() if cs}
    if not actifs:
        return None
    for m, cs in actifs.items():
        s = sum(c.poids for c in cs) or 1.0
        for c in cs:
            tous.append(c)
            poids.append(c.poids / s / len(actifs))
            modeles.append(m)
    poids = np.asarray(poids)
    X = vecteurs(tous, debut, fin, K)
    moy = np.average(X, axis=0, weights=poids)
    Xc = X - moy
    U, S, Vt = np.linalg.svd(Xc * np.sqrt(poids)[:, None], full_matrices=False)
    var = S ** 2
    ratio = var / var.sum() if var.sum() > 0 else np.zeros_like(var)
    q = int(np.clip(np.searchsorted(np.cumsum(ratio), 0.9) + 1, 1, min(5, len(ratio))))
    Z = Xc @ Vt[:q].T
    meilleur_k, meilleur_s, etiquettes = 1, None, np.zeros(len(tous), int)
    rng = np.random.default_rng(graine)
    ech = rng.choice(len(Z), size=min(len(Z), 1500), replace=False)
    scores, bics = {}, {}
    if len(Z) >= 10 and ratio[0] > 0:
        # échantillon tiré selon les poids (le mélange gaussien n'accepte pas de poids)
        Zb = Z[rng.choice(len(Z), size=min(1500, 3 * len(Z)), p=poids / poids.sum())]
        Zb = Zb + rng.normal(0, 1e-6 * (Zb.std() + 1e-12), Zb.shape)
        for k in range(1, int(ps["familles_max"]) + 1):
            if k >= len(Zb):
                break
            gm = GaussianMixture(n_components=k, covariance_type="full", n_init=2, random_state=graine,
                                 reg_covar=1e-4 * float(np.var(Zb)) + 1e-9).fit(Zb)
            bics[k] = float(gm.bic(Zb))
        k_bic = min(bics, key=bics.get) if bics else 1
        for k in range(2, k_bic + 1):
            km = KMeans(n_clusters=k, n_init=5, random_state=graine).fit(Z, sample_weight=poids)
            lab = km.labels_
            if len(np.unique(lab[ech])) < 2:
                continue
            s = float(silhouette_score(Z[ech], lab[ech]))
            scores[k] = round(s, 3)
            # une famille minuscule (< 2 % de la masse) n'est pas une « histoire » distincte
            masses = np.bincount(lab, weights=poids, minlength=k)
            if masses.min() < 0.02:
                continue
            if s >= float(ps["silhouette_min"]) and (meilleur_s is None or s > meilleur_s + 0.02):
                meilleur_k, meilleur_s, etiquettes = k, s, lab
    # ordre des familles : par masse décroissante
    masses = np.bincount(etiquettes, weights=poids, minlength=meilleur_k)
    ordre = np.argsort(-masses)
    renum = {int(old): new for new, old in enumerate(ordre)}
    etiquettes = np.array([renum[int(e)] for e in etiquettes])
    ux, uy, nx_, ny_, L = _repere(debut, fin)
    familles = []
    for f in range(meilleur_k):
        sel = etiquettes == f
        w = poids[sel]
        lat = X[sel, K:]
        lat_moy = float(np.average(lat.mean(axis=1), weights=w))
        lat_max = float(np.average(lat[:, np.argmax(np.abs(lat).mean(axis=0))], weights=w))
        ox, oy = lat_max * nx_, lat_max * ny_
        if abs(lat_max) < 0.3:
            desc = "proche de la ligne directe (écart < 300 m)"
        else:
            desc = f"jusqu'à {abs(lat_max):.1f} km au {_cardinal(ox, oy)} de la ligne directe"
        xs_m = np.zeros(K + 2)
        ys_m = np.zeros(K + 2)
        av = np.average(X[sel, :K], axis=0, weights=w)
        la = np.average(X[sel, K:], axis=0, weights=w)
        xs_m[1:-1] = debut.x + av * ux + la * nx_
        ys_m[1:-1] = debut.y + av * uy + la * ny_
        xs_m[0], ys_m[0], xs_m[-1], ys_m[-1] = debut.x, debut.y, fin.x, fin.y
        par_modele = {}
        for m, cs in actifs.items():
            masse = sum(c.poids for c, e, mm in zip(tous, etiquettes, modeles) if mm == m and e == f)
            tot = sum(c.poids for c in cs) or 1.0
            par_modele[m] = round(masse / tot, 4)
        passe = [c.passe_zone for c, e in zip(tous, etiquettes) if e == f and c.passe_zone is not None]
        familles.append({
            "identifiant": f"F{f + 1}", "description": desc,
            "probabilite_commune": round(float(w.sum() / poids.sum()), 4),
            "probabilite_par_modele": par_modele,
            "part_passant_par_zone": round(float(np.mean(passe)), 3) if passe else None,
            "ecart_lateral_moyen_km": round(lat_moy, 3),
            "dispersion_laterale_km": round(float(np.sqrt(np.average((lat.mean(axis=1) - lat_moy) ** 2, weights=w))), 3),
            "nombre_chemins": int(sel.sum()),
            "chemin_moyen": {"xs": xs_m.tolist(), "ys": ys_m.tolist()},
        })
    for c, e in zip(tous, etiquettes):
        c.famille = f"F{e + 1}"
    # coordonnées des 2 premières composantes (nuage pour l'interface ; sous-échantillon)
    nuage = []
    for m in actifs:
        idx = [i for i, mm in enumerate(modeles) if mm == m]
        idx = idx if len(idx) <= 300 else list(rng.choice(idx, 300, replace=False))
        for i in idx:
            nuage.append({"modele": m, "c1": round(float(Z[i, 0]), 4),
                          "c2": round(float(Z[i, 1]), 4) if q > 1 else 0.0,
                          "famille": f"F{etiquettes[i] + 1}", "chemin": tous[i].identifiant})
    return {
        "familles": familles, "nombre_familles": meilleur_k,
        "variance_expliquee": [round(float(v), 4) for v in ratio[:5]],
        "composantes_retenues": q, "silhouettes": scores,
        "silhouette_retenue": meilleur_s, "nuage_acp": nuage,
        "bic": {k: round(v, 1) for k, v in bics.items()},
        "methode": "ACP pondérée (repère de la ligne directe) ; nombre de familles par BIC d'un mélange gaussien "
                   "et silhouette ; affectation par k-moyennes",
    }
