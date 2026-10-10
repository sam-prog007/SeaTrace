/* SeaTrace V2 — interface (aucune donnée n'est insérée comme HTML : textContent uniquement). */
"use strict";

const COULEURS = { hmm: "#6e3ca0", appris: "#1e78b4", particules: "#c86414", pont: "#288c5a", boosting: "#7f8c8d" };
const COULEURS_FAMILLES = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#999999", "#66c2a5", "#ffd92f"];
const S = { etat: null, jeu: null, trajet: null, analyse: null, run: null, dessin: null, graphiques: [], cheminSel: null };

// ---------------------------------------------------------------- utilitaires
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...enfants) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of enfants.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return e;
}
// Les infobulles Leaflet interprètent les chaînes comme du HTML : on leur passe toujours un nœud texte.
const tip = (texte) => el("span", {}, String(texte));
const vider = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const pct = (v) => (v === null || v === undefined) ? "—" : `${Math.round(100 * v)} %`;
function table(colonnes, lignes, surClic) {
  const t = el("table", {}, el("thead", {}, el("tr", {}, colonnes.map((c) => el("th", {}, c.titre)))));
  const tb = el("tbody");
  lignes.forEach((l) => {
    const tr = el("tr", surClic ? { class: "cliquable", onclick: () => surClic(l, tr) } : {},
      colonnes.map((c) => el("td", {}, c.f ? c.f(l) : String(l[c.cle] ?? "—"))));
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}
async function api(url, opts = {}) {
  const o = { ...opts, headers: { ...(opts.headers || {}) } };
  if (o.method && o.method !== "GET") o.headers["X-SeaTrace"] = "1";
  if (o.json !== undefined) { o.body = JSON.stringify(o.json); o.headers["Content-Type"] = "application/json"; delete o.json; }
  const r = await fetch(url, o);
  let d = null;
  try { d = await r.json(); } catch (_) { d = null; }
  if (!r.ok) throw new Error((d && d.erreur) || `Erreur ${r.status}`);
  return d;
}
function msg(id, texte, classe) { const m = $(id); m.textContent = texte || ""; m.className = "msg " + (classe || ""); }

// ---------------------------------------------------------------- carte
const carte = L.map("carte", { zoomControl: true, preferCanvas: true, doubleClickZoom: false });
const fondOSM = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18, attribution: "© contributeurs OpenStreetMap (ODbL)" });
const couches = {
  geo: L.layerGroup().addTo(carte), points: L.layerGroup().addTo(carte), route: L.layerGroup().addTo(carte),
  bornes: L.layerGroup().addTo(carte), familles: L.layerGroup().addTo(carte), surbrillance: L.layerGroup().addTo(carte),
  dessin: L.layerGroup().addTo(carte),
};
const couchesModeles = {}; // modele -> {chemins, occupation}
let controle = L.control.layers({ "Fond neutre (hors ligne)": L.layerGroup(), "OpenStreetMap (en ligne)": fondOSM }, {
  "Terres et zones": couches.geo, "Positions": couches.points, "Trajet déclaré": couches.route,
  "Chemins moyens des familles": couches.familles,
}, { collapsed: true }).addTo(carte);
carte.setView([47.27, -7.47], 11);

function dessinerGeo(g) {
  couches.geo.clearLayers();
  g.terres.forEach((t) => L.polygon(t.polygone, { color: "#a89f88", weight: 1, fillColor: "#d9d3c3", fillOpacity: 0.9 })
    .bindTooltip(tip(t.nom)).addTo(couches.geo));
  g.zones.forEach((z) => L.polygon(z.polygone, { color: "#c0392b", weight: 1.5, dashArray: "5 4", fillOpacity: 0.12 })
    .bindTooltip(tip(`${z.id} · ${z.nom}`)).addTo(couches.geo));
}

function dessinerTrajet(a) {
  couches.points.clearLayers(); couches.route.clearLayers(); couches.bornes.clearLayers();
  const obs = a.points.filter((p) => p.statut === "observee");
  L.polyline(obs.map((p) => [p.lat, p.lon]), { color: "#1f6fb2", weight: 1.2, opacity: 0.6 }).addTo(couches.points);
  a.points.forEach((p) => {
    const susp = p.statut !== "observee";
    L.circleMarker([p.lat, p.lon], { radius: susp ? 4 : 2.5, color: susp ? "#e67e22" : "#1f6fb2", weight: susp ? 2 : 1,
      fillOpacity: susp ? 0 : 0.8 }).bindTooltip(tip(`${p.t} · ${susp ? "suspecte (écartée)" : "observée"}` +
      (p.sog !== null ? ` · ${p.sog} nd déclarés` : ""))).addTo(couches.points);
  });
  if (a.route_declaree) L.polyline(a.route_declaree, { color: "#566573", dashArray: "6 6", weight: 1.5 })
    .bindTooltip(tip("Trajet déclaré")).addTo(couches.route);
  const pts = a.points.map((p) => [p.lat, p.lon]);
  a.geographie.terres.concat(a.geographie.zones).forEach((x) => pts.push(...x.polygone.slice(0, 1)));
  if (pts.length) carte.fitBounds(L.latLngBounds(a.points.map((p) => [p.lat, p.lon])).pad(0.15));
}

