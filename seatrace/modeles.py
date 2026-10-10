"""Socle commun des modèles probabilistes de la V2.

Tous les modèles qui produisent des chemins renvoient un `ResultatModele` :
- la probabilité de passage dans la zone (quand une zone est choisie) ;
- TOUS les chemins générés, chacun avec sa probabilité (`poids`, somme = 1 pour un modèle) ;
- une carte d'occupation (corridors) sur la grille ;
- des diagnostics propres au modèle.

Les chemins partagent la même horloge : n + 1 instants régulièrement espacés entre
l'observation de début et l'observation de fin. On peut donc les comparer, les
segmenter (ACP) et les exporter ensemble.

Ce que signifie le poids d'un chemin :
- HMM (grille) : les chemins identiques sont fusionnés ; poids = part des tirages, et
  `log_proba` = log de la probabilité a posteriori exacte du chemin d'états tiré ;
- particules, pont brownien : chaque chemin est un tirage équiprobable de la loi a
  posteriori (poids = 1/N) ; la probabilité utile est celle de la FAMILLE de chemins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geo import KN_EN_KMH, Grille, densifier

PAS_MAX = 90

LIBELLES = {
    "hmm": "HMM sur grille à cap persistant",
    "appris": "Modèle appris sur des historiques (HMM à paramètres estimés)",
    "particules": "Filtre particulaire avec lissage (FFBSi)",
    "pont": "Pont brownien (BBMM)",
    "boosting": "Boosting stochastique (prédiction directe de la probabilité)",
}

PRODUIT_CHEMINS = {"hmm": True, "appris": True, "particules": True, "pont": True, "boosting": False}


@dataclass
class Chemin:
    modele: str
    identifiant: str
    xs: np.ndarray
    ys: np.ndarray
    poids: float
    passe_zone: bool | None = None
    log_proba: float | None = None
    famille: str | None = None
    occurrences: int = 1


@dataclass
class ResultatModele:
    modele: str
    proba_passage: float | None
    statut: str  # "ok" ou "echec"
    raisons: list = field(default_factory=list)
    chemins: list = field(default_factory=list)
    occupation: np.ndarray | None = None
    temps_calcul_s: float = 0.0
    n_pas: int = 0
    dt_s: float = 0.0
    erreur_mc: float | None = None
    intervalle: tuple | None = None  # (bas, haut) propre au modèle, si disponible
    diagnostics: dict = field(default_factory=dict)
    hypotheses: dict = field(default_factory=dict)

    @property
    def libelle(self):
        return LIBELLES.get(self.modele, self.modele)


def echec(modele, message, **kw):
    return ResultatModele(modele, None, "echec", [message], **kw)


def nombre_de_pas(duree_s, pas_temps_s):
    n = int(np.clip(round(duree_s / pas_temps_s), 1, PAS_MAX))
    return n, duree_s / n


def controles_prealables(modele, grille: Grille, debut, fin, params_type, pm, facteur_vitesse=1.0):
    """Mêmes refus que le HMM de la V1, pour que tous les modèles répondent aux mêmes cas."""
    duree = fin.t_s - debut.t_s
    if duree <= 0:
        return echec(modele, "Intervalle de durée nulle ou négative.")
    if duree / 3600 > pm["duree_max_interruption_h"]:
        return echec(modele, f"Interruption de {duree / 3600:.1f} h, au-delà du maximum retenu "
                             f"({pm['duree_max_interruption_h']} h) : estimation insuffisamment étayée.")
    vmax = params_type["vitesse_max_kn"] * facteur_vitesse
    distance = math.hypot(fin.x - debut.x, fin.y - debut.y)
    portee = vmax * KN_EN_KMH * duree / 3600
    if distance - 3 * (debut.sigma_km + fin.sigma_km) > portee:
        return echec(modele, f"Observations incompatibles : {distance:.1f} km à parcourir en {duree / 60:.0f} min, "
                             f"soit plus que la portée maximale de {portee:.1f} km à {vmax:.0f} nœuds.")
    xmax = grille.x0 + grille.nx * grille.taille
    ymax = grille.y0 + grille.ny * grille.taille
    for o, nom in ((debut, "début"), (fin, "fin")):
        if not (grille.x0 <= o.x <= xmax and grille.y0 <= o.y <= ymax):
            return echec(modele, f"L'observation de {nom} est hors de la zone d'étude.")
    return None


def passages(grille: Grille, zone_id, xs, ys, sous_pas=4):
    """Pour un lot de chemins (m, n+1) : vrai si le chemin densifié entre dans la zone."""
    if not zone_id:
        return np.zeros(len(xs), dtype=bool)
    dx, dy = densifier(xs, ys, sous_pas)
    return grille.dans_zone(zone_id, dx, dy).any(axis=1)


def occupation_chemins(grille: Grille, xs, ys, poids=None):
    """Probabilité de présence moyenne sur la grille (même définition que le HMM)."""
    i, j, dedans = grille.indices_vec(xs, ys)
    w = np.ones(xs.shape) if poids is None else np.repeat(np.asarray(poids)[:, None], xs.shape[1], axis=1)
    occ = np.zeros((grille.ny, grille.nx))
    np.add.at(occ, (i[dedans], j[dedans]), w[dedans])
    s = occ.sum()
    return occ / s if s > 0 else occ


def erreur_type(p, n):
    return math.sqrt(max(p * (1 - p), 1.0 / max(n, 1)) / max(n, 1))


def vraisemblance_externe(obs, x, y):
    """Observation externe avec probabilité d'attribution (jamais attribuée d'office au bateau)."""
    s = max(obs.sigma_km, 1e-3)
    g = np.exp(-0.5 * ((x - obs.x) ** 2 + (y - obs.y) ** 2) / s ** 2)
    p = float(np.clip(obs.p_attribution, 0.0, 1.0))
    return p * g + (1.0 - p)


def depuis_hmm(modele, r, grille: Grille, zone_id) -> ResultatModele:
    """Convertit un résultat du HMM (reconstruct.Resultat) au format commun.

    Les chemins identiques sur la grille sont fusionnés ; leur poids est la part des tirages.
    """
    if r.occupation is None:
        return ResultatModele(modele, None, "echec", list(r.raisons), temps_calcul_s=r.temps_calcul_s,
                              n_pas=r.n_pas, dt_s=r.dt_s)
    groupes = {}
    lps = r.log_probas or [None] * len(r.trajets)
    for (xs, ys, passe), lp in zip(r.trajets, lps):
        cle = (np.round(xs, 4).tobytes(), np.round(ys, 4).tobytes())
        if cle in groupes:
            groupes[cle][3] += 1
        else:
            groupes[cle] = [xs, ys, passe, 1, lp]
    n = max(len(r.trajets), 1)
    chemins = []
    for k, (xs, ys, passe, occ, lp) in enumerate(sorted(groupes.values(), key=lambda g: -g[3])):
        chemins.append(Chemin(modele, f"{modele}-{k + 1:04d}", xs, ys, occ / n,
                              bool(passe) if zone_id else None, lp, occurrences=occ))
    mc = r.controle_mc or {}
    res = ResultatModele(
        modele, r.proba_passage, "ok", list(r.raisons), chemins, r.occupation, r.temps_calcul_s, r.n_pas, r.dt_s,
        erreur_mc=mc.get("erreur_type"),
        diagnostics={"tirages": len(r.trajets), "chemins_distincts": len(chemins),
                     "log_vraisemblance": round(r.log_vraisemblance, 3),
                     "statut_v1": r.statut, "lecture": r.lecture,
                     "controle_mc": mc},
        hypotheses=r.hypotheses,
    )
    vals = [l["proba_passage"] for l in (r.sensibilite or [])
            if l["proba_passage"] is not None and "isotrope" not in l["variation"]]
    if vals:
        res.intervalle = (min(vals), max(vals))
        res.diagnostics["sensibilite"] = r.sensibilite
    return res
