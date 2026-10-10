"""Evaluation on the team's synthetic test boats A, B and C of the Kattegat study.

    python eval/evaluer_bateaux_test.py            # about 1 minute -> eval/resultats/bateaux_test.json

THIS SCRIPT IS THE ONLY PLACE THAT OPENS THE ANSWER KEY of these boats (the team's rule: the detector and the
site never read it; a test checks that nothing under src/, seatrace/, scripts/ or web/ does). The detector and the
models only receive the reported positions, exactly as in the web interface; the answer is read afterwards, to
score them.

What is measured for each boat:
  A  silence inside a restricted zone : the gap is detected? how long did the boat really stay in the zone,
     and how long do the models expect it stayed (time in zone), plus the spatial error of their paths;
  B  position jump (10 km shift for 60 min) : are both jumps alerted, which share of the shifted positions is
     set aside, and do the models refuse to bridge the incompatible observations?
  C  innocent gap far from any zone : do all models say the zone was not crossed?
Also reported: alerts that no simulated event explains (false alerts) per boat-day, on real tracks.

Limits: three boats, one event each, built from real tracks but simulated events; a sanity check of the
method on real positions, not a validation.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "eval"))

from seatrace import comparatif as CP, config, detect, geo, kattegat_test, pipeline, sources  # noqa: E402
from comparer_modeles import metriques_spatiales  # noqa: E402

SORTIE = RACINE / "eval" / "resultats"
ZONES = RACINE / "data" / "reference" / "kattegat_zones.geojson"
SYN = RACINE / "data" / "synthetic"
INTEGRITE = detect.INTEGRITE


def distance_zone(x, y, anneau_km):
    """Distance (km) from a point to a polygon given as a ring in km (0 if inside)."""
    from matplotlib.path import Path as MplPath
    ring = np.array(anneau_km + [anneau_km[0]])
    if MplPath(ring).contains_point((x, y)):
        return 0.0
    dx, dy = geo.densifier(ring[:, 0], ring[:, 1], 8)
    return float(np.min(np.hypot(dx - x, dy - y)))


def zone_la_plus_proche(polys, origine, x, y):
    """Nearest FISHING-restricted zone (the compulsory-AIS areas are not fishing restrictions)."""
    best = (math.inf, None)
    for nom, props, pts in polys:
        if "AIS" in str(props.get("zone_id")):
            continue
        lon = np.array([p[0] for p in pts]); lat = np.array([p[1] for p in pts])
        zx, zy = geo.vers_km(lat, lon, origine)
        d = distance_zone(x, y, list(zip(zx.tolist(), zy.tolist())))
        if d < best[0]:
            best = (d, props.get("zone_id"))
    return best[1], best[0]


def evaluer_bateau(x, df_all, params, polys_all, resume, vitesses_test=()):
    trip = f"TEST-{x}"
    traj = df_all[df_all["trip_id"] == trip].copy()
    traj["timestamp"] = pd.to_datetime(traj["timestamp"], utc=True)
    ptype = params["types_bateau"]["ais_peche"]
    origine = pipeline.origine_pour(traj)
    p_det = pipeline.params_pour_trajet(params, traj)
    analyse = detect.analyser_trajet(traj, ptype, p_det, origine)
    pts = analyse.points
    jours = (pts["timestamp"].iloc[-1] - pts["timestamp"].iloc[0]).total_seconds() / 86400
    verite = pd.read_parquet(SYN / f"boat_{x}_truth.parquet")  # <- the answer key (evaluation only)
    verite["timestamp"] = pd.to_datetime(verite["timestamp"], utc=True)
    infos = next(b for b in resume["boats"] if b["scenario"] == x)
    out = {"scenario": x, "trip_id": trip, "simulated_event": infos["type"], "positions": int(len(traj)),
           "days": round(jours, 2)}
    alertes = {}
    for a in analyse.alertes:
        alertes[a.type] = alertes.get(a.type, 0) + 1
    out["detection"] = {"alerts": alertes, "anomaly_score": int(analyse.score_anomalie),
                        "suspicious_points": int(analyse.qualite["points_suspects"]),
                        "periods": [{"id": p.identifiant, "start": f"{p.debut:%Y-%m-%d %H:%M}",
                                     "duration_min": float(p.duree_min), "cause": p.cause} for p in analyse.periodes]}
    t_ev0 = pd.Timestamp(infos["start"]); t_ev1 = pd.Timestamp(infos["end"])
    zid_verite = None

    if x == "B":
        # --- detection of the position jump ---
        manip = verite[verite["is_manipulated"]]
        dans = pts[(pts["timestamp"] >= t_ev0) & (pts["timestamp"] <= t_ev1)]
        marquees = int((dans["statut"] != "observee").sum())
        sauts = [a for a in analyse.alertes if a.type == "saut_position"]
        bords = [a for a in analyse.alertes if a.type in ("saut_position", "identite_dedoublee")]
        proches = lambda t: any(abs((a.debut - t).total_seconds()) <= 300 or abs((a.fin - t).total_seconds()) <= 300 for a in bords)  # noqa: E731
        out["truth"] = {"shifted_positions": int(len(manip)), "shift_km": infos["shift_km"], "minutes": infos["minutes"]}
        out["detection"]["jump_in_alerted"] = bool(proches(t_ev0))
        out["detection"]["jump_out_alerted"] = bool(proches(t_ev1))
        out["detection"]["shifted_positions_set_aside_share"] = round(marquees / max(len(dans), 1), 3)
        periode = max([p for p in analyse.periodes if p.cause == "positions_suspectes"
                       and (p.fin >= t_ev0 - pd.Timedelta(minutes=10)) and (p.debut <= t_ev1 + pd.Timedelta(minutes=10))],
                      key=lambda p: p.duree_min, default=None)
    else:
        periode = max([p for p in analyse.periodes if p.cause == "interruption"], key=lambda p: p.duree_min, default=None)
        if periode is not None:
            out["detection"]["gap_found_min"] = float(periode.duree_min)
            out["truth"] = {"gap_min": infos["minutes"] if "minutes" in infos else None}
    # false alerts: integrity alerts that the simulated event does not explain
    explique = {"A": set(), "B": {"saut_position", "identite_dedoublee", "derive_cumulee"}, "C": set()}[x]
    faux = sum(n for t, n in alertes.items() if t not in explique and t != "interruption")
    out["detection"]["unexplained_integrity_alerts"] = int(faux)
    out["detection"]["unexplained_per_day"] = round(faux / max(jours, 1e-9), 2)
    if periode is None:
        return out

    # --- zone: A = the zone of the scenario; B, C = the zone nearest to the gap (from reported positions only) ---
    deb, fin = pipeline.observations_bornes(analyse, periode, params)
    if x == "A":
        zid_verite = infos["zone_ids"][0]
        dist_zone_km = 0.0
    else:
        zid_verite, dist_zone_km = zone_la_plus_proche(polys_all, origine, 0.5 * (deb.x + fin.x), 0.5 * (deb.y + fin.y))
    out["zone"] = {"id": zid_verite, "distance_to_gap_km": round(dist_zone_km, 1)}
    cible = [p for p in polys_all if p[1].get("zone_id") == zid_verite]
    # geography including the target zone even when it is far from the track (bounds extended to its vertices)
    ext = pts[["x", "y"]].copy()
    zx = [geo.vers_km(p[1], p[0], origine) for _, _, ring in cible for p in ring]
    ext = pd.concat([ext, pd.DataFrame({"x": [float(a[0]) for a in zx], "y": [float(a[1]) for a in zx]})], ignore_index=True)
    g = pipeline.geographie_pour(ext, params, {"zones": cible, "source_zones": "Kattegat study"}, origine)
    zone_id = g.zones[0].identifiant
    grille = pipeline.grille_fenetre(g, deb, fin, ptype, params)
    reliable = pts[pts["statut"] == "observee"]
    fiables = pd.DataFrame({"x": reliable["x"], "y": reliable["y"], "t_s": reliable["t_s"],
                            "sigma_km": np.hypot(params["modele"]["incertitude_bornes_m"], 10.0) / 1000})
    t0 = time.perf_counter()
    comp = CP.reconstruire_tous(grille, deb, fin, ptype, params, zone_id, [], periode, fiables, graine=7)
    out["compute_time_s"] = round(time.perf_counter() - t0, 1)
    # --- the answer: real positions during the gap ---
    t_a, t_b = periode.debut, periode.fin
    reel = verite[(verite["timestamp"] > t_a) & (verite["timestamp"] < t_b)]
    rx, ry = geo.vers_km(reel["lat"].to_numpy(), reel["lon"].to_numpy(), origine)
    dedans = grille.dans_zone(zone_id, rx, ry)
    duree_min = (t_b - t_a).total_seconds() / 60
    part_reelle = float(dedans.mean()) if len(dedans) else 0.0
    vt = (reel["timestamp"] - t_a).dt.total_seconds().to_numpy()
    out["truth"] = {**out.get("truth", {}), "positions_in_gap": int(len(reel)), "gap_min": round(duree_min, 1),
                    "crossed_zone": bool(dedans.any()), "time_in_zone_share": round(part_reelle, 3),
                    "minutes_in_zone": round(part_reelle * duree_min, 1)}
    # for comparison with the team's own summary, which counts time inside ANY restricted area (union of the
    # neighbouring areas), not only the zone examined here
    from matplotlib.path import Path as MplPath
    dans_union = np.zeros(len(rx), dtype=bool)
    for _, props, ring in polys_all:
        if "AIS" in str(props.get("zone_id")):
            continue  # compulsory-AIS areas are not fishing restrictions
        zx2, zy2 = geo.vers_km([q[1] for q in ring], [q[0] for q in ring], origine)
        dans_union |= MplPath(np.column_stack([zx2, zy2])).contains_points(np.column_stack([rx, ry]))
    out["truth"]["time_in_any_restricted_area_share"] = round(float(dans_union.mean()), 3) if len(rx) else 0.0
    out["truth"]["note"] = ("time_in_zone_share is for the zone examined; time_in_any_restricted_area_share counts the "
                            "union of all fishing-restricted areas (the figure used in the team's own summary)")
    modeles = {}
    for m, r in comp.resultats.items():
        d = {"status": "ok" if r.statut == "ok" else "failed", "crossing_probability": r.proba_passage,
             "reason": " ".join(r.raisons)[:240] if r.statut != "ok" else None}
        if r.statut == "ok":
            d["expected_minutes_in_zone"] = r.diagnostics.get("expected_minutes_in_zone")
            if r.chemins and len(vt):
                err, cov = metriques_spatiales(r.chemins, (rx, ry, vt), r.dt_s, r.n_pas)
                d["spatial_error_km"] = None if not np.isfinite(err) else round(err, 2)
                d["coverage_90"] = None if not np.isfinite(cov) else round(cov, 2)
        modeles[m] = d
    out["models"] = modeles
    vals = [d["crossing_probability"] for d in modeles.values() if d["status"] == "ok" and d["crossing_probability"] is not None]
    out["common_reading"] = comp.synthese["lecture"]
    # --- exploratory: how much does the speed assumption matter for the time spent in the zone? ---
    if vitesses_test and x == "A":
        sens = {}
        for v in vitesses_test:
            pt2 = dict(ptype); pt2["vitesse_typique_kn"] = float(v)
            c2 = CP.reconstruire_tous(grille, deb, fin, pt2, params, zone_id, [], periode, fiables,
                                      modeles=["hmm", "particules"], graine=7, sensibilite=False)
            sens[str(v)] = {m: r.diagnostics.get("expected_minutes_in_zone") for m, r in c2.resultats.items() if r.statut == "ok"}
        out["speed_assumption_sensitivity"] = {
            "note": "Exploratory (not used to tune anything): expected minutes in the zone when the typical speed of "
                    "the vessel type is changed. Fishing vessels trawl at roughly 2-5 knots; the default is "
                    f"{ptype['vitesse_typique_kn']} knots.", "by_typical_speed_kn": sens}
    return out


def main():
    params = config.charger()
    SORTIE.mkdir(parents=True, exist_ok=True)
    df, _, _ = kattegat_test.charger(RACINE)
    polys = sources.zones_kattegat(ZONES)
    resume = json.loads((SYN / "summary_truth.json").read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    bateaux = [evaluer_bateau(x, df, params, polys, resume, vitesses_test=(3.0, 5.0)) for x in "ABC"]
    res = {"warning": "Three synthetic test boats built from real trawler tracks with ONE simulated event each; a sanity "
                      "check on real positions, not a validation.",
           "source": "Team's Kattegat pipeline (real AIS 15-17 September 2026, Danish Maritime Authority) and "
                     "src/synthetic.py scenarios A, B, C.",
           "sampling_note": "Positions thinned to at least 30 s apart (class A AIS sends about every 10 s).",
           "boats": bateaux, "total_time_s": round(time.perf_counter() - t0, 1)}
    (SORTIE / "bateaux_test.json").write_text(json.dumps(res, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    for b in bateaux:
        print(b["scenario"], b["simulated_event"], "| detection:", b["detection"].get("alerts"),
              "| truth:", b.get("truth"))
        for m, d in (b.get("models") or {}).items():
            print("    ", m, d["status"], d.get("crossing_probability"), d.get("expected_minutes_in_zone"),
                  d.get("spatial_error_km"), d.get("coverage_90"))


if __name__ == "__main__":
    main()