function effacerRun() {
  for (const m of Object.keys(couchesModeles)) {
    controle.removeLayer(couchesModeles[m].chemins); controle.removeLayer(couchesModeles[m].occupation);
    carte.removeLayer(couchesModeles[m].chemins); carte.removeLayer(couchesModeles[m].occupation);
    delete couchesModeles[m];
  }
  couches.familles.clearLayers(); couches.surbrillance.clearLayers();
}

function dessinerRun(run) {
  effacerRun();
  for (const [m, r] of Object.entries(run.modeles)) {
    const ch = L.layerGroup(), oc = L.layerGroup();
    const pmax = Math.max(...r.chemins.map((c) => c.poids), 1e-9);
    r.chemins.forEach((c) => {
      L.polyline(c.lat.map((la, i) => [la, c.lon[i]]), { color: COULEURS[m], weight: 1,
        opacity: Math.min(0.85, 0.12 + 0.6 * c.poids / pmax) })
        .bindTooltip(tip(`${c.id} · famille ${c.famille || "—"} · poids ${(100 * c.poids).toFixed(2)} %` +
          (c.passe_zone === null ? "" : (c.passe_zone ? " · traverse la zone" : " · hors zone"))))
        .on("click", () => surlignerChemin(m, c)).addTo(ch);
    });
    if (r.occupation_png) L.imageOverlay(r.occupation_png, run.bornes_grille, { opacity: 0.7, interactive: false }).addTo(oc);
    couchesModeles[m] = { chemins: ch, occupation: oc };
    if (r.chemins.length) {
      controle.addOverlay(ch, `<span style="color:${COULEURS[m]}">■</span> Chemins ${m}`);
      controle.addOverlay(oc, `Corridors ${m}`);
    }
  }
  // par défaut : chemins du HMM (ou du premier modèle disponible) et corridors des particules
  const premier = ["hmm", "particules", "appris", "pont"].find((m) => run.modeles[m] && run.modeles[m].chemins.length);
  if (premier) couchesModeles[premier].chemins.addTo(carte);
  const fam = (run.segmentation || {}).familles || [];
  fam.forEach((f, i) => {
    const pts = f.chemin_moyen.lat.map((la, k) => [la, f.chemin_moyen.lon[k]]);
    L.polyline(pts, { color: "#000", weight: 4, opacity: 0.9 }).addTo(couches.familles);
    L.polyline(pts, { color: COULEURS_FAMILLES[i % 10], weight: 2.5, opacity: 1 })
      .bindTooltip(tip(`${f.identifiant} · ${f.description} · ${pct(f.probabilite_commune)}`)).addTo(couches.familles);
    const mil = pts[Math.floor(pts.length / 2)];
    L.marker(mil, { icon: L.divIcon({ className: "", html: "", iconSize: [0, 0] }) }).addTo(couches.familles)
      .bindTooltip(tip(f.identifiant), { permanent: true, direction: "center", className: "etiquette-famille" });
  });
}

function surlignerChemin(m, c) {
  couches.surbrillance.clearLayers();
  S.cheminSel = c.id;
  L.polyline(c.lat.map((la, i) => [la, c.lon[i]]), { color: "#000", weight: 5, opacity: 0.5 }).addTo(couches.surbrillance);
  L.polyline(c.lat.map((la, i) => [la, c.lon[i]]), { color: COULEURS[m], weight: 3 })
    .bindTooltip(tip(`${c.id} · poids ${(100 * c.poids).toFixed(2)} %`)).addTo(couches.surbrillance);
}

