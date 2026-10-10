/* SeaTrace V2 — interface. No data is ever inserted as HTML: textContent only. */
"use strict";

const COLOURS = { hmm: "#6e3ca0", appris: "#1e78b4", particules: "#c86414", pont: "#288c5a", boosting: "#7f8c8d" };
const SHORT = { hmm: "Grid HMM", appris: "Learned model", particules: "Particle filter", pont: "Brownian bridge", boosting: "Boosting" };
const FAMILY_COLOURS = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628", "#f781bf", "#999999", "#66c2a5", "#ffd92f"];
const S = { state: null, dataset: null, trip: null, analysis: null, run: null, drawing: null, charts: {}, evalLoaded: false, kattegatLoaded: false };

// ---------------------------------------------------------------- utilities
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return e;
}
const tip = (text) => el("span", {}, String(text)); // Leaflet tooltips take HTML: always pass a text node
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const pct = (v, d = 0) => (v === null || v === undefined) ? "—" : `${(100 * v).toFixed(d)} %`;
const num = (v, d = 3) => (v === null || v === undefined) ? "—" : Number(v).toFixed(d);
const humanize = (k) => { const s = String(k).replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); };
function table(columns, rows, onClick) {
  const t = el("table", {}, el("thead", {}, el("tr", {}, columns.map((c) => el("th", {}, c.title)))));
  const tb = el("tbody");
  rows.forEach((r) => {
    const tr = el("tr", onClick ? { onclick: () => onClick(r, tr), style: "cursor:pointer" } : {},
      columns.map((c) => el("td", {}, c.f ? c.f(r) : String(r[c.key] ?? "—"))));
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
  if (!r.ok) throw new Error((d && d.error) || `Error ${r.status}`);
  return d;
}
function msg(id, text, cls) { const m = $(id); m.textContent = text || ""; m.className = "msg " + (cls || ""); }
function destroyCharts(tab) { (S.charts[tab] || []).forEach((c) => c.destroy()); S.charts[tab] = []; }
function addChart(tab, canvas, config) {
  Chart.defaults.color = getComputedStyle(document.body).color;
  Chart.defaults.borderColor = getComputedStyle(document.documentElement).getPropertyValue("--border") || "#ccc";
  const c = new Chart(canvas, config);
  (S.charts[tab] ||= []).push(c);
  return c;
}

// ---------------------------------------------------------------- help and interpretation boxes
const HELP = {
  anomalies: [
    "What this tab shows: the integrity checks run on the raw positions of the selected trip, before any model is used.",
    "The score (0–100) adds up the weighted integrity alerts; it measures how inconsistent the data are, not anyone's guilt. " +
    "Completeness is received positions divided by expected positions. Each alert lists the computed values behind it and the other explanations that remain possible.",
    "'Integrity' alerts point to data that may have been altered (jumps, replays, duplicated identity). 'Availability' alerts only say that data are missing, which can have a harmless cause.",
  ],
  comparison: [
    "What this tab shows: the crossing probability of the chosen zone according to each of the five models, side by side. Nothing is averaged: the models rest on different assumptions and the point is to see whether they agree.",
    "How to read the table: 'Interval' is the model's own uncertainty (the HMM over the assumptions tested, boosting over its 7 random seeds). 'Monte Carlo error' is only sampling noise from the number of drawn paths. 'Paths' is how many paths the model generated (boosting generates none).",
    "Start with the 'Common reading' box, then read the notes under it: they say, in plain words, why a model stands apart in this particular run.",
  ],
  families: [
    "What this tab shows: the generated paths of all models grouped into families — the few distinct 'stories' the paths tell — with the probability of each family according to each model.",
    "A PCA (principal component analysis) compresses every path (a long list of coordinates) into a few numbers called components; the scatter plot places each path by its first two. Paths that look alike end up close together.",
    "The bar chart is what carries the probabilities; the scatter plot only shows how well the families separate. The component table explains in words what each axis of the scatter plot means.",
  ],
  paths: [
    "What this tab shows: every path generated by the models with its probability, in a compact preview (start, middle and end of the table, like a Polars data frame) so you do not scroll through hundreds of near-identical rows.",
    "Click a row to draw that path on the map. Use the filters to look at one model or one family. The complete table is always available in the CSV export (tab 'Decision & exports').",
  ],
  decision: [
    "What this tab shows: the downloads for this run (all paths with their probability as CSV, GeoJSON for GIS tools, a JSON summary and a self-contained HTML report) and a place to record the analyst's note and decision.",
    "SeaTrace never decides a sanction or a refusal of compensation: the decision only concerns what happens next in the investigation. Both the decision and the exports are written to the tamper-evident log.",
  ],
  evaluation: [
    "What this tab shows: how well each model did on 159 gaps whose true outcome is known, on boats the models never saw. All data are synthetic, so this shows how the methods behave, not that they work at sea.",
    "The Brier score is the average squared error of the predicted probability (0 = perfect, 0.25 = always answering 50 %); lower is better. The calibration chart checks whether '70 %' really means 'happens about 7 times out of 10'.",
  ],
  sources: [
    "What this tab shows: every data source considered for SeaTrace, with its real status in this prototype. Licence terms must be checked on each source's website before any use, especially commercial use.",
  ],
  kattegat: [
    "What this tab shows: the study map of the Kattegat built by the team's data pipeline — real AIS tracks of fishing vessels (class A, 15–17 September 2026) against the regulated zones of Delegated Regulation (EU) 2022/952.",
    "It is a separate study page embedded here. It loads its map library and base tiles from the Internet, so it needs a connection. A track crossing a zone is not an offence.",
  ],
  journal: [
    "What this tab shows: the local log of everything done in this installation (imports, reconstructions, exports, decisions). Each entry carries the SHA-256 hash of the previous one, so an altered or deleted entry breaks the chain and is reported here.",
  ],
};

function helpBox(tab) {
  let open = true;
  try { open = localStorage.getItem("help-" + tab) !== "closed"; } catch (_) { /* storage may be blocked */ }
  const d = el("details", { class: "help", open: open }, el("summary", {}, "What does this tab do?"),
    HELP[tab].map((t) => el("p", {}, t)));
  d.addEventListener("toggle", () => { try { localStorage.setItem("help-" + tab, d.open ? "open" : "closed"); } catch (_) { /* ignore */ } });
  return d;
}
function interp(title, items) {
  const list = Array.isArray(items) ? items : [items];
  return el("div", { class: "interp" }, el("b", { class: "t" }, title),
    list.length === 1 ? el("span", {}, list[0]) : el("ul", {}, list.map((x) => el("li", {}, x))));
}

// ---------------------------------------------------------------- map
const map = L.map("map", { zoomControl: true, preferCanvas: true, doubleClickZoom: false });
const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap contributors (ODbL)" });
const layers = { geo: L.layerGroup().addTo(map), points: L.layerGroup().addTo(map), route: L.layerGroup().addTo(map),
  families: L.layerGroup().addTo(map), highlight: L.layerGroup().addTo(map), draw: L.layerGroup().addTo(map) };
const modelLayers = {};
const control = L.control.layers({ "Plain background (offline)": L.layerGroup(), "OpenStreetMap (online)": osm }, {
  "Land and zones": layers.geo, "Positions": layers.points, "Declared route": layers.route, "Family mean paths": layers.families,
}, { collapsed: true }).addTo(map);
map.setView([47.27, -7.47], 11);

function drawGeography(g) {
  layers.geo.clearLayers();
  g.land.forEach((t) => L.polygon(t.polygon, { color: "#a89f88", weight: 1, fillColor: "#d9d3c3", fillOpacity: 0.9 }).bindTooltip(tip(t.name)).addTo(layers.geo));
  g.zones.forEach((z) => L.polygon(z.polygon, { color: "#c0392b", weight: 1.5, dashArray: "5 4", fillOpacity: 0.12 }).bindTooltip(tip(`${z.id} · ${z.name}`)).addTo(layers.geo));
}
function drawTrip(a) {
  layers.points.clearLayers(); layers.route.clearLayers();
  const ok = a.points.filter((p) => p.status === "observee");
  L.polyline(ok.map((p) => [p.lat, p.lon]), { color: "#1f6fb2", weight: 1.2, opacity: 0.6 }).addTo(layers.points);
  a.points.forEach((p) => {
    const sus = p.status !== "observee";
    L.circleMarker([p.lat, p.lon], { radius: sus ? 4 : 2.5, color: sus ? "#e67e22" : "#1f6fb2", weight: sus ? 2 : 1, fillOpacity: sus ? 0 : 0.8 })
      .bindTooltip(tip(`${p.t} · ${sus ? "suspicious (set aside)" : "observed"}` + (p.sog !== null ? ` · ${p.sog} kn reported` : ""))).addTo(layers.points);
  });
  if (a.declared_route) L.polyline(a.declared_route, { color: "#566573", dashArray: "6 6", weight: 1.5 }).bindTooltip(tip("Declared route")).addTo(layers.route);
  if (a.points.length) map.fitBounds(L.latLngBounds(a.points.map((p) => [p.lat, p.lon])).pad(0.15));
}
function clearRun() {
  for (const m of Object.keys(modelLayers)) {
    [modelLayers[m].paths, modelLayers[m].occupation].forEach((l) => { control.removeLayer(l); map.removeLayer(l); });
    delete modelLayers[m];
  }
  layers.families.clearLayers(); layers.highlight.clearLayers();
}
function drawRun(run) {
  clearRun();
  for (const [m, r] of Object.entries(run.models)) {
    const paths = L.layerGroup(), occ = L.layerGroup();
    const wmax = Math.max(...r.paths.map((c) => c.weight), 1e-9);
    r.paths.forEach((c) => {
      L.polyline(c.lat.map((la, i) => [la, c.lon[i]]), { color: COLOURS[m], weight: 1, opacity: Math.min(0.85, 0.12 + 0.6 * c.weight / wmax) })
        .bindTooltip(tip(`${c.id} · family ${c.family || "—"} · weight ${(100 * c.weight).toFixed(2)} %` +
          (c.crosses_zone === null ? "" : (c.crosses_zone ? " · crosses the zone" : " · stays out of the zone"))))
        .on("click", () => highlightPath(m, c)).addTo(paths);
    });
    if (r.occupation_png) L.imageOverlay(r.occupation_png, run.grid_bounds, { opacity: 0.7, interactive: false }).addTo(occ);
    modelLayers[m] = { paths, occupation: occ };
    if (r.paths.length) {
      control.addOverlay(paths, `<span style="color:${COLOURS[m]}">■</span> Paths: ${SHORT[m]}`);
      control.addOverlay(occ, `Corridors: ${SHORT[m]}`);
    }
  }
  const first = ["hmm", "particules", "appris", "pont"].find((m) => run.models[m] && run.models[m].paths.length);
  if (first) modelLayers[first].paths.addTo(map);
  ((run.segmentation || {}).families || []).forEach((f, i) => {
    const pts = f.mean_path.lat.map((la, k) => [la, f.mean_path.lon[k]]);
    L.polyline(pts, { color: "#000", weight: 4, opacity: 0.9 }).addTo(layers.families);
    L.polyline(pts, { color: FAMILY_COLOURS[i % 10], weight: 2.5 }).bindTooltip(tip(`${f.id} · ${f.description} · ${pct(f.shared_probability)}`)).addTo(layers.families);
    L.marker(pts[Math.floor(pts.length / 2)], { icon: L.divIcon({ className: "", html: "", iconSize: [0, 0] }) }).addTo(layers.families)
      .bindTooltip(tip(f.id), { permanent: true, direction: "center", className: "family-label" });
  });
}
function highlightPath(m, c) {
  layers.highlight.clearLayers();
  const pts = c.lat.map((la, i) => [la, c.lon[i]]);
  L.polyline(pts, { color: "#000", weight: 5, opacity: 0.5 }).addTo(layers.highlight);
  L.polyline(pts, { color: COLOURS[m], weight: 3 }).bindTooltip(tip(`${c.id} · weight ${(100 * c.weight).toFixed(2)} %`)).addTo(layers.highlight);
}

// ---------------------------------------------------------------- drawing a zone
$("btn-draw").addEventListener("click", () => {
  if (S.drawing) { S.drawing = null; layers.draw.clearLayers(); $("btn-draw").textContent = "Draw a zone"; return; }
  if (!S.dataset) return;
  S.drawing = []; layers.draw.clearLayers(); $("btn-draw").textContent = "Cancel drawing";
  msg("layer-msg", "Click to place vertices, double-click to close the zone.");
});
map.on("click", (ev) => {
  if (!S.drawing) return;
  S.drawing.push([ev.latlng.lat, ev.latlng.lng]); layers.draw.clearLayers();
  L.polyline(S.drawing, { color: "#c0392b", dashArray: "4 4" }).addTo(layers.draw);
});
map.on("dblclick", async () => {
  if (!S.drawing) return;
  const poly = S.drawing; S.drawing = null; layers.draw.clearLayers(); $("btn-draw").textContent = "Draw a zone";
  if (poly.length < 3) { msg("layer-msg", "At least three vertices are needed.", "error"); return; }
  try {
    const d = await api(`/api/datasets/${S.dataset}/zones`, { method: "POST", json: { polygon: poly } });
    msg("layer-msg", `Zone ${d.id} added (drawn by the analyst, no regulatory value).`, "ok");
    await loadTrip(d.id);
  } catch (e) { msg("layer-msg", e.message, "error"); }
});

// ---------------------------------------------------------------- loading
async function init() {
  S.state = await api("/api/state");
  const v = S.state.versions;
  $("versions").textContent = `software ${v.logiciel} · models ${v.modeles_v2} · parameters ${v.parametres} (${v.empreinte_parametres})`;
  fillDatasets();
  const types = clear($("boat-type"));
  Object.entries(S.state.boat_types).forEach(([k, lab]) => types.appendChild(el("option", { value: k }, lab)));
  const box = clear($("models"));
  S.state.models.forEach((m) => box.appendChild(el("label", { class: "check" },
    el("input", { type: "checkbox", value: m.id, checked: true }), el("span", { class: "dot", style: `background:${COLOURS[m.id]}` }), m.label)));
  $("current-u").value = S.state.default_current_kmh[0]; $("current-v").value = S.state.default_current_kmh[1];
  $("btn-kattegat").classList.toggle("hidden", !S.state.kattegat.zones);
  Object.keys(HELP).forEach((t) => { const p = $("p-" + t); if (!p.firstChild) p.appendChild(helpBox(t)); });
  ["comparison", "families", "paths", "decision"].forEach((t) => $("p-" + t).appendChild(el("p", { class: "empty" }, "Run a reconstruction first.")));
  await loadDataset($("dataset").value);
}
function fillDatasets() {
  const sel = clear($("dataset"));
  S.state.datasets.forEach((d) => sel.appendChild(el("option", { value: d.id }, `${d.name} — ${d.trips} trips`)));
}
async function loadDataset(id) {
  S.dataset = id;
  const d = await api(`/api/datasets/${id}/trips`);
  const sel = clear($("trip"));
  d.trips.forEach((t) => sel.appendChild(el("option", { value: t.trip_id },
    `${t.trip_id} · ${t.name || t.boat_id} · ${t.positions} pos.${t.description ? " — " + t.description.slice(0, 60) : ""}`)));
  const b = $("banner");
  if (d.origin === "demonstration_synthetique") {
    b.textContent = "Synthetic data and fictitious geography: boats, coast, islands and zone Z1 are invented for the demonstration; manipulations were simulated on a local copy.";
    b.className = "banner";
  } else if (d.origin === "ais_ouvert") {
    b.textContent = "Open AIS data (real positions). No land or zone is known until you import GeoJSON (OSM, Natural Earth, Shom, CNSP…), load the Kattegat zones or draw a zone. The models are only tuned on synthetic data.";
    b.className = "banner warn";
  } else { b.textContent = "Imported data. " + (d.warnings || []).join(" "); b.className = "banner warn"; }
  if (sel.options.length) await loadTrip();
}
async function loadTrip(defaultZone) {
  S.trip = $("trip").value;
  let a;
  try { a = await api(`/api/datasets/${S.dataset}/trips/${encodeURIComponent(S.trip)}?type=${encodeURIComponent($("boat-type").value)}`); }
  catch (e) { const p = clear($("p-anomalies")); p.appendChild(helpBox("anomalies")); p.appendChild(el("p", { class: "msg error" }, e.message)); return; }
  S.analysis = a;
  $("lab-type").classList.toggle("hidden", a.type_known);
  if (!a.type_known) $("boat-type").value = a.boat_type;
  clear($("trip-summary")).appendChild(el("span", {}, `${a.boat_id}${a.name ? " · " + a.name : ""} · ${a.type_label}`));
  drawGeography(a.geography); drawTrip(a); clearRun();
  if (a.geography.land.length || a.geography.zones.length) map.fitBounds(L.latLngBounds(a.points.map((p) => [p.lat, p.lon])).pad(0.4));
  const periods = clear($("periods"));
  if (!a.periods.length) periods.appendChild(el("p", { class: "hint" }, "No missing or suspicious period on this trip: nothing to reconstruct."));
  const longest = a.periods.reduce((best, q, j, arr) => q.duration_min > arr[best].duration_min ? j : best, 0);
  a.periods.forEach((p, i) => periods.appendChild(el("label", {},
    el("input", { type: "radio", name: "period", value: p.id, checked: i === longest }),
    el("span", {}, `${p.id} · ${p.start} → ${p.end} · ${p.duration_min} min · ${({ interruption: "reception gap", positions_suspectes: "suspicious positions", mixte: "mixed" })[p.cause]}` +
      (p.boundary_reliability === "reduite" ? " · unreliable boundaries" : "")))));
  const zs = clear($("zone"));
  zs.appendChild(el("option", { value: "" }, "— none (corridors and families only) —"));
  a.geography.zones.forEach((z) => zs.appendChild(el("option", { value: z.id }, `${z.id} · ${z.name}`)));
  if (defaultZone) zs.value = defaultZone; else if (a.geography.zones.length) zs.value = a.geography.zones[0].id;
  updateZoneDesc();
  renderAnomalies(a);
  ["comparison", "families", "paths", "decision"].forEach((t) => { const p = clear($("p-" + t)); p.appendChild(helpBox(t)); p.appendChild(el("p", { class: "empty" }, "Run a reconstruction first.")); });
}
function updateZoneDesc() {
  const z = (S.analysis.geography.zones || []).find((x) => x.id === $("zone").value);
  $("zone-desc").textContent = z ? `${z.description} (source: ${z.source})` : "";
}

function renderAnomalies(a) {
  const p = clear($("p-anomalies")); p.appendChild(helpBox("anomalies"));
  const q = a.quality;
  p.appendChild(el("div", { class: "kpis" },
    el("div", { class: "kpi" }, el("b", {}, `${a.anomaly_score}/100`), el("span", {}, "data anomaly score (not guilt)")),
    el("div", { class: "kpi" }, el("b", {}, `${q.completeness_pct ?? "—"} %`), el("span", {}, "completeness")),
    el("div", { class: "kpi" }, el("b", {}, `${q.suspicious_points ?? 0} / ${q.points}`), el("span", {}, "suspicious positions set aside")),
    el("div", { class: "kpi" }, el("b", {}, a.planned_route_gap ? `${a.planned_route_gap.ecart_max_km} km` : "—"), el("span", {}, "max. gap to the declared route"))));
  const score = a.anomaly_score;
  p.appendChild(interp("How to read this", [
    score === 0 ? "No integrity check fired: nothing in the positions contradicts itself. That does not guarantee the data are true (a spoofed tracker can be perfectly consistent)."
      : `The score of ${score}/100 is the sum of the integrity alerts below; each alert adds its weight (position jump 30, speed/course inconsistency 20, drift 20, duplicated identity 40, replay 40, duplicate records 5), capped at 100. Availability alerts (gaps) add nothing.`,
    q.completeness_pct < 90 ? `Completeness is ${q.completeness_pct} %: a part of the expected positions never arrived, which is why a period can be proposed for reconstruction.` : "Completeness is high: few positions are missing.",
    a.planned_route_gap ? `The boat strayed at most ${a.planned_route_gap.ecart_max_km} km from its declared route; a detour is not a fault by itself.` : "No declared route was provided for this trip.",
  ]));
  if (a.geography.no_land) p.appendChild(el("div", { class: "callout warn" }, "No land map for these data: the models ignore obstacles. Import a GeoJSON of land (OSM land polygons, Natural Earth) in section 3."));
  if (!a.alerts.length) p.appendChild(el("div", { class: "callout" }, "No anomaly detected by the checks."));
  a.alerts.forEach((x) => p.appendChild(el("div", { class: "alert-item" },
    el("b", {}, `${x.id} · ${x.label}`), el("span", { class: "tag" + (x.category === "integrity" ? "" : " avail") }, x.category === "integrity" ? "integrity" : "availability"),
    el("span", { class: "hint" }, ` ${x.start} → ${x.end}`), el("div", {}, x.explanation),
    el("div", { class: "hint" }, "Other possible explanations: " + x.alternatives.join("; ")))));
}

// ---------------------------------------------------------------- reconstruction
async function runReconstruction() {
  const per = document.querySelector("input[name=period]:checked");
  if (!per) { msg("run-msg", "No period to reconstruct on this trip: choose a trip that has a gap (for example S4, S5 or S6 in the demonstration).", "error"); return; }
  const models = [...document.querySelectorAll("#models input:checked")].map((i) => i.value);
  if (!models.length) { msg("run-msg", "Choose at least one model.", "error"); return; }
  $("btn-run").disabled = true;
  msg("run-msg", "Computing (up to five models + sensitivity + segmentation: a few seconds)…");
  try {
    const run = await api("/api/reconstruct", { method: "POST", json: {
      dataset: S.dataset, trip: S.trip, type: $("boat-type").value, period: per.value, zone: $("zone").value || null,
      models, externals: $("externals").checked, current: [parseFloat($("current-u").value) || 0, parseFloat($("current-v").value) || 0] } });
    S.run = run;
    drawRun(run); renderComparison(run); renderFamilies(run); renderPaths(run); renderDecision(run);
    msg("run-msg", `Done in ${run.total_time_s.toFixed(1)} s · run ${run.run_id} archived (every path with its probability).`, "ok");
    activateTab("comparison");
  } catch (e) { msg("run-msg", e.message, "error"); }
  finally { $("btn-run").disabled = false; }
}

function describeComparison(run) {
  const ok = Object.values(run.models).filter((r) => r.status === "ok" && r.crossing_probability !== null);
  const out = [];
  if (!run.zone) return ["No zone was chosen, so no crossing probability exists; only corridors and path families are produced."];
  if (!ok.length) return ["No model produced an estimate."];
  const sorted = [...ok].sort((a, b) => a.crossing_probability - b.crossing_probability);
  const lo = sorted[0], hi = sorted[sorted.length - 1];
  const spread = hi.crossing_probability - lo.crossing_probability;
  out.push(`Estimates range from ${pct(lo.crossing_probability)} (${SHORT[lo.model]}) to ${pct(hi.crossing_probability)} (${SHORT[hi.model]}), a spread of ${Math.round(100 * spread)} points.`);
  out.push(spread > 0.3 ? "That spread is large: reasonable modelling choices lead to different answers, so the result depends on the assumptions and should not be used to conclude."
    : spread < 0.1 ? "The models are close together even though their assumptions differ, so the answer does not hinge on the modelling choice."
      : "The models differ moderately: look at the families tab to see which route each one favours.");
  const withBar = ok.filter((r) => r.interval);
  if (withBar.length) out.push(`The grey bar behind a bar is that model's own uncertainty: for the HMM, the range over the assumptions tested; for boosting, the range over its 7 seeds. ` +
    withBar.map((r) => `${SHORT[r.model]}: ${pct(r.interval[0])}–${pct(r.interval[1])}`).join("; ") + ".");
  out.push("The 'Monte Carlo error' column is only noise from the number of drawn paths (more paths = smaller error); it says nothing about whether the model itself is right.");
  return out;
}

function renderComparison(run) {
  destroyCharts("comparison");
  const p = clear($("p-comparison")); p.appendChild(helpBox("comparison"));
  const s = run.synthesis;
  p.appendChild(el("div", { class: s.code === "divergence" ? "callout warn" : (s.code === "aucune" ? "callout error" : "callout") }, el("b", {}, "Common reading: "), s.reading));
  if (s.notes.length) p.appendChild(interp("What distinguishes the models in this run", s.notes));
  p.appendChild(el("p", { class: "hint" }, s.reminder));
  const rows = Object.values(run.models);
  p.appendChild(table([
    { title: "Model", f: (r) => el("span", {}, el("span", { class: "dot", style: `background:${COLOURS[r.model]}` }), r.label) },
    { title: "Crossing probability", f: (r) => r.status === "ok" ? pct(r.crossing_probability) : "failed" },
    { title: "Interval (sensitivity / ensemble)", f: (r) => r.interval ? `${pct(r.interval[0])} – ${pct(r.interval[1])}` : "—" },
    { title: "Monte Carlo error", f: (r) => r.mc_error === null ? "—" : `± ${Math.round(100 * r.mc_error)} %` },
    { title: "Paths", f: (r) => String(r.n_paths) },
    { title: "Time", f: (r) => `${r.compute_time_s} s` },
    { title: "Reasons and limits", f: (r) => r.reasons.join(" ") || "—" },
  ], rows));
  const grid = el("div", { class: "grid2" });
  const canvas = el("canvas");
  grid.appendChild(el("div", { class: "chart" }, canvas));
  const diag = el("div", {}, el("h3", {}, "Diagnostics of each model"));
  rows.forEach((r) => {
    const d = { ...r.diagnostics }; delete d.sensitivity; delete d.descriptors; delete d.mc_check; delete d.importances;
    const flat = [];
    const walk = (o, pre) => Object.entries(o || {}).forEach(([k, v]) => {
      if (v && typeof v === "object" && !Array.isArray(v)) walk(v, pre + humanize(k) + " · "); else flat.push({ k: pre + humanize(k), v: Array.isArray(v) ? v.join(", ") : String(v) });
    });
    walk(d, ""); const hyp = []; Object.entries(r.hypotheses || {}).forEach(([k, v]) => hyp.push({ k: humanize(k), v: (v && typeof v === "object") ? JSON.stringify(v) : String(v) }));
    diag.appendChild(el("details", {}, el("summary", {}, r.label),
      flat.length ? table([{ title: "Diagnostic", key: "k" }, { title: "Value", key: "v" }], flat) : null,
      hyp.length ? table([{ title: "Assumption", key: "k" }, { title: "Value", key: "v" }], hyp) : null));
    if (r.diagnostics.sensitivity) {
      diag.appendChild(el("details", {}, el("summary", {}, "HMM sensitivity to its assumptions"),
        table([{ title: "Variation", key: "variation" }, { title: "Crossing probability", f: (l) => l.proba_passage === null ? "impossible" : pct(l.proba_passage) }], r.diagnostics.sensitivity),
        interp("How to read this", "Each line reruns the HMM with one assumption changed (speed dispersion, maximum speed, heading persistence, boundary uncertainty). If the probability barely moves, the result is robust to that assumption; the 'no heading' variant is shown only for comparison and is not counted in the interval.")));
    }
  });
  grid.appendChild(diag); p.appendChild(grid);
  p.appendChild(interp("How to read the chart and the table", describeComparison(run)));
  const ok = rows.filter((r) => r.status === "ok" && r.crossing_probability !== null);
  if (ok.length) addChart("comparison", canvas, {
    type: "bar",
    data: { labels: ok.map((r) => SHORT[r.model]), datasets: [
      { label: "Crossing probability", data: ok.map((r) => 100 * r.crossing_probability), backgroundColor: ok.map((r) => COLOURS[r.model]) },
      { label: "Model's own uncertainty range", data: ok.map((r) => r.interval ? [100 * r.interval[0], 100 * r.interval[1]] : null), backgroundColor: "rgba(0,0,0,0.18)", barPercentage: 0.3 }] },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100, title: { display: true, text: "%" } } },
      plugins: { title: { display: true, text: "Estimated probability of crossing the zone, by model" } } } });
}

