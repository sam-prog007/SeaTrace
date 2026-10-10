"""Contrôles explicables de cohérence des données de navigation.

Chaque alerte contient les valeurs calculées qui la justifient. Aucune alerte
n'est une preuve de fraude ni de cyberattaque :
- les alertes « intégrité » signalent des données potentiellement altérées ;
- les alertes « disponibilité » (interruptions) signalent seulement une absence
  de données, qui peut avoir une cause banale (couverture, batterie, matériel).

Les seuils dépendent du type de bateau et sont lus dans config/parametres.json.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .geo import KN_EN_KMH, vers_km

INTEGRITE = "integrite"
DISPONIBILITE = "disponibilite"

POIDS = {
    "saut_position": 30,
    "incoherence_vitesse_cap": 20,
    "derive_cumulee": 20,
    "identite_dedoublee": 40,
    "rejeu": 40,
    "doublon": 5,
    "interruption": 0,
}

LIBELLES = {
    "saut_position": "Saut de position (vitesse impossible)",
    "incoherence_vitesse_cap": "Vitesse/cap déclarés incohérents avec le déplacement",
    "derive_cumulee": "Dérive cumulée entre positions et vitesse/cap déclarés",
    "identite_dedoublee": "Identité présente à deux endroits incompatibles",
    "rejeu": "Séquence de déplacements identique à une séquence antérieure (rejeu possible)",
    "doublon": "Enregistrements en double",
    "interruption": "Interruption de réception (disponibilité)",
}


@dataclass
class Alerte:
    identifiant: str
    type: str
    categorie: str
    debut: pd.Timestamp
    fin: pd.Timestamp
    indices: list
    explication: str
    valeurs: dict = field(default_factory=dict)

    @property
    def libelle(self):
        return LIBELLES[self.type]


@dataclass
class Periode:
    """Intervalle sans positions fiables, borné par deux observations retenues."""

    identifiant: str
    i_avant: int
    i_apres: int
    debut: pd.Timestamp
    fin: pd.Timestamp
    duree_min: float
    cause: str  # "interruption" ou "positions_suspectes" ou "mixte"
    nb_points_ecartes: int
    fiabilite_bornes: str  # "normale" ou "reduite"
    remarque: str = ""


@dataclass
class Analyse:
    points: pd.DataFrame
    alertes: list
    periodes: list
    qualite: dict
    score_anomalie: int


def _preparer(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("timestamp", kind="stable").reset_index(drop=True).copy()
    d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
    x, y = vers_km(d["lat"].to_numpy(), d["lon"].to_numpy())
    d["x"], d["y"] = x, y
    d["t_s"] = (d["timestamp"] - d["timestamp"].iloc[0]).dt.total_seconds()
    return d


def _fusionner(indices: list, ecart_max: int = 2) -> list:
    """Regroupe des indices proches en suites [début, fin]."""
    if not indices:
        return []
    indices = sorted(set(indices))
    groupes = [[indices[0], indices[0]]]
    for i in indices[1:]:
        if i - groupes[-1][1] <= ecart_max:
            groupes[-1][1] = i
        else:
            groupes.append([i, i])
    return groupes


def analyser_trajet(df: pd.DataFrame, params_type: dict, params: dict) -> Analyse:
    pdet = params["detection"]
    d = _preparer(df)
    n = len(d)
    vmax = params_type["vitesse_max_kn"]
    marge = pdet["marge_vitesse"]
    acc_def = pdet["precision_position_defaut_m"]
    acc = d["accuracy_m"].fillna(acc_def).to_numpy() / 1000.0
    suspect = np.zeros(n, dtype=bool)
    alertes: list[Alerte] = []
    compteur = [0]

    def nouvelle(type_, cat, i0, i1, expl, valeurs):
        compteur[0] += 1
        alertes.append(Alerte(
            identifiant=f"A{compteur[0]:02d}", type=type_, categorie=cat,
            debut=d["timestamp"].iloc[i0], fin=d["timestamp"].iloc[i1],
            indices=list(range(i0, i1 + 1)), explication=expl, valeurs=valeurs,
        ))

    if n < 2:
        return Analyse(d.assign(statut="observee"), [], [], {"points": n, "suffisant": False}, 0)

    x, y, t = d["x"].to_numpy(), d["y"].to_numpy(), d["t_s"].to_numpy()
    dx, dy, dt = np.diff(x), np.diff(y), np.diff(t)
    dist = np.hypot(dx, dy)

    # -- Doublons exacts ------------------------------------------------------
    dup = d.duplicated(subset=["timestamp", "lat", "lon"], keep="first").to_numpy()
    if dup.any():
        idx = np.where(dup)[0].tolist()
        for g0, g1 in _fusionner(idx):
            nouvelle("doublon", INTEGRITE, g0, g1,
                     f"{g1 - g0 + 1} enregistrement(s) strictement identique(s) à un précédent.",
                     {"nb": g1 - g0 + 1})

    # -- Identité dédoublée : instants quasi simultanés, lieux incompatibles ---
    fen = pdet["dedoublement_fenetre_s"]
    dmin = pdet["dedoublement_distance_m"] / 1000.0
    paires = []
    for i in range(n):
        j = i + 1
        while j < n and t[j] - t[i] <= fen:
            sep = math.hypot(x[j] - x[i], y[j] - y[i])
            atteignable = vmax * KN_EN_KMH / 3600.0 * max(t[j] - t[i], 1.0) * marge
            if sep > max(dmin, atteignable) and not (dup[i] or dup[j]):
                paires.append((i, j, sep))
            j += 1
    zones_dedoublement = set()
    if paires:
        idx = sorted({k for i, j, _ in paires for k in (i, j)})
        for g0, g1 in _fusionner(idx, ecart_max=3):
            sep_max = max(s for i, j, s in paires if g0 <= i <= g1)
            nouvelle("identite_dedoublee", INTEGRITE, g0, g1,
                     f"Positions séparées de {sep_max:.1f} km à moins de {fen} s d'intervalle : "
                     "le même identifiant semble émis depuis deux endroits.",
                     {"separation_max_km": round(sep_max, 2), "fenetre_s": fen})
            suspect[g0:g1 + 1] = True
            zones_dedoublement.update(range(g0, g1 + 2))

    # -- Sauts de position ----------------------------------------------------
    allocation = acc[:-1] + acc[1:]
    v_kn = np.where(dt > 0, np.maximum(dist - allocation, 0) / np.maximum(dt, 1e-9) * 3600 / KN_EN_KMH, np.inf)
    v_kn = np.where((dt <= 0) & (dist <= allocation), 0.0, v_kn)
    d["v_calc_kn"] = np.concatenate([[np.nan], np.round(np.where(np.isfinite(v_kn), v_kn, np.nan), 2)])
    sauts = [i + 1 for i in range(n - 1)
             if v_kn[i] > vmax * marge and (i + 1) not in zones_dedoublement]
    for k, i in enumerate(sauts):
        nouvelle("saut_position", INTEGRITE, i - 1, i,
                 f"Déplacement de {dist[i - 1]:.2f} km en {dt[i - 1]:.0f} s, soit "
                 f"{v_kn[i - 1]:.0f} nœuds (après marge d'incertitude), au-delà du maximum retenu "
                 f"de {vmax * marge:.0f} nœuds pour ce type de bateau.",
                 {"distance_km": round(float(dist[i - 1]), 3), "duree_s": float(dt[i - 1]),
                  "vitesse_kn": round(float(v_kn[i - 1]), 1), "seuil_kn": round(vmax * marge, 1)})
    # Bloc encadré par deux sauts rapprochés : positions du bloc jugées suspectes
    for a, b in zip(sauts[:-1], sauts[1:]):
        if b - a <= 30:
            suspect[a:b] = True
    for a in sauts:
        suspect[a] = True

    # -- Incohérence vitesse / cap déclarés ------------------------------------
    sog = d["sog_kn"].to_numpy()
    cog = d["cog_deg"].to_numpy()
    if np.isfinite(sog).sum() > 2:
        sog_moy = (sog[:-1] + sog[1:]) / 2
        v_brut = np.where(dt > 0, dist / np.maximum(dt, 1e-9) * 3600 / KN_EN_KMH, np.nan)
        seuil = np.maximum(pdet["ecart_vitesse_min_kn"], pdet["ecart_vitesse_relatif"] * sog_moy)
        ecart_v = np.abs(v_brut - sog_moy) > seuil
        cap_calc = (90 - np.degrees(np.arctan2(dy, dx))) % 360
        cog_moy = cog[:-1]
        diff_cap = np.abs((cap_calc - cog_moy + 180) % 360 - 180)
        ecart_c = (diff_cap > 45) & (sog_moy > 3) & (dist > 0.05)
        incoh = np.nan_to_num(ecart_v | ecart_c, nan=False).astype(bool)
        k_min = int(pdet["ecart_vitesse_points_consecutifs"])
        i = 0
        while i < len(incoh):
            if incoh[i]:
                j = i
                while j + 1 < len(incoh) and incoh[j + 1]:
                    j += 1
                if j - i + 1 >= k_min:
                    rg = slice(i, j + 1)
                    nouvelle("incoherence_vitesse_cap", INTEGRITE, i, j + 1,
                             f"Sur {j - i + 1} intervalles consécutifs, le déplacement calculé "
                             f"(médiane {np.nanmedian(v_brut[rg]):.1f} nœuds) ne correspond pas à la "
                             f"vitesse déclarée (médiane {np.nanmedian(sog_moy[rg]):.1f} nœuds) "
                             f"ou au cap déclaré (écart médian {np.nanmedian(diff_cap[rg]):.0f}°).",
                             {"intervalles": j - i + 1,
                              "vitesse_calculee_kn": round(float(np.nanmedian(v_brut[rg])), 1),
                              "vitesse_declaree_kn": round(float(np.nanmedian(sog_moy[rg])), 1),
                              "ecart_cap_deg": round(float(np.nanmedian(diff_cap[rg])), 0)})
                    suspect[i + 1:j + 1] = True
                i = j + 1
            else:
                i += 1

        # -- Dérive cumulée (estime à l'estime contre positions) ----------------
        vx_decl = sog * KN_EN_KMH / 3600 * np.sin(np.radians(cog))
        vy_decl = sog * KN_EN_KMH / 3600 * np.cos(np.radians(cog))
        w = 10
        seuil_trou = pdet["intervalle_attendu_s"] * pdet["facteur_interruption"]
        derives = []
        for s in range(0, n - w, 2):
            e = s + w
            pas_dt = np.diff(t[s:e + 1])
            ex = np.nansum((vx_decl[s:e] + vx_decl[s + 1:e + 1]) / 2 * pas_dt)
            ey = np.nansum((vy_decl[s:e] + vy_decl[s + 1:e + 1]) / 2 * pas_dt)
            ox, oy = x[e] - x[s], y[e] - y[s]
            ecart = math.hypot(ox - ex, oy - ey)
            seuil_d = 0.3 + 0.15 * math.hypot(ox, oy)
            sans_trou = pas_dt.max() <= seuil_trou
            if ecart > seuil_d and sans_trou:
                derives.append((s, e, ecart))
        if derives:
            idx = sorted({k for s, e, _ in derives for k in range(s, e + 1)})
            deja_couverts = {k for a in alertes if a.categorie == INTEGRITE for k in a.indices}
            for g0, g1 in _fusionner(idx):
                ec = max(e_ for s, e, e_ in derives if g0 <= s <= g1)
                nouveaux = set(range(g0, g1 + 1)) - deja_couverts
                if len(nouveaux) >= 3:
                    nouvelle("derive_cumulee", INTEGRITE, g0, g1,
                             f"Sur des fenêtres de ~{w} points, les positions s'écartent jusqu'à "
                             f"{ec:.2f} km du déplacement attendu d'après la vitesse et le cap déclarés. "
                             "Les points de ces fenêtres sont écartés par prudence (la limite exacte de "
                             "l'altération n'est pas connue).",
                             {"ecart_max_km": round(ec, 2)})
                # Isolation prudente : toute la fenêtre incohérente est écartée de la reconstruction
                suspect[g0:g1 + 1] = True

    # -- Rejeu ------------------------------------------------------------------
    L = int(pdet["rejeu_longueur_fenetre"])
    tol = pdet["rejeu_tolerance_m"] / 1000.0
    if n > 2 * L:
        vecs = np.column_stack([dx, dy])
        mobile = dist > 0.02
        trouves = []
        for j in range(L, len(vecs) - L + 1):
            if not mobile[j:j + L].all():
                continue
            fen_j = vecs[j:j + L]
            for i in range(0, j - L + 1):
                if np.abs(vecs[i:i + L] - fen_j).max() <= tol:
                    trouves.append((i, j))
                    break
        if trouves:
            idx = sorted({k for i, j in trouves for k in range(j, j + L + 1)})
            for g0, g1 in _fusionner(idx):
                src = min(i for i, j in trouves if g0 <= j <= g1)
                nouvelle("rejeu", INTEGRITE, g0, g1,
                         f"La suite de déplacements à partir du point {g0} reproduit, au mètre près, "
                         f"celle observée à partir du point {src} "
                         f"({d['timestamp'].iloc[src]:%H:%M} UTC). Des mesures GNSS indépendantes ne "
                         "se répètent pas ainsi.",
                         {"indice_source": int(src), "longueur": int(g1 - g0)})
                suspect[g0:g1 + 1] = True

    # -- Interruptions (disponibilité) -----------------------------------------
    attendu = pdet["intervalle_attendu_s"]
    seuil_int = attendu * pdet["facteur_interruption"]
    for i in np.where(dt > seuil_int)[0]:
        nouvelle("interruption", DISPONIBILITE, int(i), int(i) + 1,
                 f"Aucune position pendant {dt[i] / 60:.0f} min (intervalle attendu ~{attendu} s). "
                 "Peut provenir d'une perte de réception, d'un problème matériel ou d'une suppression : "
                 "cela ne prouve pas une attaque.",
                 {"duree_min": round(float(dt[i]) / 60, 1)})

    d["statut"] = np.where(suspect, "suspecte", "observee")

    # -- Périodes à reconstruire ------------------------------------------------
    periodes = _periodes(d, suspect, dt, seuil_int)

    # -- Qualité et score d'anomalie (séparés) --------------------------------
    duree = t[-1] - t[0]
    attendus = max(1, int(duree / attendu) + 1)
    qualite = {
        "points": int(n),
        "points_attendus_approx": attendus,
        "completude_pct": round(100 * min(1.0, n / attendus), 1),
        "intervalle_median_s": round(float(np.median(dt)), 1) if len(dt) else None,
        "points_suspects": int(suspect.sum()),
        "part_suspecte_pct": round(100 * suspect.mean(), 1),
        "vitesse_cap_declares": bool(np.isfinite(sog).sum() > 2),
        "duree_trajet_min": round(duree / 60, 1),
        "suffisant": bool(n >= 10),
    }
    score = min(100, sum(POIDS[a.type] for a in alertes if a.categorie == INTEGRITE))
    return Analyse(d, alertes, periodes, qualite, int(score))


def _periodes(d, suspect, dt, seuil_int):
    n = len(d)
    fiable = ~suspect
    idx_fiables = np.where(fiable)[0]
    periodes = []
    if len(idx_fiables) < 2:
        return periodes
    k = 0
    for a, b in zip(idx_fiables[:-1], idx_fiables[1:]):
        ecartes = b - a - 1
        duree = d["t_s"].iloc[b] - d["t_s"].iloc[a]
        if ecartes == 0 and duree <= seuil_int:
            continue
        k += 1
        cause = "positions_suspectes" if ecartes and duree <= seuil_int * 2 else (
            "interruption" if not ecartes else "mixte")
        voisins = list(range(max(0, a - 2), a)) + list(range(b + 1, min(n, b + 3)))
        reduite = bool(suspect[voisins].any()) if voisins else False
        periodes.append(Periode(
            identifiant=f"P{k}", i_avant=int(a), i_apres=int(b),
            debut=d["timestamp"].iloc[a], fin=d["timestamp"].iloc[b],
            duree_min=round(duree / 60, 1), cause=cause, nb_points_ecartes=int(ecartes),
            fiabilite_bornes="reduite" if reduite else "normale",
            remarque=("Des positions suspectes sont proches des bornes : la reconstruction dépend "
                      "d'observations elles-mêmes moins fiables." if reduite else ""),
        ))
    return periodes