// ---------------------------------------------------------------- dessin de zone
$("btn-dessin").addEventListener("click", () => {
  if (S.dessin) { S.dessin = null; couches.dessin.clearLayers(); $("btn-dessin").textContent = "Dessiner une zone"; return; }
  if (!S.jeu) return;
  S.dessin = []; couches.dessin.clearLayers();
  $("btn-dessin").textContent = "Annuler le dessin";
  msg("couche-msg", "Cliquez pour placer les sommets, double-cliquez pour fermer la zone.");
});
carte.on("click", (ev) => {
  if (!S.dessin) return;
  S.dessin.push([ev.latlng.lat, ev.latlng.lng]);
  couches.dessin.clearLayers();
  L.polyline(S.dessin, { color: "#c0392b", dashArray: "4 4" }).addTo(couches.dessin);
});
carte.on("dblclick", async () => {
  if (!S.dessin) return;
  const poly = S.dessin; S.dessin = null; couches.dessin.clearLayers(); $("btn-dessin").textContent = "Dessiner une zone";
  if (poly.length < 3) { msg("couche-msg", "Au moins trois sommets.", "erreur"); return; }
  try {
    const d = await api(`/api/jeux/${S.jeu}/zones`, { method: "POST", json: { polygone: poly } });
    msg("couche-msg", `Zone ${d.id} ajoutée (dessinée par l'analyste, aucune valeur réglementaire).`, "ok");
    await chargerTrajet(d.id);
  } catch (e) { msg("couche-msg", e.message, "erreur"); }
});

// ---------------------------------------------------------------- chargement
async function init() {
  S.etat = await api("/api/etat");
  const v = S.etat.versions;
  $("versions").textContent = `logiciel ${v.logiciel} · modèles ${v.modeles_v2} · paramètres ${v.parametres} (${v.empreinte_parametres})`;
  const sel = vider($("jeu"));
  S.etat.jeux.forEach((j) => sel.appendChild(el("option", { value: j.id }, `${j.nom} — ${j.trajets} trajets`)));
  const types = vider($("type"));
  Object.entries(S.etat.types_bateau).forEach(([k, lib]) => types.appendChild(el("option", { value: k }, lib)));
  const box = vider($("modeles"));
  S.etat.modeles.forEach((m) => box.appendChild(el("label", { class: "case" },
    el("input", { type: "checkbox", value: m.id, checked: true }),
    el("span", { class: "puce", style: `background:${COULEURS[m.id]}` }), m.libelle)));
  $("courant-u").value = S.etat.courant_kmh[0]; $("courant-v").value = S.etat.courant_kmh[1];
  await chargerJeu(sel.value);
  chargerSources(); chargerEvaluation();
}

async function chargerJeu(jid) {
  S.jeu = jid;
  const d = await api(`/api/jeux/${jid}/trajets`);
  const sel = vider($("trajet"));
  d.trajets.forEach((t) => sel.appendChild(el("option", { value: t.trip_id },
    `${t.trip_id} · ${t.nom || t.boat_id} · ${t.positions} pos.${t.description ? " — " + t.description.slice(0, 60) : ""}`)));
  const b = $("bandeau");
  if (d.origine === "demonstration_synthetique") {
    b.textContent = "Données synthétiques et géographie fictive : bateaux, côte, îles et zone Z1 sont inventés pour la démonstration ; les manipulations ont été simulées sur une copie locale.";
    b.className = "bandeau";
  } else if (d.origine === "ais_ouvert") {
    b.textContent = "Données AIS ouvertes (positions réelles). Aucune terre ni zone n'est connue tant que vous n'importez pas de GeoJSON (OSM, Natural Earth, Shom, CNSP…) ou ne dessinez pas de zone. Les modèles ne sont calibrés que sur des données synthétiques.";
    b.className = "bandeau alerte";
  } else { b.textContent = "Données importées. " + (d.avertissements || []).join(" "); b.className = "bandeau alerte"; }
  if (sel.options.length) await chargerTrajet();
}

async function chargerTrajet(zoneParDefaut) {
  S.trajet = $("trajet").value;
  const type = $("type").value;
  let a;
  try { a = await api(`/api/jeux/${S.jeu}/trajets/${encodeURIComponent(S.trajet)}?type=${encodeURIComponent(type)}`); }
  catch (e) { vider($("p-anomalies")).appendChild(el("p", { class: "msg erreur" }, e.message)); return; }
  S.analyse = a;
  $("lab-type").classList.toggle("cache", a.type_connu);
  if (!a.type_connu) $("type").value = a.type_bateau;
  vider($("resume-trajet")).appendChild(el("span", {}, `${a.boat_id} ${a.nom ? "· " + a.nom : ""} · ${a.libelle_type}`));
  dessinerGeo(a.geographie); dessinerTrajet(a); effacerRun();
  if (a.geographie.terres.length || a.geographie.zones.length) {
    carte.fitBounds(L.latLngBounds(a.points.map((p) => [p.lat, p.lon])).pad(0.4));
  }
  // périodes
  const per = vider($("periodes"));
  if (!a.periodes.length) per.appendChild(el("p", { class: "aide" }, "Aucune période manquante ou suspecte sur ce trajet."));
  a.periodes.forEach((p, i) => per.appendChild(el("label", {},
    el("input", { type: "radio", name: "periode", value: p.id, checked: i === a.periodes.reduce((b, q, j, arr) => q.duree_min > arr[b].duree_min ? j : b, 0) }),
    el("span", {}, `${p.id} · ${p.debut} → ${p.fin} · ${p.duree_min} min · ${({ interruption: "interruption", positions_suspectes: "positions suspectes", mixte: "mixte" })[p.cause]}` +
      (p.fiabilite_bornes === "reduite" ? " · bornes peu fiables" : "")))));
  const zs = vider($("zone"));
  zs.appendChild(el("option", { value: "" }, "— aucune (corridors et familles seulement) —"));
  a.geographie.zones.forEach((z) => zs.appendChild(el("option", { value: z.id }, `${z.id} · ${z.nom}`)));
  if (zoneParDefaut) zs.value = zoneParDefaut; else if (a.geographie.zones.length) zs.value = a.geographie.zones[0].id;
  majDescZone();
  afficherAnomalies(a);
  ["p-comparatif", "p-familles", "p-chemins", "p-decision"].forEach((id) =>
    vider($(id)).appendChild(el("p", { class: "vide" }, "Lancez une reconstruction.")));
}
function majDescZone() {
  const z = (S.analyse.geographie.zones || []).find((x) => x.id === $("zone").value);
  $("desc-zone").textContent = z ? `${z.description} (source : ${z.source})` : "";
}