function describeFamilies(run) {
  const seg = run.segmentation; const out = [];
  const mods = Object.keys(run.models).filter((m) => run.models[m].n_paths);
  if (seg.n_families === 1) { out.push("Only one family: all models draw paths in one continuous cloud around a single route. The uncertainty is about exactly where along that route, not about which route."); return out; }
  seg.families.forEach((f) => {
    const ps = mods.map((m) => [m, f.probability_by_model[m] || 0]);
    const max = ps.reduce((a, b) => b[1] > a[1] ? b : a), min = ps.reduce((a, b) => b[1] < a[1] ? b : a);
    const gap = max[1] - min[1];
    out.push(`${f.id} (${f.description}): ${SHORT[max[0]]} gives it ${pct(max[1])}, ${SHORT[min[0]]} ${pct(min[1])}` +
      (gap > 0.3 ? " — the models disagree about this route." : " — the models broadly agree.") +
      (f.share_crossing_zone === null ? "" : ` ${pct(f.share_crossing_zone, 0)} of its paths cross the zone.`));
  });
  const cross = seg.families.filter((f) => f.share_crossing_zone !== null);
  if (cross.length >= 2 && cross.some((f) => f.share_crossing_zone > 0.9) && cross.some((f) => f.share_crossing_zone < 0.1))
    out.push("The families split cleanly into 'crosses the zone' and 'does not': the crossing probability is therefore simply the probability each model gives to the corresponding route.");
  return out;
}

