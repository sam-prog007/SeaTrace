"""Étape 5 : algorithme SeaTrace appliqué à tous les navires de la carte du Kattegat.

Pour chaque navire de reports/map.html, sans traitement particulier pour aucun :
  - contrôles d'intégrité de seatrace/detect.py (sauts de position, rejeu, identité dédoublée...) ;
  - pour chaque période sans positions fiables (trou de plus de 30 min, ou positions écartées
    par un saut), reconstruction par le modèle de Markov caché sur grille de seatrace/reconstruct.py :
    carte de présence, contour à 90 %, trajets tirés, probabilité de passage dans une zone
    restreinte et dans un tampon de 4 milles, indicateur de faible couverture ;
  - au-delà de duree_max_interruption_h (6 h) : « non reconstruit », sans extrapolation.

Grille : fenêtre autour de chaque trou (ce que le navire peut atteindre à vitesse maximale),
mer = hors du trait de côte Natural Earth, masques des zones restreintes et des tampons.

Positions : voir seatrace/carte_kattegat.py (pipeline complet si data/processed existe, sinon
positions publiées dans reports/map_animation.html et bornes exactes des trous de reports/map.html).

Interprétation : « sous ces hypothèses, probabilité estimée que le trajet ait traversé la zone ».
Ce n'est jamais un constat d'infraction. Aucune statistique de flotte n'est calculée ici :
elles sont dans eval/evaluer_carte_kattegat.py (qui applique la liste d'exclusion).

Sortie : data/processed/reconstructions_2026-09-15_2026-09-17.json (lue par src/08_map.py et par
l'interface Flask).

Courant et vent : si src/13_seatrace_export.py --context-only a été lancé, chaque trou reçoit le courant et le vent
moyens sur sa fenêtre et sa durée (± 30 min), mêmes sources que la couche de contexte de la carte (réelles si
téléchargées, sinon simulées, et étiquetées comme telles) ; dérive du modèle = courant + derive_vent x vent
(config/parametres.json). --no-context retrouve le calcul sans courant ni vent.

Usage : python src/13_reconstruct.py [--draws 300] [--draws-favorites 2000] [--only MMSI ...] [--no-context]
"""
import argparse
import json
import math
import pathlib
import sys
import time

import contourpy
import numpy as np
import pandas as pd
import shapely

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from seatrace import carte_kattegat as K  # noqa: E402
from seatrace import config, detect, geo  # noqa: E402
from seatrace import reconstruct as R  # noqa: E402

# --- Paramètres --------------------------------------------------------------------------------
DRAWS = 300  # trajets tirés par trou (probabilité exacte par la passe avant ; les tirages servent au contrôle)
PATHS_KEPT = 30  # trajets gardés pour la carte
PATH_POINTS = 30  # points au plus par trajet gardé
HEAT_CELLS = 32  # carte de présence grossière : au plus HEAT_CELLS x HEAT_CELLS cases
MASS = 0.90  # contour de la région la plus probable
COVERAGE_RADIUS_KM = 10.0  # faible couverture : autres navires ayant perdu le signal dans ce rayon
COVERAGE_MIN_VESSELS = 2
UNDERWAY_KM = 1.0  # positions avant et après à au moins 1 km : le navire se déplaçait pendant le silence
BERTH_KM = 0.5  # positions avant et après à moins de 500 m, toutes deux sur la terre du trait de côte : à quai
BOAT_TYPE = "ais_peche"
SEED = 7
# -------------------------------------------------------------------------------------------------

INTEGRITY_TYPES = ("saut_position", "identite_dedoublee", "rejeu", "incoherence_vitesse_cap", "derive_cumulee",
                   "doublon")


