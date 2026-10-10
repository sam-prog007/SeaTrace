"""Étape 4 : carte Folium des trajets de pêche et des zones restreintes du Kattegat.

Paramètres ci-dessous. Les trajets (06) et le croisement (07) sont recalculés
seulement si ces paramètres changent ; sinon les fichiers existants sont relus.

La carte est générée par Folium (fond, zones, sélecteur de couches). Un panneau
latéral en JavaScript, intégré au même fichier HTML, ajoute :
  - le choix d'un navire à étudier (recherche par nom ou MMSI) et sa fiche ;
  - des filtres (passage en zone, longueur, pavillon, statut « en pêche »,
    jours, navires immobiles) ;
  - une légende et un glossaire des termes employés.

Sorties :
  reports/map.html             carte principale
  reports/map_animation.html   trajets animés dans le temps (page séparée : le
                               plugin TimestampedGeoJson ne s'active pas via LayerControl)
"""
# --- Paramètres ------------------------------------------------------------------
PERIOD_START = "2026-09-15"  # période affichée (incluse, UTC)
PERIOD_END = "2026-09-17"
GAP_MINUTES = 30  # seuil de trou : au-delà, le trajet est coupé
SIMPLIFY_M = 30  # tolérance de simplification des lignes (m, EPSG:25832)
STILL_KN = 0.5  # vitesse moyenne sous laquelle un trajet est dit « quasi immobile »
ANIMATION = True  # produire reports/map_animation.html
ANIM_STEP_MIN = 10  # une position animée toutes les N minutes par trajet
# ---------------------------------------------------------------------------------

import datetime as dt  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402

import folium  # noqa: E402
import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import polars as pl  # noqa: E402
from branca.element import Element  # noqa: E402
from folium.plugins import TimestampedGeoJson  # noqa: E402
from shapely.geometry import mapping  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference"
REPORTS = ROOT / "reports"
TILES = "Esri.WorldGrayCanvas"  # fond clair et neutre, sans clé d'API
CENTER, ZOOM = (56.75, 11.5), 8

COLORS = {
    "a1": "#d62828",  # engins de fond interdits : rouge
    "a2": "#8b0000",  # pêche interdite : rouge foncé
    "a3": "#d62828",  # engins limités : rouge clair, contour pointillé
    "buffer": "#f4a261",  # tampons : orange clair
    "ais_zone": "#2a78d6",  # zones AIS officielles : contour bleu
    "natura2000": "#2a9d8f",
    "helcom": "#7b5ea7",
    "track": "#3d5a80",  # trajets : bleu-gris
    "selected": "#0b2545",  # navire étudié : bleu nuit
    "other": "#9aa0a6",  # autres navires en arrière-plan : gris
    "portion_zone": "#e6007e",  # passage dans une zone : magenta vif
    "portion_buffer": "#f59e0b",  # passage dans un tampon : ambre vif
}

# Restrictions en langage courant (règlement délégué (UE) 2022/952, art. 3).
CATEGORY = {
    "Areas 1": {"key": "a1", "short": "Engins de fond interdits",
                "rule": "Interdit : pêcher avec des engins traînés sur le fond (chaluts de fond, sennes, dragues). "
                        "Ces engins doivent rester arrimés et rangés à bord. Les autres engins (filets fixes, "
                        "casiers, lignes, chaluts pélagiques) ne sont pas visés par ce texte."},
    "Areas 2": {"key": "a2", "short": "Pêche interdite",
                "rule": "Interdit : toute opération de pêche, y compris la pêche de loisir."},
    "Areas 3": {"key": "a3", "short": "Pêche limitée à certains engins",
                "rule": "Seuls autorisés : lignes et cannes, chaluts pélagiques (en pleine eau), casiers à "
                        "crustacés, et filets maillants si le navire est suivi par caméras à bord. "
                        "Les mêmes règles s'appliquent à la pêche de loisir."},
}
COUNTRY = {"DK": "Danemark", "SE": "Suède"}

# Pays du pavillon d'après les 3 premiers chiffres du MMSI (MID, table de l'UIT).
FLAGS = {
    "205": "Belgique", "211": "Allemagne", "218": "Allemagne", "219": "Danemark", "220": "Danemark",
    "224": "Espagne", "225": "Espagne", "227": "France", "228": "France", "230": "Finlande",
    "231": "Îles Féroé", "232": "Royaume-Uni", "233": "Royaume-Uni", "234": "Royaume-Uni",
    "235": "Royaume-Uni", "244": "Pays-Bas", "245": "Pays-Bas", "246": "Pays-Bas", "250": "Irlande",
    "251": "Islande", "257": "Norvège", "258": "Norvège", "259": "Norvège", "261": "Pologne",
    "265": "Suède", "266": "Suède", "273": "Russie", "275": "Lettonie", "276": "Estonie", "277": "Lituanie",
}
LEN_CLASSES = [  # (clé, libellé, min inclus, max exclu)
    ("lt12", "moins de 12 m", None, 12), ("12_15", "12 à 15 m", 12, 15), ("15_24", "15 à 24 m", 15, 24),
    ("24_40", "24 à 40 m", 24, 40), ("ge40", "40 m et plus", 40, None), ("unk", "longueur inconnue", None, None),
]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def flag_of(mmsi: str) -> str:
    return FLAGS.get(mmsi[:3], f"inconnu (MID {mmsi[:3]})")


def len_class(v) -> str:
    if v is None or pd.isna(v):
        return "unk"
    for key, _, lo, hi in LEN_CLASSES[:-1]:
        if (lo is None or v >= lo) and (hi is None or v < hi):
            return key
    return "unk"


def zone_label(zone_id: str, name: str) -> str:
    """Nom lisible d'une zone, d'un tampon ou d'une zone AIS."""
    if zone_id.startswith("DK00VA247-"):
        return f"Kims Top og den Kinesiske Mur (Natura 2000), sous-zone {zone_id.split('-')[-1]}"
    if zone_id.startswith("DK-MSFD-"):
        return f"Zone MSFD {zone_id.split('-')[-1]}"
    if zone_id.endswith("-A2"):
        return f"{name} – partie où la pêche est interdite"
    if zone_id.endswith("-A3"):
        return f"{name} – partie à engins limités"
    if zone_id.startswith("BUF4NM-"):
        target = zone_id.removeprefix("BUF4NM-")
        target = "Kims Top" if target == "DK00VA247" else "zone " + target.replace("-", " ")
        return f"Tampon de 4 milles autour de {target}"
    return name


def rnd(c, nd=5):
    """Arrondit récursivement des coordonnées GeoJSON (~1 m) pour alléger le fichier."""
    if isinstance(c, (list, tuple)):
        if c and isinstance(c[0], (int, float)):
            return [round(v, nd) for v in c]
        return [rnd(x, nd) for x in c]
    return c


def feature(geom, props: dict) -> dict:
    g = mapping(geom)
    return {"type": "Feature", "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])},
            "properties": props}


