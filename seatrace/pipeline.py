"""Assemblage des briques : chargement, analyse d'un trajet, préparation d'une reconstruction.

Ce module ne lit JAMAIS les fichiers d'étiquettes (data/etiquettes) : ils ne
servent qu'à l'évaluation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, detect, geo, reconstruct as R, report, schema, sources

RACINE = config.RACINE
DOSSIER_DEMO = RACINE / "data" / "demo"
DOSSIER_IMPORTS = RACINE / "data" / "imports"
DOSSIER_RAPPORTS = RACINE / "data" / "rapports"
CHEMIN_JOURNAL = RACINE / "data" / "journal" / "journal.jsonl"
CHEMIN_METRIQUES = RACINE / "eval" / "resultats" / "metriques.json"

EXPLICATIONS_ALTERNATIVES = {
    "saut_position": ["isolated satellite positioning error (multipath near coasts)",
                      "jamming or spoofing of the satellite signal received by the tracker",
                      "data from two devices mixed under a single identifier",
                      "data altered after collection"],
    "incoherence_vitesse_cap": ["faulty speed or course sensor",
                                "wrong unit conversion during an export",
                                "positions edited without the speed/course fields"],
    "derive_cumulee": ["slow drift of a satellite receiver", "strong current not taken into account",
                       "gradual offset introduced into the positions"],
    "identite_dedoublee": ["two trackers configured with the same identifier",
                           "a tracker reassigned to another boat",
                           "an injected feed spoofing the identifier"],
    "rejeu": ["synchronisation or caching error in the collection software",
              "deliberate re-injection of old data"],
    "doublon": ["double transmission by the tracker or the collection gateway"],
    "interruption": ["area without network or satellite coverage", "battery or power cut",
                     "hardware failure of the tracker", "deliberate deletion of positions"],
    "passage": ["the crossing really took place (with or without an authorisation unknown to us)",
                "zone boundaries or hours of application different from those used",
                "wrong start/end observations", "unsuitable speed or course assumptions"],
}


@dataclass
class Jeu:
    positions: pd.DataFrame
    bateaux: dict
    routes: dict
    rf: pd.DataFrame
    empreinte: str
    origine: str
    import_info: dict


def charger_demo(params) -> Jeu:
    octets = (DOSSIER_DEMO / "positions_demo.csv").read_bytes()
    res = schema.lire_csv_securise(octets, "positions_demo.csv", params["import"])
    bateaux = json.loads((DOSSIER_DEMO / "bateaux_demo.json").read_text(encoding="utf-8"))
    routes = json.loads((DOSSIER_DEMO / "routes_declarees.json").read_text(encoding="utf-8"))
    rf_path = DOSSIER_DEMO / "observations_rf_simulees.csv"
    rf = pd.read_csv(rf_path) if rf_path.exists() else pd.DataFrame()
    if len(rf):
        rf["timestamp"] = pd.to_datetime(rf["timestamp"], utc=True)
    return Jeu(res.donnees, bateaux, routes, rf, res.empreinte, "demonstration_synthetique",
               {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                "avertissements": res.avertissements, "nom": "positions_demo.csv"})


def origine_pour(positions: pd.DataFrame):
    """Origine de la projection locale : la baie de démo si les positions s'y trouvent, sinon le
    centre du jeu de données (projection équirectangulaire locale, erreur < 1 % sur ~100 km)."""
    demo = geo.geographie_demo()
    x, y = geo.vers_km(positions["lat"].to_numpy(), positions["lon"].to_numpy())
    if geo.dans_bornes(demo, x, y):
        return geo.ORIGINE_DEMO
    return (float(np.median(positions["lat"])), float(np.median(positions["lon"])))


def geographie_pour(points: pd.DataFrame, params, couches=None, origine=None, etendue=None):
    """couches : {"terres": [(nom, props, [(lon, lat), ...])], "zones": [...], "source_terres": str,
    "source_zones": str, "zones_dessinees": [Zone en lat/lon]} (imports GeoJSON ou zones dessinées).
    etendue : (xmin, xmax, ymin, ymax) en km, ajoutée à l'emprise des points (zone atteignable d'une
    reconstruction, pour que terres et zones y soient découpées)."""
    demo = geo.geographie_demo()
    couches = couches or {}
    xs, ys = np.asarray(points["x"], dtype=float), np.asarray(points["y"], dtype=float)
    if (origine is None or tuple(origine) == geo.ORIGINE_DEMO) and geo.dans_bornes(demo, xs, ys):
        g = demo
    else:
        if etendue is not None:
            xs, ys = np.concatenate([xs, etendue[:2]]), np.concatenate([ys, etendue[2:]])
        g = geo.geographie_generique(xs, ys, params["modele"]["marge_grille_km"], origine)
    if couches.get("terres"):
        polys = sources.polygones_km(couches["terres"], g.origine, g.bornes)
        g.terres = list(g.terres) + [(nom, pts) for nom, _, pts in polys]
        g.sources.append(f"land: {couches.get('source_terres', 'imported GeoJSON')} ({len(polys)} polygons)")
    if couches.get("zones"):
        polys = sources.polygones_km(couches["zones"], g.origine, g.bornes)
        g.zones = list(g.zones) + sources.zones_depuis_geojson(polys, couches.get("source_zones", "imported GeoJSON"))
        g.sources.append(f"zones: {couches.get('source_zones', 'imported GeoJSON')} ({len(polys)} zones)")
    for z in couches.get("zones_dessinees", []):
        lon = np.array([p[0] for p in z["polygone"]])
        lat = np.array([p[1] for p in z["polygone"]])
        x, y = geo.vers_km(lat, lon, g.origine)
        g.zones = list(g.zones) + [geo.Zone(z["identifiant"], z["nom"], "zone_dessinee",
                                            "Zone drawn by the analyst: no regulatory value.",
                                            list(zip(x.tolist(), y.tolist())), fictive=False,
                                            source="drawn by the analyst")]
    return g


def grille_pour(geographie, points, params, bornes=None):
    """bornes : emprise de la grille (km), par défaut celle de la géographie ; ignorée pour la baie de
    démonstration, dont la grille reste celle de l'évaluation."""
    taille = params["modele"]["taille_case_km"]
    if geographie.fictive and not geographie.sources:
        return geo.construire_grille(geographie, taille)
    # Géographie inconnue : grille limitée, taille de case adaptée (au plus ~150 x 150 cases)
    xmin, xmax, ymin, ymax = bornes or geographie.bornes
    taille = max(taille, (xmax - xmin) / 150, (ymax - ymin) / 150)
    return geo.construire_grille(geographie, taille, bornes=(xmin, xmax, ymin, ymax))