function afficherAnomalies(a) {
  const p = vider($("p-anomalies"));
  const q = a.qualite;
  p.appendChild(el("div", { class: "kpis" },
    el("div", { class: "kpi" }, el("b", {}, `${a.score_anomalie}/100`), el("span", {}, "score d'anomalie des données (pas une culpabilité)")),
    el("div", { class: "kpi" }, el("b", {}, `${q.completude_pct ?? "—"} %`), el("span", {}, "complétude")),
    el("div", { class: "kpi" }, el("b", {}, `${q.points_suspects ?? 0} / ${q.points}`), el("span", {}, "positions suspectes écartées")),
    el("div", { class: "kpi" }, el("b", {}, a.ecart_trajet_prevu ? `${a.ecart_trajet_prevu.ecart_max_km} km` : "—"), el("span", {}, "écart max. au trajet déclaré"))));
  if (a.geographie.sans_terres) p.appendChild(el("div", { class: "encadre alerte" },
    "Aucune carte des terres pour ces données : les modèles ignorent les obstacles. Importez un GeoJSON de terres (OSM land polygons, Natural Earth) dans la section 3."));
  if (!a.alertes.length) p.appendChild(el("div", { class: "encadre" }, "Aucune anomalie détectée par les contrôles. Cela ne garantit pas que les données sont vraies."));
  a.alertes.forEach((x) => p.appendChild(el("div", { class: "alerte-item" },
    el("b", {}, `${x.id} · ${x.libelle}`), el("span", { class: "tag" + (x.categorie === "integrite" ? "" : " dispo") },
      x.categorie === "integrite" ? "intégrité" : "disponibilité"),
    el("span", { class: "aide" }, ` ${x.debut} → ${x.fin}`),
    el("div", {}, x.explication),
    el("div", { class: "aide" }, "Autres explications possibles : " + x.alternatives.join(" ; ")))));
}

// ---------------------------------------------------------------- reconstruction
async function reconstruire() {
  const per = document.querySelector("input[name=periode]:checked");
  if (!per) { msg("calcul-msg", "Choisissez une période.", "erreur"); return; }
  const modeles = [...document.querySelectorAll("#modeles input:checked")].map((i) => i.value);
  if (!modeles.length) { msg("calcul-msg", "Choisissez au moins un modèle.", "erreur"); return; }
  $("btn-reconstruire").disabled = true;
  msg("calcul-msg", "Calcul en cours (5 modèles + sensibilité + segmentation : quelques secondes)…");
  try {
    const run = await api("/api/reconstruire", { method: "POST", json: {
      jeu: S.jeu, trip: S.trajet, type: $("type").value, periode: per.value, zone: $("zone").value || null,
      modeles, externes: $("externes").checked,
      courant: [parseFloat($("courant-u").value) || 0, parseFloat($("courant-v").value) || 0] } });
    S.run = run;
    dessinerRun(run);
    afficherComparatif(run); afficherFamilles(run); afficherChemins(run); afficherDecision(run);
    msg("calcul-msg", `Terminé en ${run.temps_total_s.toFixed(1)} s · calcul ${run.run_id} archivé (tous les chemins et leur probabilité).`, "ok");
    activerOnglet("comparatif");
  } catch (e) { msg("calcul-msg", e.message, "erreur"); }
  finally { $("btn-reconstruire").disabled = false; }
}