function renderFamilies(run) {
  destroyCharts("families");
  const p = clear($("p-families")); p.appendChild(helpBox("families"));
  const seg = run.segmentation;
  if (!seg || !seg.families) { p.appendChild(el("p", { class: "empty" }, seg && seg.error ? seg.error : "No path to segment.")); return; }
  p.appendChild(el("p", { class: "hint" }, `${seg.method}. ${seg.n_families} family(ies); BIC by number of families: ${JSON.stringify(seg.bic)}; silhouette: ${JSON.stringify(seg.silhouettes)}.`));
  const mods = Object.keys(run.models).filter((m) => run.models[m].n_paths);
  p.appendChild(table([
    { title: "Family", f: (f) => el("span", {}, el("span", { class: "dot", style: `background:${FAMILY_COLOURS[(parseInt(f.id.slice(1)) - 1) % 10]}` }), f.id) },
    { title: "Description", key: "description" }, { title: "Shared probability", f: (f) => pct(f.shared_probability) },
    ...mods.map((m) => ({ title: `P(${SHORT[m]})`, f: (f) => pct(f.probability_by_model[m]) })),
    { title: "Crosses the zone", f: (f) => pct(f.share_crossing_zone) }, { title: "Paths", key: "n_paths" }], seg.families));
  p.appendChild(interp("Reading the families", describeFamilies(run)));
  const ip = seg.interpretation;
  p.appendChild(el("h3", {}, "What the PCA axes mean"));
  p.appendChild(table([{ title: "Component", key: "name" }, { title: "Variance explained", f: (c) => `${c.variance_pct} %` },
    { title: "What it measures", key: "meaning" }, { title: "High score", key: "high_means" }, { title: "Low score", key: "low_means" }], ip.components));
  p.appendChild(interp("How to read the PCA scatter plot", ip.reading));
  const g = el("div", { class: "grid2" });
  const c1 = el("canvas"), c2 = el("canvas");
  g.appendChild(el("div", { class: "chart tall" }, c1)); g.appendChild(el("div", { class: "chart tall" }, c2)); p.appendChild(g);
  const shapes = { hmm: "circle", appris: "rect", particules: "triangle", pont: "rectRot" };
  const byFam = {};
  seg.pca_cloud.forEach((pt) => { (byFam[pt.family + "|" + pt.model] ||= []).push({ x: pt.pc1, y: pt.pc2 }); });
  const comp = ip.components;
  const axis = (i) => comp[i] ? `${comp[i].name} (${comp[i].variance_pct} %): ${comp[i].main_descriptor.replace("_", " ")}` : `PC${i + 1}`;
  addChart("families", c1, { type: "scatter",
    data: { datasets: Object.entries(byFam).map(([k, pts]) => { const [f, m] = k.split("|"); return { label: `${f} · ${SHORT[m]}`, data: pts, pointStyle: shapes[m] || "circle", pointRadius: 2.5, backgroundColor: FAMILY_COLOURS[(parseInt(f.slice(1)) - 1) % 10] }; }) },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      scales: { x: { title: { display: true, text: axis(0) } }, y: { title: { display: true, text: axis(1) } } },
      plugins: { legend: { display: Object.keys(byFam).length <= 12, labels: { boxWidth: 8, font: { size: 9 } } },
        title: { display: true, text: "Paths in the plane of the first two components (colour = family, shape = model)" } } } });
  addChart("families", c2, { type: "bar",
    data: { labels: seg.families.map((f) => f.id), datasets: mods.map((m) => ({ label: SHORT[m], data: seg.families.map((f) => 100 * (f.probability_by_model[m] || 0)), backgroundColor: COLOURS[m] })) },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 100, title: { display: true, text: "% of the model's paths" } } },
      plugins: { title: { display: true, text: "Probability of each family according to each model" } } } });
}

