"""Laboratoire de simulation : trajets synthétiques et manipulations simulées.

Ce module ne sert qu'à TESTER et DÉMONTRER SeaTrace. Toutes les données qu'il
produit sont synthétiques (colonne synthetic = true) et toutes les manipulations
sont appliquées à des COPIES locales, dans notre environnement.

Le générateur de trajets est volontairement DIFFÉRENT du modèle de
reconstruction : il suit des points de passage avec inertie de cap, variations
de vitesse, mouillages, boucles et détours, alors que le modèle suppose une
marche aléatoire sur grille. On évite ainsi d'évaluer le modèle sur des données
qui reproduisent exactement ses propres hypothèses.

Les étiquettes (vérité terrain, manipulations injectées) sont écrites dans des
fichiers séparés, qui ne sont JAMAIS lus par la détection ou la reconstruction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .geo import Geographie, KN_EN_KMH, geographie_demo, vers_latlon

PAS_SIMULATION_S = 10.0

NOEUDS = {
    "P": (5.0, 13.4), "A": (6.5, 11.5), "N": (9.5, 9.3), "NE": (13.0, 9.6),
    "E": (13.8, 7.5), "SE": (12.6, 5.0), "S": (9.5, 5.3), "SS": (12.0, 3.4),
    "W": (5.6, 7.5), "WS": (6.5, 4.5), "I": (15.5, 8.5), "IN": (15.5, 10.6),
    "F": (17.5, 6.0), "O": (2.5, 9.0), "SW": (3.0, 3.0), "NN": (12.5, 11.8),
}

FLOTTE = "Loc'Mer (fictitious fleet)"


@dataclass
class Bateau:
    boat_id: str
    type_bateau: str
    nom: str


def aretes_valides(geo: Geographie, dmax=9.0):
    ar = {k: [] for k in NOEUDS}
    for a, (xa, ya) in NOEUDS.items():
        for b, (xb, yb) in NOEUDS.items():
            if a >= b:
                continue
            if math.hypot(xb - xa, yb - ya) > dmax:
                continue
            if geo.segment_traverse_terre(xa, ya, xb, yb):
                continue
            ar[a].append(b)
            ar[b].append(a)
    return ar


def point_mer_aleatoire(geo: Geographie, rng, proche_de=None, rayon=4.0):
    xmin, xmax, ymin, ymax = geo.bornes
    for _ in range(500):
        if proche_de is None:
            x, y = rng.uniform(xmin + 0.5, xmax - 0.5), rng.uniform(ymin + 0.5, ymax - 0.5)
        else:
            x = proche_de[0] + rng.uniform(-rayon, rayon)
            y = proche_de[1] + rng.uniform(-rayon, rayon)
        if xmin < x < xmax and ymin < y < ymax and not geo.est_terre(x, y)[0]:
            return (float(x), float(y))
    raise RuntimeError("Aucun point en mer trouvé")


def simuler_trajet_vrai(points, params_type, rng, geo, depart, mouillages=None, boucles=None):
    """Trajet « vrai » dense (pas de 10 s) passant par une liste de points (x, y).

    mouillages : {indice du point: minutes d'arrêt}
    boucles : {indice du point: minutes de tournage en rond}
    """
    mouillages = mouillages or {}
    boucles = boucles or {}
    vtyp = params_type["vitesse_typique_kn"] * KN_EN_KMH / 3600.0  # km/s
    vmax = params_type["vitesse_max_kn"] * KN_EN_KMH / 3600.0
    dt = PAS_SIMULATION_S
    x, y = points[0]
    t = 0.0
    lignes = [(t, x, y, 0.0, 0.0)]
    facteur_vitesse = rng.uniform(0.75, 1.1)
    bruit_cap = 0.0
    v = vtyp * facteur_vitesse

    def pas(xn, yn, vx, vy):
        nonlocal x, y, t
        x, y, t = xn, yn, t + dt
        lignes.append((t, x, y, vx, vy))

    for k in range(1, len(points)):
        cx, cy = points[k]
        garde = 0
        while math.hypot(cx - x, cy - y) > 0.12 and garde < 20000:
            garde += 1
            bruit_cap += -bruit_cap * dt / 120.0 + math.radians(9) * math.sqrt(2 * dt / 120.0) * rng.normal()
            v += (vtyp * facteur_vitesse - v) * dt / 300.0 + 0.04 * vtyp * rng.normal()
            v = float(np.clip(v, 0.3 * vtyp, 0.95 * vmax))
            cap = math.atan2(cy - y, cx - x) + bruit_cap
            vx, vy = v * math.cos(cap), v * math.sin(cap)
            xn, yn = x + vx * dt, y + vy * dt
            if geo.est_terre(xn, yn)[0]:
                cap = math.atan2(cy - y, cx - x)
                vx, vy = v * math.cos(cap), v * math.sin(cap)
                xn, yn = x + vx * dt, y + vy * dt
            pas(xn, yn, vx, vy)
        minutes = mouillages.get(k, 0)
        for _ in range(int(minutes * 60 / dt)):
            vx, vy = rng.normal(0, 0.00004, 2)
            xn, yn = x + vx * dt, y + vy * dt
            if geo.est_terre(xn, yn)[0]:
                xn, yn, vx, vy = x, y, 0.0, 0.0
            pas(xn, yn, vx, vy)
        minutes = boucles.get(k, 0)
        if minutes:
            r = 0.25
            cx0, cy0 = x - r, y
            w = (0.4 * vtyp) / r
            ang = 0.0
            for _ in range(int(minutes * 60 / dt)):
                ang += w * dt
                xn, yn = cx0 + r * math.cos(ang), cy0 + r * math.sin(ang)
                if geo.est_terre(xn, yn)[0]:
                    break
                pas(xn, yn, (xn - x) / dt, (yn - y) / dt)
    df = pd.DataFrame(lignes, columns=["t_s", "x", "y", "vx", "vy"])
    df["timestamp"] = [depart + timedelta(seconds=float(s)) for s in df["t_s"]]
    return df


def echantillonner_traceur(vrai, bateau: Bateau, trip_id: str, rng, periode_s=60.0,
                           bruit_m=8.0, precision_m=10.0):
    """Positions telles qu'un traceur les enverrait (bruit GNSS, vitesse et cap mesurés)."""
    t_max = vrai["t_s"].iloc[-1]
    instants = np.arange(0.0, t_max, periode_s) + rng.uniform(-3, 3, size=int(math.ceil(t_max / periode_s)))
    instants = np.clip(np.sort(instants), 0, t_max)
    tx = vrai["t_s"].to_numpy()
    xs = np.interp(instants, tx, vrai["x"].to_numpy()) + rng.normal(0, bruit_m / 1000, len(instants))
    ys = np.interp(instants, tx, vrai["y"].to_numpy()) + rng.normal(0, bruit_m / 1000, len(instants))
    vx = np.interp(instants, tx, vrai["vx"].to_numpy())
    vy = np.interp(instants, tx, vrai["vy"].to_numpy())
    vitesse_kn = np.hypot(vx, vy) * 3600 / KN_EN_KMH
    sog = np.clip(vitesse_kn + rng.normal(0, 0.3, len(instants)), 0, None)
    cog = (90 - np.degrees(np.arctan2(vy, vx)) + rng.normal(0, 5, len(instants))) % 360
    lat, lon = vers_latlon(xs, ys)
    depart = vrai["timestamp"].iloc[0]
    return pd.DataFrame({
        "boat_id": bateau.boat_id,
        "trip_id": trip_id,
        "timestamp": [depart + timedelta(seconds=float(s)) for s in instants],
        "lat": np.round(lat, 6),
        "lon": np.round(lon, 6),
        "sog_kn": np.round(sog, 2),
        "cog_deg": np.round(cog, 1),
        "accuracy_m": precision_m,
        "source": "tracker",
        "synthetic": True,
    })


# ---------------------------------------------------------------------------
# Manipulations simulées (sur des copies locales uniquement)
# ---------------------------------------------------------------------------

def _xy(df):
    from .geo import vers_km
    return vers_km(df["lat"].to_numpy(), df["lon"].to_numpy())


def _set_xy(df, idx, xs, ys):
    lat, lon = vers_latlon(xs, ys)
    df.loc[idx, "lat"] = np.round(lat, 6)
    df.loc[idx, "lon"] = np.round(lon, 6)


def manip_saut(df, rng, debut, n, decalage_km):
    """Bloc de positions déplacé d'un coup (téléportation)."""
    d = df.copy()
    idx = d.index[debut:debut + n]
    xs, ys = _xy(d.loc[idx])
    ang = rng.uniform(0, 2 * math.pi)
    _set_xy(d, idx, xs + decalage_km * math.cos(ang), ys + decalage_km * math.sin(ang))
    return d, (d.loc[idx[0], "timestamp"], d.loc[idx[-1], "timestamp"])


def manip_derive(df, rng, debut, n, derive_finale_km):
    """Décalage progressif des positions, sans modifier vitesse et cap déclarés."""
    d = df.copy()
    idx = d.index[debut:debut + n]
    xs, ys = _xy(d.loc[idx])
    ang = rng.uniform(0, 2 * math.pi)
    f = np.linspace(0, 1, len(idx)) * derive_finale_km
    _set_xy(d, idx, xs + f * math.cos(ang), ys + f * math.sin(ang))
    return d, (d.loc[idx[0], "timestamp"], d.loc[idx[-1], "timestamp"])


def manip_remplacement(df, debut, n, depart_xy, arrivee_xy, via=None):
    """Remplace un segment par un faux trajet lisse (positions seulement)."""
    d = df.copy()
    idx = d.index[debut:debut + n]
    pts = [depart_xy] + list(via or []) + [arrivee_xy]
    pts = np.array(pts)
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    cum = np.concatenate([[0], np.cumsum(seg)])
    s = np.linspace(0, cum[-1], len(idx))
    xs = np.interp(s, cum, pts[:, 0])
    ys = np.interp(s, cum, pts[:, 1])
    _set_xy(d, idx, xs, ys)
    return d, (d.loc[idx[0], "timestamp"], d.loc[idx[-1], "timestamp"])


def manip_rejeu(df, debut_source, n, debut_cible):
    """Rejoue un segment antérieur (mêmes déplacements) à des horodatages ultérieurs."""
    d = df.copy()
    src = d.index[debut_source:debut_source + n]
    cib = d.index[debut_cible:debut_cible + n]
    n = min(len(src), len(cib))
    sx, sy = _xy(d.loc[src[:n]])
    cx0, cy0 = _xy(d.loc[[cib[0]]])
    _set_xy(d, cib[:n], sx - sx[0] + cx0[0], sy - sy[0] + cy0[0])
    for col in ("sog_kn", "cog_deg"):
        d.loc[cib[:n], col] = d.loc[src[:n], col].to_numpy()
    return d, (d.loc[cib[0], "timestamp"], d.loc[cib[n - 1], "timestamp"])


def manip_suppression(df, debut, n):
    """Suppression de positions (indiscernable d'une perte de réception)."""
    d = df.copy()
    idx = d.index[debut:debut + n]
    periode = (d.loc[idx[0], "timestamp"], d.loc[idx[-1], "timestamp"])
    return d.drop(idx).reset_index(drop=True), periode


def manip_dedoublement(df, rng, debut, n, decalage_km=4.0):
    """Ajoute un second flux avec le même identifiant, ailleurs, aux mêmes instants."""
    d = df.copy()
    idx = d.index[debut:debut + n]
    clone = d.loc[idx].copy()
    xs, ys = _xy(clone)
    ang = rng.uniform(0, 2 * math.pi)
    lat, lon = vers_latlon(xs + decalage_km * math.cos(ang), ys + decalage_km * math.sin(ang))
    clone["lat"] = np.round(lat, 6)
    clone["lon"] = np.round(lon, 6)
    clone["timestamp"] = clone["timestamp"] + pd.to_timedelta(rng.uniform(1, 8, len(clone)), unit="s")
    periode = (clone["timestamp"].iloc[0], clone["timestamp"].iloc[-1])
    d = pd.concat([d, clone]).sort_values("timestamp", kind="stable").reset_index(drop=True)
    return d, periode


# ---------------------------------------------------------------------------
# Scénarios de démonstration
# ---------------------------------------------------------------------------

def _bateaux_demo():
    return {
        "B101": Bateau("B101", "semi_rigide_location", "Albatross (fictitious)"),
        "B102": Bateau("B102", "semi_rigide_location", "Sandpiper (fictitious)"),
        "B103": Bateau("B103", "vedette_location", "Cormorant (fictitious)"),
        "B104": Bateau("B104", "semi_rigide_location", "Dolphin (fictitious)"),
        "B105": Bateau("B105", "semi_rigide_location", "Eider (fictitious)"),
        "B106": Bateau("B106", "voilier_location", "Gannet (fictitious)"),
        "B107": Bateau("B107", "vedette_location", "Gull (fictitious)"),
    }


def _route(*noms):
    return [NOEUDS[n] for n in noms]


def _masquer(df, debut_min, fin_min):
    t0 = df["timestamp"].iloc[0]
    rel = (df["timestamp"] - t0).dt.total_seconds() / 60
    garde = (rel < debut_min) | (rel > fin_min)
    periode = (t0 + timedelta(minutes=debut_min), t0 + timedelta(minutes=fin_min))
    return df[garde].reset_index(drop=True), periode


def generer_demo(params: dict, graine: int = 7):
    """Produit les jeux de démonstration et leurs étiquettes (fichiers séparés)."""
    geo = geographie_demo()
    rng = np.random.default_rng(graine)
    bateaux = _bateaux_demo()
    types = params["types_bateau"]
    jour = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
    positions, etiquettes, verites, routes = [], [], [], {}
    rf = []

    def ajouter(trip_id, bateau, route_reelle, route_declaree, depart, description,
                mouillages=None, boucles=None):
        vrai = simuler_trajet_vrai(route_reelle, types[bateau.type_bateau], rng, geo, depart,
                                   mouillages, boucles)
        obs = echantillonner_traceur(vrai, bateau, trip_id, rng)
        routes[trip_id] = {
            "boat_id": bateau.boat_id,
            "description": description,
            "route_declaree_xy": [list(p) for p in route_declaree],
        }
        v = vrai[["timestamp", "x", "y"]].copy()
        v["trip_id"] = trip_id
        verites.append(v)
        return obs

    # 1. Trajet normal
    o = ajouter("S1-normal", bateaux["B101"], _route("P", "A", "N", "NE", "IN", "NN", "P"),
                _route("P", "A", "N", "NE", "IN", "NN", "P"), jour,
                "Normal outing: loop round the north, complete data.")
    positions.append(o)

    # 2. Détour légitime (mouillage non prévu)
    o = ajouter("S2-detour", bateaux["B102"], _route("P", "A", "W", "O", "A", "P"),
                _route("P", "A", "N", "A", "P"), jour + timedelta(minutes=20),
                "Detour to the west anchorage with a 30 min stop: deviation from the planned route, "
                "consistent data.", mouillages={2: 30})
    positions.append(o)

    # 3. Interruption de réception sans manipulation connue (loin de la zone)
    o = ajouter("S3-interruption", bateaux["B103"], _route("P", "A", "N", "NE", "NN", "P"),
                _route("P", "A", "N", "NE", "NN", "P"), jour + timedelta(minutes=40),
                "Reception lost for about 30 min north of the island, no known manipulation.")
    duree3 = (o["timestamp"].iloc[-1] - o["timestamp"].iloc[0]).total_seconds() / 60
    o, periode = _masquer(o, 0.3 * duree3, 0.3 * duree3 + min(30.0, 0.4 * duree3))
    etiquettes.append({"trip_id": "S3-interruption", "type": "interruption_reception",
                       "debut": periode[0], "fin": periode[1], "manipulation": False})
    positions.append(o)

    # 4. Positions falsifiées en laboratoire : passage réel dans Z1 remplacé par un faux trajet
    o = ajouter("S4-falsification", bateaux["B104"], _route("P", "A", "N", "NE", "E", "SE", "S", "W", "A", "P"),
                _route("P", "A", "N", "NE", "E", "SS", "WS", "W", "A", "P"), jour + timedelta(hours=1),
                "Lab case: the positions of a real crossing of Z1 were replaced by a fake route "
                "going around the zone. Detection and isolation, then an ambiguous case: without these positions, several "
                "paths remain plausible.")
    from .geo import vers_km
    xs, ys = vers_km(o["lat"].to_numpy(), o["lon"].to_numpy())
    z = geo.zone("Z1").chemin()
    dans = z.contains_points(np.column_stack([xs, ys]))
    i_in = np.where(dans)[0]
    debut = max(0, int(i_in[0]) - 3)
    fin = min(len(o) - 1, int(i_in[-1]) + 3)
    o, periode = manip_remplacement(o, debut, fin - debut + 1, (xs[debut], ys[debut]),
                                    (xs[fin], ys[fin]), via=[(14.4, 3.6), (9.2, 3.6)])
    etiquettes.append({"trip_id": "S4-falsification", "type": "remplacement_positions",
                       "debut": periode[0], "fin": periode[1], "manipulation": True})
    positions.append(o)

    # 5. Segment manquant compatible avec un passage dans Z1 (temps court : route directe)
    o = ajouter("S5-trou-zone", bateaux["B105"], _route("P", "A", "N", "NE", "E", "SE", "S", "W", "A", "P"),
                _route("P", "A", "N", "NE", "E", "SS", "WS", "W", "A", "P"), jour + timedelta(hours=1, minutes=30),
                "Positions missing during the passage south-east of the island.")
    xs, ys = vers_km(o["lat"].to_numpy(), o["lon"].to_numpy())
    dans = z.contains_points(np.column_stack([xs, ys]))
    i_in = np.where(dans)[0]
    t0 = o["timestamp"].iloc[0]
    deb_min = (o["timestamp"].iloc[max(0, int(i_in[0]) - 4)] - t0).total_seconds() / 60
    fin_min = (o["timestamp"].iloc[min(len(o) - 1, int(i_in[-1]) + 4)] - t0).total_seconds() / 60
    o, periode = _masquer(o, deb_min, fin_min)
    etiquettes.append({"trip_id": "S5-trou-zone", "type": "suppression_ou_interruption",
                       "debut": periode[0], "fin": periode[1], "manipulation": None})
    positions.append(o)

    # 6. Cas ambigu : interruption longue, nord ou sud de l'île également plausibles
    o = ajouter("S6-ambigu", bateaux["B106"], _route("P", "A", "W", "S", "SE", "E", "NE", "NN", "P"),
                _route("P", "A", "W", "N", "NE", "NN", "P"), jour + timedelta(minutes=10),
                "Sailboat: 74 min gap between the west and the east of the island; a simulated RF "
                "observation, with uncertain attribution, is available to cross-check sources.")
    xs, ys = vers_km(o["lat"].to_numpy(), o["lon"].to_numpy())
    t0 = o["timestamp"].iloc[0]
    rel = ((o["timestamp"] - t0).dt.total_seconds() / 60).to_numpy()
    i_w = int(np.argmin(np.hypot(xs - NOEUDS["W"][0], ys - NOEUDS["W"][1])))
    i_e = int(np.argmin(np.hypot(xs - NOEUDS["E"][0], ys - NOEUDS["E"][1])))
    o, periode = _masquer(o, rel[i_w] + 2, rel[i_e] + 2)
    etiquettes.append({"trip_id": "S6-ambigu", "type": "interruption_reception",
                       "debut": periode[0], "fin": periode[1], "manipulation": None})
    positions.append(o)
    # Observation RF SIMULÉE pendant l'interruption, au sud de l'île, attribution incertaine
    v6 = verites[-1]
    t_mid = periode[0] + (periode[1] - periode[0]) / 2
    k = int(np.argmin(np.abs((v6["timestamp"] - t_mid).dt.total_seconds().to_numpy())))
    ox = float(v6["x"].iloc[k]) + rng.normal(0, 0.6)
    oy = float(v6["y"].iloc[k]) + rng.normal(0, 0.6)
    lat, lon = vers_latlon(ox, oy)
    rf.append({"obs_id": "RF-SIM-001", "boat_id_candidat": "B106", "trip_id": "S6-ambigu",
               "timestamp": v6["timestamp"].iloc[k], "lat": round(float(lat), 6), "lon": round(float(lon), 6),
               "precision_m": 1000.0, "proba_attribution": 0.7,
               "source": "rf_simulee", "mention": "simulated RF observations"})

    # 7. Laboratoire : rejeu et identité dédoublée
    o = ajouter("S7-rejeu-dedoublement", bateaux["B107"], _route("P", "A", "N", "NE", "NN", "P"),
                _route("P", "A", "N", "NE", "NN", "P"), jour + timedelta(hours=2),
                "Lab copy: an old segment is replayed later, then a second "
                "feed carrying the same identifier appears elsewhere.")
    n = len(o)
    o, per_rejeu = manip_rejeu(o, int(n * 0.15), 12, int(n * 0.55))
    etiquettes.append({"trip_id": "S7-rejeu-dedoublement", "type": "rejeu",
                       "debut": per_rejeu[0], "fin": per_rejeu[1], "manipulation": True})
    o, per_dup = manip_dedoublement(o, rng, int(n * 0.75), 8)
    etiquettes.append({"trip_id": "S7-rejeu-dedoublement", "type": "dedoublement_identite",
                       "debut": per_dup[0], "fin": per_dup[1], "manipulation": True})
    positions.append(o)

    pos = pd.concat(positions, ignore_index=True)
    bateaux_meta = {b.boat_id: {"type_bateau": b.type_bateau, "nom": b.nom, "flotte": FLOTTE}
                    for b in bateaux.values()}
    return {
        "positions": pos,
        "bateaux": bateaux_meta,
        "routes": routes,
        "etiquettes": pd.DataFrame(etiquettes),
        "verite": pd.concat(verites, ignore_index=True),
        "rf": pd.DataFrame(rf),
    }


# ---------------------------------------------------------------------------
# Jeux d'apprentissage / réglage / évaluation (séparés par bateau)
# ---------------------------------------------------------------------------

def generer_flotte(params: dict, n_bateaux=30, trajets_par_bateau=4, graine=2026):
    geo = geographie_demo()
    rng = np.random.default_rng(graine)
    aretes = aretes_valides(geo)
    types = [t for t in params["types_bateau"] if not t.startswith("ais_")]
    poids_types = np.array([0.3, 0.45, 0.25])[: len(types)]
    poids_types = poids_types / poids_types.sum()
    trajets = []
    jour0 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
    for b in range(n_bateaux):
        boat_id = f"F{b + 1:03d}"
        tb = str(rng.choice(types, p=poids_types))
        bateau = Bateau(boat_id, tb, f"Boat {boat_id} (fictitious)")
        if b < int(n_bateaux * 0.4):
            jeu = "apprentissage"
        elif b < int(n_bateaux * 0.6):
            jeu = "reglage"
        else:
            jeu = "evaluation"
        for k in range(trajets_par_bateau):
            exotique = jeu == "evaluation" and rng.random() < 0.35
            noms = ["P"]
            courant = "P"
            for _ in range(int(rng.integers(3, 7))):
                choix = [c for c in aretes[courant] if c != (noms[-2] if len(noms) > 1 else None)]
                courant = str(rng.choice(choix))
                noms.append(courant)
            pts = [NOEUDS[n] for n in noms]
            mouillages, boucles = {}, {}
            if exotique:
                # Comportements hors habitudes : points en mer aléatoires et boucles
                ins = int(rng.integers(1, len(pts)))
                p_alea = point_mer_aleatoire(geo, rng, proche_de=pts[ins - 1], rayon=3.0)
                if not geo.segment_traverse_terre(*pts[ins - 1], *p_alea) and not geo.segment_traverse_terre(*p_alea, *pts[ins]):
                    pts.insert(ins, p_alea)
                boucles[int(rng.integers(1, len(pts)))] = float(rng.uniform(8, 20))
            if rng.random() < 0.5:
                mouillages[int(rng.integers(1, len(pts)))] = float(rng.uniform(10, 40))
            # retour au port par le plus court chemin connu
            retour = _chemin_retour(courant, aretes)
            pts += [NOEUDS[n] for n in retour[1:]]
            depart = jour0 + timedelta(days=b, hours=int(rng.integers(0, 6)), minutes=int(rng.integers(0, 59)))
            vrai = simuler_trajet_vrai(pts, params["types_bateau"][tb], rng, geo, depart, mouillages, boucles)
            trip_id = f"{boat_id}-T{k + 1}"
            obs = echantillonner_traceur(vrai, bateau, trip_id, rng)
            trajets.append({"trip_id": trip_id, "boat_id": boat_id, "type_bateau": tb,
                            "jeu": jeu, "exotique": exotique, "vrai": vrai, "obs": obs})
    return trajets


def _chemin_retour(depart, aretes):
    from collections import deque
    file = deque([[depart]])
    vus = {depart}
    while file:
        ch = file.popleft()
        if ch[-1] == "P":
            return ch
        for v in aretes[ch[-1]]:
            if v not in vus:
                vus.add(v)
                file.append(ch + [v])
    return [depart, "P"]