function detruireGraphiques() { S.graphiques.forEach((g) => g.destroy()); S.graphiques = []; }

function afficherComparatif(run) {
  const p = vider($("p-comparatif"));
  const s = run.synthese;
  const classe = s.code === "divergence" ? "encadre alerte" : (s.code === "aucune" ? "encadre erreur" : "encadre");
  p.appendChild(el("div", { class: classe }, el("b", {}, "Lecture commune : "), s.lecture));
  p.appendChild(el("p", { class: "aide" }, s.rappel));
  const lignes = Object.values(run.modeles);
  p.appendChild(table([
    { titre: "Modèle", f: (r) => el("span", {}, el("span", { class: "puce", style: `background:${COULEURS[r.modele]}` }), r.libelle) },
    { titre: "Probabilité de passage", f: (r) => r.statut === "ok" ? pct(r.proba_passage) : "échec" },
    { titre: "Intervalle (sensibilité / ensemble)", f: (r) => r.intervalle ? `${pct(r.intervalle[0])} – ${pct(r.intervalle[1])}` : "—" },
    { titre: "Erreur Monte Carlo", f: (r) => r.erreur_mc === null ? "—" : `± ${Math.round(100 * r.erreur_mc)} %` },
    { titre: "Chemins", f: (r) => String(r.nombre_chemins) },
    { titre: "Temps", f: (r) => `${r.temps_calcul_s} s` },
    { titre: "Raisons et limites", f: (r) => r.raisons.join(" ") || "—" },
  ], lignes));
  const cadre = el("div", { class: "grille2" });
  const c1 = el("canvas"), d1 = el("div", { class: "graph" }, c1);
  cadre.appendChild(d1);
  const diag = el("div", {}, el("h3", {}, "Diagnostics propres à chaque modèle"));
  lignes.forEach((r) => {
    const d = { ...r.diagnostics }; delete d.sensibilite; delete d.descripteurs; delete d.controle_mc;
    diag.appendChild(el("details", {}, el("summary", {}, r.libelle),
      el("pre", { style: "white-space:pre-wrap;font-size:.75rem" }, JSON.stringify({ diagnostics: d, hypotheses: r.hypotheses }, null, 1))));
    if (r.diagnostics.sensibilite) {
      diag.appendChild(el("details", {}, el("summary", {}, "Sensibilité du HMM aux hypothèses"),
        table([{ titre: "Variation", cle: "variation" }, { titre: "Probabilité", f: (l) => l.proba_passage === null ? "impossible" : pct(l.proba_passage) }],
          r.diagnostics.sensibilite)));
    }
  });
  cadre.appendChild(diag);
  p.appendChild(cadre);
  detruireGraphiques();
  const ok = lignes.filter((r) => r.statut === "ok" && r.proba_passage !== null);
  if (ok.length) {
    S.graphiques.push(new Chart(c1, {
      type: "bar",
      data: { labels: ok.map((r) => r.modele), datasets: [
        { label: "Probabilité de passage", data: ok.map((r) => 100 * r.proba_passage), backgroundColor: ok.map((r) => COULEURS[r.modele]) },
        { label: "Intervalle", type: "bar", data: ok.map((r) => r.intervalle ? [100 * r.intervalle[0], 100 * r.intervalle[1]] : null),
          backgroundColor: "rgba(0,0,0,0.18)", barPercentage: 0.3 } ] },
      options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100, title: { display: true, text: "%" } } },
        plugins: { title: { display: true, text: "Probabilité estimée de passage dans la zone, par modèle" } } },
    }));
  }
}