def bornes_reconstruction(debut, fin, params_type, params, externes=()):
    """Emprise (km) d'une reconstruction : tout ce que le bateau peut atteindre entre les deux bornes.

    Un point atteignable est à moins de la moitié de la portée du milieu des bornes (inégalité
    triangulaire) ; portée = vitesse maximale du type x durée x 1,25 (au-delà du +20 % de la
    sensibilité), plus la marge de grille. Sans cette emprise, la grille couvre tout le trajet :
    sur un chalutier suivi 3 jours (~90 km), les cases passent de 0,2 km à 0,6 km."""
    duree_h = max(fin.t_s - debut.t_s, 0.0) / 3600
    demi = params_type["vitesse_max_kn"] * geo.KN_EN_KMH * duree_h * 1.25 / 2 + params["modele"]["marge_grille_km"]
    cx, cy = (debut.x + fin.x) / 2, (debut.y + fin.y) / 2
    marge = params["modele"]["marge_grille_km"]
    xs = [cx - demi, cx + demi] + [v for o in externes for v in (o.x - marge, o.x + marge)]
    ys = [cy - demi, cy + demi] + [v for o in externes for v in (o.y - marge, o.y + marge)]
    return (float(min(xs)), float(max(xs)), float(min(ys)), float(max(ys)))


def courant_du_jeu(table, bornes_km, origine, debut_ts, fin_ts):
    """Courant moyen (km/h, est et nord) de la table du jeu sur l'emprise et la période d'une
    reconstruction, ou None s'il n'y a aucune valeur. La période est élargie de 30 min de chaque côté
    pour contenir au moins un pas horaire ; l'emprise est élargie une fois de 10 km si elle est vide
    (près des côtes, la grille de courant n'a pas de valeur à terre)."""
    xmin, xmax, ymin, ymax = bornes_km
    debut, fin = debut_ts - pd.Timedelta(minutes=30), fin_ts + pd.Timedelta(minutes=30)
    for elargi in (0.0, 10.0):
        lat, lon = geo.vers_latlon(np.array([xmin - elargi, xmax + elargi]), np.array([ymin - elargi, ymax + elargi]),
                                   origine)
        r = sources.courant_local(table, (float(lat.min()), float(lat.max()), float(lon.min()), float(lon.max())),
                                  debut, fin)
        if r is not None:
            return r
    return None


