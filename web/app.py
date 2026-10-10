"""SeaTrace V2 interface: local Flask application + Leaflet map (English).

Why Flask rather than Streamlit (V1): Streamlit re-runs the whole script on every click, which limits
interaction with the map (selecting a path, drawing a zone, hovering) and mixes computation with display.
Here the computation is exposed by a JSON API (reusable by a client or another tool) and the interface is
an independent HTML/JS page.

Security (local prototype):
- listens on 127.0.0.1 by default, no debug mode;
- request size is bounded, files are validated by the same functions as in V1;
- every request that changes state requires the header "X-SeaTrace: 1" (a third-party page cannot add it
  without CORS permission, which blocks cross-site requests);
- strict CSP headers; the interface never inserts data as HTML;
- identifiers are checked against a pattern; no supplied file name is ever used as a path.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from seatrace import (archive, audit, carte_kattegat as CK, catalogue, comparatif as CP, config, detect, geo,
                      kattegat_test, pipeline,
                      report, schema, sources)
from . import kattegat_i18n

RACINE = config.RACINE
MOTIF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
LOCK = threading.Lock()
KATTEGAT_ZONES = RACINE / "data" / "reference" / "kattegat_zones.geojson"
KATTEGAT_FILES = {"map.html": RACINE / "reports" / "map.html",
                  "map_animation.html": RACINE / "reports" / "map_animation.html"}
CSP_APP = ("default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
           "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
           "frame-src 'self'; frame-ancestors 'none'")
# The Kattegat study page is a standalone Folium report: it loads Leaflet, jQuery and Bootstrap from public
# CDNs and map tiles from a tile server, so it gets its own, wider (but still closed-list) policy.
CSP_KATTEGAT = ("default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net "
                "https://code.jquery.com https://cdnjs.cloudflare.com; "
                "style-src 'unsafe-inline' https://cdn.jsdelivr.net https://netdna.bootstrapcdn.com "
                "https://cdnjs.cloudflare.com; img-src data: https:; font-src https://cdn.jsdelivr.net "
                "https://netdna.bootstrapcdn.com https://cdnjs.cloudflare.com; connect-src https:; "
                "frame-ancestors 'self'")


class DatasetState:
    def __init__(self, jeu: pipeline.Jeu, name: str):
        self.jeu = jeu
        self.name = name
        self.layers = {}
        self.externals = pd.DataFrame()
        self.origin = pipeline.origine_pour(jeu.positions)
        self.detection_step_s = None  # set for the Kattegat fleet: a gap is > 30 min, as on the map
        self.known_gaps = None  # Kattegat fleet: signal gaps listed by the map ({mmsi: [...]}) and source kind


def create_app(params=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
    app.json.sort_keys = False
    state = {"params": params or config.charger(), "datasets": {}, "runs": {}}
    journal = audit.Journal(pipeline.CHEMIN_JOURNAL)

    def P():
        return state["params"]

    # ------------------------------------------------------------------ helpers
    def require_header():
        if request.headers.get("X-SeaTrace") != "1":
            abort(403)

    def valid_id(v):
        if not v or not MOTIF_ID.match(v):
            abort(400)
        return v

    def dataset_or_404(did):
        valid_id(did)
        if did == "demo":
            load_demo()
        elif did == "kattegat-test":
            load_kattegat_test()
        elif did == "kattegat-fleet":
            load_kattegat_fleet()
        elif did.startswith("ais-") and did not in state["datasets"]:
            load_local_ais()
        d = state["datasets"].get(did)
        if d is None:
            abort(404)
        return d

    def ll(xs, ys, origin):
        lat, lon = geo.vers_latlon(np.asarray(xs), np.asarray(ys), origin)
        return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(np.atleast_1d(lat), np.atleast_1d(lon))]

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers.setdefault("X-Frame-Options", "DENY")
        if resp.mimetype == "text/html" and "Content-Security-Policy" not in resp.headers:
            resp.headers["Content-Security-Policy"] = CSP_APP
        return resp

    @app.errorhandler(413)
    def too_big(_):
        return jsonify({"error": "File too large."}), 413

    def load_demo():
        if "demo" not in state["datasets"]:
            state["datasets"]["demo"] = DatasetState(pipeline.charger_demo(P()),
                                                     "Demonstration (synthetic, fictitious bay)")
        return state["datasets"]["demo"]

    def load_kattegat_test():
        """Team's synthetic test boats A/B/C (reported positions only) with the real Kattegat zones preloaded."""
        if "kattegat-test" in state["datasets"] or not kattegat_test.disponible(RACINE):
            return state["datasets"].get("kattegat-test")
        df, boats, routes = kattegat_test.charger(RACINE)
        p_imp = {**P()["import"], "taille_max_octets": P()["import"]["taille_max_ais_octets"]}
        res = schema.lire_csv_securise(df.to_csv(index=False).encode("utf-8"), "kattegat_test.csv", p_imp)
        jeu = pipeline.Jeu(res.donnees, boats, routes, pd.DataFrame(), res.empreinte, "kattegat_test_synthetique",
                           {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                            "avertissements": res.avertissements, "nom": "kattegat_test"})
        ds = DatasetState(jeu, "Kattegat test boats A/B/C (real tracks, one simulated event each)")
        if KATTEGAT_ZONES.exists():
            ds.layers["zones"] = sources.zones_kattegat(KATTEGAT_ZONES)
            ds.layers["source_zones"] = "Delegated Regulation (EU) 2022/952 (Kattegat study)"
        state["datasets"]["kattegat-test"] = ds
        return ds

    def load_kattegat_fleet():
        """All fishing vessels of the Kattegat map (reports/map.html), with land and regulated zones preloaded:
        the same positions as the batch screening (src/13_reconstruct.py), see seatrace/carte_kattegat.py."""
        if "kattegat-fleet" in state["datasets"] or not CK.disponible(RACINE):
            return state["datasets"].get("kattegat-fleet")
        df, known, vessels, src = CK.charger_positions(RACINE, pas_min_s=120.0)
        pos, boats = CK.jeu_seatrace(df, vessels)
        p_imp = {**P()["import"], "taille_max_octets": 4 * P()["import"]["taille_max_ais_octets"], "lignes_max": 2_000_000}
        res = schema.lire_csv_securise(pos.to_csv(index=False).encode("utf-8"), "kattegat_fleet.csv", p_imp)
        jeu = pipeline.Jeu(res.donnees, boats, {}, pd.DataFrame(), res.empreinte, "kattegat_flotte",
                           {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees, "avertissements": res.avertissements,
                            "nom": "kattegat_fleet", "source": src["label"]})
        ds = DatasetState(jeu, f"Kattegat fishing fleet ({len(boats)} vessels, real AIS, 15–17 Sept 2026)")
        ds.detection_step_s = src["step_s"] if src["kind"] == "published_map" else 120.0
        ds.known_gaps = (known, src["kind"])
        ds.layers["zones"] = sources.zones_kattegat(KATTEGAT_ZONES)
        ds.layers["source_zones"] = "Delegated Regulation (EU) 2022/952 (Kattegat study)"
        land = next((f for f in CK.chemins(RACINE)["land"] if f.exists()), None)
        if land is not None:
            ds.layers["terres"] = sources.lire_geojson(land.read_bytes())
            ds.layers["source_terres"] = "Natural Earth 1:10m land"
        state["datasets"]["kattegat-fleet"] = ds
        return ds

    def screening():
        """Batch results of src/13_reconstruct.py: the full file if present, else the copy embedded in the map."""
        f = CK.chemins(RACINE)["analysis"]
        if f.exists():
            a = json.loads(f.read_text(encoding="utf-8"))
        elif KATTEGAT_FILES["map.html"].exists():
            a = CK.donnees_carte(KATTEGAT_FILES["map.html"]).get("analysis")
        else:
            a = None
        return a

    def load_local_ais():
        folder = RACINE / "data" / "ais"
        if not folder.exists():
            return
        for f in sorted(folder.glob("*.csv")):
            did = "ais-" + re.sub(r"[^A-Za-z0-9_.\-]", "_", f.stem)[:50]
            if did in state["datasets"]:
                continue
            try:
                p_imp = {**P()["import"], "taille_max_octets": P()["import"]["taille_max_ais_octets"]}
                res = schema.lire_csv_securise(f.read_bytes(), f.name, p_imp)
            except schema.ErreurImport:
                continue
            meta_f = f.with_suffix(".json")
            meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
            jeu = pipeline.Jeu(res.donnees, meta.get("bateaux", {}), {}, pd.DataFrame(), res.empreinte,
                               "ais_ouvert", {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                                              "avertissements": res.avertissements, "nom": f.name})
            state["datasets"][did] = DatasetState(jeu, f"Open AIS: {f.stem}")

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html", version=config.versions(P()))

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.get("/kattegat/<name>")
    def kattegat(name):
        """Teammate's Kattegat study map (real AIS fishing tracks vs. regulated zones), served in English."""
        f = KATTEGAT_FILES.get(name)
        if f is None or not f.exists():
            abort(404)
        resp = Response(kattegat_i18n.translate_file(f), mimetype="text/html")
        resp.headers["Content-Security-Policy"] = CSP_KATTEGAT
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    @app.get("/api/state")
    def api_state():
        load_demo()
        load_kattegat_test()
        load_kattegat_fleet()
        load_local_ais()
        return jsonify({
            "versions": config.versions(P()),
            "models": [{"id": m, "label": CP.MD.LIBELLES[m], "makes_paths": CP.MD.PRODUIT_CHEMINS[m]}
                       for m in CP.MODELES],
            "boat_types": {k: v["libelle"] for k, v in P()["types_bateau"].items()},
            "datasets": [{"id": k, "name": v.name, "origin": v.jeu.origine, "trips": int(v.jeu.positions["trip_id"].nunique()),
                          "positions": int(len(v.jeu.positions))} for k, v in state["datasets"].items()],
            "default_current_kmh": P()["modeles"]["courant_kmh"],
            "kattegat": {"map": KATTEGAT_FILES["map.html"].exists(), "zones": KATTEGAT_ZONES.exists(),
                         "fleet": "kattegat-fleet" in state["datasets"]},
        })

    @app.get("/api/sources")
    def api_sources():
        return jsonify(catalogue.SOURCES)

    # ------------------------------------------------------------------ datasets
    @app.post("/api/datasets")
    def api_import():
        require_header()
        f = request.files.get("file")
        if f is None:
            return jsonify({"error": "No file."}), 400
        data = f.read()
        name = schema.nom_affichable(f.filename or "")
        try:
            header = data[:4096].decode("utf-8-sig", errors="replace").splitlines()[0].split(",")
        except IndexError:
            return jsonify({"error": "Empty file."}), 400
        fmt = sources.detecter_format(header) if not data[:2] == b"PK" else "ais_zip"
        try:
            boats, conv_info = {}, None
            if fmt in ("dma", "noaa", "ais_zip"):
                if len(data) > P()["import"]["taille_max_ais_octets"]:
                    raise schema.ErreurImport("AIS file too large for the interface: use scripts/importer_ais.py.")
                df, boats, conv_info = sources.convertir_ais(data, lignes_max=P()["import"]["lignes_max"])
                converted = df.to_csv(index=False).encode("utf-8")
                p_imp = {**P()["import"], "taille_max_octets": P()["import"]["taille_max_ais_octets"]}
                res = schema.lire_csv_securise(converted, name, p_imp)
                origin = "ais_ouvert"
            else:
                res = schema.lire_csv_securise(data, name, P()["import"])
                origin = "import_utilisateur"
            schema.conserver_original(data, res, pipeline.DOSSIER_IMPORTS)
        except (schema.ErreurImport, ValueError) as exc:
            journal.ajouter("import_refuse", {"nom": name, "motif": str(exc)[:120]})
            return jsonify({"error": str(exc)}), 400
        did = "imp-" + res.empreinte[:12]
        jeu = pipeline.Jeu(res.donnees, boats, {}, pd.DataFrame(), res.empreinte, origin,
                           {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                            "avertissements": res.avertissements + (conv_info or {}).get("avertissements", []),
                            "nom": name})
        with LOCK:
            state["datasets"][did] = DatasetState(jeu, f"{'AIS' if origin == 'ais_ouvert' else 'Import'}: {name}")
        journal.ajouter("import", {"empreinte": res.empreinte[:16], "nom": name, "format": fmt,
                                   "lignes": res.lignes_lues, "rejetees": res.lignes_rejetees})
        return jsonify({"id": did, "format": fmt, "rows": res.lignes_lues, "rejected": res.lignes_rejetees,
                        "warnings": jeu.import_info["avertissements"], "conversion": conv_info})

    @app.get("/api/datasets/<did>/trips")
    def api_trips(did):
        d = dataset_or_404(did)
        out = []
        for (b, t), g in d.jeu.positions.groupby(["boat_id", "trip_id"], sort=True):
            meta = d.jeu.bateaux.get(b, {})
            out.append({"boat_id": b, "trip_id": t, "name": meta.get("nom", ""), "boat_type": meta.get("type_bateau"),
                        "description": d.jeu.routes.get(t, {}).get("description", ""), "positions": int(len(g)),
                        "start": g["timestamp"].min().strftime("%Y-%m-%d %H:%M")})
        return jsonify({"trips": out, "warnings": d.jeu.import_info.get("avertissements", []), "origin": d.jeu.origine})

    def prepare(d: DatasetState, trip, boat_type):
        traj = d.jeu.positions[d.jeu.positions["trip_id"] == trip]
        if traj.empty:
            abort(404)
        b = traj["boat_id"].iloc[0]
        meta = d.jeu.bateaux.get(b, {})
        tb = meta.get("type_bateau") or boat_type
        if tb not in P()["types_bateau"]:
            tb = "ais_autre" if d.jeu.origine == "ais_ouvert" else next(iter(P()["types_bateau"]))
        ptype = P()["types_bateau"][tb]
        pdet = (CK.params_detection(P(), d.detection_step_s) if d.detection_step_s
                else pipeline.params_pour_trajet(P(), traj))
        analysis = detect.analyser_trajet(traj, ptype, pdet, d.origin)
        if d.known_gaps is not None:  # same periods as the batch screening (src/13_reconstruct.py)
            analysis.periodes = [p for p in analysis.periodes if CK.garder_periode(p, b, *d.known_gaps)]
        geography = pipeline.geographie_pour(analysis.points, P(), d.layers, d.origin)
        grid = pipeline.grille_pour(geography, analysis.points, P())
        return b, meta, tb, ptype, analysis, geography, grid

    @app.get("/api/datasets/<did>/trips/<trip>")
    def api_trip(did, trip):
        d = dataset_or_404(did)
        valid_id(trip)
        try:
            b, meta, tb, ptype, a, g, grid = prepare(d, trip, request.args.get("type"))
        except Exception as exc:
            if hasattr(exc, "code"):
                raise
            journal.ajouter("erreur_analyse", {"trip_id": trip, "erreur": type(exc).__name__})
            return jsonify({"error": f"Analysis impossible ({type(exc).__name__}): no result is shown."}), 500
        pts = a.points
        route = d.jeu.routes.get(trip, {}).get("route_declaree_xy")
        q = a.qualite
        return jsonify(archive.propre({
            "boat_id": b, "name": meta.get("nom", ""), "boat_type": tb, "type_label": ptype["libelle"],
            "type_known": bool(meta.get("type_bateau")),
            "synthetic": bool(pts["synthetic"].any()) or d.jeu.origine == "demonstration_synthetique",
            "points": [{"lat": float(r.lat), "lon": float(r.lon), "t": r.timestamp.strftime("%H:%M:%S"),
                        "status": r.statut, "sog": r.sog_kn, "v": r.v_calc_kn} for r in pts.itertuples()],
            "alerts": [{"id": x.identifiant, "type": x.type, "label": x.libelle,
                        "category": "integrity" if x.categorie == detect.INTEGRITE else "availability",
                        "start": x.debut.strftime("%H:%M:%S"), "end": x.fin.strftime("%H:%M:%S"),
                        "explanation": x.explication, "values": x.valeurs,
                        "alternatives": pipeline.EXPLICATIONS_ALTERNATIVES.get(x.type, [])} for x in a.alertes],
            "periods": [{"id": p.identifiant, "start": p.debut.strftime("%H:%M:%S"), "end": p.fin.strftime("%H:%M:%S"),
                         "start_iso": p.debut.strftime("%Y-%m-%dT%H:%M:%SZ"), "day": p.debut.strftime("%d/%m"),
                         "duration_min": p.duree_min, "cause": p.cause, "boundary_reliability": p.fiabilite_bornes,
                         "remark": p.remarque, "points_set_aside": p.nb_points_ecartes} for p in a.periodes],
            "quality": {"points": q.get("points"), "completeness_pct": q.get("completude_pct"),
                        "suspicious_points": q.get("points_suspects"), "sufficient": q.get("suffisant")},
            "anomaly_score": a.score_anomalie,
            "planned_route_gap": pipeline.ecart_trajet_prevu(pts, route),
            "declared_route": ll([p[0] for p in route], [p[1] for p in route], g.origine) if route else None,
            "geography": {
                "name": g.nom, "fictitious": g.fictive, "sources": g.sources,
                "land": [{"name": n, "polygon": ll([p[0] for p in poly], [p[1] for p in poly], g.origine)}
                         for n, poly in g.terres],
                "zones": [{"id": z.identifiant, "name": z.nom, "description": z.description, "source": z.source,
                           "polygon": ll([p[0] for p in z.polygone], [p[1] for p in z.polygone], g.origine)}
                          for z in g.zones],
                "no_land": not g.terres,
            },
            "external_observations": int(len(d.externals)) + int(len(d.jeu.rf)),
        }))

    @app.post("/api/datasets/<did>/layers")
    def api_layers(did):
        require_header()
        d = dataset_or_404(did)
        role = request.form.get("role")
        f = request.files.get("file")
        if role not in ("land", "zones", "observations", "current") or f is None:
            return jsonify({"error": "Missing role or file."}), 400
        data = f.read()
        name = schema.nom_affichable(f.filename or "")
        source = schema.nom_affichable(request.form.get("source") or name)
        try:
            if role in ("land", "zones"):
                polys = sources.lire_geojson(data)
                key = "terres" if role == "land" else "zones"
                with LOCK:
                    d.layers[key] = polys
                    d.layers[f"source_{key}"] = source
                info = {"polygons": len(polys)}
            elif role == "observations":
                obs = sources.lire_observations_externes(data)
                with LOCK:
                    d.externals = obs
                info = {"observations": len(obs)}
            else:
                pos = d.jeu.positions
                bbox = (pos["lat"].min() - 0.2, pos["lat"].max() + 0.2, pos["lon"].min() - 0.2, pos["lon"].max() + 0.2)
                u, v, n = sources.courant_moyen(data, bbox)
                info = {"current_kmh": [round(u, 3), round(v, 3)], "values": n}
                with LOCK:
                    d.layers["courant_kmh"] = [u, v]
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        journal.ajouter("couche_importee", {"jeu": did, "role": role, "nom": name, **info})
        return jsonify({"ok": True, **info})

    @app.post("/api/datasets/<did>/kattegat-zones")
    def api_kattegat_zones(did):
        """Load the regulated zones of the Kattegat study (Delegated Regulation (EU) 2022/952)."""
        require_header()
        d = dataset_or_404(did)
        if not KATTEGAT_ZONES.exists():
            return jsonify({"error": "data/reference/kattegat_zones.geojson not found."}), 404
        polys = sources.zones_kattegat(KATTEGAT_ZONES)
        with LOCK:
            d.layers["zones"] = polys
            d.layers["source_zones"] = "Delegated Regulation (EU) 2022/952 (Kattegat study)"
        journal.ajouter("couche_importee", {"jeu": did, "role": "zones_kattegat", "polygons": len(polys)})
        return jsonify({"ok": True, "polygons": len(polys)})

    @app.post("/api/datasets/<did>/zones")
    def api_drawn_zone(did):
        require_header()
        d = dataset_or_404(did)
        body = request.get_json(silent=True) or {}
        pts = body.get("polygon") or []
        try:
            poly = [(float(p[1]), float(p[0])) for p in pts][:200]  # (lon, lat)
        except (TypeError, ValueError, IndexError):
            return jsonify({"error": "Invalid polygon."}), 400
        if len(poly) < 3 or not all(abs(la) <= 90 and abs(lo) <= 180 for lo, la in poly):
            return jsonify({"error": "At least three valid vertices are needed."}), 400
        with LOCK:
            drawn = d.layers.setdefault("zones_dessinees", [])
            ident = f"D{len(drawn) + 1}"
            name = schema.nom_affichable(body.get("name") or f"Drawn zone {ident}")
            drawn.append({"identifiant": ident, "nom": name, "polygone": poly})
        journal.ajouter("zone_dessinee", {"jeu": did, "zone": ident, "sommets": len(poly)})
        return jsonify({"id": ident})

    # ------------------------------------------------------------------ reconstruction
    @app.post("/api/reconstruct")
    def api_reconstruct():
        require_header()
        body = request.get_json(silent=True) or {}
        d = dataset_or_404(body.get("dataset"))
        trip = valid_id(body.get("trip"))
        models = [m for m in (body.get("models") or CP.MODELES) if m in CP.MODELES]
        b, meta, tb, ptype, a, g, grid = prepare(d, trip, body.get("type"))
        period = next((p for p in a.periodes if p.identifiant == body.get("period")), None)
        if period is None:
            return jsonify({"error": "Unknown period."}), 400
        zone_id = body.get("zone") or None
        if zone_id and zone_id not in grid.zones:
            return jsonify({"error": "Unknown zone."}), 400
        start, end = pipeline.observations_bornes(a, period, P())
        if not g.fictive or g.sources:  # real geography: finer grid limited to what the boat can reach in the gap
            grid = pipeline.grille_fenetre(g, start, end, ptype, P())
        externals = []
        if body.get("externals"):
            if d.origin == geo.ORIGINE_DEMO:
                externals = pipeline.observations_rf(d.jeu.rf, trip, period.debut, period.fin)
            externals += pipeline.observations_externes(d.externals, trip, period.debut, period.fin, d.origin)
        params = config.copie(P())
        current = body.get("current")
        if isinstance(current, list) and len(current) == 2:
            try:
                params["modeles"]["courant_kmh"] = [float(np.clip(float(c), -15, 15)) for c in current]
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid current."}), 400
        elif "courant_kmh" in d.layers:
            params["modeles"]["courant_kmh"] = list(d.layers["courant_kmh"])
        pts = a.points
        reliable = pts[pts["statut"] == "observee"]
        acc = reliable["accuracy_m"].fillna(params["detection"]["precision_position_defaut_m"])
        reliable = pd.DataFrame({"x": reliable["x"], "y": reliable["y"], "t_s": reliable["t_s"],
                                 "sigma_km": np.hypot(params["modele"]["incertitude_bornes_m"], acc) / 1000})
        comp = CP.reconstruire_tous(grid, start, end, ptype, params, zone_id, externals, period, reliable, models,
                                    graine=7)
        context = {"dataset": body.get("dataset"), "dataset_name": d.name, "file_sha256": d.jeu.empreinte,
                   "boat_id": b, "trip_id": trip, "boat_type": tb, "period": period.identifiant,
                   "start": period.debut.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "end": period.fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "duration_min": period.duree_min, "cause": period.cause,
                   "boundary_reliability": period.fiabilite_bornes,
                   "zone_name": g.zone(zone_id).nom if zone_id else None,
                   "external_observations": [o.libelle for o in externals],
                   "versions": config.versions(params),
                   "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "synthetic": d.jeu.origine == "demonstration_synthetique" or bool(pts["synthetic"].any())}
        run = archive.comparaison_vers_dict(comp, grid, period.debut, zone_id, context)
        run["run_id"] = archive.nouveau_run_id(json.dumps(context, sort_keys=True))
        archive.sauver(run)
        with LOCK:
            state["runs"][run["run_id"]] = run
            if len(state["runs"]) > 30:
                state["runs"].pop(next(iter(state["runs"])))
        journal.ajouter("reconstruction_multi", {
            "run_id": run["run_id"], "trip_id": trip, "periode": period.identifiant, "zone": zone_id,
            "probas": {m: r["crossing_probability"] for m, r in run["models"].items()},
            "empreinte_parametres": config.empreinte(params)})
        return jsonify(run)

    def run_or_404(run_id):
        if run_id in state["runs"]:
            return state["runs"][run_id]
        if archive.charger(run_id) is None:
            abort(404)
        return None

    @app.get("/api/runs")
    def api_runs():
        return jsonify(archive.lister())

    @app.get("/api/runs/<run_id>/paths.csv")
    def api_paths_csv(run_id):
        run = run_or_404(run_id)
        d = archive.charger(run_id)
        journal.ajouter("export", {"run_id": run_id, "format": "paths_csv"})
        if d is not None and (d / "chemins.csv").exists():
            return send_file(d / "chemins.csv", mimetype="text/csv", as_attachment=True,
                             download_name=f"seatrace_paths_{run_id}.csv")
        return Response(schema.exporter_csv(archive.chemins_dataframe(run)), mimetype="text/csv")

    @app.get("/api/runs/<run_id>/paths.geojson")
    def api_paths_geojson(run_id):
        run = run_or_404(run_id)
        if run is None:
            abort(404)  # GeoJSON is available for runs of the current session (the CSV stays archived)
        journal.ajouter("export", {"run_id": run_id, "format": "geojson"})
        return Response(json.dumps(archive.geojson(run), ensure_ascii=False), mimetype="application/geo+json",
                        headers={"Content-Disposition": f"attachment; filename=seatrace_paths_{run_id}.geojson"})

    @app.get("/api/runs/<run_id>/summary.json")
    def api_summary(run_id):
        run_or_404(run_id)
        d = archive.charger(run_id)
        return send_file(d / "resume.json", mimetype="application/json", as_attachment=True,
                         download_name=f"seatrace_summary_{run_id}.json")

    @app.post("/api/runs/<run_id>/decision")
    def api_decision(run_id):
        require_header()
        run = run_or_404(run_id)
        body = request.get_json(silent=True) or {}
        dec = body.get("decision")
        if dec not in list(report.DECISIONS_EN)[:3]:
            return jsonify({"error": "Invalid decision."}), 400
        decision = {"decision": dec, "analyst": str(body.get("analyst", ""))[:12].strip(),
                    "note": str(body.get("note", ""))[:1000].strip(),
                    "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if run is not None:
            run["human_decision"] = decision
        journal.ajouter("decision_humaine", {"run_id": run_id, "decision": dec, "analyste": decision["analyst"],
                                             "longueur_note": len(decision["note"])})
        return jsonify({"ok": True})

    @app.get("/api/runs/<run_id>/report.html")
    def api_report(run_id):
        run = run_or_404(run_id)
        if run is None:
            abort(404)
        html_txt = report.comparison_report_html(run)
        journal.ajouter("export", {"run_id": run_id, "format": "report_html"})
        resp = Response(html_txt, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        resp.headers["Content-Disposition"] = f"attachment; filename=seatrace_report_{run_id}.html"
        return resp

    # ------------------------------------------------------------------ evaluation, journal
    @app.get("/api/evaluation")
    def api_evaluation():
        out = {}
        for key, name in (("comparison", "comparatif.json"), ("test_boats", "bateaux_test.json")):
            f = RACINE / "eval" / "resultats" / name
            out[key] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return jsonify(out)

    @app.get("/api/kattegat/screening")
    def api_screening():
        """One row per vessel of the Kattegat map: integrity alerts and estimated passage during its gaps."""
        a = screening()
        if not a:
            return jsonify({"available": False})
        fleet = load_kattegat_fleet()
        names = {b: m.get("nom", "") for b, m in (fleet.jeu.bateaux.items() if fleet else [])}
        rows = []
        for mmsi, v in a["vessels"].items():
            gaps = v["gaps"]
            ok = [g for g in gaps if g.get("status") == "ok"]
            best = max(ok, key=lambda g: (g.get("pz") or 0, g.get("pb") or 0), default=None)
            top = next((z for z in (best or {}).get("zones", []) if z["k"] == "zone"), None)
            rows.append({
                "mmsi": mmsi, "name": names.get(mmsi, ""), "level": v["lvl"], "max_p_zone": v["pz"], "max_p_buffer": v["pb"],
                "alerts": sorted({x["type"] for x in v["alerts"]}),
                "alert_labels": sorted({detect.LIBELLES.get(x["type"], x["type"]) for x in v["alerts"]}),
                "gaps": len(gaps), "reconstructed": len(ok),
                "not_reconstructed": sum(g.get("status") == "not_reconstructed" for g in gaps),
                "failed": sum(g.get("status") == "failed" for g in gaps),
                "at_berth": sum(g.get("status") == "at_berth" for g in gaps),
                "low_coverage": any(g.get("low_cov") for g in gaps),
                "unexplained_silence": any(g.get("unexplained") for g in gaps),
                "best": None if best is None else {"start": best["start"], "minutes": best["min"], "cause": best["cause"],
                                                    "p_zone": best.get("pz"), "p_buffer": best.get("pb"),
                                                    "zone": top["id"] if top else None, "zone_name": top["n"] if top else None},
            })
        rows.sort(key=lambda r: (-r["level"], -(r["max_p_zone"] or 0), -(r["max_p_buffer"] or 0), r["mmsi"]))
        n = len(fleet.jeu.bateaux) if fleet else None
        src = a.get("source")
        return jsonify({"available": True, "computed": a.get("computed"),
                        "source": src.get("kind") if isinstance(src, dict) else src,
                        "draws": a.get("draws"), "max_gap_h": a.get("max_gap_h"), "vessels_total": n,
                        "coverage": a.get("coverage"), "rows": rows})

    @app.get("/api/journal")
    def api_journal():
        ok, _, msg = journal.verifier()
        return jsonify({"intact": ok, "message": msg, "entries": journal.entrees()[-50:][::-1]})

    return app


creer_app = create_app  # alias kept for older callers