function afficherFamilles(run) {
  const p = vider($("p-familles"));
  const seg = run.segmentation;
  if (!seg || !seg.familles) { p.appendChild(el("p", { class: "vide" }, seg && seg.erreur ? seg.erreur : "Aucun chemin à segmenter.")); return; }
  p.appendChild(el("p", { class: "aide" }, `${seg.methode}. ${seg.nombre_familles} famille(s) ; variance expliquée : ` +
    seg.variance_expliquee.map((v) => `${Math.round(100 * v)} %`).join(", ") + ` ; silhouettes : ${JSON.stringify(seg.silhouettes)}.`));
  const mods = Object.keys(run.modeles).filter((m) => run.modeles[m].nombre_chemins);
  p.appendChild(table([
    { titre: "Famille", f: (f) => el("span", {}, el("span", { class: "puce", style: `background:${COULEURS_FAMILLES[(parseInt(f.identifiant.slice(1)) - 1) % 10]}` }), f.identifiant) },
    { titre: "Description", cle: "description" },
    { titre: "Probabilité commune", f: (f) => pct(f.probabilite_commune) },
    ...mods.map((m) => ({ titre: `P(${m})`, f: (f) => pct(f.probabilite_par_modele[m]) })),
    { titre: "Passe par la zone", f: (f) => pct(f.part_passant_par_zone) },
    { titre: "Chemins", cle: "nombre_chemins" },
  ], seg.familles));
  const g = el("div", { class: "grille2" });
  const c1 = el("canvas"), c2 = el("canvas");
  g.appendChild(el("div", { class: "graph" }, c1)); g.appendChild(el("div", { class: "graph" }, c2));
  p.appendChild(g);
  const formes = { hmm: "circle", appris: "rect", particules: "triangle", pont: "rectRot" };
  const parFam = {};
  seg.nuage_acp.forEach((pt) => { (parFam[pt.famille + "|" + pt.modele] ||= []).push({ x: pt.c1, y: pt.c2 }); });
  const ds = Object.entries(parFam).map(([k, pts]) => {
    const [f, m] = k.split("|");
    return { label: `${f} · ${m}`, data: pts, pointStyle: formes[m] || "circle", pointRadius: 2.5,
      backgroundColor: COULEURS_FAMILLES[(parseInt(f.slice(1)) - 1) % 10] };
  });
  S.graphiques.push(new Chart(c1, { type: "scatter", data: { datasets: ds }, options: { responsive: true, maintainAspectRatio: false,
    animation: false, scales: { x: { title: { display: true, text: "composante 1" } }, y: { title: { display: true, text: "composante 2" } } },
    plugins: { legend: { display: ds.length <= 12, labels: { boxWidth: 8, font: { size: 9 } } },
      title: { display: true, text: "Chemins dans le plan des 2 premières composantes (couleur = famille, forme = modèle)" } } } }));
  S.graphiques.push(new Chart(c2, { type: "bar", data: { labels: seg.familles.map((f) => f.identifiant),
    datasets: mods.map((m) => ({ label: m, data: seg.familles.map((f) => 100 * (f.probabilite_par_modele[m] || 0)), backgroundColor: COULEURS[m] })) },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100, title: { display: true, text: "%" } } },
      plugins: { title: { display: true, text: "Probabilité de chaque famille selon chaque modèle" } } } }));
}

function afficherChemins(run) {
  const p = vider($("p-chemins"));
  p.appendChild(el("p", { class: "aide" },
    "Tous les chemins générés sont conservés avec leur probabilité. HMM et modèle appris : chemins identiques fusionnés, " +
    "poids = part des tirages, log-proba = probabilité a posteriori exacte du chemin d'états sur la grille. Particules et pont " +
    "brownien : tirages équiprobables de la loi a posteriori (la probabilité utile est celle de la famille). Cliquez sur une ligne pour voir le chemin."));
  const filtres = el("div", { class: "ligne" });
  const sm = el("select", {}, el("option", { value: "" }, "tous les modèles"),
    ...Object.keys(run.modeles).filter((m) => run.modeles[m].nombre_chemins).map((m) => el("option", { value: m }, m)));
  const fams = ((run.segmentation || {}).familles || []).map((f) => f.identifiant);
  const sf = el("select", {}, el("option", { value: "" }, "toutes les familles"), ...fams.map((f) => el("option", { value: f }, f)));
  filtres.appendChild(el("label", {}, "Modèle ", sm)); filtres.appendChild(el("label", {}, "Famille ", sf));
  p.appendChild(filtres);
  const zone = el("div");
  p.appendChild(zone);
  const maj = () => {
    const lignes = [];
    for (const [m, r] of Object.entries(run.modeles)) {
      if (sm.value && sm.value !== m) continue;
      r.chemins.forEach((c) => { if (!sf.value || c.famille === sf.value) lignes.push({ ...c, modele: m }); });
    }
    lignes.sort((a, b) => b.poids - a.poids);
    vider(zone).appendChild(el("p", { class: "aide" }, `${lignes.length} chemins${lignes.length > 400 ? " (400 premiers affichés ; tous dans l'export CSV)" : ""}.`));
    zone.appendChild(table([
      { titre: "Chemin", cle: "id" }, { titre: "Modèle", cle: "modele" }, { titre: "Famille", f: (c) => c.famille || "—" },
      { titre: "Poids (probabilité)", f: (c) => `${(100 * c.poids).toFixed(2)} %` },
      { titre: "Tirages", cle: "occurrences" },
      { titre: "log-proba exacte", f: (c) => c.log_proba === null ? "—" : c.log_proba.toFixed(2) },
      { titre: "Traverse la zone", f: (c) => c.passe_zone === null ? "—" : (c.passe_zone ? "oui" : "non") },
    ], lignes.slice(0, 400), (c, tr) => {
      zone.querySelectorAll("tr.selection").forEach((x) => x.classList.remove("selection"));
      tr.classList.add("selection"); surlignerChemin(c.modele, c);
    }));
  };
  sm.addEventListener("change", maj); sf.addEventListener("change", maj); maj();
}