def fc(features: list) -> dict:
    return {"type": "FeatureCollection", "features": features}


def zone_tooltip(title: str, badge: str, country: str, rule: str, source: str) -> str:
    return (f"<div class='st-tip'><b>{title}</b><br><span class='st-badge'>{badge}</span> · {country}"
            f"<div class='st-rule'>{rule}</div><div class='st-src'>{source}</div></div>")


def zones_layers(m, zones: gpd.GeoDataFrame):
    """Une couche par type de restriction, pour pouvoir les afficher séparément."""
    z = zones[zones.layer == "zone"]
    for cat, label in [("Areas 1", "Zones où les engins de fond sont interdits (DK)"),
                       ("Areas 2", "Zones où la pêche est interdite (SE)"),
                       ("Areas 3", "Zones où seuls certains engins sont permis (SE)")]:
        info = CATEGORY[cat]
        feats = [feature(r.geometry, {"tip": zone_tooltip(
            zone_label(r.zone_id, r.name), info["short"], COUNTRY.get(r.country, r.country), info["rule"],
            f"Règlement délégué (UE) 2022/952, {r.legal_ref} · {r.area_km2:.1f} km²")})
            for r in z[z.category == cat].itertuples()]
        color = COLORS[info["key"]]
        style = {"color": color, "weight": 1.5, "fillColor": color,
                 "fillOpacity": {"a1": 0.35, "a2": 0.55, "a3": 0.15}[info["key"]]}
        if info["key"] == "a3":
            style["dashArray"] = "5 4"
        fg = folium.FeatureGroup(label, show=True)
        folium.GeoJson(fc(feats), style_function=lambda f, s=style: s,
                       tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
        fg.add_to(m)

    b = zones[zones.layer == "buffer"]
    feats = [feature(r.geometry, {"tip": zone_tooltip(
        zone_label(r.zone_id, r.name), "Tampon de 4 milles (hypothèse du projet)", "Danemark",
        "Bande de 4 milles nautiques (7,4 km) autour des zones danoises. Hypothèse de travail : les chalutiers "
        "de fond doivent y avoir un AIS de classe A. Ce n'est pas un tracé juridique : les zones AIS "
        "officielles sont dans l'annexe V (couche bleue).",
        f"Calcul SeaTrace en EPSG:25832 · {r.area_km2:.0f} km²")}) for r in b.itertuples()]
    fg = folium.FeatureGroup("Tampons de 4 milles autour des zones danoises (hypothèse)", show=True)
    folium.GeoJson(fc(feats), style_function=lambda f: {"color": COLORS["buffer"], "weight": 1,
                                                        "fillColor": COLORS["buffer"], "fillOpacity": 0.22},
                   tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
    fg.add_to(m)

    a = zones[zones.layer == "ais_zone"]
    feats = [feature(r.geometry, {"tip": zone_tooltip(
        r.name, "Zone AIS obligatoire (officielle)", COUNTRY.get(r.country, r.country), r.restriction
        .replace("AIS obligatoire pour les navires utilisant des engins de fond mobiles (art. 5(b))",
                 "AIS obligatoire pour les navires qui pêchent avec des engins traînés sur le fond.")
        .replace("AIS obligatoire pour tout navire de pêche présent (art. 5(c))",
                 "AIS obligatoire pour tout navire de pêche présent dans la zone."),
        f"Règlement délégué (UE) 2022/952, {r.legal_ref}")}) for r in a.itertuples()]
    fg = folium.FeatureGroup("Zones où l'AIS est obligatoire (annexe V, officielles)", show=False)
    folium.GeoJson(fc(feats), style_function=lambda f: {"color": COLORS["ais_zone"], "weight": 1.5,
                                                        "dashArray": "6 4", "fillOpacity": 0},
                   tooltip=folium.GeoJsonTooltip(["tip"], labels=False, sticky=True)).add_to(fg)
    fg.add_to(m)


def protected_layers(m) -> list[str]:
    missing = []
    for key, label, color, code, name in [
        ("natura2000_kattegat", "Sites Natura 2000 (EEA, fin 2024)", COLORS["natura2000"], "SITECODE", "SITENAME"),
        ("helcom_mpa_kattegat", "Aires protégées HELCOM", COLORS["helcom"], "MPA_ID", "Name"),
    ]:
        f = REF / f"{key}.geojson"
        if not f.exists():
            missing.append(label)
            continue
        g = gpd.read_file(f).to_crs("EPSG:4326")
        g["geometry"] = g.simplify(0.0003)
        fields = [c for c in (code, name) if c in g.columns]
        fg = folium.FeatureGroup(label, show=False)
        folium.GeoJson(g[fields + ["geometry"]],
                       style_function=lambda f, c=color: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": 0.15},
                       tooltip=folium.GeoJsonTooltip(fields) if fields else None).add_to(fg)
        fg.add_to(m)
    return missing


def build_data(tracks: gpd.GeoDataFrame, per_zone: pl.DataFrame, portions: gpd.GeoDataFrame, meta: dict) -> dict:
    """Données du panneau : trajets, passages en zone, navires, période."""
    pz = per_zone.to_pandas()
    pz["label"] = [zone_label(z, n) for z, n in zip(pz.zone_id, pz.name)]
    pz["short"] = [CATEGORY[c]["short"] if c in CATEGORY else "Tampon de 4 milles" for c in pz.category]
    zones_by_track = {
        tid: [{"n": r.label, "k": r.layer, "c": r.short, "m": round(float(r.minutes_in), 1)}
              for r in g.sort_values("minutes_in", ascending=False).itertuples()]
        for tid, g in pz.groupby("track_id")
    }
    track_feats = []
    for r in tracks.itertuples():
        track_feats.append(feature(r.geometry, {
            "tid": r.track_id, "mmsi": r.mmsi, "name": r.name.strip() or None if isinstance(r.name, str) else None,
            "flag": flag_of(r.mmsi), "len": None if pd.isna(r.length_m) else int(r.length_m),
            "lc": len_class(r.length_m), "sog": None if pd.isna(r.sog_mean_kn) else round(float(r.sog_mean_kn), 2),
            "start": r.start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": r.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "n": int(r.n_points), "km": round(float(r.length_km), 1),
            "eng": round(float(r.share_engaged_fishing), 3),
            "mz": round(float(r.minutes_in_zone), 1), "mb": round(float(r.minutes_in_buffer), 1),
            "zones": zones_by_track.get(r.track_id, []),
        }))
    portion_feats = [feature(r.geometry, {"tid": r.track_id, "k": r.layer, "m": round(float(r.minutes or 0), 1)})
                     for r in portions.itertuples() if not r.geometry.is_empty]
    d0 = dt.date.fromisoformat(PERIOD_START)
    days = [(d0 + dt.timedelta(days=i)).isoformat()
            for i in range((dt.date.fromisoformat(PERIOD_END) - d0).days + 1)]
    return {
        "tracks": fc(track_feats), "portions": fc(portion_feats), "days": days,
        "lenClasses": [{"k": k, "label": lab} for k, lab, _, _ in LEN_CLASSES],
        "flags": sorted({f["properties"]["flag"] for f in track_feats}),
        "still": STILL_KN, "gap": GAP_MINUTES, "simplify": SIMPLIFY_M,
        "nTracks": meta["segments_kept"], "nVessels": meta["vessels"],
        "colors": {k: COLORS[k] for k in ("track", "selected", "other", "portion_zone", "portion_buffer")},
        "animation": ANIMATION,
    }


PANEL_CSS = """
<style>
#st-panel{position:absolute;top:10px;left:10px;z-index:1000;width:350px;max-width:calc(100vw - 20px);
  max-height:calc(100% - 20px);overflow:auto;background:#fff;color:#1f1f1d;border:1px solid #c9c7c0;
  border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.15);font:13px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
#st-panel.collapsed{width:auto}#st-panel.collapsed .st-body{display:none}
.st-head{position:sticky;top:0;background:#0b2545;color:#fff;padding:9px 12px;display:flex;align-items:center;gap:8px;z-index:2}
.st-head h1{font-size:15px;margin:0;flex:1;font-weight:600}
.st-head button{background:rgba(255,255,255,.15);color:#fff;border:0;border-radius:4px;padding:4px 8px;cursor:pointer;font:inherit;font-size:12px}
.st-head button:hover{background:rgba(255,255,255,.3)}
.st-body{padding:4px 12px 12px}
.st-sec{border-top:1px solid #e6e4df;padding:10px 0 4px}.st-sec:first-child{border-top:0}
.st-sec h2{font-size:13px;margin:0 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#55534e}
.st-row{margin:6px 0}.st-row label.t{display:block;font-weight:600;margin-bottom:3px}
.st-row select,.st-row input[type=search]{width:100%;box-sizing:border-box;padding:5px 6px;border:1px solid #b9b7b0;border-radius:4px;font:inherit;background:#fff}
.st-checks{display:flex;flex-wrap:wrap;gap:2px 12px}.st-checks label{cursor:pointer}#st-lens label{white-space:nowrap}
.leaflet-control-layers{font:12.5px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1f1f1d}
.leaflet-control-layers-list::before{content:"Couches affichées";display:block;font-weight:700;margin-bottom:4px}
.st-help{display:inline-block;width:15px;height:15px;line-height:15px;text-align:center;border-radius:50%;background:#e6e4df;color:#55534e;font-size:11px;font-weight:700;cursor:help;margin-left:4px}
.st-hint{color:#55534e;font-size:12px;margin-top:2px}
.st-count{background:#f3f2ef;border-radius:4px;padding:6px 8px;margin-top:8px}
.st-btn{border:1px solid #b9b7b0;background:#fff;border-radius:4px;padding:4px 10px;cursor:pointer;font:inherit}
.st-btn:hover{background:#f3f2ef}.st-btn.primary{background:#0b2545;color:#fff;border-color:#0b2545}
.st-card{border:1px solid #d9d7d0;border-radius:6px;padding:8px 10px;margin-top:8px;background:#fbfaf8}
.st-card h3{margin:0 0 4px;font-size:15px}
.st-card table{border-collapse:collapse;width:100%}.st-card td{padding:2px 0;vertical-align:top}
.st-card td:first-child{color:#55534e;width:46%;padding-right:6px}
.st-zl{margin:4px 0 0;padding-left:16px}.st-zl li{margin:2px 0}
.st-warn{color:#7c2d12;background:#fff4e5;border-radius:4px;padding:6px 8px;margin-top:6px;font-size:12px}
.st-leg div{margin:3px 0;display:flex;align-items:center;gap:8px}
.st-sw{display:inline-block;width:22px;height:12px;flex:none}
.st-tip{max-width:330px;white-space:normal;font:12.5px/1.4 system-ui,sans-serif}
.st-badge{display:inline-block;background:#f3f2ef;border-radius:3px;padding:0 5px;font-weight:600}
.st-rule{margin-top:4px}.st-src{margin-top:4px;color:#6b6962;font-size:11.5px}
#st-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:2000}
#st-modal.open{display:flex;align-items:center;justify-content:center}
#st-modal .box{background:#fff;max-width:720px;width:calc(100vw - 32px);max-height:calc(100vh - 48px);overflow:auto;border-radius:8px;padding:16px 20px;font:14px/1.5 system-ui,sans-serif;color:#1f1f1d}
#st-modal h2{font-size:18px;margin:0 0 8px}#st-modal h3{font-size:14px;margin:14px 0 4px;color:#0b2545;text-transform:uppercase;letter-spacing:.03em}
#st-modal dl{margin:0}#st-modal dt{font-weight:700;margin-top:8px}#st-modal dd{margin:2px 0 0 0;color:#33312c}
.leaflet-popup-content{margin:10px 12px;font:13px/1.45 system-ui,sans-serif}
</style>
"""

GLOSSARY = """
<div id="st-modal" role="dialog" aria-modal="true" aria-labelledby="st-gl-title"><div class="box">
<div style="display:flex;align-items:center"><h2 id="st-gl-title" style="flex:1">Comprendre les termes de la carte</h2>
<button class="st-btn" id="st-gl-close">Fermer</button></div>

<h3>Les navires et leur signal</h3><dl>
<dt>AIS (Automatic Identification System)</dt><dd>Balise radio à bord qui émet automatiquement la position, la vitesse,
le cap et l'identité du navire. Les positions de cette carte ont été reçues par les stations côtières danoises
(données publiques de l'administration danoise).</dd>
<dt>MMSI</dt><dd>Numéro d'identification radio du navire, à 9 chiffres. Les 3 premiers chiffres (le « MID ») indiquent
le pays du pavillon : 219 et 220 = Danemark, 265 et 266 = Suède, etc.</dd>
<dt>Pavillon</dt><dd>Pays d'immatriculation du navire, déduit ici du MMSI (table de l'Union internationale des
télécommunications). Ce n'est pas vérifié dans un registre.</dd>
<dt>Classe A / classe B</dt><dd>Deux catégories d'émetteurs AIS. La classe A est l'équipement professionnel : elle émet
plus souvent et plus d'informations. Dans nos données, un navire en classe A envoie un message toutes les 10 s
(médiane), contre 57 s en classe B. Dans l'UE, l'AIS est obligatoire pour les navires de pêche de plus de 15 m.
<b>Cette carte ne montre que des messages de classe A.</b></dd>
<dt>Navire de pêche</dt><dd>Navire dont le type déclaré dans son AIS est « Fishing » (code AIS 30). C'est une
déclaration de l'équipage, non vérifiée dans un registre.</dd>
<dt>Longueur</dt><dd>Longueur déclarée dans l'AIS (en mètres). Parfois absente ou mal saisie.</dd>
<dt>Vitesse moyenne (nœuds)</dt><dd>Moyenne des vitesses envoyées par le navire. 1 nœud = 1,852 km/h.
En dessous de 0,5 nœud en moyenne, le navire est quasi immobile : il est probablement à quai ou au mouillage.</dd>
<dt>Statut « en pêche » déclaré</dt><dd>Statut de navigation choisi à la main par l'équipage (« Engaged in fishing »).
C'est purement déclaratif et souvent laissé activé, y compris au port. Ce n'est pas une preuve d'activité de pêche.</dd>
</dl>

<h3>Les trajets</h3><dl>
<dt>Trajet</dt><dd>Suite des positions d'un même navire. Le trajet est coupé quand le navire n'envoie plus de message
pendant plus de 30 minutes : un navire peut donc avoir plusieurs trajets.</dd>
<dt>Trou de signal</dt><dd>Période de plus de 30 minutes sans message. Causes possibles : navire hors de portée
des antennes, AIS éteint, navire sorti de la zone étudiée.</dd>
<dt>Simplification à 30 m</dt><dd>Pour alléger la carte, les lignes sont redessinées avec moins de points ; l'écart
avec le trajet réel ne dépasse pas 30 m. Les calculs de durée, eux, utilisent toutes les positions.</dd>
</dl>

<h3>Les zones</h3><dl>
<dt>Règlement délégué (UE) 2022/952</dt><dd>Texte européen qui fixe des mesures de protection des fonds marins dans
le Kattegat, côté danois et côté suédois. Les zones de la carte reprennent ses coordonnées officielles.</dd>
<dt>Engins de fond (« engins de fond mobiles »)</dt><dd>Engins de pêche traînés sur le fond : chaluts de fond
(à panneaux, à perche, jumeaux, bœufs), chaluts à langoustines ou à crevettes, sennes (danoise, écossaise, de bateau)
et dragues.</dd>
<dt>Zone où les engins de fond sont interdits (rouge)</dt><dd>Zones danoises (« Areas 1 » dans le règlement) : pêcher
avec des engins de fond y est interdit, et ces engins doivent rester rangés à bord. Les autres engins ne sont pas
visés.</dd>
<dt>Zone où la pêche est interdite (rouge foncé)</dt><dd>Zones suédoises (« Areas 2 ») : toute pêche y est interdite,
y compris la pêche de loisir.</dd>
<dt>Zone où seuls certains engins sont permis (rouge clair, pointillés)</dt><dd>Zones suédoises (« Areas 3 ») : seuls
les lignes, les chaluts pélagiques, les casiers à crustacés et les filets maillants sous surveillance par caméras
y sont autorisés.</dd>
<dt>Zone MSFD</dt><dd>Zone désignée au titre de la directive-cadre « stratégie pour le milieu marin » (en anglais
Marine Strategy Framework Directive, directive 2008/56/CE), pour protéger les fonds marins. Le Danemark en a six
dans le Kattegat (A à F).</dd>
<dt>Natura 2000 · Kims Top og den Kinesiske Mur</dt><dd>Natura 2000 est le réseau européen de sites naturels
protégés. Kims Top og den Kinesiske Mur est un site danois (code DK00VA247), découpé ici en sous-zones.</dd>
<dt>Tampon de 4 milles (orange)</dt><dd>Bande de 4 milles nautiques (7,4 km) autour des zones danoises, <b>calculée par
le projet</b>. Hypothèse : les chalutiers de fond doivent y avoir un AIS de classe A. Les zones où l'AIS est
légalement obligatoire sont celles de l'annexe V (couche bleue, à activer en haut à droite).
1 mille nautique = 1,852 km.</dd>
<dt>Zone où l'AIS est obligatoire (bleu, pointillés)</dt><dd>Zones officielles de l'annexe V du règlement.
Côté danois, l'AIS est obligatoire pour les navires qui pêchent avec des engins de fond ; côté suédois, pour tout
navire de pêche présent.</dd>
</dl>

<h3>Les calculs</h3><dl>
<dt>Passage dans une zone (magenta) / dans un tampon (ambre)</dt><dd>Partie d'un trajet située à l'intérieur d'une
zone restreinte ou d'un tampon.</dd>
<dt>Temps dans une zone</dt><dd>Durée estimée en supposant que le navire avance à vitesse constante entre deux messages.
Si les zones se chevauchent, le même temps n'est compté qu'une fois dans le total.</dd>
</dl>

<h3>Ce que la carte ne dit pas</h3><dl>
<dd>• <b>Un passage dans une zone n'est pas une infraction.</b> Un navire en transit, engins relevés, est en règle,
et l'AIS ne dit pas quel engin est à bord.</dd>
<dd>• Les petits navires (moins de 15 m) ne sont pas tenus d'avoir l'AIS : leur absence de la carte est normale.</dd>
<dd>• Les zones plus anciennes du règlement 2017/118 ne sont pas encore affichées. Les sites Natura 2000 et HELCOM
ne le sont pas non plus (données non récupérées).</dd>
<dd>• Période : 3 jours seulement (15 au 17 septembre 2026, heures en temps universel, UTC).</dd>
</dl></div></div>
"""

PANEL_HTML = """
<div id="st-panel">
 <div class="st-head"><h1>SeaTrace · Kattegat</h1>
  <button id="st-gl-open" title="Définitions des termes employés">? Comprendre les termes</button>
  <button id="st-collapse" title="Réduire ou agrandir le panneau">Réduire</button></div>
 <div class="st-body">
  <div class="st-sec">
   <div class="st-hint">Trajets AIS des navires de pêche (classe A) du <b id="st-period"></b>, et zones où la pêche est
   restreinte. Survolez une zone ou un trajet pour l'expliquer ; cliquez sur un trajet pour sa fiche.</div>
  </div>
  <div class="st-sec"><h2>Étudier un navire</h2>
   <div class="st-row"><input type="search" id="st-search" placeholder="Chercher par nom ou par MMSI…" aria-label="Chercher un navire"></div>
   <div class="st-row"><select id="st-vessel" aria-label="Navire à étudier"></select></div>
   <div class="st-row st-checks"><label><input type="checkbox" id="st-others" checked> Garder les autres navires en gris</label></div>
   <div id="st-card"></div>
  </div>
  <div class="st-sec"><h2>Filtres</h2>
   <div class="st-row"><label class="t" for="st-passage">Passage dans une zone
     <span class="st-help" title="Zone restreinte : zone du règlement 2022/952 (rouge). Tampon : bande de 4 milles autour des zones danoises (orange).">?</span></label>
    <select id="st-passage">
     <option value="all">Tous les navires</option>
     <option value="zone">Passés dans une zone restreinte</option>
     <option value="any">Passés dans une zone ou un tampon</option>
     <option value="buffer">Passés seulement dans un tampon</option>
     <option value="none">Jamais passés dans une zone ni un tampon</option>
    </select></div>
   <div class="st-row"><label class="t">Longueur déclarée
     <span class="st-help" title="Longueur déclarée dans l'AIS. Dans l'UE, l'AIS est obligatoire pour les navires de pêche de plus de 15 m.">?</span></label>
    <div class="st-checks" id="st-lens"></div></div>
   <div class="st-row"><label class="t" for="st-flag">Pavillon
     <span class="st-help" title="Pays du pavillon, déduit des 3 premiers chiffres du MMSI.">?</span></label>
    <select id="st-flag"><option value="all">Tous les pavillons</option></select></div>
   <div class="st-row"><label class="t" for="st-fishing">Statut « en pêche » déclaré
     <span class="st-help" title="Statut choisi à la main par l'équipage (Engaged in fishing). Déclaratif : souvent laissé activé, même au port.">?</span></label>
    <select id="st-fishing">
     <option value="all">Indifférent</option>
     <option value="yes">A déclaré être en pêche au moins une fois</option>
     <option value="no">N'a jamais déclaré être en pêche</option>
    </select></div>
   <div class="st-row"><label class="t">Jours (UTC)
     <span class="st-help" title="Un trajet qui chevauche plusieurs jours est affiché en entier.">?</span></label>
    <div style="display:flex;gap:6px;align-items:center"><span>du</span><select id="st-d0"></select><span>au</span><select id="st-d1"></select></div></div>
   <div class="st-row st-checks"><label><input type="checkbox" id="st-still"> Masquer les trajets quasi immobiles
     (vitesse moyenne &lt; <span id="st-still-kn"></span> nœud, navire probablement à quai)</label></div>
   <div class="st-row"><button class="st-btn" id="st-reset">Tout réinitialiser</button></div>
   <div class="st-count" id="st-count"></div>
  </div>
  <div class="st-sec"><h2>Légende</h2><div class="st-leg" id="st-legend"></div>
   <div class="st-hint" style="margin-top:6px">Les couches s'affichent ou se masquent en haut à droite.</div>
   <div class="st-warn"><b>À lire :</b> un passage dans une zone n'est pas une infraction. Un navire en transit, engins
   relevés, est en règle.</div>
   <div class="st-hint" id="st-anim" style="margin-top:6px"></div>
  </div>
 </div>
</div>
"""

PANEL_JS = """
<script>
document.addEventListener("DOMContentLoaded", function () {
const DATA = JSON.parse(document.getElementById("st-data").textContent);
const map = __MAP__, fgTracks = __FG_TRACKS__, fgPortions = __FG_PORTIONS__;
const C = DATA.colors;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtMin = m => { if (!m || m < 0.5) return "aucun"; const t = Math.round(m); if (t < 60) return t + " min";
  const h = Math.floor(t / 60), r = t % 60; return h + " h" + (r ? " " + String(r).padStart(2, "0") : ""); };
const fmtDate = iso => { const d = new Date(iso); const p = n => String(n).padStart(2, "0");
  return p(d.getUTCDate()) + "/" + p(d.getUTCMonth() + 1) + " à " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()); };
const fmtDay = s => { const [y, m, d] = s.split("-"); return d + "/" + m + "/" + y; };
const pct = x => Math.round(100 * x) + " %";
const nameOf = p => p.name || "(sans nom)";
const lenTxt = v => v == null ? "inconnue" : v + " m";
const kindTxt = {zone: "une zone restreinte", buffer: "un tampon de 4 milles"};

const tracks = DATA.tracks.features;
const portionsByTid = {};
DATA.portions.features.forEach(f => (portionsByTid[f.properties.tid] ||= []).push(f));

// --- Commandes ---------------------------------------------------------------
$("st-period").textContent = fmtDay(DATA.days[0]) + " au " + fmtDay(DATA.days[DATA.days.length - 1]);
$("st-still-kn").textContent = String(DATA.still).replace(".", ",");
DATA.lenClasses.forEach(c => { $("st-lens").insertAdjacentHTML("beforeend",
  `<label><input type="checkbox" value="${c.k}" checked> ${c.label}</label>`); });
DATA.flags.forEach(f => $("st-flag").insertAdjacentHTML("beforeend", `<option>${esc(f)}</option>`));
DATA.days.forEach((d, i) => { ["st-d0", "st-d1"].forEach(id => $(id).insertAdjacentHTML("beforeend",
  `<option value="${i}">${fmtDay(d)}</option>`)); });
$("st-d1").value = DATA.days.length - 1;
if (DATA.animation) $("st-anim").innerHTML = '<a href="map_animation.html" target="_blank">Voir les trajets animés dans le temps</a> (page séparée).';

const legend = [
  ["fill", "#d62828", 0.35, "", "Zone où les engins de fond sont interdits (DK)"],
  ["fill", "#8b0000", 0.55, "", "Zone où la pêche est interdite (SE)"],
  ["fill", "#d62828", 0.15, "dash", "Zone où seuls certains engins sont permis (SE)"],
  ["fill", "#f4a261", 0.22, "", "Tampon de 4 milles (hypothèse du projet)"],
  ["fill", "#2a78d6", 0, "dash", "Zone où l'AIS est obligatoire (officielle, masquée par défaut)"],
  ["line", C.track, 1, "", "Trajet d'un navire de pêche"],
  ["line", C.selected, 1, "thick", "Navire étudié"],
  ["line", C.other, 1, "", "Autres navires (quand un navire est étudié)"],
  ["line", C.portion_zone, 1, "thick", "Passage dans une zone restreinte"],
  ["line", C.portion_buffer, 1, "thick", "Passage dans un tampon de 4 milles"],
];
legend.forEach(([t, c, o, s, label]) => {
  const hex = Math.round(o * 255).toString(16).padStart(2, "0");
  const sw = t === "fill"
    ? `<span class="st-sw" style="background:${c}${hex};border:1.5px ${s === "dash" ? "dashed" : "solid"} ${c}"></span>`
    : `<span class="st-sw" style="height:0;border-top:${s === "thick" ? 4 : 2}px solid ${c}"></span>`;
  $("st-legend").insertAdjacentHTML("beforeend", `<div>${sw}<span>${esc(label)}</span></div>`);
});

// --- État et filtres ----------------------------------------------------------
const state = {};
function readState() {
  state.vessel = $("st-vessel").value;
  state.others = $("st-others").checked;
  state.passage = $("st-passage").value;
  state.flag = $("st-flag").value;
  state.fishing = $("st-fishing").value;
  state.lens = new Set([...$("st-lens").querySelectorAll("input:checked")].map(i => i.value));
  state.still = $("st-still").checked;
  let a = +$("st-d0").value, b = +$("st-d1").value; if (a > b) [a, b] = [b, a];
  state.t0 = Date.parse(DATA.days[a] + "T00:00:00Z"); state.t1 = Date.parse(DATA.days[b] + "T00:00:00Z") + 864e5;
  state.search = $("st-search").value.trim().toLowerCase();
}
const inDays = f => Date.parse(f.properties.end) >= state.t0 && Date.parse(f.properties.start) < state.t1;
const notStill = f => !state.still || f.properties.sog == null || f.properties.sog >= DATA.still;

function aggregate(list) {
  const by = {};
  list.forEach(f => { const p = f.properties;
    const v = by[p.mmsi] ||= {mmsi: p.mmsi, name: p.name, flag: p.flag, len: p.len, lc: p.lc, tracks: [],
      mz: 0, mb: 0, n: 0, sogW: 0, sogN: 0, engW: 0, km: 0, start: p.start, end: p.end, zones: {}};
    v.tracks.push(f); v.mz += p.mz; v.mb += p.mb; v.n += p.n; v.km += p.km;
    if (p.sog != null) { v.sogW += p.sog * p.n; v.sogN += p.n; }
    v.engW += p.eng * p.n;
    if (p.start < v.start) v.start = p.start; if (p.end > v.end) v.end = p.end;
    p.zones.forEach(z => { const k = z.n; (v.zones[k] ||= {n: z.n, k: z.k, c: z.c, m: 0}).m += z.m; });
  });
  Object.values(by).forEach(v => { v.sog = v.sogN ? v.sogW / v.sogN : null; v.eng = v.n ? v.engW / v.n : 0;
    v.status = v.mz > 0 ? "zone" : v.mb > 0 ? "buffer" : "none"; });
  return by;
}
function vesselOk(v) {
  if (state.flag !== "all" && v.flag !== state.flag) return false;
  if (!state.lens.has(v.lc)) return false;
  if (state.fishing === "yes" && !(v.eng > 0)) return false;
  if (state.fishing === "no" && v.eng > 0) return false;
  const s = state.passage;
  if (s === "zone" && v.status !== "zone") return false;
  if (s === "any" && v.status === "none") return false;
  if (s === "buffer" && v.status !== "buffer") return false;
  if (s === "none" && v.status !== "none") return false;
  return true;
}

// --- Dessin -------------------------------------------------------------------
// Même moteur de dessin que les zones (un second calque bloquerait leurs infobulles),
// avec une tolérance de clic élargie pour attraper les lignes fines.
const renderer = map.getRenderer(L.polyline([[0, 0], [0, 0]]));
renderer.options.tolerance = 6;
function trackPopup(f) {
  const p = f.properties;
  const zl = p.zones.length ? "<ul class='st-zl'>" + p.zones.map(z =>
    `<li>${esc(z.n)}${z.k === "zone" ? ` <i>(${esc(z.c)})</i>` : ""} : ${fmtMin(z.m)}</li>`).join("") + "</ul>" : " aucune";
  return `<b style="font-size:14px">${esc(nameOf(p))}</b><br>MMSI ${p.mmsi} · ${esc(p.flag)} · ${lenTxt(p.len)}
   <table style="margin-top:4px">
   <tr><td>Trajet</td><td>du ${fmtDate(p.start)} au ${fmtDate(p.end)} UTC</td></tr>
   <tr><td>Vitesse moyenne</td><td>${p.sog == null ? "inconnue" : String(p.sog.toFixed(1)).replace(".", ",") + " nœud"}${p.sog != null && p.sog < DATA.still ? " (quasi immobile)" : ""}</td></tr>
   <tr><td>Statut « en pêche »</td><td>${pct(p.eng)} des messages</td></tr>
   <tr><td>Temps en zone restreinte</td><td>${fmtMin(p.mz)}</td></tr>
   <tr><td>Temps en tampon</td><td>${fmtMin(p.mb)}</td></tr></table>
   <div>Zones traversées :${zl}</div>
   <div style="margin-top:6px"><button class="st-btn primary" data-study="${p.mmsi}">Étudier ce navire</button></div>`;
}
function draw(list, mode) {
  const style = mode === "selected" ? {color: C.selected, weight: 3, opacity: 0.95}
              : mode === "other" ? {color: C.other, weight: 1, opacity: 0.45}
              : {color: C.track, weight: 1.6, opacity: 0.7};
  const layer = L.geoJSON({type: "FeatureCollection", features: list}, {renderer, style: () => style,
    onEachFeature: (f, l) => {
      l.bindTooltip(`<b>${esc(nameOf(f.properties))}</b> · MMSI ${f.properties.mmsi}<br><span style="color:#55534e">Cliquer pour la fiche</span>`, {sticky: true});
      l.bindPopup(() => trackPopup(f), {maxWidth: 340});
      if (mode !== "other") l.on("mouseover", () => l.setStyle({weight: style.weight + 2}))
                             .on("mouseout", () => l.setStyle({weight: style.weight}));
    }});
  fgTracks.addLayer(layer);
}
function drawPortions(list) {
  const feats = list.flatMap(f => portionsByTid[f.properties.tid] || []);
  const byTid = Object.fromEntries(list.map(f => [f.properties.tid, f.properties]));
  fgPortions.addLayer(L.geoJSON({type: "FeatureCollection", features: feats}, {renderer,
    style: f => ({color: f.properties.k === "zone" ? C.portion_zone : C.portion_buffer, weight: 4, opacity: 0.95}),
    onEachFeature: (f, l) => { const t = byTid[f.properties.tid];
      const zs = t.zones.filter(z => z.k === f.properties.k).map(z => `${esc(z.n)} : ${fmtMin(z.m)}`).join("<br>");
      l.bindTooltip(`<b>${esc(nameOf(t))}</b> · passage dans ${kindTxt[f.properties.k]}<br>Temps total : ${fmtMin(f.properties.m)}<br>${zs}`, {sticky: true});
      l.bindPopup(() => trackPopup({properties: t}), {maxWidth: 340}); }}));
}

function vesselCard(v) {
  if (!v) return "";
  const zones = Object.values(v.zones).sort((a, b) => b.m - a.m);
  const rz = zones.filter(z => z.k === "zone"), bz = zones.filter(z => z.k === "buffer");
  const zl = (rz.length ? "<div style='margin-top:4px'>Zones restreintes :</div><ul class='st-zl'>" + rz.map(z =>
      `<li><b>${esc(z.n)}</b> : ${fmtMin(z.m)}<br><span style="color:#55534e">${esc(z.c)}</span></li>`).join("") + "</ul>" : "")
    + (bz.length ? "<div style='margin-top:4px'>Tampons de 4 milles :</div><ul class='st-zl'>" + bz.map(z =>
      `<li>${esc(z.n.replace("Tampon de 4 milles autour de ", "autour de "))} : ${fmtMin(z.m)}</li>`).join("") + "</ul>" : "")
    || "<div class='st-hint'>Aucune zone ni tampon traversé sur la période.</div>";
  const still = v.sog != null && v.sog < DATA.still;
  return `<div class="st-card"><h3>${esc(nameOf(v))}</h3>
   <table>
    <tr><td>MMSI</td><td>${v.mmsi}</td></tr>
    <tr><td>Pavillon (d'après le MMSI)</td><td>${esc(v.flag)}</td></tr>
    <tr><td>Longueur déclarée</td><td>${lenTxt(v.len)}</td></tr>
    <tr><td>Observé</td><td>du ${fmtDate(v.start)} au ${fmtDate(v.end)} UTC</td></tr>
    <tr><td>Trajets</td><td>${v.tracks.length} (coupés après ${DATA.gap} min sans signal)</td></tr>
    <tr><td>Distance parcourue</td><td>${Math.round(v.km)} km</td></tr>
    <tr><td>Vitesse moyenne</td><td>${v.sog == null ? "inconnue" : v.sog.toFixed(1).replace(".", ",") + " nœud"}</td></tr>
    <tr><td>Statut « en pêche » déclaré</td><td>${pct(v.eng)} des messages</td></tr>
    <tr><td>Temps en zone restreinte</td><td><b>${fmtMin(v.mz)}</b></td></tr>
    <tr><td>Temps en tampon de 4 milles</td><td><b>${fmtMin(v.mb)}</b></td></tr>
   </table>
   <div style="margin-top:6px"><b>Zones traversées</b>${zl}</div>
   ${zones.filter(z => z.k === "buffer").reduce((a, z) => a + z.m, 0) > v.mb + 1 ? `<div class="st-hint">Les tampons se chevauchent : un même moment peut compter pour plusieurs tampons. Le temps total en tampon ci-dessus ne le compte qu'une fois.</div>` : ""}
   ${still ? `<div class="st-warn">Vitesse moyenne très faible : le navire est probablement resté à quai ou au mouillage. Son « trajet » est alors surtout le bruit du GPS.</div>` : ""}
   ${v.eng > 0.9 && still ? `<div class="st-warn">Le statut « en pêche » reste activé alors que le navire est quasi immobile : ce statut est déclaratif.</div>` : ""}
   <div style="margin-top:8px;display:flex;gap:6px"><button class="st-btn" id="st-zoom">Centrer la carte</button>
   <button class="st-btn" id="st-clear">Revenir à tous les navires</button></div></div>`;
}

let lastFit = null;
function fit(features) {
  if (!features.length) return;
  const b = L.geoJSON({type: "FeatureCollection", features}).getBounds();
  if (!b.isValid()) return;
  const panel = $("st-panel"); const w = panel.classList.contains("collapsed") ? 0 : panel.offsetWidth;
  map.fitBounds(b, {paddingTopLeft: [w + 30, 30], paddingBottomRight: [30, 30], maxZoom: 12});
}

function fillVesselSelect(vessels) {
  const sel = $("st-vessel"), cur = state.vessel;
  const groups = {zone: "Passés dans une zone restreinte", buffer: "Passés seulement dans un tampon", none: "Sans passage dans une zone ni un tampon"};
  let html = `<option value="">— Tous les navires (${vessels.length}) —</option>`;
  for (const g of ["zone", "buffer", "none"]) {
    const vs = vessels.filter(v => v.status === g).filter(v => !state.search ||
      (v.name || "").toLowerCase().includes(state.search) || v.mmsi.includes(state.search))
      .sort((a, b) => (b.mz + b.mb) - (a.mz + a.mb) || nameOf(a).localeCompare(nameOf(b)));
    if (!vs.length) continue;
    html += `<optgroup label="${groups[g]} (${vs.length})">` + vs.map(v =>
      `<option value="${v.mmsi}">${esc(nameOf(v))} · ${lenTxt(v.len)} · ${esc(v.flag)}${g !== "none" ? " · " + fmtMin(g === "zone" ? v.mz : v.mb) : ""}</option>`).join("") + "</optgroup>";
  }
  sel.innerHTML = html;
  if (cur && [...sel.options].some(o => o.value === cur)) sel.value = cur;
  else if (cur) { sel.insertAdjacentHTML("afterbegin", `<option value="${cur}">${cur} (hors filtres)</option>`); sel.value = cur; }
}

function apply(opts = {}) {
  readState();
  fgTracks.clearLayers(); fgPortions.clearLayers();
  const dayTracks = tracks.filter(inDays);
  const shown = dayTracks.filter(notStill);
  const vessels = aggregate(shown);
  const okVessels = Object.values(vessels).filter(vesselOk);
  const okSet = new Set(okVessels.map(v => v.mmsi));
  fillVesselSelect(okVessels);
  let visible;
  if (state.vessel) {
    const mine = dayTracks.filter(f => f.properties.mmsi === state.vessel);
    if (state.others) draw(shown.filter(f => okSet.has(f.properties.mmsi) && f.properties.mmsi !== state.vessel), "other");
    draw(mine, "selected"); drawPortions(mine); visible = mine;
    $("st-card").innerHTML = vesselCard(aggregate(mine)[state.vessel]);
    $("st-zoom") && ($("st-zoom").onclick = () => fit(mine));
    $("st-clear") && ($("st-clear").onclick = () => { $("st-vessel").value = ""; apply({fit: true}); });
    if (opts.fit) fit(mine);
  } else {
    visible = shown.filter(f => okSet.has(f.properties.mmsi));
    draw(visible, "normal"); drawPortions(visible);
    $("st-card").innerHTML = "";
    if (opts.fit) fit(visible);
  }
  const nInZone = okVessels.filter(v => v.status === "zone").length;
  const nStill = dayTracks.length - shown.length;
  $("st-count").innerHTML = state.vessel
    ? `Navire étudié : <b>${$("st-vessel").selectedOptions[0]?.text.split(" · ")[0] || state.vessel}</b>, ${visible.length} trajet(s).`
    : `<b>${okVessels.length}</b> navires et <b>${visible.length}</b> trajets affichés, sur ${DATA.nVessels} navires et ${DATA.nTracks} trajets.<br>
       Dont ${nInZone} navire(s) passés dans une zone restreinte.` + (state.still ? `<br>${nStill} trajet(s) quasi immobiles masqués.` : "");
}

// --- Événements -----------------------------------------------------------------
$("st-vessel").addEventListener("change", () => apply({fit: true}));
$("st-search").addEventListener("input", () => apply());
["st-others", "st-passage", "st-flag", "st-fishing", "st-d0", "st-d1", "st-still"].forEach(id =>
  $(id).addEventListener("change", () => apply()));
$("st-lens").addEventListener("change", () => apply());
$("st-reset").addEventListener("click", () => {
  $("st-vessel").value = ""; $("st-search").value = ""; $("st-others").checked = true;
  ["st-passage", "st-flag", "st-fishing"].forEach(id => $(id).selectedIndex = 0);
  $("st-lens").querySelectorAll("input").forEach(i => i.checked = true);
  $("st-d0").value = 0; $("st-d1").value = DATA.days.length - 1; $("st-still").checked = false;
  apply({fit: true});
});
map.on("popupopen", e => { const b = e.popup.getElement().querySelector("[data-study]");
  if (b) b.addEventListener("click", () => { map.closePopup(); $("st-search").value = ""; readState();
    state.vessel = ""; apply(); $("st-vessel").value = b.dataset.study;
    if ($("st-vessel").value !== b.dataset.study) $("st-vessel").insertAdjacentHTML("afterbegin", `<option value="${b.dataset.study}">${b.dataset.study}</option>`), $("st-vessel").value = b.dataset.study;
    apply({fit: true}); }); });
$("st-collapse").addEventListener("click", () => { const p = $("st-panel"); p.classList.toggle("collapsed");
  $("st-collapse").textContent = p.classList.contains("collapsed") ? "Ouvrir" : "Réduire"; });
$("st-gl-open").addEventListener("click", () => $("st-modal").classList.add("open"));
$("st-gl-close").addEventListener("click", () => $("st-modal").classList.remove("open"));
$("st-modal").addEventListener("click", e => { if (e.target.id === "st-modal") e.target.classList.remove("open"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") $("st-modal").classList.remove("open"); });
L.DomEvent.disableClickPropagation($("st-panel")); L.DomEvent.disableScrollPropagation($("st-panel"));
L.control.zoom({position: "bottomright", zoomInTitle: "Zoomer", zoomOutTitle: "Dézoomer"}).addTo(map);
L.control.scale({position: "bottomright", imperial: false}).addTo(map);

apply({fit: true});
});
</script>
"""


def add_panel(m, data: dict, fg_tracks, fg_portions):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    js = (PANEL_JS.replace("__MAP__", m.get_name()).replace("__FG_TRACKS__", fg_tracks.get_name())
          .replace("__FG_PORTIONS__", fg_portions.get_name()))
    html = (PANEL_CSS + PANEL_HTML + GLOSSARY
            + f'<script type="application/json" id="st-data">{payload}</script>' + js)
    # Le contenu passe par Jinja (branca) : on le protège tel quel.
    m.get_root().html.add_child(Element("{% raw %}" + html + "{% endraw %}"))


def animation(tracks_points: pl.DataFrame, zones: gpd.GeoDataFrame, names: dict, out: pathlib.Path):
    """Positions échantillonnées toutes les ANIM_STEP_MIN minutes, une ligne par trajet."""
    s = (tracks_points.with_columns(pl.col("timestamp").dt.truncate(f"{ANIM_STEP_MIN}m").alias("bucket"))
         .group_by(["track_id", "bucket"], maintain_order=True).first()
         .sort(["track_id", "timestamp"]))
    feats = []
    for (tid,), g in s.group_by(["track_id"], maintain_order=True):
        if g.height < 2:
            continue
        mmsi = tid.split("-")[0]
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString",
                                   "coordinates": [[round(x, 5), round(y, 5)] for x, y in zip(g["lon"], g["lat"])]},
                      "properties": {"times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in g["timestamp"]],
                                     "popup": f"{names.get(mmsi) or '(sans nom)'} · MMSI {mmsi}",
                                     "style": {"color": COLORS["track"], "weight": 2, "opacity": 0.8}}})
    m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=TILES, prefer_canvas=True, control_scale=True)
    for layer, color, op in [("buffer", COLORS["buffer"], 0.2), ("zone", COLORS["a1"], 0.35)]:
        zz = zones[zones.layer == layer]
        folium.GeoJson(fc([feature(r.geometry, {"n": zone_label(r.zone_id, r.name)}) for r in zz.itertuples()]),
                       style_function=lambda f, c=color, o=op: {"color": c, "weight": 1, "fillColor": c, "fillOpacity": o},
                       tooltip=folium.GeoJsonTooltip(["n"], labels=False)).add_to(m)
    TimestampedGeoJson({"type": "FeatureCollection", "features": feats}, period=f"PT{ANIM_STEP_MIN}M",
                       duration="PT2H", add_last_point=True, auto_play=False, loop=False,
                       date_options="YYYY-MM-DD HH:mm", transition_time=100).add_to(m)
    m.get_root().html.add_child(Element(
        "<div style='position:fixed;top:10px;left:50px;z-index:1000;background:#fff;padding:6px 10px;"
        "border:1px solid #c9c7c0;border-radius:4px;font:13px system-ui,sans-serif'>"
        "<b>Trajets animés</b> · utilisez le curseur en bas pour avancer dans le temps (UTC) · "
        "<a href='map.html'>retour à la carte principale</a></div>"))
    m.save(out)
    return len(feats), int(s.height)