def ecart_trajet_prevu(points: pd.DataFrame, route_xy) -> dict | None:
    if not route_xy or len(route_xy) < 2:
        return None
    fiables = points[points["statut"] == "observee"]
    if fiables.empty:
        return None
    d = geo.distance_a_polyligne(fiables["x"], fiables["y"], route_xy)
    return {"ecart_max_km": round(float(d.max()), 2), "ecart_moyen_km": round(float(d.mean()), 2),
            "part_points_a_plus_de_1km_pct": round(100 * float((d > 1.0).mean()), 1)}


def params_pour_trajet(params, traj: pd.DataFrame):
    """Detection thresholds scaled to the real sampling interval of a trip.

    The defaults assume one position per minute; AIS data are sampled every 10-30 s and have natural gaps
    of a few minutes, so the expected interval is the trip's median interval and a reception gap needs at
    least ten minutes. Trips close to the nominal interval keep the parameters untouched."""
    dt = pd.to_datetime(traj["timestamp"], utc=True).diff().dt.total_seconds().median()
    attendu = params["detection"]["intervalle_attendu_s"]
    if not np.isfinite(dt) or dt <= 0 or 0.8 * attendu <= dt <= 1.25 * attendu:
        return params
    p = config.copie(params)
    p["detection"]["intervalle_attendu_s"] = int(round(float(np.clip(dt, 1, 3600))))
    p["detection"]["facteur_interruption"] = float(np.clip(max(5.0, 600.0 / dt), 1.5, 100.0))
    return p


def grille_fenetre(geographie, debut, fin, params_type, params):
    """Grid limited to the area a boat can reach during a gap (finer cells than a grid over the whole trip).

    A path between two observations cannot stray further than half of the range at maximum speed from the
    straight line, hence the margin. Only for real geographies; the fictitious demonstration bay keeps its grid."""
    duree_h = max(fin.t_s - debut.t_s, 1.0) / 3600.0
    portee = params_type["vitesse_max_kn"] * geo.KN_EN_KMH * duree_h
    marge = portee / 2.0 + 2.0
    xmin, xmax = min(debut.x, fin.x) - marge, max(debut.x, fin.x) + marge
    ymin, ymax = min(debut.y, fin.y) - marge, max(debut.y, fin.y) + marge
    taille = params["modele"]["taille_case_km"]
    taille = max(taille, (xmax - xmin) / 150, (ymax - ymin) / 150)
    return geo.construire_grille(geographie, taille, bornes=(xmin, xmax, ymin, ymax))


def observations_bornes(analyse: detect.Analyse, periode: detect.Periode, params):
    pts = analyse.points
    pm = params["modele"]
    acc = params["detection"]["precision_position_defaut_m"]

    def obs(i, t0):
        a = pts["accuracy_m"].iloc[i]
        a = acc if not np.isfinite(a) else a
        return R.Observation(
            x=float(pts["x"].iloc[i]), y=float(pts["y"].iloc[i]),
            sigma_km=math.hypot(pm["incertitude_bornes_m"], a) / 1000.0,
            t_s=float(pts["t_s"].iloc[i] - t0), source=str(pts["source"].iloc[i]),
            cap_deg=float(pts["cog_deg"].iloc[i]) if np.isfinite(pts["cog_deg"].iloc[i]) else None,
            vitesse_kn=float(pts["sog_kn"].iloc[i]) if np.isfinite(pts["sog_kn"].iloc[i]) else None,
        )

    t0 = float(pts["t_s"].iloc[periode.i_avant])
    return obs(periode.i_avant, t0), obs(periode.i_apres, t0)


def observations_rf(rf: pd.DataFrame, trip_id: str, debut_ts, fin_ts):
    if rf is None or rf.empty:
        return []
    sel = rf[(rf["trip_id"] == trip_id) & (rf["timestamp"] > debut_ts) & (rf["timestamp"] < fin_ts)]
    out = []
    for _, o in sel.iterrows():
        x, y = geo.vers_km(o["lat"], o["lon"])
        out.append(R.Observation(
            x=float(x), y=float(y), sigma_km=float(o["precision_m"]) / 1000.0,
            t_s=float((o["timestamp"] - debut_ts).total_seconds()),
            p_attribution=float(o["proba_attribution"]), source="rf_simulee", statut="simulee",
            libelle=f"{o['obs_id']} (simulated RF observation)",
        ))
    return out