// ---------------------------------------------------------------- all paths (Polars-style preview)
function frameView(columns, rows, opts = {}) {
  const box = el("div", { class: "frame" });
  let n = opts.n || 5, all = false;
  const shape = el("div", { class: "shape" });
  const holder = el("div");
  const sel = el("select", {}, [3, 5, 10].map((k) => el("option", { value: k, selected: k === n }, `${k} rows per block`)));
  const btn = el("button", {}, "Show the full table");
  const ctl = el("div", { class: "controls" }, sel, btn, el("span", { class: "hint" }, "Preview = start, middle and end, sorted by weight (like a Polars data frame)."));
  function render() {
    const N = rows.length;
    shape.textContent = `shape: (${N}, ${columns.length})`;
    const cap = 400;
    let parts;
    if (all || N <= 3 * n + 2) parts = [{ rows: rows.slice(0, cap).map((r, i) => [i, r]) }];
    else {
      const mid = Math.floor((N - n) / 2);
      parts = [{ rows: rows.slice(0, n).map((r, i) => [i, r]) }, { gap: mid - n },
        { rows: rows.slice(mid, mid + n).map((r, i) => [mid + i, r]) }, { gap: N - n - (mid + n) },
        { rows: rows.slice(N - n).map((r, i) => [N - n + i, r]) }];
    }
    const t = el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "#", el("small", {}, "i64")),
      columns.map((c) => el("th", {}, c.title, el("small", {}, c.dtype))))));
    const tb = el("tbody");
    parts.forEach((pt) => {
      if (pt.gap !== undefined) { tb.appendChild(el("tr", { class: "gap" }, el("td", {}, "…"), columns.map(() => el("td", {}, "…")))); return; }
      pt.rows.forEach(([i, r]) => {
        const tr = el("tr", opts.onRow ? { class: "clickable", onclick: () => { box.querySelectorAll("tr.selected").forEach((x) => x.classList.remove("selected")); tr.classList.add("selected"); opts.onRow(r); } } : {},
          el("td", { class: "num" }, String(i)), columns.map((c) => el("td", { class: c.dtype === "str" || c.dtype === "bool" ? "" : "num" }, c.f(r))));
        tb.appendChild(tr);
      });
    });
    t.appendChild(tb); clear(holder).appendChild(t);
    if (all && N > cap) holder.appendChild(el("p", { class: "hint" }, `Showing the first ${cap} rows; every row is in the CSV export.`));
    btn.textContent = all ? "Back to the preview" : "Show the full table";
  }
  sel.addEventListener("change", () => { n = parseInt(sel.value); render(); });
  btn.addEventListener("click", () => { all = !all; render(); });
  box.appendChild(shape); box.appendChild(ctl); box.appendChild(holder); render();
  return box;
}

