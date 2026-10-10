"""Interface V2 de SeaTrace : application Flask locale + carte Leaflet.

Pourquoi Flask plutôt que Streamlit (V1) : Streamlit ré-exécute tout le script à chaque clic,
ce qui limite l'interaction avec la carte (sélection d'un chemin, dessin d'une zone, survol)
et mélange calcul et affichage. Ici, le calcul est exposé par une API JSON (réutilisable par
un client ou un autre outil) et l'interface est une page HTML/JS indépendante.

Sécurité (prototype local) :
- écoute sur 127.0.0.1 par défaut, aucun mode debug ;
- taille des requêtes bornée, fichiers validés par les mêmes fonctions que la V1 ;
- toute requête qui modifie l'état exige l'en-tête « X-SeaTrace: 1 » (une page tierce ne peut
  pas l'ajouter sans autorisation CORS, ce qui bloque les requêtes intersites) ;
- en-têtes CSP stricts ; l'interface n'insère jamais de données comme du HTML ;
- identifiants vérifiés par motif ; aucun nom de fichier fourni n'est utilisé comme chemin.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, jsonify, render_template, request, send_file

from seatrace import (archive, audit, catalogue, comparatif as CP, config, detect, geo, pipeline,
                      report, schema, sources)

RACINE = config.RACINE
MOTIF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")
VERROU = threading.Lock()


class EtatJeu:
    def __init__(self, jeu: pipeline.Jeu, nom: str):
        self.jeu = jeu
        self.nom = nom
        self.couches = {}
        self.externes = pd.DataFrame()
        self.origine = pipeline.origine_pour(jeu.positions)


def creer_app(params=None):
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
    app.json.sort_keys = False
    etat = {"params": params or config.charger(), "jeux": {}, "runs": {}}
    journal = audit.Journal(pipeline.CHEMIN_JOURNAL)

    def P():
        return etat["params"]

    # ------------------------------------------------------------------ utilitaires
    def exiger_entete():
        if request.headers.get("X-SeaTrace") != "1":
            abort(403)

    def id_valide(v):
        if not v or not MOTIF_ID.match(v):
            abort(400)
        return v

    def jeu_ou_404(jid):
        id_valide(jid)
        if jid == "demo":
            charger_demo()
        elif jid.startswith("ais-") and jid not in etat["jeux"]:
            charger_ais_locaux()
        e = etat["jeux"].get(jid)
        if e is None:
            abort(404)
        return e

    def ll(xs, ys, origine):
        lat, lon = geo.vers_latlon(np.asarray(xs), np.asarray(ys), origine)
        return [[round(float(a), 6), round(float(b), 6)] for a, b in zip(np.atleast_1d(lat), np.atleast_1d(lon))]

    @app.after_request
    def entetes(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if resp.mimetype == "text/html" and not request.path.endswith("rapport.html"):
            resp.headers["Content-Security-Policy"] = (
                "default-src 'self'; img-src 'self' data: https://tile.openstreetmap.org; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    @app.errorhandler(413)
    def trop_gros(_):
        return jsonify({"erreur": "Fichier trop volumineux."}), 413

    def charger_demo():
        if "demo" not in etat["jeux"]:
            etat["jeux"]["demo"] = EtatJeu(pipeline.charger_demo(P()), "Démonstration (synthétique, baie fictive)")
        return etat["jeux"]["demo"]

    def charger_ais_locaux():
        dossier = RACINE / "data" / "ais"
        if not dossier.exists():
            return
        for f in sorted(dossier.glob("*.csv")):
            jid = "ais-" + re.sub(r"[^A-Za-z0-9_.\-]", "_", f.stem)[:50]
            if jid in etat["jeux"]:
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
            etat["jeux"][jid] = EtatJeu(jeu, f"AIS ouvert : {f.stem}")

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html", version=config.versions(P()))

    @app.get("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.get("/api/etat")
    def api_etat():
        charger_demo()
        charger_ais_locaux()
        return jsonify({
            "versions": config.versions(P()),
            "modeles": [{"id": m, "libelle": CP.MD.LIBELLES[m], "chemins": CP.MD.PRODUIT_CHEMINS[m]}
                        for m in CP.MODELES],
            "types_bateau": {k: v["libelle"] for k, v in P()["types_bateau"].items()},
            "jeux": [{"id": k, "nom": v.nom, "origine": v.jeu.origine,
                      "trajets": int(v.jeu.positions["trip_id"].nunique()),
                      "positions": int(len(v.jeu.positions))} for k, v in etat["jeux"].items()],
            "courant_kmh": P()["modeles"]["courant_kmh"],
        })

    @app.get("/api/sources")
    def api_sources():
        return jsonify(catalogue.SOURCES)

    # ------------------------------------------------------------------ jeux de données
    @app.post("/api/jeux")
    def api_import():
        exiger_entete()
        f = request.files.get("fichier")
        if f is None:
            return jsonify({"erreur": "Aucun fichier."}), 400
        octets = f.read()
        nom = schema.nom_affichable(f.filename or "")
        try:
            entete = octets[:4096].decode("utf-8-sig", errors="replace").splitlines()[0].split(",")
        except IndexError:
            return jsonify({"erreur": "Fichier vide."}), 400
        fmt = sources.detecter_format(entete) if not octets[:2] == b"PK" else "ais_zip"
        try:
            bateaux, info_conv = {}, None
            if fmt in ("dma", "noaa", "ais_zip"):
                if len(octets) > P()["import"]["taille_max_ais_octets"]:
                    raise schema.ErreurImport("Fichier AIS trop volumineux pour l'interface : utiliser "
                                              "scripts/importer_ais.py.")
                df, bateaux, info_conv = sources.convertir_ais(octets, lignes_max=P()["import"]["lignes_max"])
                octets_conv = df.to_csv(index=False).encode("utf-8")
                p_imp = {**P()["import"], "taille_max_octets": P()["import"]["taille_max_ais_octets"]}
                res = schema.lire_csv_securise(octets_conv, nom, p_imp)
                origine = "ais_ouvert"
            else:
                res = schema.lire_csv_securise(octets, nom, P()["import"])
                origine = "import_utilisateur"
            schema.conserver_original(octets, res, pipeline.DOSSIER_IMPORTS)
        except (schema.ErreurImport, ValueError) as exc:
            journal.ajouter("import_refuse", {"nom": nom, "motif": str(exc)[:120]})
            return jsonify({"erreur": str(exc)}), 400
        jid = "imp-" + res.empreinte[:12]
        jeu = pipeline.Jeu(res.donnees, bateaux, {}, pd.DataFrame(), res.empreinte, origine,
                           {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                            "avertissements": res.avertissements + (info_conv or {}).get("avertissements", []),
                            "nom": nom})
        with VERROU:
            etat["jeux"][jid] = EtatJeu(jeu, f"{'AIS' if origine == 'ais_ouvert' else 'Import'} : {nom}")
        journal.ajouter("import", {"empreinte": res.empreinte[:16], "nom": nom, "format": fmt,
                                   "lignes": res.lignes_lues, "rejetees": res.lignes_rejetees})
        return jsonify({"id": jid, "format": fmt, "lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                        "avertissements": jeu.import_info["avertissements"], "conversion": info_conv})

    @app.get("/api/jeux/<jid>/trajets")
    def api_trajets(jid):
        e = jeu_ou_404(jid)
        df = e.jeu.positions
        out = []
        for (b, t), g in df.groupby(["boat_id", "trip_id"], sort=True):
            meta = e.jeu.bateaux.get(b, {})
            out.append({"boat_id": b, "trip_id": t, "nom": meta.get("nom", ""), "type_bateau": meta.get("type_bateau"),
                        "description": e.jeu.routes.get(t, {}).get("description", ""), "positions": int(len(g)),
                        "debut": g["timestamp"].min().strftime("%Y-%m-%d %H:%M")})
        return jsonify({"trajets": out, "avertissements": e.jeu.import_info.get("avertissements", []),
                        "origine": e.jeu.origine, "couches": {k: v for k, v in e.couches.items()
                                                             if k.startswith("source")}})

    def preparer(e: EtatJeu, trip, type_b):
        df = e.jeu.positions
        traj = df[df["trip_id"] == trip]
        if traj.empty:
            abort(404)
        b = traj["boat_id"].iloc[0]
        meta = e.jeu.bateaux.get(b, {})
        tb = meta.get("type_bateau") or type_b
        if tb not in P()["types_bateau"]:
            tb = "ais_autre" if e.jeu.origine == "ais_ouvert" else next(iter(P()["types_bateau"]))
        ptype = P()["types_bateau"][tb]
        analyse = detect.analyser_trajet(traj, ptype, P(), e.origine)
        geographie = pipeline.geographie_pour(analyse.points, P(), e.couches, e.origine)
        grille = pipeline.grille_pour(geographie, analyse.points, P())
        return b, meta, tb, ptype, analyse, geographie, grille

    @app.get("/api/jeux/<jid>/trajets/<trip>")
    def api_trajet(jid, trip):
        e = jeu_ou_404(jid)
        id_valide(trip)
        try:
            b, meta, tb, ptype, a, g, grille = preparer(e, trip, request.args.get("type"))
        except Exception as exc:
            if hasattr(exc, "code"):
                raise
            journal.ajouter("erreur_analyse", {"trip_id": trip, "erreur": type(exc).__name__})
            return jsonify({"erreur": f"Analyse impossible ({type(exc).__name__}) : aucun résultat affiché."}), 500
        pts = a.points
        route = e.jeu.routes.get(trip, {}).get("route_declaree_xy")
        return jsonify(archive.propre({
            "boat_id": b, "nom": meta.get("nom", ""), "type_bateau": tb, "libelle_type": ptype["libelle"],
            "type_connu": bool(meta.get("type_bateau")),
            "synthetique": bool(pts["synthetic"].any()) or e.jeu.origine == "demonstration_synthetique",
            "points": [{"lat": float(r.lat), "lon": float(r.lon), "t": r.timestamp.strftime("%H:%M:%S"),
                        "statut": r.statut, "sog": r.sog_kn, "v": r.v_calc_kn} for r in pts.itertuples()],
            "alertes": [{"id": x.identifiant, "type": x.type, "libelle": x.libelle, "categorie": x.categorie,
                         "debut": x.debut.strftime("%H:%M:%S"), "fin": x.fin.strftime("%H:%M:%S"),
                         "explication": x.explication, "valeurs": x.valeurs,
                         "alternatives": pipeline.EXPLICATIONS_ALTERNATIVES.get(x.type, [])} for x in a.alertes],
            "periodes": [{"id": p.identifiant, "debut": p.debut.strftime("%H:%M:%S"), "fin": p.fin.strftime("%H:%M:%S"),
                          "duree_min": p.duree_min, "cause": p.cause, "fiabilite_bornes": p.fiabilite_bornes,
                          "remarque": p.remarque, "points_ecartes": p.nb_points_ecartes} for p in a.periodes],
            "qualite": a.qualite, "score_anomalie": a.score_anomalie,
            "ecart_trajet_prevu": pipeline.ecart_trajet_prevu(pts, route),
            "route_declaree": ll([p[0] for p in route], [p[1] for p in route], g.origine) if route else None,
            "geographie": {
                "nom": g.nom, "fictive": g.fictive, "sources": g.sources,
                "terres": [{"nom": n, "polygone": ll([p[0] for p in poly], [p[1] for p in poly], g.origine)}
                           for n, poly in g.terres],
                "zones": [{"id": z.identifiant, "nom": z.nom, "description": z.description, "source": z.source,
                           "polygone": ll([p[0] for p in z.polygone], [p[1] for p in z.polygone], g.origine)}
                          for z in g.zones],
                "sans_terres": not g.terres,
            },
            "externes": int(len(e.externes)) + int(len(e.jeu.rf)),
        }))

    @app.post("/api/jeux/<jid>/couches")
    def api_couches(jid):
        exiger_entete()
        e = jeu_ou_404(jid)
        role = request.form.get("role")
        f = request.files.get("fichier")
        if role not in ("terres", "zones", "observations", "courant") or f is None:
            return jsonify({"erreur": "Rôle ou fichier manquant."}), 400
        octets = f.read()
        nom = schema.nom_affichable(f.filename or "")
        source = schema.nom_affichable(request.form.get("source") or nom)
        try:
            if role in ("terres", "zones"):
                polys = sources.lire_geojson(octets)
                with VERROU:
                    e.couches[role] = polys
                    e.couches[f"source_{role}"] = source
                info = {"polygones": len(polys)}
            elif role == "observations":
                obs = sources.lire_observations_externes(octets)
                with VERROU:
                    e.externes = obs
                info = {"observations": len(obs)}
            else:
                pos = e.jeu.positions
                bbox = (pos["lat"].min() - 0.2, pos["lat"].max() + 0.2, pos["lon"].min() - 0.2, pos["lon"].max() + 0.2)
                u, v, n = sources.courant_moyen(octets, bbox)
                info = {"courant_kmh": [round(u, 3), round(v, 3)], "valeurs": n}
                with VERROU:
                    e.couches["courant_kmh"] = [u, v]
        except ValueError as exc:
            return jsonify({"erreur": str(exc)}), 400
        journal.ajouter("couche_importee", {"jeu": jid, "role": role, "nom": nom, **info})
        return jsonify({"ok": True, **info})

    @app.post("/api/jeux/<jid>/zones")
    def api_zone_dessinee(jid):
        exiger_entete()
        e = jeu_ou_404(jid)
        d = request.get_json(silent=True) or {}
        pts = d.get("polygone") or []
        try:
            poly = [(float(p[1]), float(p[0])) for p in pts][:200]  # (lon, lat)
        except (TypeError, ValueError, IndexError):
            return jsonify({"erreur": "Polygone invalide."}), 400
        if len(poly) < 3 or not all(abs(la) <= 90 and abs(lo) <= 180 for lo, la in poly):
            return jsonify({"erreur": "Au moins trois sommets valides."}), 400
        with VERROU:
            dess = e.couches.setdefault("zones_dessinees", [])
            ident = f"D{len(dess) + 1}"
            nom = schema.nom_affichable(d.get("nom") or f"Zone dessinée {ident}")
            dess.append({"identifiant": ident, "nom": nom, "polygone": poly})
        journal.ajouter("zone_dessinee", {"jeu": jid, "zone": ident, "sommets": len(poly)})
        return jsonify({"id": ident})

    # ------------------------------------------------------------------ reconstruction
    @app.post("/api/reconstruire")
    def api_reconstruire():
        exiger_entete()
        d = request.get_json(silent=True) or {}
        e = jeu_ou_404(d.get("jeu"))
        trip = id_valide(d.get("trip"))
        modeles = [m for m in (d.get("modeles") or CP.MODELES) if m in CP.MODELES]
        b, meta, tb, ptype, a, g, grille = preparer(e, trip, d.get("type"))
        per = next((p for p in a.periodes if p.identifiant == d.get("periode")), None)
        if per is None:
            return jsonify({"erreur": "Période inconnue."}), 400
        zone_id = d.get("zone") or None
        if zone_id and zone_id not in grille.zones:
            return jsonify({"erreur": "Zone inconnue."}), 400
        deb, fin = pipeline.observations_bornes(a, per, P())
        externes = []
        if d.get("externes"):
            externes = pipeline.observations_rf(e.jeu.rf, trip, per.debut, per.fin) if e.origine == geo.ORIGINE_DEMO else []
            externes += pipeline.observations_externes(e.externes, trip, per.debut, per.fin, e.origine)
        params = config.copie(P())
        courant = d.get("courant")
        if isinstance(courant, list) and len(courant) == 2:
            try:
                params["modeles"]["courant_kmh"] = [float(np.clip(float(c), -15, 15)) for c in courant]
            except (TypeError, ValueError):
                return jsonify({"erreur": "Courant invalide."}), 400
        elif "courant_kmh" in e.couches:
            params["modeles"]["courant_kmh"] = list(e.couches["courant_kmh"])
        pts = a.points
        fiables = pts[pts["statut"] == "observee"]
        acc = fiables["accuracy_m"].fillna(params["detection"]["precision_position_defaut_m"])
        fiables = pd.DataFrame({"x": fiables["x"], "y": fiables["y"], "t_s": fiables["t_s"],
                                "sigma_km": np.hypot(params["modele"]["incertitude_bornes_m"], acc) / 1000})
        comp = CP.reconstruire_tous(grille, deb, fin, ptype, params, zone_id, externes, per, fiables, modeles,
                                    graine=7)
        contexte = {"jeu": d.get("jeu"), "jeu_nom": e.nom, "empreinte_fichier": e.jeu.empreinte,
                    "boat_id": b, "trip_id": trip, "type_bateau": tb, "periode": per.identifiant,
                    "debut": per.debut.strftime("%Y-%m-%dT%H:%M:%SZ"), "fin": per.fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duree_min": per.duree_min, "cause": per.cause, "fiabilite_bornes": per.fiabilite_bornes,
                    "zone_nom": g.zone(zone_id).nom if zone_id else None,
                    "observations_externes": [o.libelle for o in externes],
                    "versions": config.versions(params), "genere_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "synthetique": e.jeu.origine == "demonstration_synthetique" or bool(pts["synthetic"].any())}
        run = archive.comparaison_vers_dict(comp, grille, per.debut, zone_id, contexte)
        run["run_id"] = archive.nouveau_run_id(json.dumps(contexte, sort_keys=True))
        archive.sauver(run)
        with VERROU:
            etat["runs"][run["run_id"]] = run
            if len(etat["runs"]) > 30:
                etat["runs"].pop(next(iter(etat["runs"])))
        journal.ajouter("reconstruction_multi", {
            "run_id": run["run_id"], "trip_id": trip, "periode": per.identifiant, "zone": zone_id,
            "probas": {m: r["proba_passage"] for m, r in run["modeles"].items()},
            "empreinte_parametres": config.empreinte(params)})
        return jsonify(run)

    def run_ou_404(run_id):
        if run_id in etat["runs"]:
            return etat["runs"][run_id]
        d = archive.charger(run_id)
        if d is None:
            abort(404)
        return None

    @app.get("/api/runs")
    def api_runs():
        return jsonify(archive.lister())

    @app.get("/api/runs/<run_id>/chemins.csv")
    def api_chemins_csv(run_id):
        run = run_ou_404(run_id)
        d = archive.charger(run_id)
        journal.ajouter("export", {"run_id": run_id, "format": "chemins_csv"})
        if d is not None and (d / "chemins.csv").exists():
            return send_file(d / "chemins.csv", mimetype="text/csv", as_attachment=True,
                             download_name=f"seatrace_chemins_{run_id}.csv")
        return Response(schema.exporter_csv(archive.chemins_dataframe(run)), mimetype="text/csv")

    @app.get("/api/runs/<run_id>/chemins.geojson")
    def api_chemins_geojson(run_id):
        run = run_ou_404(run_id)
        if run is None:
            abort(404)  # GeoJSON disponible pour les runs de la session (le CSV reste archivé)
        journal.ajouter("export", {"run_id": run_id, "format": "geojson"})
        return Response(json.dumps(archive.geojson(run), ensure_ascii=False), mimetype="application/geo+json",
                        headers={"Content-Disposition": f"attachment; filename=seatrace_chemins_{run_id}.geojson"})

    @app.get("/api/runs/<run_id>/resume.json")
    def api_resume(run_id):
        run_ou_404(run_id)
        d = archive.charger(run_id)
        return send_file(d / "resume.json", mimetype="application/json", as_attachment=True,
                         download_name=f"seatrace_resume_{run_id}.json")

    @app.post("/api/runs/<run_id>/decision")
    def api_decision(run_id):
        exiger_entete()
        run = run_ou_404(run_id)
        d = request.get_json(silent=True) or {}
        dec = d.get("decision")
        if dec not in list(report.DECISIONS)[:3]:
            return jsonify({"erreur": "Décision invalide."}), 400
        decision = {"decision": dec, "analyste": str(d.get("analyste", ""))[:12].strip(),
                    "note": str(d.get("note", ""))[:1000].strip(),
                    "horodatage": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if run is not None:
            run["decision_humaine"] = decision
        journal.ajouter("decision_humaine", {"run_id": run_id, "decision": dec, "analyste": decision["analyste"],
                                             "longueur_note": len(decision["note"])})
        return jsonify({"ok": True})

    @app.get("/api/runs/<run_id>/rapport.html")
    def api_rapport(run_id):
        run = run_ou_404(run_id)
        if run is None:
            abort(404)
        html_txt = report.rapport_comparatif_html(run)
        journal.ajouter("export", {"run_id": run_id, "format": "rapport_html"})
        resp = Response(html_txt, mimetype="text/html")
        resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        resp.headers["Content-Disposition"] = f"attachment; filename=seatrace_rapport_{run_id}.html"
        return resp

    # ------------------------------------------------------------------ évaluation, journal
    @app.get("/api/evaluation")
    def api_evaluation():
        out = {}
        for nom in ("comparatif.json", "metriques.json"):
            f = RACINE / "eval" / "resultats" / nom
            out[nom.split(".")[0]] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        return jsonify(out)

    @app.get("/api/evaluation/<nom>.png")
    def api_eval_png(nom):
        if nom not in ("comparatif", "calibration"):
            abort(404)
        f = RACINE / "eval" / "resultats" / f"{nom}.png"
        if not f.exists():
            abort(404)
        return send_file(f, mimetype="image/png")

    @app.get("/api/journal")
    def api_journal():
        ok, _, msg = journal.verifier()
        return jsonify({"integre": ok, "message": msg, "entrees": journal.entrees()[-50:][::-1]})

    return app
