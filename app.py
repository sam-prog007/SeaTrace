"""SeaTrace — application de démonstration (Streamlit, locale).

Lancement :  streamlit run app.py

Traitement local uniquement : aucune donnée n'est envoyée à l'extérieur, aucune
API externe, aucun fond de carte en ligne (la carte est dessinée localement).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from seatrace import audit, config, detect, geo, pipeline, reconstruct as R, report, schema

st.set_page_config(page_title="SeaTrace", page_icon="🧭", layout="wide")

COUL = {
    "observee": "#1f6fb2", "suspecte": "#e67e22", "terre": "#d9d3c3", "zone": "#c0392b",
    "route": "#2c3e50", "estime_zone": "#8e44ad", "estime_hors": "#95a5a6", "rf": "#16a085",
}
LIB_STATUT = {
    "etayee": ("Estimation étayée", "success"),
    "fragile": ("Estimation fragile — à confirmer", "warning"),
    "insuffisamment_etayee": ("Estimation insuffisamment étayée — l'outil ne peut pas conclure", "error"),
}


# ---------------------------------------------------------------------------
# État et journal
# ---------------------------------------------------------------------------

def journal():
    return audit.Journal(pipeline.CHEMIN_JOURNAL)


@st.cache_data(show_spinner=False)
def _params_fichier():
    return config.charger()


def params_session():
    if "params" not in st.session_state:
        st.session_state["params"] = config.copie(_params_fichier())
    return st.session_state["params"]


@st.cache_data(show_spinner=False)
def _demo():
    return pipeline.charger_demo(config.charger())


@st.cache_data(show_spinner=False)
def _grille(geo_nom, bornes, taille, fictive):
    g = geo.geographie_demo() if fictive else geo.Geographie(geo_nom, bornes, fictive=False)
    return geo.construire_grille(g, taille)


# ---------------------------------------------------------------------------
# Barre latérale : données et paramètres
# ---------------------------------------------------------------------------

def barre_laterale(params):
    st.sidebar.title("🧭 SeaTrace")
    st.sidebar.caption("Prototype local · Secure Horizons 2026 · aide à l'investigation")
    source = st.sidebar.radio("1. Données", ["Exemple de démonstration (synthétique)", "Importer un fichier CSV"])
    jeu = None
    if source.startswith("Exemple"):
        jeu = _demo()
        if not st.session_state.get("demo_journalise"):
            journal().ajouter("chargement_demo", {"empreinte": jeu.empreinte[:16], "lignes": jeu.import_info["lignes"]})
            st.session_state["demo_journalise"] = True
    else:
        st.sidebar.caption("CSV UTF-8, colonnes : boat_id, trip_id, timestamp (ISO 8601 UTC), lat, lon "
                           "[, sog_kn, cog_deg, accuracy_m, source, synthetic]. "
                           f"Maximum {params['import']['taille_max_octets'] // 1_000_000} Mo, "
                           f"{params['import']['lignes_max']} lignes.")
        f = st.sidebar.file_uploader("Fichier de positions", type=["csv"], accept_multiple_files=False)
        if f is not None:
            octets = f.getvalue()
            cle = schema.empreinte_octets(octets)
            if st.session_state.get("import_cle") != cle:
                try:
                    res = schema.lire_csv_securise(octets, f.name, params["import"])
                    schema.conserver_original(octets, res, pipeline.DOSSIER_IMPORTS)
                    st.session_state["import"] = pipeline.Jeu(
                        res.donnees, {}, {}, pd.DataFrame(), res.empreinte, "import_utilisateur",
                        {"lignes": res.lignes_lues, "rejetees": res.lignes_rejetees,
                         "avertissements": res.avertissements, "nom": res.nom_fourni_affichable,
                         "motifs": res.motifs_rejet})
                    journal().ajouter("import", {"empreinte": res.empreinte[:16], "nom": res.nom_fourni_affichable,
                                                 "lignes": res.lignes_lues, "rejetees": res.lignes_rejetees})
                    st.session_state.pop("import_erreur", None)
                except schema.ErreurImport as exc:
                    st.session_state.pop("import", None)
                    st.session_state["import_erreur"] = str(exc)
                    journal().ajouter("import_refuse", {"empreinte": cle[:16], "motif": str(exc)[:120]})
                st.session_state["import_cle"] = cle
            if st.session_state.get("import_erreur"):
                st.sidebar.error("Import refusé : " + st.session_state["import_erreur"])
            jeu = st.session_state.get("import")
    return jeu


def panneau_parametres(params):
    with st.sidebar.expander("Paramètres du modèle (journalisés)"):
        pm = params["modele"]
        st.caption("Valeurs de référence réglées sur données synthétiques. Toute modification est "
                   "journalisée et apparaît dans le rapport (empreinte des paramètres).")
        pers = st.number_input("Persistance du cap", 0.0, 0.99, float(pm["persistance_cap"]), 0.05)
        disp = st.number_input("Dispersion de vitesse (relative)", 0.05, 3.0, float(pm["dispersion_relative"]), 0.05)
        inc = st.number_input("Incertitude des positions encadrantes (m)", 1.0, 5000.0, float(pm["incertitude_bornes_m"]), 10.0)
        if st.button("Appliquer"):
            nouveau = config.copie(params)
            nouveau["modele"].update({"persistance_cap": pers, "dispersion_relative": disp, "incertitude_bornes_m": inc})
            try:
                config.valider(nouveau)
                avant = config.empreinte(params)
                st.session_state["params"] = nouveau
                st.session_state["reconstructions"] = {}
                journal().ajouter("modification_parametres", {
                    "avant": avant, "apres": config.empreinte(nouveau),
                    "valeurs": {"persistance_cap": pers, "dispersion_relative": disp, "incertitude_bornes_m": inc}})
                st.rerun()
            except config.ErreurParametres as exc:
                st.error(str(exc))
        if st.button("Revenir aux valeurs de référence"):
            st.session_state["params"] = config.copie(_params_fichier())
            st.session_state["reconstructions"] = {}
            journal().ajouter("reinitialisation_parametres", {"empreinte": config.empreinte(_params_fichier())})
            st.rerun()
        st.caption(f"Empreinte des paramètres actifs : `{config.empreinte(params)}`")


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

def figure_carte(points, geographie, route_xy, periode=None, resultat=None, grille=None,
                 zone_id=None, externes=None, hauteur=620):
    fig = go.Figure()
    for nom, poly in geographie.terres:
        a = np.array(poly + [poly[0]])
        fig.add_trace(go.Scatter(x=a[:, 0], y=a[:, 1], fill="toself", mode="lines", line=dict(color="#a89f88", width=1),
                                 fillcolor=COUL["terre"], name=nom, hoverinfo="name", showlegend=False))
    for z in geographie.zones:
        a = np.array(z.polygone + [z.polygone[0]])
        fig.add_trace(go.Scatter(x=a[:, 0], y=a[:, 1], fill="toself", mode="lines",
                                 line=dict(color=COUL["zone"], width=2, dash="dash"),
                                 fillcolor="rgba(192,57,43,0.18)" if z.identifiant == zone_id else "rgba(192,57,43,0.08)",
                                 name=f"{z.nom}", hovertext=z.description, hoverinfo="text"))
    if resultat is not None and resultat.occupation is not None and grille is not None:
        occ = resultat.occupation / max(resultat.occupation.max(), 1e-12)
        occ = np.where(occ < 0.01, np.nan, occ)
        fig.add_trace(go.Heatmap(x=grille.xc, y=grille.yc, z=occ, colorscale="Purples", zmin=0, zmax=1,
                                 opacity=0.6, showscale=False, name="Corridor estimé (probabilité de présence)",
                                 hovertemplate="présence relative %{z:.2f}<extra>estimé</extra>"))
        premier = {True: True, False: True}
        for xs, ys, passe in resultat.trajets[:60]:
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", line=dict(color=COUL["estime_zone"] if passe else COUL["estime_hors"], width=1),
                opacity=0.45, hoverinfo="skip", showlegend=premier[passe],
                name="Trajets ESTIMÉS passant par la zone" if passe else "Trajets ESTIMÉS hors zone"))
            premier[passe] = False
    if route_xy:
        r = np.array(route_xy)
        fig.add_trace(go.Scatter(x=r[:, 0], y=r[:, 1], mode="lines", line=dict(color=COUL["route"], dash="dot", width=2),
                                 name="Trajet déclaré (prévu)"))
    obs = points[points["statut"] == "observee"]
    sus = points[points["statut"] == "suspecte"]
    survol = "%{customdata[0]}<br>vitesse déclarée %{customdata[1]} nd · calculée %{customdata[2]} nd<extra>%{fullData.name}</extra>"

    def cdata(df):
        return np.column_stack([df["timestamp"].dt.strftime("%H:%M:%S UTC"), df["sog_kn"].round(1).astype(str),
                                df["v_calc_kn"].round(1).astype(str)])

    fig.add_trace(go.Scatter(x=obs["x"], y=obs["y"], mode="lines+markers", marker=dict(size=5, color=COUL["observee"]),
                             line=dict(color="rgba(31,111,178,0.35)", width=1), name="Positions observées",
                             customdata=cdata(obs), hovertemplate=survol))
    if len(sus):
        fig.add_trace(go.Scatter(x=sus["x"], y=sus["y"], mode="markers", marker=dict(size=8, color=COUL["suspecte"], symbol="x"),
                                 name="Positions suspectes (écartées)", customdata=cdata(sus), hovertemplate=survol))
    if periode is not None:
        for i, lib in ((periode.i_avant, "Avant"), (periode.i_apres, "Après")):
            fig.add_trace(go.Scatter(x=[points["x"].iloc[i]], y=[points["y"].iloc[i]], mode="markers+text",
                                     marker=dict(size=13, color="white", line=dict(color="black", width=2)),
                                     text=[lib], textposition="top center", name=f"Observation {lib.lower()}",
                                     showlegend=False))
    for o in externes or []:
        t = np.linspace(0, 2 * math.pi, 60)
        fig.add_trace(go.Scatter(x=o.x + o.sigma_km * np.cos(t), y=o.y + o.sigma_km * np.sin(t), mode="lines",
                                 line=dict(color=COUL["rf"], dash="dash"), name="Observation RF SIMULÉE (incertitude)"))
        fig.add_trace(go.Scatter(x=[o.x], y=[o.y], mode="markers", marker=dict(symbol="triangle-up", size=12, color=COUL["rf"]),
                                 name=o.libelle, showlegend=False))
    xmin, xmax, ymin, ymax = geographie.bornes
    fig.update_xaxes(range=[xmin, xmax], title="Est (km, repère local)", showgrid=False, zeroline=False)
    fig.update_yaxes(range=[ymin, ymax], title="Nord (km)", scaleanchor="x", scaleratio=1, showgrid=False, zeroline=False)
    fig.update_layout(height=hauteur, margin=dict(l=10, r=10, t=30, b=10), plot_bgcolor="#eaf2f8",
                      legend=dict(orientation="h", yanchor="top", y=-0.08, font=dict(size=11)),
                      title=dict(text=geographie.nom, font=dict(size=13)))
    return fig


def figure_chronologie(points, analyse, vmax):
    fig = go.Figure()
    for a in analyse.alertes:
        coul = "rgba(230,126,34,0.20)" if a.categorie == detect.INTEGRITE else "rgba(127,140,141,0.20)"
        fin = a.fin if a.fin > a.debut else a.debut + pd.Timedelta(seconds=30)
        fig.add_vrect(x0=a.debut, x1=fin, fillcolor=coul, line_width=0, annotation_text=a.identifiant,
                      annotation_position="top left", annotation_font_size=10)
    for statut, coul in (("observee", COUL["observee"]), ("suspecte", COUL["suspecte"])):
        sous = points[points["statut"] == statut]
        fig.add_trace(go.Scatter(x=sous["timestamp"], y=sous["v_calc_kn"].clip(upper=vmax * 3), mode="markers",
                                 marker=dict(color=coul, size=6, symbol="circle" if statut == "observee" else "x"),
                                 name="Vitesse calculée — " + ("observée" if statut == "observee" else "suspecte")))
    fig.add_trace(go.Scatter(x=points["timestamp"], y=points["sog_kn"], mode="lines", line=dict(color="#7f8c8d", width=1),
                             name="Vitesse déclarée par le traceur"))
    fig.add_hline(y=vmax, line_dash="dot", line_color="#c0392b", annotation_text="vitesse max retenue",
                  annotation_font_size=10)
    fig.update_layout(height=260, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="nœuds",
                      legend=dict(orientation="h", y=-0.25, font=dict(size=10)))
    return fig


# ---------------------------------------------------------------------------
# Reconstruction (mise en cache dans la session)
# ---------------------------------------------------------------------------

def lancer_reconstruction(cle, grille, deb, fin, ptype, params, zone_id, externes, periode):
    cache = st.session_state.setdefault("reconstructions", {})
    if cle in cache:
        return cache[cle]
    with st.spinner("Reconstruction et analyse de sensibilité…"):
        r = R.reconstruire(grille, deb, fin, ptype, params["modele"], zone_id, externes, graine=7)
        if r.proba_passage is not None:
            sens = R.analyse_sensibilite(grille, deb, fin, ptype, params["modele"], zone_id, externes)
            r = R.qualifier(r, sens, periode.fiabilite_bornes, calibre=True, duree_min=periode.duree_min)
    cache[cle] = r
    journal().ajouter("reconstruction", {"cle": cle, "proba_passage": None if r.proba_passage is None else round(r.proba_passage, 4),
                                         "statut": r.statut, "empreinte_parametres": config.empreinte(params)})
    return r


# ---------------------------------------------------------------------------
# Page principale
# ---------------------------------------------------------------------------

def page_analyse(jeu, params):
    df = jeu.positions
    if jeu.origine == "demonstration_synthetique":
        st.info("**Données synthétiques et géographie fictive.** Les bateaux, la côte, les îles et la zone Z1 sont "
                "inventés pour la démonstration. Les manipulations ont été simulées sur une copie locale.", icon="🧪")
    if jeu.import_info.get("avertissements"):
        for a in jeu.import_info["avertissements"]:
            st.warning(a)

    # 2. Sélection du bateau et du trajet
    c1, c2 = st.columns(2)
    bateau = c1.selectbox("2. Bateau", sorted(df["boat_id"].unique()))
    trajets = sorted(df.loc[df["boat_id"] == bateau, "trip_id"].unique())
    trip = c2.selectbox("Trajet", trajets, format_func=lambda t: f"{t} — {jeu.routes.get(t, {}).get('description', '')[:70]}")
    meta = jeu.bateaux.get(bateau)
    types = list(params["types_bateau"].keys())
    if meta:
        type_b = meta["type_bateau"]
    else:
        type_b = st.selectbox("Type de bateau (non fourni par le fichier)", types,
                              format_func=lambda t: params["types_bateau"][t]["libelle"])
    ptype = params["types_bateau"][type_b]
    traj = df[(df["boat_id"] == bateau) & (df["trip_id"] == trip)]
    try:
        analyse = detect.analyser_trajet(traj, ptype, params)
    except Exception as exc:  # aucune conclusion rassurante en cas de panne
        st.error(f"Analyse impossible ({type(exc).__name__}). Aucun résultat n'est affiché pour ce trajet.")
        journal().ajouter("erreur_analyse", {"trip_id": trip, "erreur": type(exc).__name__})
        return
    if not analyse.qualite.get("suffisant", False):
        st.error("Données insuffisantes pour analyser ce trajet (moins de 10 positions). Aucun résultat n'est affiché.")
        return
    pts = analyse.points
    geographie = pipeline.geographie_pour(pts, params)
    grille = (_grille(geographie.nom, geographie.bornes, params["modele"]["taille_case_km"], geographie.fictive)
              if geographie.fictive else pipeline.grille_pour(geographie, pts, params))
    route = jeu.routes.get(trip, {}).get("route_declaree_xy")
    ecart = pipeline.ecart_trajet_prevu(pts, route)
    dossier = f"ST-{trip}-{jeu.empreinte[:8]}"

    # Synthèse : scores SÉPARÉS
    st.subheader(f"Dossier {dossier}")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Score d'anomalie des données", f"{analyse.score_anomalie}/100",
              help="Incohérence des données (pas une culpabilité). Somme pondérée des alertes d'intégrité, plafonnée à 100.")
    k2.metric("Complétude des données", f"{analyse.qualite['completude_pct']} %",
              help="Positions reçues / positions attendues à l'intervalle nominal.")
    k3.metric("Positions suspectes écartées", f"{analyse.qualite['points_suspects']} / {analyse.qualite['points']}")
    k4.metric("Écart max. au trajet prévu", f"{ecart['ecart_max_km']} km" if ecart else "non fourni",
              help="Distance maximale entre les positions fiables et le trajet déclaré. Un détour n'est pas une faute.")
    if not geographie.fictive:
        st.warning("Données hors de la baie de démonstration : aucune carte des terres ni aucune zone n'est disponible. "
                   "La reconstruction ignore donc les obstacles, et la probabilité de passage ne peut pas être calculée.")

    # 3. Carte et alertes
    col_carte, col_alertes = st.columns([3, 2])
    periodes = analyse.periodes
    with col_alertes:
        st.markdown("#### 3. Anomalies et explications")
        if not analyse.alertes:
            st.success("Aucune anomalie détectée par les contrôles. Cela ne garantit pas que les données sont vraies.")
        for a in analyse.alertes:
            icone = "🟠" if a.categorie == detect.INTEGRITE else "⚪"
            with st.expander(f"{icone} {a.identifiant} · {a.libelle} · {a.debut:%H:%M}–{a.fin:%H:%M}"):
                st.write(a.explication)
                st.caption("Catégorie : " + ("intégrité des données" if a.categorie == detect.INTEGRITE else "disponibilité"))
                st.caption("Autres explications possibles : " + "; ".join(pipeline.EXPLICATIONS_ALTERNATIVES.get(a.type, [])))
                st.json(a.valeurs, expanded=False)
        st.markdown("#### 4. Périodes manquantes ou suspectes")
        if not periodes:
            st.info("Aucune période à reconstruire sur ce trajet.")
            choix = None
        else:
            libs = [f"{p.identifiant} · {p.debut:%H:%M}→{p.fin:%H:%M} · {p.duree_min} min · "
                    f"{ {'interruption': 'interruption', 'positions_suspectes': 'positions suspectes', 'mixte': 'mixte'}[p.cause]}"
                    for p in periodes]
            i_sel = st.radio("Période à examiner", range(len(periodes)), format_func=lambda i: libs[i])
            choix = periodes[i_sel]
    externes = []
    zone_id = None
    resultat = None
    if choix is not None:
        deb, fin = pipeline.observations_bornes(analyse, choix, params)
        with col_alertes:
            st.markdown("**5. Observations qui encadrent la période**")
            st.dataframe(pd.DataFrame([
                {"": "Avant", "heure (UTC)": f"{choix.debut:%H:%M:%S}", "fiabilité": choix.fiabilite_bornes,
                 "incertitude (m)": round(deb.sigma_km * 1000), "cap déclaré": deb.cap_deg, "vitesse déclarée": deb.vitesse_kn},
                {"": "Après", "heure (UTC)": f"{choix.fin:%H:%M:%S}", "fiabilité": choix.fiabilite_bornes,
                 "incertitude (m)": round(fin.sigma_km * 1000), "cap déclaré": fin.cap_deg, "vitesse déclarée": fin.vitesse_kn},
            ]), hide_index=True, width="stretch")
            if choix.remarque:
                st.warning(choix.remarque)
            zones = {z.identifiant: z for z in geographie.zones}
            if zones:
                zone_id = st.selectbox("7. Zone à examiner", list(zones), format_func=lambda z: zones[z].nom)
                st.caption(zones[zone_id].description)
            rf = pipeline.observations_rf(jeu.rf, trip, choix.debut, choix.fin)
            if rf:
                if st.checkbox("Inclure les observations RF simulées", value=False,
                               help="Observations radiofréquences SIMULÉES pour la démonstration. Aucune intégration "
                                    "ni aucun partenariat réel avec un fournisseur n'existe à ce jour."):
                    externes = rf
                    for o in rf:
                        st.caption(f"{o.libelle} : précision ±{o.sigma_km * 1000:.0f} m, probabilité d'attribution "
                                   f"au bateau {o.p_attribution:.0%} (non attribuée automatiquement).")
            lancer = st.button("6. Reconstruire les trajets compatibles", type="primary")
        cle = f"{jeu.empreinte[:12]}|{trip}|{choix.identifiant}|{zone_id}|{len(externes)}|{config.empreinte(params)}"
        if lancer or cle in st.session_state.get("reconstructions", {}):
            resultat = lancer_reconstruction(cle, grille, deb, fin, ptype, params, zone_id, externes, choix)
    with col_carte:
        st.plotly_chart(figure_carte(pts, geographie, route, choix, resultat, grille, zone_id, externes),
                        width="stretch")
        st.plotly_chart(figure_chronologie(pts, analyse, ptype["vitesse_max_kn"] * params["detection"]["marge_vitesse"]),
                        width="stretch")

    if resultat is not None:
        afficher_resultat(resultat, choix, zone_id, geographie)

    # 9. Décision humaine et 10. export
    st.divider()
    st.markdown("#### 9. Note et décision de l'analyste")
    st.caption("SeaTrace ne décide jamais d'une sanction ni d'un refus d'indemnisation. La décision ci-dessous "
               "porte uniquement sur la suite de l'investigation.")
    decisions = st.session_state.setdefault("decisions", {})
    with st.form(f"decision_{dossier}"):
        dec = st.radio("Décision", list(report.DECISIONS)[:3], format_func=lambda d: report.DECISIONS[d], horizontal=False)
        analyste = st.text_input("Initiales de l'analyste", max_chars=12)
        note = st.text_area("Note", max_chars=1000)
        if st.form_submit_button("Enregistrer la décision"):
            decisions[dossier] = {"decision": dec, "analyste": analyste.strip(), "note": note.strip(),
                                  "horodatage": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            journal().ajouter("decision_humaine", {"dossier": dossier, "decision": dec, "analyste": analyste.strip()[:12],
                                                   "longueur_note": len(note.strip())})
            st.success("Décision enregistrée et journalisée.")

    st.markdown("#### 10. Export")
    contexte = pipeline.contexte_rapport(jeu, dossier, bateau, meta, type_b, params, trip, analyse, ecart, choix,
                                zone_id, resultat, externes, decisions.get(dossier), geographie)
    rapport = report.construire(contexte)
    rapport["empreinte_rapport"] = report.empreinte_json(rapport)
    png = report.carte_png(pts, geographie, route, resultat, grille, zone_id, externes)
    html_txt = report.vers_html(rapport, png)
    e1, e2, e3 = st.columns(3)
    nom = f"seatrace_{dossier.replace('|', '_')}"
    if e1.download_button("Rapport HTML", html_txt.encode("utf-8"), f"{nom}.html", "text/html"):
        journal().ajouter("export", {"dossier": dossier, "format": "html", "empreinte_rapport": rapport["empreinte_rapport"][:16]})
    if e2.download_button("Rapport JSON", json.dumps(rapport, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                          f"{nom}.json", "application/json"):
        journal().ajouter("export", {"dossier": dossier, "format": "json", "empreinte_rapport": rapport["empreinte_rapport"][:16]})
    export_pts = pts[["boat_id", "trip_id", "timestamp", "lat", "lon", "sog_kn", "cog_deg", "v_calc_kn", "statut", "source"]]
    if e3.download_button("Positions annotées (CSV)", schema.exporter_csv(export_pts), f"{nom}_positions.csv", "text/csv"):
        journal().ajouter("export", {"dossier": dossier, "format": "csv"})
    st.caption("Les exports tableur sont protégés contre l'injection de formules. Le rapport HTML est autonome "
               "(aucun lien externe, aucun script).")


def afficher_resultat(r, periode, zone_id, geographie):
    st.divider()
    st.markdown("#### 8. Estimation de passage, hypothèses et limites")
    if r.proba_passage is None:
        if zone_id is None:
            st.info("Corridors reconstruits affichés. Aucune zone définie : probabilité de passage non calculée.")
        else:
            st.error("**Estimation insuffisamment étayée.** " + " ".join(r.raisons))
        return
    lib, niveau = LIB_STATUT[r.statut]
    c1, c2 = st.columns([1, 2])
    with c1:
        st.metric(f"Probabilité estimée de passage dans {zone_id}", f"{100 * r.proba_passage:.0f} %")
        getattr(st, niveau)(lib)
        st.markdown(f"**Lecture : {r.lecture}**")
        vals = [l["proba_passage"] for l in r.sensibilite if l["proba_passage"] is not None and "isotropic" not in l["variation"]]
        if vals:
            st.caption(f"Selon les hypothèses raisonnables testées : de {min(vals):.0%} à {max(vals):.0%}.")
        mc = r.controle_mc
        if mc:
            st.caption(f"Contrôle Monte Carlo : {mc['proportion_passage']:.0%} ± {mc['erreur_type']:.0%} sur "
                       f"{mc['n_trajets']} trajets tirés (erreur due au nombre de tirages, distincte de l'incertitude "
                       "liée aux hypothèses).")
        st.caption(f"Calcul : {r.temps_calcul_s:.2f} s · {r.n_pas} pas de {r.dt_s:.0f} s.")
    with c2:
        st.markdown("**Ce que signifie ce chiffre** : sous les hypothèses ci-dessous, et à la résolution de la grille, "
                    "probabilité que le trajet réel soit entré au moins une fois dans la zone pendant la période. "
                    "**Ce n'est pas une probabilité de fraude.** Traverser une zone ne démontre pas une activité "
                    "interdite. Le modèle n'est calibré que sur des données synthétiques.")
        if r.raisons:
            st.markdown("**Raisons et limites :**\n" + "\n".join(f"- {x}" for x in r.raisons))
        st.markdown("**Autres explications possibles :** " + "; ".join(pipeline.EXPLICATIONS_ALTERNATIVES["passage"]))
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Sensibilité aux hypothèses**")
        sdf = pd.DataFrame(r.sensibilite)
        sdf["proba_passage"] = sdf["proba_passage"].map(lambda v: "impossible" if v is None else f"{100 * v:.0f} %")
        st.dataframe(sdf, hide_index=True, width="stretch")
    with t2:
        st.markdown("**Modèle et hypothèses**")
        st.dataframe(pd.DataFrame([{"hypothèse": k, "valeur": str(v)} for k, v in r.hypotheses.items()]),
                     hide_index=True, width="stretch")


def page_evaluation():
    st.subheader("Évaluation du modèle (données synthétiques)")
    m = pipeline.metriques_evaluation()
    if not m:
        st.info("Aucune évaluation disponible. Lancer : `python eval/run_eval.py`")
        return
    st.warning(m["avertissement"])
    d = m["detection"]
    st.markdown("##### Détection des manipulations (jeu d'évaluation, bateaux jamais vus)")
    c = st.columns(4)
    c[0].metric("Rappel (hors suppression)", f"{d['rappel_global_hors_suppression']:.0%}")
    c[1].metric("Précision des alertes", f"{d['precision_alertes']:.0%}")
    c[2].metric("Trajets propres sans alerte", f"{d['part_trajets_propres_sans_alerte']:.0%}")
    c[3].metric("Temps d'analyse médian", f"{d['temps_analyse_median_s'] * 1000:.0f} ms")
    st.dataframe(pd.DataFrame([{"manipulation": k, "rappel": f"{v:.0%}"} for k, v in d["rappel_par_type"].items()]),
                 hide_index=True)
    st.caption(f"Suppressions de positions signalées comme interruption (disponibilité) : "
               f"{d['suppression_signalee_comme_interruption']:.0%}. Une suppression n'est pas distinguable d'une perte de "
               "réception à partir des seules données : c'est attendu et documenté.")
    p = m["passage"]
    st.markdown("##### Probabilité de passage — score de Brier (plus bas = meilleur)")
    lignes = [("Modèle à cap (réglé)", p["modele"]["brier"]), ("Variante sans cap", p["modele_sans_cap_isotrope"]["brier"]),
              ("Modèle + habitudes apprises", p["modele_avec_habitudes_beta0.5"]["brier"]),
              ("Interpolation linéaire", p["reference_interpolation_lineaire"]["brier"]),
              ("Zone atteignable (oui/non)", p["reference_zone_atteignable"]["brier"]),
              ("Taux de base", p["reference_taux_de_base"]["brier"])]
    st.dataframe(pd.DataFrame(lignes, columns=["méthode", "Brier"]), hide_index=True)
    st.caption(f"{p['interruptions_evaluation']} interruptions simulées ; {p.get('note_echantillon', '')}.")
    cal = pipeline.RACINE / "eval" / "resultats" / "calibration.png"
    c1, c2 = st.columns(2)
    if cal.exists():
        c1.image(str(cal))
    rec = m["reconstruction"]
    c2.markdown("##### Reconstruction")
    c2.write(rec.get("definition", ""))
    c2.json({k: v for k, v in rec.items() if k != "definition"}, expanded=False)
    st.json(m, expanded=False)


def page_journal():
    st.subheader("Journal local")
    j = journal()
    ok, idx, msg = j.verifier()
    (st.success if ok else st.error)(msg)
    st.caption("Chaînage SHA-256 : une modification isolée est détectée, mais quelqu'un qui contrôle la machine "
               "peut réécrire tout le journal. Ce n'est pas un journal inviolable.")
    e = j.entrees()
    if e:
        st.dataframe(pd.DataFrame([{"horodatage": x["horodatage"], "type": x["type"],
                                    "details": json.dumps(x["details"], ensure_ascii=False), "hash": x["hash"][:12]}
                                   for x in e[::-1][:200]]), hide_index=True, width="stretch")


def page_apropos():
    st.subheader("À propos, sécurité et limites")
    st.markdown("""
