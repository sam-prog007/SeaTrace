"""Connecteurs vers les données ouvertes (et formats d'échange) utilisés par la V2.

Tout fichier reste NON FIABLE : limites de taille, colonnes attendues, valeurs bornées ;
rien n'est exécuté. Les conversions produisent le format partagé SeaTrace, qui repasse
ensuite par l'import sécurisé (`schema.lire_csv_securise`).

Formats pris en charge :
- AIS de l'Autorité maritime danoise (DMA, web.ais.dk/aisdata) : CSV quotidiens
  « # Timestamp, Type of mobile, MMSI, Latitude, Longitude, ..., SOG, COG, Ship type, Name » ;
- AIS NOAA / MarineCadastre (coast.noaa.gov/htdata/CMSP/AISDataHandler) : CSV
  « MMSI, BaseDateTime, LAT, LON, SOG, COG, VesselName, VesselType, ... » (et la variante en
  minuscules « mmsi, base_date_time, latitude, longitude, ... ») ;
- GeoJSON de terres (OpenStreetMap land polygons, Natural Earth, GSHHG converti) et de zones
  (Shom délimitations maritimes, CNSP réglementation des pêches, Protected Planet/WDPA,
  Marine Regions) — Polygon et MultiPolygon, anneaux extérieurs ;
- densité de trafic EMODnet exportée en XYZ (lon, lat, valeur), par ex. `gdal_translate -of XYZ` ;
- courants Copernicus Marine exportés en CSV (longitude, latitude, time, uo, vo en m/s) ;
- détections externes (Sentinel-1/2, Global Fishing Watch SAR, RF) au format
  `obs_id, timestamp, lat, lon, precision_m, proba_attribution, source`.

Les licences sont rappelées dans `seatrace/catalogue.py` et docs/DONNEES_OUVERTES.md.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .geo import Zone, vers_km

# --- AIS --------------------------------------------------------------------------

ALIAS_AIS = {
    "mmsi": ["mmsi"],
    "timestamp": ["# timestamp", "timestamp", "basedatetime", "base_date_time"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon"],
    "sog": ["sog"],
    "cog": ["cog"],
    "nom": ["name", "vesselname", "vessel_name"],
    "type": ["ship type", "vesseltype", "vessel_type"],
    "mobile": ["type of mobile", "transceiverclass", "transceiver_class", "transceiver"],
}

# DMA : libellés texte ; NOAA : codes numériques ITU (30 pêche, 36 voile, 37 plaisance)
TYPES_DMA = {"sailing": "ais_voilier", "pleasure": "ais_plaisance", "fishing": "ais_peche"}
TYPES_NOAA = {30: "ais_peche", 36: "ais_voilier", 37: "ais_plaisance"}


def _colonnes(entete):
    norm = {c.strip().lower(): c for c in entete}
    out = {}
    for cle, alias in ALIAS_AIS.items():
        for a in alias:
            if a in norm:
                out[cle] = norm[a]
                break
    return out


def detecter_format(entete) -> str:
    e = {c.strip().lower() for c in entete}
    if {"boat_id", "trip_id", "timestamp", "lat", "lon"} <= e:
        return "seatrace"
    if "# timestamp" in e or ("type of mobile" in e and "latitude" in e):
        return "dma"
    if "basedatetime" in e or "base_date_time" in e:
        return "noaa"
    return "inconnu"


def _type_bateau(fmt, valeur):
    if fmt == "dma":
        return TYPES_DMA.get(str(valeur).strip().lower(), "ais_autre")
    try:
        return TYPES_NOAA.get(int(float(valeur)), "ais_autre")
    except (TypeError, ValueError):
        return "ais_autre"


def _lire_tableau(source, chunksize=200_000):
    """`source` : chemin (CSV ou ZIP contenant un CSV) ou octets. Itère sur des morceaux."""
    if isinstance(source, (bytes, bytearray)):
        flux = io.BytesIO(source)
        if zipfile.is_zipfile(flux):
            z = zipfile.ZipFile(flux)
            noms = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not noms:
                raise ValueError("Archive sans fichier CSV.")
            flux = z.open(noms[0])
        else:
            flux.seek(0)
        return pd.read_csv(flux, chunksize=chunksize, dtype=str, keep_default_na=False)
    chemin = Path(source)
    if zipfile.is_zipfile(chemin):
        z = zipfile.ZipFile(chemin)
        noms = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not noms:
            raise ValueError("Archive sans fichier CSV.")
        return pd.read_csv(z.open(noms[0]), chunksize=chunksize, dtype=str, keep_default_na=False)
    return pd.read_csv(chemin, chunksize=chunksize, dtype=str, keep_default_na=False)


def convertir_ais(source, bbox=None, types=None, mmsi=None, debut=None, fin=None, ecart_trajet_min=30.0,
                  min_points=20, pas_min_s=30.0, lignes_max=50_000):
    """Convertit un fichier AIS (DMA ou NOAA) au format SeaTrace.

    bbox : (lat_min, lat_max, lon_min, lon_max) ; types : liste de types SeaTrace (ex. ["ais_voilier"]) ;
    mmsi : liste d'identifiants ; debut/fin : bornes temporelles (Timestamp UTC).
    Un trajet = positions d'un même MMSI sans interruption de plus de `ecart_trajet_min` minutes.
    Les positions plus rapprochées que `pas_min_s` sont sous-échantillonnées.

    Retourne (DataFrame au format SeaTrace, dict bateaux, informations)."""
    morceaux, fmt, cols = [], None, None
    lues = 0
    for bloc in _lire_tableau(source):
        if fmt is None:
            fmt = detecter_format(bloc.columns)
            if fmt not in ("dma", "noaa"):
                raise ValueError("Format AIS non reconnu (attendu : DMA ou NOAA MarineCadastre).")
            cols = _colonnes(bloc.columns)
            manquantes = [c for c in ("mmsi", "timestamp", "lat", "lon") if c not in cols]
            if manquantes:
                raise ValueError("Colonnes AIS absentes : " + ", ".join(manquantes))
        lues += len(bloc)
        d = pd.DataFrame({k: bloc[v] for k, v in cols.items()})
        d["lat"] = pd.to_numeric(d["lat"], errors="coerce")
        d["lon"] = pd.to_numeric(d["lon"], errors="coerce")
        d = d[(d["lat"].abs() <= 90) & (d["lon"].abs() <= 180) & ~((d["lat"] == 0) & (d["lon"] == 0))]
        if bbox:
            d = d[(d["lat"] >= bbox[0]) & (d["lat"] <= bbox[1]) & (d["lon"] >= bbox[2]) & (d["lon"] <= bbox[3])]
        if "mobile" in d and fmt == "dma":
            d = d[d["mobile"].str.strip().str.lower().isin(["class a", "class b"])]
        d = d[d["mmsi"].str.fullmatch(r"\d{9}")]
        if mmsi:
            d = d[d["mmsi"].isin([str(m) for m in mmsi])]
        if d.empty:
            continue
        d["type_bateau"] = d["type"].map(lambda v: _type_bateau(fmt, v)) if "type" in d else "ais_autre"
        if types:
            d = d[d["type_bateau"].isin(types)]
        if d.empty:
            continue
        if fmt == "dma":
            d["timestamp"] = pd.to_datetime(d["timestamp"], format="%d/%m/%Y %H:%M:%S", errors="coerce", utc=True)
        else:
            d["timestamp"] = pd.to_datetime(d["timestamp"], errors="coerce", utc=True)
        d = d.dropna(subset=["timestamp"])
        if debut is not None:
            d = d[d["timestamp"] >= debut]
        if fin is not None:
            d = d[d["timestamp"] <= fin]
        morceaux.append(d)
    if not morceaux:
        raise ValueError("Aucune position AIS ne correspond aux filtres (zone, types, période).")
    d = pd.concat(morceaux, ignore_index=True).sort_values(["mmsi", "timestamp"], kind="stable")
    d = d.drop_duplicates(subset=["mmsi", "timestamp"])
    sog = pd.to_numeric(d.get("sog"), errors="coerce") if "sog" in d else np.nan
    cog = pd.to_numeric(d.get("cog"), errors="coerce") if "cog" in d else np.nan
    d["sog_kn"] = np.where((sog >= 0) & (sog < 100), sog, np.nan)  # 102.3 = non disponible
    d["cog_deg"] = np.where((cog >= 0) & (cog < 360), cog, np.nan)  # 360 = non disponible
    lignes, bateaux = [], {}
    for m, g in d.groupby("mmsi", sort=False):
        t = (g["timestamp"] - g["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
        coupe = np.concatenate([[True], np.diff(t) > ecart_trajet_min * 60])
        num = np.cumsum(coupe)
        for k, seg in g.groupby(num):
            ts = (seg["timestamp"] - seg["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
            garde = [0]
            for i in range(1, len(ts)):
                if ts[i] - ts[garde[-1]] >= pas_min_s:
                    garde.append(i)
            seg = seg.iloc[garde]
            if len(seg) < min_points:
                continue
            trip = f"{m}-{seg['timestamp'].iloc[0]:%Y%m%d%H%M}"
            lignes.append(pd.DataFrame({
                "boat_id": m, "trip_id": trip,
                "timestamp": seg["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lat": seg["lat"].round(6), "lon": seg["lon"].round(6),
                "sog_kn": seg["sog_kn"].round(2), "cog_deg": seg["cog_deg"].round(1),
                "accuracy_m": 10.0, "source": "ais", "synthetic": False,
            }))
            nom = re.sub(r"[^A-Za-z0-9 _\-.']", "", str(seg["nom"].iloc[0])) if "nom" in seg else ""
            bateaux[m] = {"type_bateau": seg["type_bateau"].iloc[0], "nom": nom[:40] or f"MMSI {m}",
                          "flotte": f"AIS {fmt.upper()}", "mmsi": m}
    if not lignes:
        raise ValueError(f"Aucun trajet d'au moins {min_points} positions après découpage.")
    out = pd.concat(lignes, ignore_index=True)
    avert = []
    if len(out) > lignes_max:
        # on garde les trajets les plus longs (en nombre de positions)
        tailles = out.groupby("trip_id").size().sort_values(ascending=False)
        garder, total = [], 0
        for trip, n in tailles.items():
            if total + n > lignes_max:
                continue
            garder.append(trip)
            total += n
        out = out[out["trip_id"].isin(garder)]
        avert.append(f"Limite de {lignes_max} lignes : {len(garder)} trajets conservés sur {len(tailles)}.")
        bateaux = {b: v for b, v in bateaux.items() if b in set(out["boat_id"])}
    info = {"format": fmt, "lignes_lues": lues, "positions": len(out), "trajets": out["trip_id"].nunique(),
            "bateaux": out["boat_id"].nunique(), "avertissements": avert}
    return out.reset_index(drop=True), bateaux, info


# --- GeoJSON : terres et zones -------------------------------------------------------

def _decoupe_rectangle(poly, xmin, xmax, ymin, ymax):
    """Sutherland-Hodgman contre un rectangle (fenêtre convexe)."""
    def couper(pts, dedans, inter):
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            ia, ib = dedans(a), dedans(b)
            if ib:
                if not ia:
                    out.append(inter(a, b))
                out.append(b)
            elif ia:
                out.append(inter(a, b))
        return out

    def ix(a, b, x):
        t = (x - a[0]) / (b[0] - a[0])
        return (x, a[1] + t * (b[1] - a[1]))

    def iy(a, b, y):
        t = (y - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), y)

    pts = list(poly)
    for dedans, inter in ((lambda p: p[0] >= xmin, lambda a, b: ix(a, b, xmin)),
                          (lambda p: p[0] <= xmax, lambda a, b: ix(a, b, xmax)),
                          (lambda p: p[1] >= ymin, lambda a, b: iy(a, b, ymin)),
                          (lambda p: p[1] <= ymax, lambda a, b: iy(a, b, ymax))):
        if not pts:
            break
        pts = couper(pts, dedans, inter)
    return pts


def _anneaux(geom):
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Polygon":
        return [c[0]] if c else []
    if t == "MultiPolygon":
        return [p[0] for p in c if p]
    if t == "GeometryCollection":
        return [a for g in geom.get("geometries", []) for a in _anneaux(g)]
    return []


def _nom(props, k):
    for cle in ("nom", "NOM", "name", "NAME", "libelle", "LIBELLE", "ORIG_NAME", "label", "id", "ID"):
        if props.get(cle):
            return re.sub(r"[^\w '\-.(),/]", "", str(props[cle]))[:80]
    return f"Zone {k}"


def lire_geojson(octets: bytes, taille_max=20_000_000, sommets_max=2_000_000):
    if len(octets) > taille_max:
        raise ValueError(f"GeoJSON trop volumineux (maximum {taille_max} octets) : le découper au préalable "
                         "(ex. ogr2ogr -clipsrc xmin ymin xmax ymax).")
    try:
        doc = json.loads(octets.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("GeoJSON illisible (UTF-8 et JSON attendus).") from exc
    feats = doc.get("features") if doc.get("type") == "FeatureCollection" else [doc]
    out, n = [], 0
    for k, f in enumerate(feats or [], 1):
        geom = f.get("geometry") if f.get("type") == "Feature" else f
        props = f.get("properties") or {}
        for anneau in _anneaux(geom or {}):
            n += len(anneau)
            if n > sommets_max:
                raise ValueError("Trop de sommets dans le GeoJSON : simplifier ou découper.")
            pts = [(float(p[0]), float(p[1])) for p in anneau
                   if len(p) >= 2 and math.isfinite(float(p[0])) and math.isfinite(float(p[1]))]
            if len(pts) >= 3:
                out.append((_nom(props, k), props, pts))
    if not out:
        raise ValueError("Aucun polygone dans le GeoJSON.")
    return out


def polygones_km(polys, origine, bornes_km):
    """Projette (lon, lat) -> km et découpe sur l'emprise d'étude ; ignore ce qui est hors emprise."""
    xmin, xmax, ymin, ymax = bornes_km
    out = []
    for nom, props, pts in polys:
        lon = np.array([p[0] for p in pts])
        lat = np.array([p[1] for p in pts])
        x, y = vers_km(lat, lon, origine)
        if x.max() < xmin or x.min() > xmax or y.max() < ymin or y.min() > ymax:
            continue
        coupe = _decoupe_rectangle(list(zip(x.tolist(), y.tolist())), xmin, xmax, ymin, ymax)
        if len(coupe) >= 3:
            out.append((nom, props, coupe))
    return out


def zones_depuis_geojson(polys_km, source, nature="imported_zone"):
    zones, used = [], set()
    for k, (nom, props, pts) in enumerate(polys_km, 1):
        ident = re.sub(r"[^A-Za-z0-9_.\-]", "", str(props.get("zone_id") or f"G{k}"))[:40] or f"G{k}"
        n = 1
        while ident in used:  # a MultiPolygon yields several rings with the same properties
            n += 1
            ident = f"{ident.split('#')[0]}#{n}"
        used.add(ident)
        desc = props.get("description") or (
            f"Imported zone ({source}). Check the version, the dates of application and the applicable text: "
            "a boundary is not necessarily a ban on passage.")
        zones.append(Zone(ident, nom, nature, str(desc)[:600], pts, fictive=False, source=source))
    return zones


KATTEGAT_RULES = {
    "Areas 1": "Danish area where mobile bottom-contacting gear is prohibited (bottom trawls, seines, dredges); "
               "such gear on board must be stowed. Other gear is not covered.",
    "Areas 2": "Swedish area where all fishing is prohibited, including recreational fishing.",
    "Areas 3": "Swedish area where only lines and rods, pelagic trawls, crustacean pots and gillnets "
               "(with on-board cameras) are allowed.",
    "AIS zone": "Area where AIS is compulsory for fishing vessels (Annex V): a reception gap here is "
                "particularly worth investigating.",
}


def _kattegat_name(props):
    name = str(props.get("name", ""))
    zid = str(props.get("zone_id", ""))
    name = name.replace("sous-zone", "sub-area").replace("Zone AIS MSFD", "AIS zone MSFD")
    if zid.endswith("-A2"):
        name += " (no-fishing part)"
    elif zid.endswith("-A3"):
        name += " (restricted-gear part)"
    return name


def zones_kattegat(chemin):
    """Regulated zones of the Kattegat study (Delegated Regulation (EU) 2022/952), with English names.
    The 4-nautical-mile buffers are a project hypothesis, not a legal boundary, so they are left out."""
    doc = json.loads(Path(chemin).read_text(encoding="utf-8"))
    feats = []
    for f in doc["features"]:
        p = f.get("properties", {})
        if p.get("layer") not in ("zone", "ais_zone"):
            continue
        cat = p.get("category")
        country = {"DK": "Denmark", "SE": "Sweden"}.get(p.get("country"), p.get("country"))
        f = dict(f)
        f["properties"] = {
            "name": _kattegat_name(p), "zone_id": p.get("zone_id"),
            "description": f"{KATTEGAT_RULES.get(cat, '')} ({country}; Delegated Regulation (EU) 2022/952, "
                           "see the regulation text for the exact legal reference.) Passing through a zone is not "
                           "an offence in itself."}
        feats.append(f)
    return lire_geojson(json.dumps({"type": "FeatureCollection", "features": feats}).encode("utf-8"))


# --- EMODnet (densité), Copernicus Marine (courants), détections externes -----------

def lire_densite_xyz(octets: bytes, taille_max=50_000_000):
    """XYZ « lon lat valeur » (espaces, virgules ou points-virgules) -> (grille lat×lon, bornes)."""
    if len(octets) > taille_max:
        raise ValueError("Fichier de densité trop volumineux.")
    df = pd.read_csv(io.BytesIO(octets), sep=r"[,\s;]+", engine="python", header=None, comment="#")
    df = df.apply(pd.to_numeric, errors="coerce").dropna()
    if df.shape[1] < 3 or df.empty:
        raise ValueError("Format XYZ attendu : longitude latitude valeur.")
    lon, lat, val = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy(), df.iloc[:, 2].to_numpy()
    ulon, ulat = np.unique(lon), np.unique(lat)
    if len(ulon) * len(ulat) > 4_000_000:
        raise ValueError("Grille de densité trop grande : la découper sur la zone d'étude.")
    j = np.searchsorted(ulon, lon)
    i = np.searchsorted(ulat, lat)
    G = np.zeros((len(ulat), len(ulon)))
    G[i, j] = np.clip(val, 0, None)
    G = G / max(G[G > 0].mean() if (G > 0).any() else 1.0, 1e-12)
    return G, (float(ulat.min()), float(ulat.max()), float(ulon.min()), float(ulon.max()))


def lire_courants(octets: bytes, taille_max=50_000_000) -> pd.DataFrame:
    """Table de courant d'un export CSV Copernicus Marine (time, latitude, longitude, uo, vo en m/s) :
    colonnes time (UTC, NaT si absente), lat, lon (NaN si absentes), u_kmh, v_kmh ; lignes sans valeur
    retirées (mailles à terre)."""
    if len(octets) > taille_max:
        raise ValueError("Fichier de courant trop volumineux.")
    df = pd.read_csv(io.BytesIO(octets))
    cols = {c.lower(): c for c in df.columns}
    for c in ("uo", "vo"):
        if c not in cols:
            raise ValueError("Colonnes uo et vo attendues (m/s).")

    def num(*noms):
        nom = next((cols[n] for n in noms if n in cols), None)
        return pd.to_numeric(df[nom], errors="coerce") if nom else pd.Series(np.nan, index=df.index)

    out = pd.DataFrame({
        "time": pd.to_datetime(df[cols["time"]], utc=True, errors="coerce") if "time" in cols else pd.NaT,
        "lat": num("latitude", "lat"), "lon": num("longitude", "lon"),
        "u_kmh": num("uo") * 3.6, "v_kmh": num("vo") * 3.6,
    })
    out = out[np.isfinite(out["u_kmh"]) & np.isfinite(out["v_kmh"])
              & (out["u_kmh"].abs() <= 15) & (out["v_kmh"].abs() <= 15)]
    return out.reset_index(drop=True)


def courant_local(table: pd.DataFrame, bbox=None, debut=None, fin=None):
    """Moyenne (u_kmh, v_kmh, nombre de valeurs) sur l'emprise (lat_min, lat_max, lon_min, lon_max) et la
    période ; None si aucune valeur. Une table sans coordonnées ou sans dates n'est pas filtrée sur ce
    critère."""
    m = np.ones(len(table), bool)
    if bbox is not None and table["lat"].notna().any():
        m &= ((table["lat"] >= bbox[0]) & (table["lat"] <= bbox[1])
              & (table["lon"] >= bbox[2]) & (table["lon"] <= bbox[3])).to_numpy()
    if table["time"].notna().any():
        if debut is not None:
            m &= (table["time"] >= debut).to_numpy()
        if fin is not None:
            m &= (table["time"] <= fin).to_numpy()
    sel = table[m]
    if sel.empty:
        return None
    return float(sel["u_kmh"].mean()), float(sel["v_kmh"].mean()), int(len(sel))


def courant_moyen(octets: bytes, bbox=None, debut=None, fin=None):
    """Courant moyen (km/h, est et nord) d'un export CSV Copernicus Marine (uo, vo en m/s)."""
    r = courant_local(lire_courants(octets), bbox, debut, fin)
    if r is None:
        raise ValueError("Aucune valeur de courant dans l'emprise et la période.")
    return r