def main():
    crossing_mod = _load("07_crossing")
    tracks_mod = crossing_mod.tracks_mod
    crossing_mod.crossing(PERIOD_START, PERIOD_END, GAP_MINUTES, SIMPLIFY_M)
    tag = tracks_mod.tag(PERIOD_START, PERIOD_END, GAP_MINUTES, SIMPLIFY_M)
    d = tracks_mod.OUT_DIR
    meta = json.loads((d / f"tracks_{tag}.json").read_text())
    tracks = gpd.read_parquet(d / f"tracks_{tag}.parquet")
    tot = pd.read_parquet(d / f"crossing_tracks_{tag}.parquet")
    tracks = tracks.merge(tot, on="track_id", how="left").fillna({"minutes_in_zone": 0, "minutes_in_buffer": 0})
    per_zone = pl.read_parquet(d / f"crossing_{tag}.parquet")
    portions = gpd.read_parquet(d / f"portions_{tag}.parquet")
    dur = tot.melt(id_vars="track_id", var_name="layer", value_name="minutes")
    dur["layer"] = dur.layer.str.replace("minutes_in_", "")
    portions = portions.merge(dur, on=["track_id", "layer"], how="left")

    zones = gpd.read_file(REF / "kattegat_zones.geojson")
    if zones.crs is None or zones.crs.to_epsg() != 4326:
        raise SystemExit(f"kattegat_zones.geojson : CRS inattendu {zones.crs}")
    if not zones.is_valid.all():
        raise SystemExit("kattegat_zones.geojson contient des géométries invalides : relancer 03_zones.py.")

    m = folium.Map(location=CENTER, zoom_start=ZOOM, tiles=None, prefer_canvas=True,
                   zoom_control=False, control_scale=False)
    folium.TileLayer(TILES, name="Fond de carte gris clair (Esri)").add_to(m)
    zones_layers(m, zones)
    missing = protected_layers(m)
    fg_tracks = folium.FeatureGroup("Trajets des navires de pêche", show=True).add_to(m)
    fg_portions = folium.FeatureGroup("Passages dans une zone ou un tampon", show=True).add_to(m)
    add_panel(m, build_data(tracks, per_zone, portions, meta), fg_tracks, fg_portions)
    folium.LayerControl(collapsed=False, position="topright").add_to(m)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / "map.html"
    m.save(out)
    print(f"[out ] {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} Mo)")
    if missing:
        print("[miss] couches absentes :", "; ".join(missing))
    if ANIMATION:
        pts = pl.read_parquet(d / f"points_{tag}.parquet")
        names = {k: v for k, v in zip(tracks.mmsi, tracks.name) if isinstance(v, str)}
        aout = REPORTS / "map_animation.html"
        n, k = animation(pts, zones, names, aout)
        print(f"[out ] {aout.relative_to(ROOT)} ({aout.stat().st_size / 1e6:.1f} Mo, {n} trajets, {k} positions)")


if __name__ == "__main__":
    main()
