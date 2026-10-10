"""Rapport d'analyse exportable (HTML autonome + JSON).

Le rapport est une AIDE À L'INVESTIGATION. Il ne constitue ni une preuve, ni une
certification juridique, ni une décision. Toutes les valeurs proviennent de
calculs vérifiables ; aucun texte n'est généré par un modèle de langage.
Tous les champs issus des données importées sont échappés (aucun HTML actif).
"""

from __future__ import annotations

import base64
import html
import io
import json
from datetime import datetime, timezone

import numpy as np

from . import config, geo

STATUTS = {
    "etayee": "Estimation étayée (sous les hypothèses indiquées)",
    "fragile": "Estimation fragile : à confirmer par d'autres éléments",
    "insuffisamment_etayee": "Estimation insuffisamment étayée : l'outil ne peut pas conclure",
}

DECISIONS = {
    "aucune": "Pas d'investigation complémentaire",
    "informations": "Demander des informations complémentaires (gestionnaire de flotte, assuré)",
    "investigation": "Ouvrir une investigation approfondie",
    "non_renseignee": "Décision non renseignée",
}


def e(v) -> str:
    return html.escape(str(v), quote=True)


def carte_png(points, geographie, route_xy=None, resultat=None, grille=None, zone_id=None, externes=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.5, 6.0))
    for nom, poly in geographie.terres:
        a = np.array(poly + [poly[0]])
        ax.fill(a[:, 0], a[:, 1], color="#d9d3c3", ec="#a89f88", lw=0.6)
    for z in geographie.zones:
        a = np.array(z.polygone + [z.polygone[0]])
        ax.fill(a[:, 0], a[:, 1], color="#c0392b", alpha=0.15 if z.identifiant != zone_id else 0.28,
                ec="#c0392b", lw=1.2, ls="--")
        ax.text(a[:, 0].mean(), a[:, 1].mean(), z.identifiant, color="#922b21", ha="center", fontsize=9)
    if resultat is not None and resultat.occupation is not None and grille is not None:
        occ = resultat.occupation
        g = grille
        ext = [g.x0, g.x0 + g.nx * g.taille, g.y0, g.y0 + g.ny * g.taille]
        masque = np.ma.masked_less(occ / max(occ.max(), 1e-12), 0.01)
        ax.imshow(masque, origin="lower", extent=ext, cmap="Purples", alpha=0.75, vmin=0, vmax=1)
        for xs, ys, passe in resultat.trajets[:40]:
            ax.plot(xs, ys, color="#8e44ad" if passe else "#7f8c8d", lw=0.5, alpha=0.5)
    if route_xy:
        r = np.array(route_xy)
        ax.plot(r[:, 0], r[:, 1], color="#2c3e50", ls=":", lw=1.2, label="Trajet déclaré")
    obs = points[points["statut"] == "observee"]
    sus = points[points["statut"] == "suspecte"]
    ax.plot(obs["x"], obs["y"], ".", color="#1f6fb2", ms=4, label="Positions observées")
    if len(sus):
        ax.plot(sus["x"], sus["y"], "x", color="#e67e22", ms=5, label="Positions suspectes (écartées)")
    for o in externes or []:
        ax.add_patch(plt.Circle((o.x, o.y), o.sigma_km, fill=False, ec="#16a085", ls="--"))
        ax.plot(o.x, o.y, "^", color="#16a085", label="Observation RF simulée")
    xmin, xmax, ymin, ymax = geographie.bornes
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect("equal")
    ax.set_xlabel("Est (km, repère local)")
    ax.set_ylabel("Nord (km, repère local)")
    if resultat is not None:
        ax.plot([], [], color="#8e44ad", label="Trajets reconstruits (estimés) passant par la zone")
        ax.plot([], [], color="#7f8c8d", label="Trajets reconstruits (estimés) hors zone")
    ax.legend(fontsize=7, loc="lower left")
    titre = geographie.nom + (" — données synthétiques" if geographie.fictive else "")
    ax.set_title(titre, fontsize=9)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def construire(contexte: dict) -> dict:
    """Assemble le contenu du rapport (dictionnaire sérialisable)."""
    return {
        "titre": "Rapport d'analyse SeaTrace — aide à l'investigation",
        "genere_le": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "avertissement": (
            "Ce document est une aide à l'investigation. Il ne prouve pas une fraude, ne constitue "
            "pas une certification juridique et ne décide d'aucune sanction ni d'aucun refus "
            "d'indemnisation. Une probabilité de passage n'est pas une probabilité de fraude."
        ),
        **contexte,
    }