def observations_externes(obs: pd.DataFrame, trip_id: str, debut_ts, fin_ts, origine=None):
    """Observations externes génériques (Sentinel-1/2, GFW SAR, RF…) dans l'intervalle.
    Les lignes avec un trip_id renseigné ne valent que pour ce trajet ; sans trip_id, elles sont
    candidates pour tout trajet (l'attribution reste probabiliste, jamais automatique)."""
    if obs is None or obs.empty:
        return []
    sel = obs[(obs["timestamp"] > debut_ts) & (obs["timestamp"] < fin_ts)
              & ((obs["trip_id"] == "") | (obs["trip_id"] == trip_id))]
    out = []
    for _, o in sel.iterrows():
        x, y = geo.vers_km(o["lat"], o["lon"], origine)
        out.append(R.Observation(
            x=float(x), y=float(y), sigma_km=float(o["precision_m"]) / 1000.0,
            t_s=float((o["timestamp"] - debut_ts).total_seconds()), p_attribution=float(o["proba_attribution"]),
            source=str(o["source"]), statut="externe",
            libelle=f"{o['obs_id']} ({o['source']})"))
    return out


def metriques_evaluation():
    if CHEMIN_METRIQUES.exists():
        return json.loads(CHEMIN_METRIQUES.read_text(encoding="utf-8"))
    return None


def contexte_rapport(jeu, dossier, bateau, meta, type_b, params, trip, analyse, ecart, periode, zone_id,
                     resultat, externes, decision, geographie):
    pts = analyse.points
    rec = None
    if periode is not None and resultat is not None:
        z = geographie.zone(zone_id) if zone_id else None
        rec = {
            "periode": {"identifiant": periode.identifiant, "debut": f"{periode.debut:%Y-%m-%d %H:%M:%S} UTC",
                        "fin": f"{periode.fin:%Y-%m-%d %H:%M:%S} UTC", "duree_min": periode.duree_min,
                        "cause": periode.cause, "fiabilite_bornes": periode.fiabilite_bornes},
            "zone": {"identifiant": zone_id, "nom": z.nom if z else "—", "description": z.description if z else "—"},
            "proba_passage": resultat.proba_passage, "statut": resultat.statut, "raisons": resultat.raisons,
            "lecture": resultat.lecture,
            "controle_mc": resultat.controle_mc, "hypotheses": resultat.hypotheses,
            "sensibilite": [{"variation": l["variation"],
                             "proba_passage": "impossible" if l["proba_passage"] is None else f"{100 * l['proba_passage']:.0f} %"}
                            for l in resultat.sensibilite],
            "alternatives": EXPLICATIONS_ALTERNATIVES["passage"],
            "observations_externes": ", ".join(o.libelle for o in externes) or "aucune",
        }
    return {
        "dossier": dossier,
        "bateau": {"boat_id": bateau, "nom": (meta or {}).get("nom", "—"),
                   "type": params["types_bateau"][type_b]["libelle"]},
        "trip_id": trip,
        "periode_donnees": f"{pts['timestamp'].iloc[0]:%Y-%m-%d %H:%M} → {pts['timestamp'].iloc[-1]:%H:%M} UTC",
        "sources": ", ".join(sorted(pts["source"].unique())) + (" + simulated RF observations" if externes else ""),
        "empreinte_fichier": jeu.empreinte,
        "mention_synthetique": ("boats, trips, geography and zone are fictitious; manipulations simulated in the lab."
                                if bool(pts["synthetic"].any()) or jeu.origine == "demonstration_synthetique" else ""),
        "qualite": analyse.qualite,
        "score_anomalie": analyse.score_anomalie,
        "anomalies": [{"identifiant": a.identifiant, "libelle": a.libelle, "categorie": a.categorie,
                       "debut": f"{a.debut:%H:%M:%S}", "fin": f"{a.fin:%H:%M:%S}", "explication": a.explication,
                       "valeurs": a.valeurs, "alternatives": EXPLICATIONS_ALTERNATIVES.get(a.type, [])}
                      for a in analyse.alertes],
        "ecart_trajet_prevu": ecart,
        "reconstruction": rec,
        "versions": config.versions(params),
        "decision_humaine": decision,
        "limites": report.LIMITES_GENERALES,
    }
