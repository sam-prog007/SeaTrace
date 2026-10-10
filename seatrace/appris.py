"""Modèle probabiliste APPRIS sur des historiques de trajectoires.

Même machinerie que le HMM sur grille (passe avant/arrière exacte, drapeau de passage),
mais ses lois ne sont plus fixées à la main : elles sont ESTIMÉES sur des trajets passés
(AIS ouverts : Autorité maritime danoise, NOAA MarineCadastre ; ou historiques d'un client) :

1. loi des vitesses : distribution empirique du rapport vitesse / vitesse de croisière du
   trajet (arrêts et ralentissements compris), remise à l'échelle du type du bateau étudié ;
2. matrice de changement de cap : fréquences empiriques de passage d'une direction à une
   autre entre deux pas de temps consécutifs (lissage de Laplace) ;
3. a priori spatial : densité de trafic historique (cases fréquentées), utilisée avec un
   exposant `poids_densite` (0 = ignorée). Elle ne sert que si l'historique couvre la zone.

Le modèle est enregistré en .npz (tableaux numériques uniquement, lu avec allow_pickle=False).

Limite essentielle : un modèle appris sur les habitudes favorise les routes habituelles ; un
trajet volontairement inhabituel est, par construction, jugé moins probable. C'est pourquoi il
est présenté À CÔTÉ des modèles non appris, jamais à leur place.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import KN_EN_KMH, Grille, vers_km, vers_latlon


@dataclass
class ModeleAppris:
    nb_caps: int
    dt_ref_s: float
    rapports_vitesse: np.ndarray
    matrice: np.ndarray
    densite: np.ndarray | None = None
    densite_bornes: tuple | None = None  # (lat_min, lat_max, lon_min, lon_max)
    poids_densite: float = 0.1
    meta: dict = field(default_factory=dict)

    def tireur_vitesses(self, vtyp_kn):
        r = self.rapports_vitesse

        def tirer(rng, m):
            v = rng.choice(r, size=m) * vtyp_kn
            return np.abs(v + rng.normal(0, 0.05 * vtyp_kn, m))
        return tirer

    def matrice_caps_dt(self, dt_s):
        """Matrice adaptée au pas de temps effectif (puissance fractionnaire si besoin)."""
        ratio = dt_s / self.dt_ref_s
        if abs(ratio - 1) < 0.15:
            return self.matrice
        from scipy.linalg import fractional_matrix_power
        M = np.real(fractional_matrix_power(self.matrice, ratio))
        M = np.clip(M, 1e-6, None)
        return M / M.sum(axis=1, keepdims=True)

    def densite_sur_grille(self, grille: Grille):
        if self.densite is None or self.poids_densite <= 0:
            return None
        lat_min, lat_max, lon_min, lon_max = self.densite_bornes
        gx, gy = np.meshgrid(grille.xc, grille.yc)
        lat, lon = vers_latlon(gx, gy, grille.origine)
        ny, nx = self.densite.shape
        dedans = (lat >= lat_min) & (lat <= lat_max) & (lon >= lon_min) & (lon <= lon_max)
        if dedans[grille.mer].mean() < 0.5:
            return None  # historique hors de la zone étudiée : a priori non utilisé
        i = np.clip(((lat - lat_min) / (lat_max - lat_min) * ny).astype(int), 0, ny - 1)
        j = np.clip(((lon - lon_min) / (lon_max - lon_min) * nx).astype(int), 0, nx - 1)
        d = np.where(dedans, self.densite[i, j], np.nan)
        moy = np.nanmean(d[grille.mer]) if np.isfinite(d[grille.mer]).any() else 1.0
        d = np.nan_to_num(d / max(moy, 1e-12), nan=1.0)
        return np.clip(d, 0.05, None)

    def description(self):
        return {"source": self.meta.get("source", "?"), "trajets": self.meta.get("trajets"),
                "pas_observes": self.meta.get("pas"), "nb_caps": self.nb_caps,
                "densite_utilisee": self.densite is not None and self.poids_densite > 0,
                "poids_densite": self.poids_densite,
                "persistance_cap_apprise": round(float(np.mean(np.diag(self.matrice))), 3),
                "rapport_vitesse_median": round(float(np.median(self.rapports_vitesse)), 3),
                "part_arrets": round(float(np.mean(self.rapports_vitesse < 0.15)), 3)}

    def sauver(self, chemin: Path):
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            chemin, nb_caps=self.nb_caps, dt_ref_s=self.dt_ref_s, rapports_vitesse=self.rapports_vitesse,
            matrice=self.matrice,
            densite=self.densite if self.densite is not None else np.zeros((0, 0)),
            densite_bornes=np.array(self.densite_bornes if self.densite_bornes else [0, 0, 0, 0], float),
            meta=np.array(json.dumps(self.meta, ensure_ascii=False)))

    @classmethod
    def charger(cls, chemin: Path, poids_densite=0.1):
        with np.load(chemin, allow_pickle=False) as z:
            dens = z["densite"]
            return cls(int(z["nb_caps"]), float(z["dt_ref_s"]), z["rapports_vitesse"].astype(float),
                       z["matrice"].astype(float), dens if dens.size else None,
                       tuple(float(v) for v in z["densite_bornes"]) if dens.size else None,
                       poids_densite, json.loads(str(z["meta"])))


def apprendre(positions: pd.DataFrame, dt_ref_s=120.0, nb_caps=8, ecart_max_s=600.0, resolution_deg=0.004,
              source="?", seuil_mouvement_kn=1.0, lissage=0.5) -> ModeleAppris:
    """Estime le modèle sur un tableau de positions (format SeaTrace : boat_id, trip_id, timestamp, lat, lon).

    Les positions marquées suspectes (colonne `statut` différente de « observee ») sont écartées si la
    colonne existe. Les trajets sont rééchantillonnés à pas fixe uniquement sur les segments sans trou
    de plus de `ecart_max_s` (on n'apprend jamais sur une interpolation de longue absence)."""
    df = positions.copy()
    if "statut" in df.columns:
        df = df[df["statut"] == "observee"]
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    rapports, transitions = [], np.full((nb_caps, nb_caps), lissage)
    n_trajets, n_pas = 0, 0
    largeur = 2 * math.pi / nb_caps
    for _, tr in df.groupby(["boat_id", "trip_id"], sort=False):
        tr = tr.sort_values("timestamp")
        if len(tr) < 10:
            continue
        origine = (float(tr["lat"].iloc[0]), float(tr["lon"].iloc[0]))
        x, y = vers_km(tr["lat"].to_numpy(), tr["lon"].to_numpy(), origine)
        t = (tr["timestamp"] - tr["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
        coupures = np.where(np.diff(t) > ecart_max_s)[0]
        bornes = np.concatenate([[0], coupures + 1, [len(t)]])
        v_tr, h_tr = [], []
        for a, b in zip(bornes[:-1], bornes[1:]):
            if b - a < 3 or t[b - 1] - t[a] < 3 * dt_ref_s:
                continue
            tk = np.arange(t[a], t[b - 1], dt_ref_s)
            xk, yk = np.interp(tk, t[a:b], x[a:b]), np.interp(tk, t[a:b], y[a:b])
            dx, dy = np.diff(xk), np.diff(yk)
            v = np.hypot(dx, dy) / (dt_ref_s / 3600.0) / KN_EN_KMH
            h = np.mod(np.round(np.arctan2(dy, dx) / largeur), nb_caps).astype(int)
            v_tr.append(v)
            h_tr.append(np.where(v >= seuil_mouvement_kn, h, -1))
        if not v_tr:
            continue
        v_all = np.concatenate(v_tr)
        mouv = v_all[v_all >= seuil_mouvement_kn]
        if len(mouv) < 5:
            continue
        v_ref = float(np.median(mouv))
        rapports.append(v_all / v_ref)
        for h in h_tr:
            for h0, h1 in zip(h[:-1], h[1:]):
                if h0 >= 0 and h1 >= 0:
                    transitions[h0, h1] += 1
        n_trajets += 1
        n_pas += len(v_all)
    if n_trajets == 0:
        raise ValueError("Aucun trajet exploitable pour l'apprentissage (au moins 10 positions et des segments "
                         "continus).")
    M = transitions / transitions.sum(axis=1, keepdims=True)
    r = np.clip(np.concatenate(rapports), 0, 3.0)
    # densité de trafic historique (positions observées), en lat/lon
    lat, lon = df["lat"].to_numpy(), df["lon"].to_numpy()
    bornes = (float(lat.min()), float(lat.max()), float(lon.min()), float(lon.max()))
    ny = int(np.clip((bornes[1] - bornes[0]) / resolution_deg, 4, 400))
    nx = int(np.clip((bornes[3] - bornes[2]) / resolution_deg, 4, 400))
    H, _, _ = np.histogram2d(lat, lon, bins=[ny, nx], range=[[bornes[0], bornes[1]], [bornes[2], bornes[3]]])
    from scipy.ndimage import gaussian_filter
    H = gaussian_filter(H, 2.0)
    H = H / max(H.mean(), 1e-12)
    meta = {"source": source, "trajets": n_trajets, "pas": n_pas, "dt_ref_s": dt_ref_s,
            "ecart_max_s": ecart_max_s, "resolution_deg": resolution_deg}
    return ModeleAppris(nb_caps, float(dt_ref_s), r, M, H, bornes, 0.1, meta)