def _table(lignes, colonnes):
    tete = "".join(f"<th>{e(c)}</th>" for c in colonnes)
    corps = "".join("<tr>" + "".join(f"<td>{e(l.get(c, ''))}</td>" for c in colonnes) + "</tr>" for l in lignes)
    return f"<table><thead><tr>{tete}</tr></thead><tbody>{corps}</tbody></table>"


def vers_html(rapport: dict, png: bytes | None) -> str:
    r = rapport
    img = ""
    if png:
        img = f'<img alt="Carte" src="data:image/png;base64,{base64.b64encode(png).decode()}"/>'
    alertes = "".join(
        f"<li><b>{e(a['libelle'])}</b> ({e(a['categorie'])}, {e(a['debut'])} → {e(a['fin'])})<br>"
        f"{e(a['explication'])}<br><i>Autres explications possibles : {e('; '.join(a['alternatives']))}</i></li>"
        for a in r["anomalies"]) or "<li>Aucune anomalie détectée par les contrôles.</li>"
    rec = r.get("reconstruction")
    rec_html = "<p>Aucune reconstruction réalisée pour ce rapport.</p>"
    if rec:
        p = rec.get("proba_passage")
        valeur = "non calculée" if p is None else f"{100 * p:.0f} %"
        sens = _table(rec.get("sensibilite", []), ["variation", "proba_passage"])
        hyp = _table([{"hypothèse": k, "valeur": v} for k, v in rec.get("hypotheses", {}).items()],
                     ["hypothèse", "valeur"])
        mc = rec.get("controle_mc") or {}
        rec_html = f"""
        <p><b>Période :</b> {e(rec['periode']['debut'])} → {e(rec['periode']['fin'])}
        ({e(rec['periode']['duree_min'])} min, cause : {e(rec['periode']['cause'])},
        fiabilité des bornes : {e(rec['periode']['fiabilite_bornes'])})</p>
        <p><b>Zone examinée :</b> {e(rec['zone']['nom'])} — {e(rec['zone']['description'])}</p>
        <div class="proba"><span>Probabilité estimée de passage au moins une fois dans la zone :</span>
        <b>{e(valeur)}</b><br><small>{e(STATUTS.get(rec['statut'], rec['statut']))}</small>
        <br><b>Lecture :</b> {e(rec.get('lecture', ''))}</div>
        <p>Signification : estimation sous les hypothèses ci-dessous, à la résolution de la grille.
        <b>Ce n'est pas une probabilité de fraude.</b></p>
        <p><b>Raisons / limites :</b></p><ul>{''.join(f'<li>{e(x)}</li>' for x in rec.get('raisons', [])) or '<li>—</li>'}</ul>
        <p><b>Contrôle Monte Carlo</b> (trajets tirés dans la loi a posteriori) :
        {e(mc.get('n_trajets', '—'))} trajets, proportion passant par la zone
        {e(round(mc.get('proportion_passage', float('nan')), 3) if mc else '—')}
        ± {e(round(mc.get('erreur_type', float('nan')), 3) if mc else '—')} (erreur due au nombre de tirages,
        distincte de l'incertitude liée aux hypothèses ci-dessous).</p>
        <h3>Sensibilité aux hypothèses</h3>{sens}
        <h3>Modèle et hypothèses</h3>{hyp}
        <p><i>Autres explications possibles : {e('; '.join(rec.get('alternatives', [])))}</i></p>
        <p><b>Observations externes :</b> {e(rec.get('observations_externes', 'aucune'))}</p>
        """
    qual = _table([{"indicateur": k, "valeur": v} for k, v in r["qualite"].items()], ["indicateur", "valeur"])
    ecart = r.get("ecart_trajet_prevu")
    ecart_html = "<p>Aucun trajet prévu fourni.</p>" if not ecart else _table(
        [{"indicateur": k, "valeur": v} for k, v in ecart.items()], ["indicateur", "valeur"])
    vers = _table([{"élément": k, "version": v} for k, v in r["versions"].items()], ["élément", "version"])
    dec = r.get("decision_humaine") or {}
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>Rapport SeaTrace {e(r['dossier'])}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1d2a35;line-height:1.45}}
h1{{font-size:1.5rem}} h2{{font-size:1.15rem;border-bottom:1px solid #d5dde5;padding-bottom:4px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:.88rem;margin:8px 0}} td,th{{border:1px solid #d5dde5;padding:4px 6px;text-align:left;vertical-align:top}}
.avert{{background:#fff4e5;border-left:4px solid #e67e22;padding:10px 12px}} .synth{{background:#eef6ff;border-left:4px solid #1f6fb2;padding:10px 12px}}
.proba{{background:#f4ecf7;border:1px solid #c39bd3;padding:10px 12px;font-size:1.05rem;margin:10px 0}}
img{{max-width:100%;border:1px solid #d5dde5}} small{{color:#566573}}
</style></head><body>
<h1>{e(r['titre'])}</h1>
<p class="avert">{e(r['avertissement'])}</p>
{'<p class="synth"><b>Données synthétiques</b> : ' + e(r['mention_synthetique']) + '</p>' if r.get('mention_synthetique') else ''}
<h2>1. Dossier</h2>
<table><tr><td>Identifiant du dossier</td><td>{e(r['dossier'])}</td></tr>
<tr><td>Bateau</td><td>{e(r['bateau']['boat_id'])} — {e(r['bateau'].get('nom', ''))} ({e(r['bateau'].get('type', ''))})</td></tr>
<tr><td>Trajet</td><td>{e(r['trip_id'])}</td></tr>
<tr><td>Période des données</td><td>{e(r['periode_donnees'])}</td></tr>
<tr><td>Sources</td><td>{e(r['sources'])}</td></tr>
<tr><td>Empreinte SHA-256 du fichier analysé</td><td style="word-break:break-all">{e(r['empreinte_fichier'])}</td></tr>
<tr><td>Généré le</td><td>{e(r['genere_le'])}</td></tr></table>
<h2>2. Qualité et complétude des données</h2>{qual}
<p><small>Le score d'anomalie des données ({e(r['score_anomalie'])}/100) mesure l'incohérence des données,
pas la culpabilité de qui que ce soit. Il est présenté séparément de la qualité, de la probabilité de
passage et de la conclusion humaine.</small></p>
<h2>3. Anomalies observées</h2><ul>{alertes}</ul>
<h2>4. Écart au trajet prévu</h2>{ecart_html}
<h2>5. Carte (observations et estimations distinguées)</h2>{img}
<p><small>Points bleus : positions observées. Croix orange : positions suspectes écartées de la
reconstruction (conservées dans les données d'origine). Violet/gris : trajets <b>estimés</b>, qui ne
sont pas le trajet réel.</small></p>
<h2>6. Reconstruction et probabilité de passage</h2>{rec_html}
<h2>7. Versions du modèle, des règles et des paramètres</h2>{vers}
<h2>8. Note et décision de l'analyste</h2>
<p><b>Décision :</b> {e(DECISIONS.get(dec.get('decision', 'non_renseignee'), dec.get('decision')))}</p>
<p><b>Analyste :</b> {e(dec.get('analyste', '—'))} — <b>le</b> {e(dec.get('horodatage', '—'))}</p>
<p><b>Note :</b> {e(dec.get('note', '—'))}</p>
<h2>9. Limites</h2><ul>{''.join(f'<li>{e(x)}</li>' for x in r['limites'])}</ul>
<p><small>Empreinte de ce rapport (JSON) : {e(r.get('empreinte_rapport', '—'))}</small></p>
</body></html>"""


def empreinte_json(rapport: dict) -> str:
    import hashlib
    contenu = {k: v for k, v in rapport.items() if k != "empreinte_rapport"}
    return hashlib.sha256(json.dumps(contenu, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


LIMITES_GENERALES = [
    "Prototype de hackathon : modèle réglé et évalué uniquement sur des données synthétiques ; "
    "il n'est pas calibré sur des données réelles.",
    "La géographie de démonstration et la zone Z1 sont fictives ; aucune conclusion juridique ne peut en être tirée.",
    "Une interruption de réception n'est pas une preuve d'attaque ; une trajectoire inhabituelle n'est pas une preuve de fraude.",
    "Les positions déclarées peuvent être fausses à la source (traceur leurré) : une empreinte ou un journal "
    "chaîné prouve que les données n'ont pas changé depuis l'import, pas qu'elles étaient vraies.",
    "Météo, courants et règles de navigation ne sont pas modélisés.",
    "La présence dans la zone est évaluée à la résolution de la grille (cases de 200 m).",
    "Deux sources reprenant le même signal ne sont pas deux confirmations indépendantes.",
]


# ---------------------------------------------------------------------------
# V2 : rapport du comparatif multi-modèles
# ---------------------------------------------------------------------------

def _pct(v):
    return "—" if v is None else f"{100 * v:.0f} %"


def carte_comparatif_png(run: dict):
    """Carte statique (matplotlib, lat/lon) : chemins de chaque modèle et chemins moyens des familles."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    couleurs = {"hmm": "#6e3ca0", "appris": "#1e78b4", "particules": "#c86414", "pont": "#288c5a"}
    fig, ax = plt.subplots(figsize=(7.5, 6.0))
    for m, r in run["modeles"].items():
        for c in r["chemins"][:60]:
            ax.plot(c["lon"], c["lat"], color=couleurs.get(m, "#888"), lw=0.5, alpha=0.35)
    for f in (run.get("segmentation") or {}).get("familles", []):
        cm = f["chemin_moyen"]
        ax.plot(cm["lon"], cm["lat"], color="black", lw=2.2)
        ax.text(cm["lon"][len(cm["lon"]) // 2], cm["lat"][len(cm["lat"]) // 2], f["identifiant"], fontsize=10,
                weight="bold", bbox={"fc": "white", "ec": "black", "lw": 0.5})
    for m, col in couleurs.items():
        if m in run["modeles"]:
            ax.plot([], [], color=col, label=m)
    ax.legend(fontsize=8, loc="best")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title("Chemins ESTIMÉS par modèle (noir : chemin moyen de chaque famille)", fontsize=9)
    ax.set_aspect(1 / max(np.cos(np.radians(np.mean(ax.get_ylim()))), 0.1))
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return buf.getvalue()


def rapport_comparatif_html(run: dict) -> str:
    c = run["contexte"]
    lignes = []
    for m, r in run["modeles"].items():
        iv = r.get("intervalle")
        lignes.append({"modèle": r["libelle"], "statut": r["statut"], "probabilité": _pct(r["proba_passage"]),
                       "intervalle": f"{_pct(iv[0])} – {_pct(iv[1])}" if iv else "—",
                       "erreur Monte Carlo": "—" if r.get("erreur_mc") is None else f"± {100 * r['erreur_mc']:.0f} %",
                       "chemins": r["nombre_chemins"], "temps (s)": r["temps_calcul_s"],
                       "raisons / limites": " ".join(r["raisons"])})
    tab = _table(lignes, ["modèle", "statut", "probabilité", "intervalle", "erreur Monte Carlo", "chemins",
                          "temps (s)", "raisons / limites"])
    seg = run.get("segmentation") or {}
    fam = []
    for f in seg.get("familles", []):
        d = {"famille": f["identifiant"], "description": f["description"],
             "probabilité commune": _pct(f["probabilite_commune"]),
             "passe par la zone": _pct(f["part_passant_par_zone"])}
        for m, v in f["probabilite_par_modele"].items():
            d[m] = _pct(v)
        fam.append(d)
    cols_fam = ["famille", "description", "probabilité commune", "passe par la zone"] + \
        [m for m in run["modeles"] if run["modeles"][m]["nombre_chemins"]]
    fam_html = _table(fam, cols_fam) if fam else "<p>Aucune famille (pas de chemins).</p>"
    png = carte_comparatif_png(run)
    img = f'<img alt="Carte" src="data:image/png;base64,{base64.b64encode(png).decode()}"/>'
    vers = _table([{"élément": k, "version": v} for k, v in c["versions"].items()], ["élément", "version"])
    dec = run.get("decision_humaine") or {}
    hyp = "".join(f"<h3>{e(r['libelle'])}</h3>" + _table(
        [{"hypothèse": k, "valeur": json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v}
         for k, v in (r.get("hypotheses") or {}).items()], ["hypothèse", "valeur"])
        for r in run["modeles"].values())
    synth = run["synthese"]
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
<title>Rapport SeaTrace {e(run['run_id'])}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1d2a35;line-height:1.45}}
h1{{font-size:1.5rem}} h2{{font-size:1.15rem;border-bottom:1px solid #d5dde5;padding-bottom:4px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:.85rem;margin:8px 0}} td,th{{border:1px solid #d5dde5;padding:4px 6px;text-align:left;vertical-align:top}}
.avert{{background:#fff4e5;border-left:4px solid #e67e22;padding:10px 12px}} .synth{{background:#eef6ff;border-left:4px solid #1f6fb2;padding:10px 12px}}
.proba{{background:#f4ecf7;border:1px solid #c39bd3;padding:10px 12px;font-size:1.05rem;margin:10px 0}}
img{{max-width:100%;border:1px solid #d5dde5}}
</style></head><body>
<h1>Rapport SeaTrace — comparatif des modèles (aide à l'investigation)</h1>
<p class="avert">Ce document est une aide à l'investigation. Il ne prouve pas une fraude, ne constitue pas une
certification juridique et ne décide d'aucune sanction ni d'aucun refus d'indemnisation. Une probabilité de
passage n'est pas une probabilité de fraude.</p>
{'<p class="synth"><b>Données synthétiques</b> : bateaux, trajets, géographie et zone fictifs.</p>' if c.get('synthetique') else ''}
<h2>1. Dossier</h2>
<table><tr><td>Identifiant du calcul</td><td>{e(run['run_id'])}</td></tr>
<tr><td>Jeu de données</td><td>{e(c['jeu_nom'])}</td></tr>
<tr><td>Bateau / trajet</td><td>{e(c['boat_id'])} / {e(c['trip_id'])} ({e(c['type_bateau'])})</td></tr>
<tr><td>Période reconstruite</td><td>{e(c['periode'])} : {e(c['debut'])} → {e(c['fin'])} ({e(c['duree_min'])} min,
cause : {e(c['cause'])}, fiabilité des bornes : {e(c['fiabilite_bornes'])})</td></tr>
<tr><td>Zone examinée</td><td>{e(c.get('zone_nom') or 'aucune')}</td></tr>
<tr><td>Observations externes</td><td>{e(', '.join(c.get('observations_externes') or []) or 'aucune')}</td></tr>
<tr><td>Empreinte SHA-256 du fichier</td><td style="word-break:break-all">{e(c['empreinte_fichier'])}</td></tr>
<tr><td>Généré le</td><td>{e(c['genere_le'])}</td></tr></table>
<h2>2. Lecture commune</h2>
<div class="proba">{e(synth['lecture'])}</div>
<p><small>{e(synth['rappel'])}</small></p>
<h2>3. Comparatif des modèles</h2>{tab}
<h2>4. Familles de chemins (segmentation par ACP)</h2>
<p><small>{e(seg.get('methode', ''))}. Variance expliquée par les premières composantes :
{e(', '.join(f'{100 * v:.0f} %' for v in seg.get('variance_expliquee', [])))}.</small></p>{fam_html}
<h2>5. Carte</h2>{img}
<p><small>Chemins ESTIMÉS (ce ne sont pas des positions observées). Tous les chemins générés sont archivés
avec leur probabilité dans data/chemins/{e(run['run_id'])}/chemins.csv.</small></p>
<h2>6. Hypothèses de chaque modèle</h2>{hyp}
<h2>7. Versions</h2>{vers}
<h2>8. Note et décision de l'analyste</h2>
<p><b>Décision :</b> {e(DECISIONS.get(dec.get('decision', 'non_renseignee'), dec.get('decision')))}
— <b>Analyste :</b> {e(dec.get('analyste', '—'))} — <b>le</b> {e(dec.get('horodatage', '—'))}</p>
<p><b>Note :</b> {e(dec.get('note', '—'))}</p>
<h2>9. Limites</h2><ul>{''.join(f'<li>{e(x)}</li>' for x in LIMITES_GENERALES + LIMITES_V2)}</ul>
</body></html>"""


LIMITES_V2 = [
    "Les cinq modèles reposent sur des hypothèses différentes ; leur accord n'est pas une preuve, leur désaccord "
    "signale que l'estimation dépend des hypothèses.",
    "Le modèle appris favorise les routes habituelles de son historique ; un trajet inhabituel y est jugé moins probable.",
    "Le boosting n'est valable que dans le domaine de ses exemples d'entraînement (synthétiques dans ce prototype).",
    "Le pont brownien ignore l'inertie et ne contourne les terres que par rejet de chemins.",
]