function renderPaths(run) {
  const p = clear($("p-paths")); p.appendChild(helpBox("paths"));
  const modelsWithPaths = Object.keys(run.models).filter((m) => run.models[m].n_paths);
  const fams = ((run.segmentation || {}).families || []).map((f) => f.id);
  const sm = el("select", {}, el("option", { value: "" }, "all models"), modelsWithPaths.map((m) => el("option", { value: m }, SHORT[m])));
  const sf = el("select", {}, el("option", { value: "" }, "all families"), fams.map((f) => el("option", { value: f }, f)));
  p.appendChild(el("div", { class: "row" }, el("label", {}, "Model ", sm), el("label", {}, "Family ", sf)));
  const zone = el("div"); p.appendChild(zone);
  p.appendChild(interp("How to read the columns", [
    "weight: the probability of this path inside its own model (the weights of one model add up to 1). For the HMM and the learned model, identical paths on the grid are merged, so the weight is the share of draws that gave exactly this path. For the particle filter and the Brownian bridge every path is one equally likely draw (weight = 1/N), so the useful probability is the one of the family, not of a single path.",
    "draws: how many of the sampled paths were identical (HMM and learned model only).",
    "log_prob: exact log-probability of the state sequence on the grid (HMM and learned model only); closer to 0 = more probable path. Paths that look the same to the eye can differ by many units because the grid keeps track of heading and speed too.",
    "crosses_zone: whether the path enters the chosen zone at any time (densified every quarter of a step so thin corners are not missed).",
  ]));
  const build = () => {
    const rows = [];
    for (const [m, r] of Object.entries(run.models)) {
      if (sm.value && sm.value !== m) continue;
      r.paths.forEach((c) => { if (!sf.value || c.family === sf.value) rows.push({ ...c, model: m }); });
    }
    rows.sort((a, b) => b.weight - a.weight || a.id.localeCompare(b.id));
    clear(zone).appendChild(frameView([
      { title: "path_id", dtype: "str", f: (r) => r.id }, { title: "model", dtype: "str", f: (r) => r.model },
      { title: "family", dtype: "str", f: (r) => r.family || "—" }, { title: "weight", dtype: "f64", f: (r) => num(r.weight, 5) },
      { title: "draws", dtype: "i64", f: (r) => String(r.draws) }, { title: "log_prob", dtype: "f64", f: (r) => r.log_prob === null ? "null" : num(r.log_prob, 2) },
      { title: "crosses_zone", dtype: "bool", f: (r) => r.crosses_zone === null ? "null" : String(r.crosses_zone) }],
    rows, { n: 5, onRow: (r) => highlightPath(r.model, r) }));
  };
  sm.addEventListener("change", build); sf.addEventListener("change", build); build();
}

