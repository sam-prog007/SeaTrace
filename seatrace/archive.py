"""Archiving of ALL generated paths with their probability, and serialisation (English keys).

Each reconstruction (a "run") is stored locally in data/chemins/<run_id>/ :
  chemins.csv    one row per point of every path: model, path id, family, weight (probability of the
                 path in the model's distribution), exact log-probability (HMM), draws, crosses_zone,
                 step, timestamp, latitude, longitude;
  resume.json    probabilities, intervals, families (PCA), diagnostics, hypotheses, versions,
                 fingerprints of the source file and of the parameters.
Spreadsheet exports are protected against formula injection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, schema
from .geo import vers_latlon

DOSSIER = config.RACINE / "data" / "chemins"
MOTIF_RUN = re.compile(r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$")


def _f(v, nd=4):
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), nd)
    return v


def propre(o):
    """Convertit récursivement en types JSON (numpy -> python, NaN -> None)."""
    if isinstance(o, dict):
        return {str(k): propre(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [propre(v) for v in o]
    if isinstance(o, np.ndarray):
        return propre(o.tolist())
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.strftime("%Y-%m-%dT%H:%M:%SZ")
    return _f(o, 6)


def png_rgba(rgba: np.ndarray) -> bytes:
    """Encodeur PNG minimal (aucune dépendance) ; rgba : (h, w, 4) uint8, ligne 0 = haut."""
    h, w, _ = rgba.shape
    brut = b"".join(b"\x00" + rgba[i].tobytes() for i in range(h))

    def bloc(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + bloc(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(brut, 6)) + bloc(b"IEND", b""))


def occupation_png(occ: np.ndarray, couleur=(110, 60, 160)) -> str:
    m = occ / max(float(occ.max()), 1e-12)
    a = np.where(m < 0.01, 0, np.clip(40 + 215 * np.sqrt(m), 0, 255)).astype(np.uint8)
    rgba = np.zeros(occ.shape + (4,), np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = couleur
    rgba[..., 3] = a
    return "data:image/png;base64," + base64.b64encode(png_rgba(rgba[::-1])).decode()


def bornes_grille_latlon(grille):
    lat0, lon0 = vers_latlon(grille.x0, grille.y0, grille.origine)
    lat1, lon1 = vers_latlon(grille.x0 + grille.nx * grille.taille, grille.y0 + grille.ny * grille.taille,
                             grille.origine)
    return [[float(lat0), float(lon0)], [float(lat1), float(lon1)]]


COULEURS = {"hmm": (110, 60, 160), "appris": (30, 120, 180), "particules": (200, 100, 20),
            "pont": (40, 140, 90), "boosting": (120, 120, 120)}


def nouveau_run_id(contenu: str) -> str:
    h = hashlib.sha256(contenu.encode()).hexdigest()[:8]
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + h


def comparaison_vers_dict(comp, grille, debut_ts, zone_id, contexte: dict) -> dict:
    """Run dictionary (English keys) shared by the API, the exports and the report."""
    origine = grille.origine
    timestamps = [debut_ts + timedelta(seconds=k * comp.dt_s) for k in range(comp.n_pas + 1)]
    models = {}
    ordre = ["hmm", "appris", "particules", "pont", "boosting"]
    for m, r in sorted(comp.resultats.items(), key=lambda kv: ordre.index(kv[0]) if kv[0] in ordre else 99):
        paths = []
        for c in r.chemins:
            lat, lon = vers_latlon(c.xs, c.ys, origine)
            paths.append({"id": c.identifiant, "weight": _f(c.poids, 6), "log_prob": _f(c.log_proba, 3),
                          "draws": c.occurrences, "crosses_zone": c.passe_zone, "family": c.famille,
                          "lat": np.round(lat, 6).tolist(), "lon": np.round(lon, 6).tolist()})
        models[m] = {
            "model": m, "label": r.libelle, "status": "ok" if r.statut == "ok" else "failed",
            "crossing_probability": _f(r.proba_passage),
            "interval": [_f(v) for v in r.intervalle] if r.intervalle else None,
            "mc_error": _f(r.erreur_mc), "reasons": r.raisons, "compute_time_s": _f(r.temps_calcul_s, 3),
            "n_steps": r.n_pas, "dt_s": _f(r.dt_s, 1), "diagnostics": propre(r.diagnostics),
            "hypotheses": propre(r.hypotheses), "n_paths": len(r.chemins), "paths": paths,
            "occupation_png": occupation_png(r.occupation, COULEURS.get(m, (100, 100, 100)))
            if r.occupation is not None else None,
        }
    seg = None
    if comp.segmentation and "families" in comp.segmentation:
        seg = dict(comp.segmentation)
        fams = []
        for f in seg["families"]:
            f = dict(f)
            lat, lon = vers_latlon(np.array(f["mean_path"]["xs"]), np.array(f["mean_path"]["ys"]), origine)
            f["mean_path"] = {"lat": np.round(lat, 6).tolist(), "lon": np.round(lon, 6).tolist()}
            fams.append(f)
        seg["families"] = fams
    elif comp.segmentation:
        seg = comp.segmentation
    syn = comp.synthese
    synthesis = {"reading": syn["lecture"], "code": syn["code"], "min_probability": syn["proba_min"],
                 "max_probability": syn["proba_max"], "failed_models": syn["modeles_en_echec"],
                 "reminder": syn["rappel"], "notes": syn.get("notes", [])}
    return propre({
        "context": contexte, "zone": zone_id, "synthesis": synthesis, "models": models,
        "segmentation": seg, "timestamps": timestamps, "n_steps": comp.n_pas, "dt_s": comp.dt_s,
        "total_time_s": comp.temps_total_s, "grid_bounds": bornes_grille_latlon(grille),
        "common_hypotheses": comp.hypotheses_communes,
    })


def chemins_dataframe(run: dict) -> pd.DataFrame:
    rows = []
    ts = run["timestamps"]
    for m, r in run["models"].items():
        for c in r["paths"]:
            for k, (la, lo) in enumerate(zip(c["lat"], c["lon"])):
                rows.append((run["run_id"], m, c["id"], c.get("family"), c["weight"], c.get("log_prob"),
                             c.get("draws"), c.get("crosses_zone"), k, ts[k], la, lo))
    return pd.DataFrame(rows, columns=["run_id", "model", "path_id", "family", "weight", "log_prob", "draws",
                                       "crosses_zone", "step", "timestamp", "lat", "lon"])


def geojson(run: dict) -> dict:
    feats = []
    for m, r in run["models"].items():
        for c in r["paths"]:
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": [[lo, la] for la, lo in zip(c["lat"], c["lon"])]},
                          "properties": {"model": m, "path_id": c["id"], "family": c.get("family"),
                                         "weight": c["weight"], "log_prob": c.get("log_prob"),
                                         "crosses_zone": c.get("crosses_zone"), "estimated": True}})
    for f in (run.get("segmentation") or {}).get("families", []):
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[lo, la] for la, lo in zip(f["mean_path"]["lat"], f["mean_path"]["lon"])]},
                      "properties": {"family": f["id"], "mean_path": True,
                                     "probability_by_model": f["probability_by_model"], "estimated": True}})
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"run_id": run["run_id"], "warning": "ESTIMATED paths (not observed positions)."}}


def sauver(run: dict, dossier: Path | None = None) -> Path:
    dossier = dossier or DOSSIER
    if not MOTIF_RUN.match(run["run_id"]):
        raise ValueError("Identifiant de run invalide.")
    d = (dossier / run["run_id"]).resolve()
    if dossier.resolve() not in d.parents:
        raise ValueError("Chemin invalide.")
    d.mkdir(parents=True, exist_ok=True)
    df = chemins_dataframe(run)
    (d / "chemins.csv").write_bytes(schema.exporter_csv(df))
    resume = {k: v for k, v in run.items() if k != "models"}
    resume["models"] = {m: {k: v for k, v in r.items() if k not in ("paths", "occupation_png")}
                        for m, r in run["models"].items()}
    (d / "resume.json").write_text(json.dumps(resume, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def charger(run_id: str, dossier: Path | None = None):
    dossier = dossier or DOSSIER
    if not MOTIF_RUN.match(run_id or ""):
        return None
    d = (dossier / run_id).resolve()
    if dossier.resolve() not in d.parents or not (d / "resume.json").exists():
        return None
    return d


def lister(dossier: Path | None = None, n=50):
    dossier = dossier or DOSSIER
    if not dossier.exists():
        return []
    out = []
    for d in sorted(dossier.iterdir(), reverse=True)[:n]:
        if MOTIF_RUN.match(d.name) and (d / "resume.json").exists():
            try:
                r = json.loads((d / "resume.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            out.append({"run_id": d.name, "context": r.get("context"), "zone": r.get("zone"),
                        "probabilities": {m: v.get("crossing_probability") for m, v in r.get("models", {}).items()},
                        "reading": (r.get("synthesis") or {}).get("reading")})
    return out