function afficherDecision(run) {
  const p = vider($("p-decision"));
  p.appendChild(el("h3", {}, "Exports"));
  p.appendChild(el("p", {},
    el("a", { href: `/api/runs/${run.run_id}/chemins.csv` }, "Tous les chemins + probabilités (CSV)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/chemins.geojson` }, "Chemins (GeoJSON)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/resume.json` }, "Résumé (JSON)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/rapport.html` }, "Rapport HTML autonome")));
  p.appendChild(el("p", { class: "aide" }, `Archivé localement dans data/chemins/${run.run_id}/. Les exports tableur sont protégés contre l'injection de formules.`));
  p.appendChild(el("h3", {}, "Note et décision de l'analyste"));
  p.appendChild(el("p", { class: "aide" }, "SeaTrace ne décide jamais d'une sanction ni d'un refus d'indemnisation. La décision porte uniquement sur la suite de l'investigation."));
  const sel = el("select", {}, el("option", { value: "aucune" }, "Pas d'investigation complémentaire"),
    el("option", { value: "informations" }, "Demander des informations complémentaires"),
    el("option", { value: "investigation" }, "Ouvrir une investigation approfondie"));
  const ini = el("input", { type: "text", maxlength: "12" });
  const note = el("textarea", { maxlength: "1000", rows: "3" });
  const m = el("div", { class: "msg" });
  p.appendChild(el("label", {}, "Décision", sel)); p.appendChild(el("label", {}, "Initiales", ini)); p.appendChild(el("label", {}, "Note", note));
  p.appendChild(el("button", { onclick: async () => {
    try { await api(`/api/runs/${run.run_id}/decision`, { method: "POST", json: { decision: sel.value, analyste: ini.value, note: note.value } });
      m.textContent = "Décision enregistrée et journalisée (elle figure dans le rapport HTML)."; m.className = "msg ok"; }
    catch (e) { m.textContent = e.message; m.className = "msg erreur"; } } }, "Enregistrer"));
  p.appendChild(m);
}

// ---------------------------------------------------------------- évaluation, sources, journal
async function chargerEvaluation() {
  const p = vider($("p-evaluation"));
  const d = await api("/api/evaluation");
  const c = d.comparatif;
  if (!c) { p.appendChild(el("p", { class: "vide" }, "Aucune évaluation : lancer python eval/comparer_modeles.py")); return; }
  p.appendChild(el("div", { class: "encadre alerte" }, c.avertissement));
  p.appendChild(el("p", {}, `${c.interruptions_evaluation} interruptions sur des bateaux jamais vus ; ${pct(c.part_passages_reels)} de passages réels (${c.note_echantillon}).`));
  const lignes = Object.entries(c.modeles).map(([m, b]) => ({ m, ...b }));
  p.appendChild(table([
    { titre: "Modèle", f: (l) => el("span", {}, el("span", { class: "puce", style: `background:${COULEURS[l.m]}` }), l.m) },
    { titre: "Brier (↓)", f: (l) => String(l.brier ?? "—") },
    { titre: "IC 95 % (bootstrap)", f: (l) => { const b = ((c.bootstrap || {}).par_modele || {})[l.m]; return b ? `${b.ic95_brier[0]} – ${b.ic95_brier[1]}` : "—"; } },
    { titre: "Écart au meilleur, IC 95 %", f: (l) => { const b = ((c.bootstrap || {}).par_modele || {})[l.m]; return !b ? "—" : (l.m === c.bootstrap.meilleur_brier ? "meilleur" : `${b.ecart_au_meilleur_ic95[0]} – ${b.ecart_au_meilleur_ic95[1]}`); } },
    { titre: "Log-loss (↓)", f: (l) => String(l.log_loss ?? "—") },
    { titre: "Brier (échec = 0,5)", f: (l) => String(l["brier_echec_0.5"]) },
    { titre: "Échecs", f: (l) => String(l.echecs) },
    { titre: "Erreur spatiale (km)", f: (l) => String(l.erreur_spatiale_km ?? "—") },
    { titre: "Couverture 90 %", f: (l) => l.couverture_region_90pct === undefined ? "—" : pct(l.couverture_region_90pct) },
    { titre: "Temps médian", f: (l) => `${l.temps_median_s} s` },
  ], lignes));
  if (c.note_bootstrap) p.appendChild(el("p", { class: "aide" }, "Bootstrap apparié : " + c.note_bootstrap + "."));
  p.appendChild(el("p", { class: "aide" }, `Références : interpolation linéaire Brier ${c.references.interpolation_lineaire.brier} ; taux de base Brier ${c.references.taux_de_base.brier}. ` +
    `Poids de densité du modèle appris réglé à ${c.reglage_appris.poids_densite_retenu}.`));
  p.appendChild(el("img", { src: "/api/evaluation/comparatif.png", alt: "Calibration des modèles", style: "max-width:480px;width:100%" }));
  if (c.boosting && c.boosting.importances) {
    p.appendChild(el("h3", {}, "Boosting : importance des descripteurs"));
    p.appendChild(table([{ titre: "Descripteur", cle: "k" }, { titre: "Importance", cle: "v" }],
      Object.entries(c.boosting.importances).map(([k, v]) => ({ k, v }))));
  }
}