def iso(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def failure_code(msg: str) -> str:
    m = msg.lower()
    if "beyond the maximum" in m:
        return "too_long"
    if "incompatible" in m or "no possible path" in m or "do not reach" in m:
        return "incompatible"
    if "outside the study area" in m:
        return "outside"
    if "no sea cell" in m:
        return "no_sea"
    return "failed"


def latlon(xs, ys, origin):
    lat, lon = geo.vers_latlon(np.asarray(xs, float), np.asarray(ys, float), origin)
    return np.round(lat, 4), np.round(lon, 4)


def heat(gr: geo.Grille, occ: np.ndarray, origin) -> dict:
    """Coarse presence map: block sums, scaled to 0-99 (99 = most likely cell)."""
    f = max(1, math.ceil(max(gr.nx, gr.ny) / HEAT_CELLS))
    ny, nx = math.ceil(gr.ny / f), math.ceil(gr.nx / f)
    pad = np.zeros((ny * f, nx * f))
    pad[:gr.ny, :gr.nx] = occ
    c = pad.reshape(ny, f, nx, f).sum(axis=(1, 3))
    v = np.round(99 * c / max(c.max(), 1e-300)).astype(int)
    lat0, lon0 = latlon([gr.x0], [gr.y0], origin)
    lat1, lon1 = latlon([gr.x0 + nx * f * gr.taille], [gr.y0 + ny * f * gr.taille], origin)
    return {"b": [float(lat0[0]), float(lon0[0]), float(lat1[0]), float(lon1[0])], "nx": nx, "ny": ny,
            "v": v.ravel().tolist()}


def contour90(gr: geo.Grille, occ: np.ndarray, origin) -> list:
    """Smallest set of cells holding MASS of the expected presence time, as simplified lat/lon rings."""
    flat = np.sort(occ.ravel())[::-1]
    if flat.sum() <= 0:
        return []
    k = int(np.searchsorted(np.cumsum(flat) / flat.sum(), MASS))
    level = flat[min(k, len(flat) - 1)]
    z = np.pad(occ, 1)  # closed contours at the grid edge
    xc = np.concatenate([[gr.xc[0] - gr.taille], gr.xc, [gr.xc[-1] + gr.taille]])
    yc = np.concatenate([[gr.yc[0] - gr.taille], gr.yc, [gr.yc[-1] + gr.taille]])
    gen = contourpy.contour_generator(x=xc, y=yc, z=z, line_type="Separate")
    rings = []
    for line in gen.lines(level * 0.999):
        if len(line) < 4:
            continue
        lat, lon = geo.vers_latlon(line[:, 0], line[:, 1], origin)
        ring = shapely.simplify(shapely.LineString(np.column_stack([lon, lat])), 2e-4)
        cs = np.round(np.asarray(ring.coords), 4)
        if len(cs) >= 4:
            rings.append([[float(b), float(a)] for a, b in cs])  # [lat, lon]
    return rings


def kept_paths(res, origin) -> list:
    if not res.trajets:
        return []
    idx = np.linspace(0, len(res.trajets) - 1, min(PATHS_KEPT, len(res.trajets))).round().astype(int)
    out = []
    for i in idx:
        xs, ys, crosses = res.trajets[i]
        keep = np.unique(np.linspace(0, len(xs) - 1, min(PATH_POINTS, len(xs))).round().astype(int))
        lat, lon = latlon(xs[keep], ys[keep], origin)
        out.append([int(bool(crosses)), [[float(a), float(b)] for a, b in zip(lat, lon)]])
    return out


def zone_shares(res, gr: geo.Grille, g: geo.Geographie) -> list:
    """Share of the drawn paths entering each restricted area or buffer (Monte Carlo, for naming the zones)."""
    if not res.trajets:
        return []
    names = {}
    for z in g.zones:
        names.setdefault(z.identifiant.split("#")[0], (z.nom, z.nature, []))[2].append(z.identifiant)
    cells = [gr.indices_vec(xs, ys) for xs, ys, _ in res.trajets]
    out = []
    for zid, (name, nature, parts) in names.items():
        mask = np.logical_or.reduce([gr.zones[p] for p in parts])
        if not mask.any():
            continue
        share = sum(bool((mask[i, j] & inside).any()) for i, j, inside in cells) / len(cells)
        if share >= 0.01:
            out.append({"id": zid, "k": nature, "n": name, "p": round(share, 3)})
    return sorted(out, key=lambda z: -z["p"])[:4]


def coverage(lat, lon, mmsi, endpoints) -> int:
    """Number of OTHER vessels that lost the signal within COVERAGE_RADIUS_KM of this point."""
    near = {m for m, la, lo in endpoints if m != mmsi and K.distance_km(lat, lon, la, lo) <= COVERAGE_RADIUS_KM}
    return len(near)


def analyse_gap(a, p, mmsi, P, pt, cch, draws, endpoints, seed, ctx=None):
    pts = a.points
    i0, i1 = p.i_avant, p.i_apres
    la0, lo0, la1, lo1 = (float(pts["lat"].iloc[i0]), float(pts["lon"].iloc[i0]),
                          float(pts["lat"].iloc[i1]), float(pts["lon"].iloc[i1]))
    out = {"id": p.identifiant, "start": iso(p.debut), "end": iso(p.fin), "min": round(float(p.duree_min), 1),
           "cause": p.cause, "aside": int(p.nb_points_ecartes), "a": [round(la0, 5), round(lo0, 5)],
           "b": [round(la1, 5), round(lo1, 5)], "km": round(K.distance_km(la0, lo0, la1, lo1), 2),
           "cov": max(coverage(la0, lo0, mmsi, endpoints), coverage(la1, lo1, mmsi, endpoints))}
    out["low_cov"] = out["cov"] >= COVERAGE_MIN_VESSELS
    # Silence d'un navire en route que la réception n'explique pas (aucun autre navire n'a perdu le signal à côté)
    out["unexplained"] = p.cause == "interruption" and out["km"] >= UNDERWAY_KM and not out["low_cov"]
    if p.duree_min / 60 > P["modele"]["duree_max_interruption_h"]:
        out.update(status="not_reconstructed", code="too_long", pz=None, pb=None)
        return out
    origin = (la0, lo0)
    deb, fin = K.observations_locales(a, p, P, origin)
    g = K.geographie(cch, origin, K.bornes_fenetre(deb, fin, pt))
    gr = K.grille(g, deb, fin, pt, P)
    if out["km"] < BERTH_KM and not gr.en_mer(np.array([deb.x, fin.x]), np.array([deb.y, fin.y])).any():
        # Trait de côte au 1:10 000 000 : les quais tombent sur la terre ; navire immobile, rien à reconstruire
        out.update(status="at_berth", code="on_land", pz=None, pb=None)
        return out
    has_z, has_b = bool(gr.zones[K.ZONE_ALL].any()), bool(gr.zones[K.BUFFER_ALL].any())
    # Courant + part du vent moyens sur la fenêtre et le trou (mêmes tables que l'interface ; (0, 0) sans tables)
    dr = K.derive_locale(ctx or {}, gr, origin, p.debut, p.fin, float(P["modeles"].get("derive_vent", 0.0)))
    out.update(cur=[round(c, 3) for c in dr["courant"]], wind=[round(c, 2) for c in dr["vent"]],
               drift=[round(c, 3) for c in dr["derive"]])
    t = time.perf_counter()
    res = R.reconstruire(gr, deb, fin, pt, P["modele"], zone_id=K.ZONE_ALL if has_z else None,
                         n_trajets=draws, graine=seed, courant_kmh=dr["derive"])
    if res.occupation is None:
        out.update(status="failed", code=failure_code(res.raisons[0] if res.raisons else ""), pz=None, pb=None,
                   why=res.raisons[0] if res.raisons else "")
        return out
    pb = 0.0
    if has_b:
        rb = R.reconstruire(gr, deb, fin, pt, P["modele"], zone_id=K.BUFFER_ALL, echantillonner=False,
                            courant_kmh=dr["derive"])
        pb = rb.proba_passage if rb.proba_passage is not None else None
    se = res.controle_mc.get("erreur_type") if has_z else 0.0
    out.update(status="ok", pz=round(res.proba_passage, 4) if has_z else 0.0,
               pb=None if pb is None else round(pb, 4), se=None if se is None else round(se, 4),
               draws=len(res.trajets), cell_km=round(gr.taille, 3), steps=res.n_pas,
               heat=heat(gr, res.occupation, origin), c90=contour90(gr, res.occupation, origin),
               paths=kept_paths(res, origin), zones=zone_shares(res, gr, g), s=round(time.perf_counter() - t, 2))
    return out


def integrity_alerts(a) -> list:
    pts = a.points
    out = []
    for x in a.alertes:
        if x.categorie != detect.INTEGRITE:
            continue
        ix = [i for i in x.indices if 0 <= i < len(pts)]
        out.append({"type": x.type, "start": iso(x.debut), "end": iso(x.fin),
                    "pts": [[round(float(pts["lat"].iloc[i]), 5), round(float(pts["lon"].iloc[i]), 5)] for i in ix],
                    "v": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.valeurs.items()
                          if isinstance(v, (int, float, str))}})
    return out


