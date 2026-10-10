"""Conservation de TOUS les chemins générés, avec leur probabilité, et sérialisation.

Chaque reconstruction (« run ») est enregistrée localement dans data/chemins/<run_id>/ :
  chemins.csv    une ligne par point de chaque chemin : modèle, identifiant, famille, poids
                 (probabilité du chemin dans la loi du modèle), log-probabilité exacte (HMM),
                 passage dans la zone, instant, latitude, longitude ;
  resume.json    probabilités, intervalles, familles (ACP), diagnostics, hypothèses, versions,
                 empreintes du fichier source et des paramètres.
Les exports tableur sont protégés contre l'injection de formules.
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
    origine = grille.origine
    instants = [debut_ts + timedelta(seconds=k * comp.dt_s) for k in range(comp.n_pas + 1)]
    modeles = {}
    ordre = ["hmm", "appris", "particules", "pont", "boosting"]
    for m, r in sorted(comp.resultats.items(), key=lambda kv: ordre.index(kv[0]) if kv[0] in ordre else 99):
        chemins = []
        for c in r.chemins:
            lat, lon = vers_latlon(c.xs, c.ys, origine)
            chemins.append({"id": c.identifiant, "poids": _f(c.poids, 6), "log_proba": _f(c.log_proba, 3),
                            "occurrences": c.occurrences, "passe_zone": c.passe_zone, "famille": c.famille,
                            "lat": np.round(lat, 6).tolist(), "lon": np.round(lon, 6).tolist()})
        modeles[m] = {
            "modele": m, "libelle": r.libelle, "statut": r.statut, "proba_passage": _f(r.proba_passage),
            "intervalle": [_f(v) for v in r.intervalle] if r.intervalle else None,
            "erreur_mc": _f(r.erreur_mc), "raisons": r.raisons, "temps_calcul_s": _f(r.temps_calcul_s, 3),
            "n_pas": r.n_pas, "dt_s": _f(r.dt_s, 1), "diagnostics": propre(r.diagnostics),
            "hypotheses": propre(r.hypotheses), "nombre_chemins": len(r.chemins), "chemins": chemins,
            "occupation_png": occupation_png(r.occupation, COULEURS.get(m, (100, 100, 100)))
            if r.occupation is not None else None,
        }
    seg = None
    if comp.segmentation and "familles" in comp.segmentation:
        seg = dict(comp.segmentation)
        fams = []
        for f in seg["familles"]:
            f = dict(f)
            lat, lon = vers_latlon(np.array(f["chemin_moyen"]["xs"]), np.array(f["chemin_moyen"]["ys"]), origine)
            f["chemin_moyen"] = {"lat": np.round(lat, 6).tolist(), "lon": np.round(lon, 6).tolist()}
            fams.append(f)
        seg["familles"] = fams
    elif comp.segmentation:
        seg = comp.segmentation
    return propre({
        "contexte": contexte, "zone": zone_id, "synthese": comp.synthese, "modeles": modeles,
        "segmentation": seg, "instants": instants, "n_pas": comp.n_pas, "dt_s": comp.dt_s,
        "temps_total_s": comp.temps_total_s, "bornes_grille": bornes_grille_latlon(grille),
        "hypotheses_communes": comp.hypotheses_communes,
    })


def chemins_dataframe(run: dict) -> pd.DataFrame:
    lignes = []
    instants = run["instants"]
    for m, r in run["modeles"].items():
        for c in r["chemins"]:
            for k, (la, lo) in enumerate(zip(c["lat"], c["lon"])):
                lignes.append((run["run_id"], m, c["id"], c.get("famille"), c["poids"], c.get("log_proba"),
                               c.get("occurrences"), c.get("passe_zone"), k, instants[k], la, lo))
    return pd.DataFrame(lignes, columns=["run_id", "modele", "chemin_id", "famille", "poids", "log_proba",
                                         "occurrences", "passe_zone", "etape", "timestamp", "lat", "lon"])


def geojson(run: dict) -> dict:
    feats = []
    for m, r in run["modeles"].items():
        for c in r["chemins"]:
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": [[lo, la] for la, lo in zip(c["lat"], c["lon"])]},
                          "properties": {"modele": m, "chemin_id": c["id"], "famille": c.get("famille"),
                                         "poids": c["poids"], "log_proba": c.get("log_proba"),
                                         "passe_zone": c.get("passe_zone"), "estime": True}})
    for f in (run.get("segmentation") or {}).get("familles", []):
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[lo, la] for la, lo in zip(f["chemin_moyen"]["lat"], f["chemin_moyen"]["lon"])]},
                      "properties": {"famille": f["identifiant"], "chemin_moyen": True,
                                     "probabilite_par_modele": f["probabilite_par_modele"], "estime": True}})
    return {"type": "FeatureCollection", "features": feats,
            "properties": {"run_id": run["run_id"], "avertissement": "Chemins ESTIMÉS (pas des positions observées)."}}


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
    resume = {k: v for k, v in run.items() if k != "modeles"}
    resume["modeles"] = {m: {k: v for k, v in r.items() if k not in ("chemins", "occupation_png")}
                         for m, r in run["modeles"].items()}
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
            out.append({"run_id": d.name, "contexte": r.get("contexte"), "zone": r.get("zone"),
                        "probas": {m: v.get("proba_passage") for m, v in r.get("modeles", {}).items()},
                        "lecture": (r.get("synthese") or {}).get("lecture")})
    return out