SOURCES_EXTERNES = {"rf_simulee", "sentinel1", "sentinel2", "gfw_sar", "rf", "autre"}


def lire_observations_externes(octets: bytes, taille_max=5_000_000):
    if len(octets) > taille_max:
        raise ValueError("Fichier d'observations trop volumineux.")
    df = pd.read_csv(io.BytesIO(octets), dtype=str, keep_default_na=False)
    req = ["obs_id", "timestamp", "lat", "lon", "precision_m", "proba_attribution"]
    manquantes = [c for c in req if c not in df.columns]
    if manquantes:
        raise ValueError("Colonnes absentes : " + ", ".join(manquantes))
    out = pd.DataFrame({
        "obs_id": df["obs_id"].str.replace(r"[^A-Za-z0-9_\-.]", "", regex=True).str[:32],
        "trip_id": df["trip_id"] if "trip_id" in df else "",
        "timestamp": pd.to_datetime(df["timestamp"], utc=True, errors="coerce"),
        "lat": pd.to_numeric(df["lat"], errors="coerce"), "lon": pd.to_numeric(df["lon"], errors="coerce"),
        "precision_m": pd.to_numeric(df["precision_m"], errors="coerce"),
        "proba_attribution": pd.to_numeric(df["proba_attribution"], errors="coerce"),
        "source": (df["source"].str.lower() if "source" in df else "autre"),
    }).dropna(subset=["timestamp", "lat", "lon", "precision_m", "proba_attribution"])
    out = out[(out["lat"].abs() <= 90) & (out["lon"].abs() <= 180) & (out["precision_m"] > 0)
              & (out["precision_m"] <= 50_000) & out["proba_attribution"].between(0, 1)]
    out.loc[~out["source"].isin(SOURCES_EXTERNES), "source"] = "autre"
    return out.reset_index(drop=True)