async function chargerSources() {
  const p = vider($("p-sources"));
  const d = await api("/api/sources");
  const lib = { integre: "intégré", import: "import manuel", feuille: "feuille de route", commercial: "commercial (contrat)" };
  p.appendChild(el("p", { class: "aide" }, "Statut réel dans le prototype. Les licences sont à vérifier sur le site de chaque source avant tout usage, en particulier commercial."));
  p.appendChild(table([
    { titre: "Source", f: (s) => s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.nom) : s.nom },
    { titre: "Catégorie", cle: "categorie" }, { titre: "Statut", f: (s) => lib[s.statut] || s.statut },
    { titre: "Format", cle: "format" }, { titre: "Usage dans SeaTrace", cle: "usage" }, { titre: "Licence (à vérifier)", cle: "licence" },
  ], d));
}

async function chargerJournal() {
  const p = vider($("p-journal"));
  const d = await api("/api/journal");
  p.appendChild(el("div", { class: d.integre ? "encadre" : "encadre erreur" }, `Vérification du chaînage SHA-256 : ${d.message}`));
  p.appendChild(table([{ titre: "Horodatage", cle: "horodatage" }, { titre: "Événement", cle: "type" },
    { titre: "Détails", f: (e) => JSON.stringify(e.details) }], d.entrees));
}

// ---------------------------------------------------------------- événements
function activerOnglet(nom) {
  document.querySelectorAll("#onglets button").forEach((b) => b.classList.toggle("actif", b.dataset.onglet === nom));
  document.querySelectorAll(".panneau").forEach((x) => x.classList.toggle("actif", x.id === "p-" + nom));
  if (nom === "journal") chargerJournal();
}
$("onglets").addEventListener("click", (ev) => { if (ev.target.dataset.onglet) activerOnglet(ev.target.dataset.onglet); });
$("jeu").addEventListener("change", (ev) => chargerJeu(ev.target.value));
$("trajet").addEventListener("change", () => chargerTrajet());
$("type").addEventListener("change", () => chargerTrajet());
$("zone").addEventListener("change", majDescZone);
$("btn-reconstruire").addEventListener("click", reconstruire);
$("btn-import").addEventListener("click", async () => {
  const f = $("fichier").files[0];
  if (!f) { msg("import-msg", "Choisissez un fichier.", "erreur"); return; }
  const fd = new FormData(); fd.append("fichier", f);
  msg("import-msg", "Import et validation…");
  try {
    const d = await api("/api/jeux", { method: "POST", body: fd });
    msg("import-msg", `Importé (${d.format}) : ${d.lignes} lignes, ${d.rejetees} rejetées. ${(d.avertissements || []).join(" ")}`, "ok");
    S.etat = await api("/api/etat");
    const sel = vider($("jeu"));
    S.etat.jeux.forEach((j) => sel.appendChild(el("option", { value: j.id }, `${j.nom} — ${j.trajets} trajets`)));
    sel.value = d.id; await chargerJeu(d.id);
  } catch (e) { msg("import-msg", "Import refusé : " + e.message, "erreur"); }
});
$("btn-couche").addEventListener("click", async () => {
  const f = $("fichier-couche").files[0];
  if (!f || !S.jeu) { msg("couche-msg", "Choisissez un fichier.", "erreur"); return; }
  const fd = new FormData(); fd.append("fichier", f); fd.append("role", $("role-couche").value);
  try {
    const d = await api(`/api/jeux/${S.jeu}/couches`, { method: "POST", body: fd });
    if (d.courant_kmh) { $("courant-u").value = d.courant_kmh[0]; $("courant-v").value = d.courant_kmh[1]; }
    msg("couche-msg", "Couche ajoutée : " + JSON.stringify(d), "ok");
    await chargerTrajet();
  } catch (e) { msg("couche-msg", e.message, "erreur"); }
});
init().catch((e) => { vider($("p-anomalies")).appendChild(el("p", { class: "msg erreur" }, "Démarrage impossible : " + e.message)); });