def level(v: dict) -> int:
    """Uniform review priority from the algorithm's outputs only: 3 integrity alert or likely passage through a
    restricted area during a silence; 2 possible passage, likely buffer passage, a silence under way that
    reception does not explain, or a gap the model cannot reconstruct; 1 other gaps."""
    gaps, al = v["gaps"], v["alerts"]
    if any(x["type"] in ("saut_position", "identite_dedoublee", "rejeu") for x in al) or \
            any((g.get("pz") or 0) >= 0.5 for g in gaps):
        return 3
    if any((g.get("pz") or 0) >= 0.1 or (g.get("pb") or 0) >= 0.5 or g["status"] == "failed" or g.get("unexplained")
           for g in gaps) or al:
        return 2
    return 1 if gaps else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=DRAWS)
    ap.add_argument("--draws-favorites", type=int, default=None,
                    help="tirages pour les favoris de départ de la carte (par défaut : comme les autres)")
    ap.add_argument("--only", nargs="*", default=None, help="limiter à quelques MMSI (essais)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-context", action="store_true",
                    help="sans courant ni vent (comportement d'avant le branchement des tables de contexte)")
    args = ap.parse_args()

    t_all = time.perf_counter()
    P = config.charger()
    pt = P["types_bateau"][BOAT_TYPE]
    df, known, vessels, source = K.charger_positions(ROOT)
    pos, _ = K.jeu_seatrace(df, vessels)
    pos["timestamp"] = pd.to_datetime(pos["timestamp"], utc=True)
    Pd = K.params_detection(P, source["step_s"])
    cch = K.couches(ROOT)
    ctx, ctx_warn = ({}, []) if args.no_context else K.contexte(ROOT)
    for w in ctx_warn:
        print(f"[warn] {w}")
    print(f"[info] courant : {ctx.get('source_courants', 'aucun')} ; vent : {ctx.get('source_vents', 'aucun')} "
          f"(part du vent dans la dérive : {P['modeles'].get('derive_vent', 0.0):.0%})")
    favs = set(K.donnees_carte(K.chemins(ROOT)["map"]).get("favDefault", [])) if K.chemins(ROOT)["map"].exists() else set()
    endpoints = [(m, g[0], g[1]) for m, gs in known.items() for g in gs] + \
                [(m, g[2], g[3]) for m, gs in known.items() for g in gs]

    analyses = {}
    t = time.perf_counter()
    for mmsi, g in pos.groupby("trip_id", sort=True):
        if args.only and mmsi not in args.only:
            continue
        analyses[mmsi] = detect.analyser_trajet(g, pt, Pd)
    t_detect = time.perf_counter() - t
    todo = [(m, p) for m, a in analyses.items() for p in a.periodes if K.garder_periode(p, m, known, source["kind"])]
    long_ = sum(p.duree_min / 60 > P["modele"]["duree_max_interruption_h"] for _, p in todo)
    print(f"[info] {len(analyses)} navires, {len(df):,} positions ({source['kind']}), détection {t_detect:.1f} s")
    print(f"[info] {len(todo)} périodes à reconstruire, dont {long_} de plus de "
          f"{P['modele']['duree_max_interruption_h']:g} h (non reconstruites)")

    vessels_out, t = {}, time.perf_counter()
    for k, (mmsi, p) in enumerate(todo, 1):
        draws = args.draws_favorites if (args.draws_favorites and mmsi in favs) else args.draws
        gap = analyse_gap(analyses[mmsi], p, mmsi, P, pt, cch, draws, endpoints, SEED + k, ctx)
        vessels_out.setdefault(mmsi, {"gaps": [], "alerts": []})["gaps"].append(gap)
        if k % 10 == 0 or k == len(todo):
            el = time.perf_counter() - t
            print(f"[....] {k}/{len(todo)} périodes, {el:.0f} s écoulées, reste ~{el / k * (len(todo) - k):.0f} s")
    t_rec = time.perf_counter() - t
    for mmsi, a in analyses.items():
        al = integrity_alerts(a)
        if al:
            vessels_out.setdefault(mmsi, {"gaps": [], "alerts": []})["alerts"] = al
    for v in vessels_out.values():
        ps = [g["pz"] for g in v["gaps"] if g.get("pz") is not None]
        pb = [g["pb"] for g in v["gaps"] if g.get("pb") is not None]
        v["pz"] = max(ps) if ps else None
        v["pb"] = max(pb) if pb else None
        v["lvl"] = level(v)

    out = {
        "period": list(K.PERIODE), "source": source, "gap_min": K.ECART_MIN,
        "max_gap_h": P["modele"]["duree_max_interruption_h"], "boat_type": pt, "draws": args.draws,
        "draws_favorites": args.draws_favorites, "mass": MASS,
        "coverage": {"radius_km": COVERAGE_RADIUS_KM, "min_vessels": COVERAGE_MIN_VESSELS},
        "grid": {"cell_km_min": P["modele"]["taille_case_km"], "time_step_s": P["modele"]["pas_temps_s"],
                 "headings": P["modele"]["nb_caps"], "land": cch["land_source"]},
        "context": {"current": ctx.get("source_courants"), "wind": ctx.get("source_vents"),
                    "wind_drift_share": P["modeles"].get("derive_vent", 0.0) if ctx.get("vents") is not None else 0.0},
        "params_hash": config.empreinte(P), "computed": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timing_s": {"detection": round(t_detect, 1), "reconstruction": round(t_rec, 1),
                     "total": round(time.perf_counter() - t_all, 1)},
        "n_vessels": len(analyses), "n_periods": len(todo), "vessels": vessels_out,
    }
    dest = pathlib.Path(args.out) if args.out else K.chemins(ROOT)["analysis"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[out ] {dest} ({dest.stat().st_size / 1e6:.2f} Mo) en {out['timing_s']['total']:.0f} s")


if __name__ == "__main__":
    main()