**SeaTrace** analyse les données de navigation d'une petite embarcation après une sortie : il détecte des
incohérences pouvant indiquer une altération, isole les positions suspectes, reconstruit les trajets
compatibles pendant les périodes manquantes ou suspectes et estime la probabilité de passage dans une zone.

**Ce que SeaTrace ne fait pas** : prouver une fraude, décider d'une sanction ou d'un refus d'indemnisation,
garantir que des positions étaient vraies à leur collecte.

**Sources de données**
- *Réellement intégrées* : fichiers CSV de positions (traceurs) importés localement.
- *Simulées* : toutes les données de démonstration, y compris les « observations RF simulées ».
- *Prévues* (non intégrées) : observations radiofréquences d'un fournisseur spécialisé (accès, droits d'usage et
  attribution à valider), historiques de traceurs fournis lors d'un pilote avec un assureur et un gestionnaire de flotte.

**Sécurité du prototype** : traitement local, imports validés (taille, lignes, longueur, types, bornes), originaux
conservés sous un nom interne, aucun contenu exécuté, aucun modèle chargé depuis l'extérieur, exports protégés,
journal chaîné. Le prototype n'a ni authentification ni isolation entre clients : il ne doit pas être exposé
sur un réseau. Voir `docs/SECURITE.md`.
""")
    st.markdown("**Limites générales**\n" + "\n".join(f"- {x}" for x in report.LIMITES_GENERALES))


def main():
    params = params_session()
    jeu = barre_laterale(params)
    panneau_parametres(params)
    onglets = st.tabs(["Analyse", "Évaluation du modèle", "Journal", "À propos et limites"])
    with onglets[0]:
        if jeu is None:
            st.info("Choisissez l'exemple de démonstration ou importez un fichier CSV dans la barre latérale.")
        else:
            page_analyse(jeu, params)
    with onglets[1]:
        page_evaluation()
    with onglets[2]:
        page_journal()
    with onglets[3]:
        page_apropos()


main()