function renderDecision(run) {
  const p = clear($("p-decision")); p.appendChild(helpBox("decision"));
  p.appendChild(el("h3", {}, "Exports"));
  p.appendChild(el("p", {},
    el("a", { href: `/api/runs/${run.run_id}/paths.csv` }, "All paths + probabilities (CSV)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/paths.geojson` }, "Paths (GeoJSON)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/summary.json` }, "Summary (JSON)"), " · ",
    el("a", { href: `/api/runs/${run.run_id}/report.html` }, "Self-contained HTML report")));
  p.appendChild(el("p", { class: "hint" }, `Archived locally in data/chemins/${run.run_id}/. Spreadsheet exports are protected against formula injection.`));
  p.appendChild(el("h3", {}, "Analyst note and decision"));
  p.appendChild(el("p", { class: "hint" }, "SeaTrace never decides a sanction or a refusal of compensation. The decision only concerns what happens next in the investigation."));
  const sel = el("select", {}, el("option", { value: "aucune" }, "No further investigation"),
    el("option", { value: "informations" }, "Request additional information"), el("option", { value: "investigation" }, "Open an in-depth investigation"));
  const ini = el("input", { type: "text", maxlength: "12" }), note = el("textarea", { maxlength: "1000", rows: "3" }), m = el("div", { class: "msg" });
  p.appendChild(el("label", {}, "Decision", sel)); p.appendChild(el("label", {}, "Initials", ini)); p.appendChild(el("label", {}, "Note", note));
  p.appendChild(el("button", { onclick: async () => {
    try { await api(`/api/runs/${run.run_id}/decision`, { method: "POST", json: { decision: sel.value, analyst: ini.value, note: note.value } });
      m.textContent = "Decision saved and logged (it appears in the HTML report)."; m.className = "msg ok"; }
    catch (e) { m.textContent = e.message; m.className = "msg error"; } } }, "Save"));
  p.appendChild(m);
}

// ---------------------------------------------------------------- evaluation
function describeCalibration(c) {
  const out = ["Each point is a group of gaps whose predicted probability fell in the same range: the x position is the average prediction, the y position is how often the crossing really happened. On the dashed diagonal the model is perfectly calibrated; above it the crossing happens more often than predicted (the model is too cautious), below it less often (too confident)."];
  for (const [m, b] of Object.entries(c.models)) {
    const bins = b.calibration || []; const n = bins.reduce((s, x) => s + x.n, 0);
    if (!n) continue;
    const bias = bins.reduce((s, x) => s + x.n * (x.observed_frequency - x.mean_probability), 0) / n;
    const small = bins.filter((x) => x.n < 10).length;
    out.push(`${SHORT[m]}: on average the observed frequency is ${Math.abs(100 * bias).toFixed(0)} points ${bias >= 0 ? "above" : "below"} the prediction (${bias >= 0 ? "under-estimates" : "over-estimates"} the crossing).` +
      (small ? ` ${small} of ${bins.length} groups hold fewer than 10 gaps, so those points are noisy.` : ""));
  }
  return out;
}
function describeBrier(c) {
  const rows = Object.entries(c.models).filter(([, b]) => b.brier !== null).sort((a, b) => a[1].brier - b[1].brier);
  const best = c.bootstrap.best, boot = c.bootstrap.by_model;
  const out = [`Lowest (best) Brier score: ${SHORT[best]} at ${c.models[best].brier}. For context, always answering 50 % scores 0.25, always answering the base rate scores ${c.references.base_rate.brier}, and a straight-line interpolation scores ${c.references.linear_interpolation.brier}.`];
  const tie = rows.filter(([m]) => m !== best && boot[m].gap_to_best_ci95[0] <= 0).map(([m]) => SHORT[m]);
  const sure = rows.filter(([m]) => m !== best && boot[m].gap_to_best_ci95[0] > 0).map(([m]) => SHORT[m]);
  if (sure.length) out.push(`Clearly worse than ${SHORT[best]} (the 95 % interval of the gap excludes 0): ${sure.join(", ")}.`);
  if (tie.length) out.push(`Not distinguishable from ${SHORT[best]} on this sample (interval of the gap includes 0): ${tie.join(", ")}. With ${c.n_interruptions} gaps, small differences are noise.`);
  const imp = c.boosting.importances || {};
  if (best === "boosting" && (imp.p_hmm || 0) > 0.5) out.push("Caution: boosting learns from the HMM probability (stacking, importance " + imp.p_hmm + ") and was trained in the same fictitious bay, so its edge shows it can correct the HMM in that bay, not that it is independent evidence.");
  const pont = c.models.pont; if (pont && pont.failures) out.push(`The Brownian bridge failed on ${pont.failures} gaps (land on the direct line); the column 'Brier (failure = 0.5)' counts those as 'don't know'.`);
  const cov = Object.entries(c.models).filter(([, b]) => b.coverage_90 !== undefined);
  if (cov.length) out.push("Coverage 90 %: share of moments where the true position lay inside the model's 90 % region (ideal ≈ 90 %). " + cov.map(([m, b]) => `${SHORT[m]} ${pct(b.coverage_90)}`).join(", ") + ". Much below 90 % means over-confident; near 100 % means cautious.");
  return out;
}
async function loadEvaluation() {
  if (S.evalLoaded) return; S.evalLoaded = true;
  destroyCharts("evaluation");
  const p = clear($("p-evaluation")); p.appendChild(helpBox("evaluation"));
  const d = await api("/api/evaluation"); const c = d.comparison;
  if (!c) { p.appendChild(el("p", { class: "empty" }, "No evaluation yet: run python eval/comparer_modeles.py")); return; }
  p.appendChild(el("div", { class: "callout warn" }, c.warning));
  p.appendChild(el("p", {}, `${c.n_interruptions} gaps on boats never seen; ${pct(c.real_crossing_share)} are real crossings (${c.sample_note}).`));
  const rows = Object.entries(c.models).map(([m, b]) => ({ m, ...b }));
  const ci = (m) => (c.bootstrap.by_model || {})[m];
  p.appendChild(table([
    { title: "Model", f: (l) => el("span", {}, el("span", { class: "dot", style: `background:${COLOURS[l.m]}` }), SHORT[l.m]) },
    { title: "Brier (lower is better)", f: (l) => String(l.brier ?? "—") },
    { title: "95 % interval (bootstrap)", f: (l) => ci(l.m) ? `${ci(l.m).brier_ci95[0]} – ${ci(l.m).brier_ci95[1]}` : "—" },
    { title: "Gap to the best, 95 % interval", f: (l) => !ci(l.m) ? "—" : (l.m === c.bootstrap.best ? "best" : `${ci(l.m).gap_to_best_ci95[0]} – ${ci(l.m).gap_to_best_ci95[1]}`) },
    { title: "Brier (failure = 0.5)", f: (l) => String(l.brier_failure_0_5) }, { title: "Failures", f: (l) => String(l.failures) },
    { title: "Spatial error (km)", f: (l) => String(l.spatial_error_km ?? "—") }, { title: "Coverage 90 %", f: (l) => l.coverage_90 === undefined ? "—" : pct(l.coverage_90) },
    { title: "Median time", f: (l) => `${l.median_time_s} s` }], rows));
  p.appendChild(interp("How to read the table", describeBrier(c)));
  const g = el("div", { class: "grid2" });
  const cc = el("canvas"); g.appendChild(el("div", { class: "chart tall" }, cc)); p.appendChild(g);
  addChart("evaluation", cc, { type: "line",
    data: { datasets: [...Object.entries(c.models).map(([m, b]) => ({ label: SHORT[m], data: (b.calibration || []).map((x) => ({ x: x.mean_probability, y: x.observed_frequency, n: x.n })),
      borderColor: COLOURS[m], backgroundColor: COLOURS[m], pointRadius: (ctx) => 3 + Math.sqrt((ctx.raw && ctx.raw.n) || 1), tension: 0 })),
      { label: "Perfect calibration", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: "#888", borderDash: [5, 5], pointRadius: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, parsing: false,
      scales: { x: { type: "linear", min: 0, max: 1, title: { display: true, text: "Predicted crossing probability" } }, y: { type: "linear", min: 0, max: 1, title: { display: true, text: "Observed crossing frequency" } } },
      plugins: { title: { display: true, text: "Calibration of the five models (point size = number of gaps in the group)" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: predicted ${num(ctx.raw.x, 2)}, observed ${num(ctx.raw.y, 2)}${ctx.raw.n ? ` (${ctx.raw.n} gaps)` : ""}` } } } } });
  p.appendChild(interp("How to read the calibration chart", describeCalibration(c)));
  // boosting
  const bo = c.boosting;
  if (bo && bo.importances) {
    p.appendChild(el("h3", {}, "Boosting: what it relies on, and how it was tuned"));
    const g2 = el("div", { class: "grid2" });
    const ci1 = el("canvas"); g2.appendChild(el("div", { class: "chart tall" }, ci1));
    const entries = Object.entries(bo.importances).slice(0, 10);
    addChart("evaluation", ci1, { type: "bar", data: { labels: entries.map((e) => e[0]), datasets: [{ label: "Importance", data: entries.map((e) => e[1]), backgroundColor: COLOURS.boosting }] },
      options: { indexAxis: "y", responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, title: { display: true, text: "Descriptor importance in the boosting ensemble (top 10)" } } } });
    const t = bo.tuning;
    if (t) {
      const ci2 = el("canvas"); g2.appendChild(el("div", { class: "chart tall" }, ci2));
      addChart("evaluation", ci2, { type: "line", data: { labels: t.trials.map((x) => x.number + 1), datasets: [
        { label: "Trial score", data: t.trials.map((x) => x.value), borderColor: "#bbb", backgroundColor: "#bbb", showLine: false, pointRadius: 3 },
        { label: "Best so far", data: t.trials.map((x) => x.best_so_far), borderColor: COLOURS.boosting, stepped: true, pointRadius: 0 },
        { label: "Default hyperparameters", data: t.trials.map(() => t.reference_cv_brier), borderColor: "#c0392b", borderDash: [5, 5], pointRadius: 0 }] },
        options: { responsive: true, maintainAspectRatio: false, scales: { x: { title: { display: true, text: "Optuna trial" } }, y: { title: { display: true, text: "Cross-validated Brier score" } } },
          plugins: { title: { display: true, text: "Optuna search: score of each trial" } } } });
    }
    p.appendChild(g2);
    const top = entries[0];
    const msgs = [`The most used descriptor is '${top[0]}' (${pct(top[1])} of the importance). ` + (top[0] === "p_hmm" ? "Boosting mostly re-calibrates the HMM probability and adds smaller corrections from the geometry (duration, range, detour margin, heading towards the zone)." : "")];
    msgs.push("A descriptor with importance 0 was never useful for any split in this training set; it is not necessarily useless in general.");
    if (t) {
      const gain = 100 * (t.reference_cv_brier - t.best_cv_brier) / t.reference_cv_brier;
      msgs.push(`Optuna (${t.sampler}) tried ${t.n_trials} hyperparameter combinations. Scored by 5-fold cross-validation grouped by boat (${t.n_boats} training boats, ${t.n_examples} gaps), the Brier score fell from ${t.reference_cv_brier} with the default hyperparameters to ${t.best_cv_brier} (${gain.toFixed(0)} % lower). Each dot is one trial, the line is the best so far; when it flattens, more trials would add little.`);
      msgs.push("Selected hyperparameters: " + Object.entries(t.best_params).map(([k, v]) => `${k} = ${typeof v === "number" ? +v.toFixed(4) : v}`).join(", ") + ".");
      const pi = Object.entries(t.param_importance || {});
      if (pi.length) msgs.push("Hyperparameters that mattered most for the score: " + pi.slice(0, 3).map(([k, v]) => `${k} (${pct(v)})`).join(", ") + ".");
      msgs.push("Tuning used only the training and tuning boats; the evaluation boats in the table above were never used, so the evaluation score is not inflated by the search.");
    }
    p.appendChild(interp("How to read the boosting charts", msgs));
  }
  p.appendChild(el("p", { class: "hint" }, `Density weight of the learned model: ${c.density_tuning.selected_density_weight} (chosen on the tuning boats by Brier score). Total run time ${c.total_time_s} s.`));
}

async function loadSources() {
  const p = clear($("p-sources")); p.appendChild(helpBox("sources"));
  const d = await api("/api/sources");
  const lab = { integrated: "integrated", manual: "manual import", roadmap: "roadmap", commercial: "commercial (contract)" };
  p.appendChild(table([
    { title: "Source", f: (s) => s.url ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.name) : s.name },
    { title: "Category", key: "category" }, { title: "Status", f: (s) => lab[s.status] || s.status },
    { title: "Format", key: "format" }, { title: "Use in SeaTrace", key: "usage" }, { title: "Licence (to check)", key: "license" }], d));
}

function loadKattegat() {
  if (S.kattegatLoaded) return; S.kattegatLoaded = true;
  const p = clear($("p-kattegat")); p.appendChild(helpBox("kattegat"));
  if (!S.state.kattegat.map) { p.appendChild(el("p", { class: "empty" }, "reports/map.html is not present in this checkout.")); return; }
  p.appendChild(el("p", {},
    el("a", { href: "/kattegat/map.html", target: "_blank" }, "Open the map in a new tab"), " · ",
    el("a", { href: "/kattegat/map_animation.html", target: "_blank" }, "Open the animated tracks")));
  p.appendChild(interp("How this connects to the reconstruction", [
    "The map answers 'which fishing vessels crossed which regulated zones?' from the positions that were received. SeaTrace answers the next question: 'what about the stretches where positions are missing?'.",
    "To run a reconstruction on this region, import a Danish AIS file for the Kattegat (see 'Data sources'), then press 'Load Kattegat zones' in section 3: the regulated zones become selectable zones. Add a GeoJSON of land too, otherwise the models do not know where the coast is.",
  ]));
  p.appendChild(el("iframe", { class: "embed", src: "/kattegat/map.html", title: "Kattegat study map" }));
}

// The log file keeps its original (French) identifiers so the hash chain stays valid: translate for display only.
const LOG_EVENTS = { chargement_demo: "Demo data loaded", import: "File imported", import_refuse: "Import refused", reconstruction: "Reconstruction (V1)",
  reconstruction_multi: "Multi-model reconstruction", export: "Export", decision_humaine: "Human decision", couche_importee: "Layer imported",
  zone_dessinee: "Zone drawn", erreur_analyse: "Analysis error", modification_parametres: "Parameters changed", reinitialisation_parametres: "Parameters reset" };
const LOG_KEYS = { analyste: "analyst", cle: "key", decision: "decision", empreinte: "fingerprint", empreinte_parametres: "parameters_fingerprint", format: "format",
  lignes: "rows", rejetees: "rejected", longueur_note: "note_length", periode: "period", proba_passage: "crossing_probability", probas: "probabilities",
  run_id: "run_id", statut: "status", trip_id: "trip_id", zone: "zone", nom: "name", motif: "reason", jeu: "dataset", role: "role", sommets: "vertices",
  erreur: "error", dossier: "case", polygons: "polygons", observations: "observations", courant_kmh: "current_kmh", valeurs: "values" };
const logDetails = (d) => Object.entries(d || {}).map(([k, v]) => `${LOG_KEYS[k] || k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("; ");
async function loadJournal() {
  const p = clear($("p-journal")); p.appendChild(helpBox("journal"));
  const d = await api("/api/journal");
  p.appendChild(el("div", { class: d.intact ? "callout" : "callout error" }, `SHA-256 chain check: ${d.message}`));
  p.appendChild(table([{ title: "Timestamp", f: (e) => e.horodatage }, { title: "Event", f: (e) => LOG_EVENTS[e.type] || e.type }, { title: "Details", f: (e) => logDetails(e.details) }], d.entries));
}

// ---------------------------------------------------------------- events
function activateTab(name) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((x) => x.classList.toggle("active", x.id === "p-" + name));
  if (name === "journal") loadJournal();
  if (name === "evaluation") loadEvaluation().catch((e) => clear($("p-evaluation")).appendChild(el("p", { class: "msg error" }, e.message)));
  if (name === "sources" && !$("p-sources").querySelector("table")) loadSources();
  if (name === "kattegat") loadKattegat();
}
$("tabs").addEventListener("click", (ev) => { if (ev.target.dataset.tab) activateTab(ev.target.dataset.tab); });
$("dataset").addEventListener("change", (ev) => loadDataset(ev.target.value));
$("trip").addEventListener("change", () => loadTrip());
$("boat-type").addEventListener("change", () => loadTrip());
$("zone").addEventListener("change", updateZoneDesc);
$("btn-run").addEventListener("click", runReconstruction);
$("btn-import").addEventListener("click", async () => {
  const f = $("file").files[0];
  if (!f) { msg("import-msg", "Choose a file.", "error"); return; }
  const fd = new FormData(); fd.append("file", f);
  msg("import-msg", "Importing and validating…");
  try {
    const d = await api("/api/datasets", { method: "POST", body: fd });
    msg("import-msg", `Imported (${d.format}): ${d.rows} rows, ${d.rejected} rejected. ${(d.warnings || []).join(" ")}`, "ok");
    S.state = await api("/api/state"); fillDatasets(); $("dataset").value = d.id; await loadDataset(d.id);
  } catch (e) { msg("import-msg", "Import refused: " + e.message, "error"); }
});
$("btn-layer").addEventListener("click", async () => {
  const f = $("layer-file").files[0];
  if (!f || !S.dataset) { msg("layer-msg", "Choose a file.", "error"); return; }
  const fd = new FormData(); fd.append("file", f); fd.append("role", $("layer-role").value);
  try {
    const d = await api(`/api/datasets/${S.dataset}/layers`, { method: "POST", body: fd });
    if (d.current_kmh) { $("current-u").value = d.current_kmh[0]; $("current-v").value = d.current_kmh[1]; }
    msg("layer-msg", "Layer added: " + JSON.stringify(d), "ok"); await loadTrip();
  } catch (e) { msg("layer-msg", e.message, "error"); }
});
$("btn-kattegat").addEventListener("click", async () => {
  if (!S.dataset) return;
  try { const d = await api(`/api/datasets/${S.dataset}/kattegat-zones`, { method: "POST" });
    msg("layer-msg", `${d.polygons} Kattegat zone polygons loaded. They only matter if the trip is in the Kattegat (about 56–57.5°N, 10.5–12.5°E).`, "ok"); await loadTrip(); }
  catch (e) { msg("layer-msg", e.message, "error"); }
});
init().catch((e) => { clear($("p-anomalies")).appendChild(el("p", { class: "msg error" }, "Start-up failed: " + e.message)); });
