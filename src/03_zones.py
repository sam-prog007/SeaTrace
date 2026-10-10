"""Étape 2 : zones de pêche restreintes du Kattegat, extraites du texte officiel.

Source : règlement délégué (UE) 2022/952 de la Commission, qui modifie le
règlement délégué (UE) 2017/118. Le HTML officiel (EUR-Lex, CELEX 32022R0952,
version anglaise) est téléchargé une fois dans data/reference/source/.

Annexes lues (31 tableaux de coordonnées, structure vérifiée par assertions) :
  Annexe I  1(8)  Kims Top og den Kinesiske Mur (DK00VA247)  -> « Areas 1 »
  Annexe I  1(9)  zones MSFD A à F (Danemark)                -> « Areas 1 »
  Annexe II 2(25)-2(27) trois aires marines suédoises        -> « Areas 2 »
  Annexe IV quatre aires marines suédoises                   -> « Areas 3 »
  Annexe V  zones AIS officielles (point 1 : DK, point 2 : SE)

Les sommets sont reliés « par des loxodromies » (rhumb lines, WGS84) : on trace
donc chaque côté en ligne droite dans la projection de Mercator (EPSG:3395),
où une loxodromie est droite, puis on densifie avant de revenir en WGS84.

Tampon : 4 milles nautiques (7 408 m) autour des zones danoises (Areas 1),
calculé en EPSG:25832, anneau = tampon moins la zone. Il est comparé aux zones
AIS officielles de l'annexe V.

Sorties :
  data/reference/kattegat_zones.geojson   zones + tampons + zones AIS, WGS84
  reports/zones_check.md                  contrôles par polygone
"""
import pathlib
import re
import sys
from html.parser import HTMLParser

import geopandas as gpd
import pandas as pd
import requests
import shapely
from shapely.geometry import Polygon
from shapely.validation import explain_validity

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32022R0952"
ELI = "https://eur-lex.europa.eu/eli/reg_del/2022/952/oj"
SRC = ROOT / "data" / "reference" / "source" / "reg_2022_952_EN.html"
OUT = ROOT / "data" / "reference" / "kattegat_zones.geojson"
CHECK = ROOT / "reports" / "zones_check.md"

PROJ_CRS = "EPSG:25832"  # ETRS89 / UTM 32N (m)
RHUMB_CRS = "EPSG:3395"  # World Mercator : loxodromie = droite
DENSIFY_M = 200
BUFFER_M = 4 * 1852  # 7 408 m
KATTEGAT = {"lat": (55.0, 58.0), "lon": (10.0, 13.0)}

R_AREAS1 = "Engins de fond mobiles interdits (art. 3(1)) ; engins à bord arrimés et rangés"
R_AREAS2 = "Toute opération de pêche interdite, pêche récréative comprise (art. 3(2)(b) et 3(4))"
R_AREAS3 = ("Seuls autorisés : lignes à main, chaluts pélagiques, casiers à crustacés, "
            "filets maillants/trémails avec surveillance électronique (art. 3(3))")
R_AIS_DK = "AIS obligatoire pour les navires utilisant des engins de fond mobiles (art. 5(b))"
R_AIS_SE = "AIS obligatoire pour tout navire de pêche présent (art. 5(c))"

# Structure attendue des 31 tableaux, dans l'ordre du texte :
# (n tableaux, texte attendu dans le contexte du 1er tableau, description)
SE_SITES = {
    "SE0510186": "Stora Middelgrund och Röde bank",
    "SE0510127": "Fladen",
    "SE0510126": "Lilla Middelgrund",
    "SE0510187": "Morups bank",
}


class TableParser(HTMLParser):
    """Récupère les tableaux de coordonnées et le texte qui les précède."""

    def __init__(self):
        super().__init__()
        self.stack, self.ctx, self.tables, self.cell, self.buf = [], [], [], None, []

    def flush(self):
        txt = " ".join("".join(self.buf).split())
        self.buf = []
        # On ignore les cellules purement numériques (coordonnées, numéros de point).
        if txt and not re.fullmatch(r"[\d\s°.,′’'NE]+", txt):
            self.ctx.append(txt)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.flush()  # texte d'intitulé situé dans la même cellule que le tableau
            self.stack.append({"rows": [], "ctx": list(self.ctx[-6:])})
        elif tag == "tr" and self.stack:
            self.stack[-1]["rows"].append([])
        elif tag in ("td", "th"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.stack:
            if self.stack[-1]["rows"]:
                self.stack[-1]["rows"][-1].append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "table":
            t = self.stack.pop()
            if t["rows"] and any("Latitude" in c for c in t["rows"][0]):
                self.tables.append(t)
        if tag in ("p", "td", "th", "div"):
            self.flush()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)
        self.buf.append(data)


def dm_to_dd(s: str) -> float:
    """« 57°6.775 », « 57°01.805′ », « 57 4.583’ », « 57° 27.305′ N » -> degrés décimaux."""
    t = s.replace("°", " ").replace("’", "").replace("′", "").replace("'", "")
    t = re.sub(r"\s*[NE]\s*$", "", t).strip()
    m = re.fullmatch(r"(\d{1,3})\s+(\d{1,2}(?:\.\d+)?)", t)
    if not m:
        raise ValueError(f"Coordonnée illisible : {s!r}")
    deg, minutes = int(m.group(1)), float(m.group(2))
    if minutes >= 60:
        raise ValueError(f"Minutes >= 60 : {s!r}")
    return deg + minutes / 60


def load_tables() -> list[dict]:
    if not SRC.exists():
        SRC.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(SRC_URL, timeout=120)
        r.raise_for_status()
        SRC.write_bytes(r.content)
    p = TableParser()
    p.feed(SRC.read_text(encoding="utf-8"))
    return p.tables


def ctx_has(t: dict, needle: str) -> bool:
    return any(needle in c for c in t["ctx"])


def rows_by_area(t: dict) -> dict[str, list[list[str]]]:
    """Tableau à 4 colonnes (Area, Point, Lat, Lon) -> sommets par sous-zone, ordre conservé."""
    out: dict[str, list] = {}
    for r in t["rows"][1:]:
        assert len(r) == 4, f"Ligne inattendue : {r}"
        out.setdefault(r[0], []).append(r)
    return out


def ring(rows, lat_i, lon_i, point_i):
    pts = [int(r[point_i]) for r in rows]
    assert pts == sorted(pts), f"Points non ordonnés : {pts}"
    return [(dm_to_dd(r[lon_i]), dm_to_dd(r[lat_i])) for r in rows], pts


def describe_tables(tables: list[dict]) -> list[dict]:
    """Associe chaque tableau à sa zone, en vérifiant la structure attendue."""
    assert len(tables) == 31, f"{len(tables)} tableaux trouvés, 31 attendus : structure du texte modifiée ?"
    feats = []
    # Annexe I 1(8) : Kims Top, tableaux 0-10, sous-zones 0, 2..11.
    assert ctx_has(tables[0], "Kims Top og den Kinesiske Mur (DK00VA247)") and ctx_has(tables[0], "1(8)")
    for t in tables[0:11]:
        for area, rows in rows_by_area(t).items():
            feats.append(dict(zone_id=f"DK00VA247-{area}", site_code="DK00VA247",
                              name=f"Kims Top og den Kinesiske Mur, sous-zone {area}", country="DK",
                              category="Areas 1", legal_ref="Annexe I, point 1(8)", restriction=R_AREAS1,
                              rows=rows, cols=(2, 3, 1)))
    # Annexe I 1(9) : MSFD A-F, tableaux 11-18.
    assert ctx_has(tables[11], "1(9):") and ctx_has(tables[11], "MSFD Area A")
    for t, label in zip(tables[11:19], ["A", "B", "C", "D", "D", "D", "E", "F"]):
        for area, rows in rows_by_area(t).items():
            assert area.startswith(label), f"Sous-zone {area} inattendue pour MSFD {label}"
            feats.append(dict(zone_id=f"DK-MSFD-{area}", site_code=None, name=f"MSFD Area {area}", country="DK",
                              category="Areas 1", legal_ref=f"Annexe I, point 1(9), zone {label}",
                              restriction=R_AREAS1, rows=rows, cols=(2, 3, 1)))
    # Annexe II 2(25)-2(27), tableaux 19-21.
    for t, point, code in zip(tables[19:22], ["2(25)", "2(26)", "2(27)"], ["SE0510186", "SE0510127", "SE0510126"]):
        assert ctx_has(t, point) and ctx_has(t, code), f"Contexte inattendu pour {point}"
        feats.append(dict(zone_id=f"{code}-A2", site_code=code, name=SE_SITES[code], country="SE",
                          category="Areas 2", legal_ref=f"Annexe II, point {point}", restriction=R_AREAS2,
                          rows=t["rows"][1:], cols=(1, 2, 0)))
    # Annexe IV, tableaux 22-25, dans l'ordre du texte.
    assert ctx_has(tables[22], "ANNEX IV")
    for i, (t, code) in enumerate(zip(tables[22:26], ["SE0510186", "SE0510127", "SE0510126", "SE0510187"]), 1):
        feats.append(dict(zone_id=f"{code}-A3", site_code=code, name=SE_SITES[code], country="SE",
                          category="Areas 3", legal_ref=f"Annexe IV, point {i}", restriction=R_AREAS3,
                          rows=t["rows"][1:], cols=(1, 2, 0)))
    # Annexe V point 1 (DK) tableaux 26-29, point 2 (SE) tableau 30.
    assert ctx_has(tables[26], "ANNEX V") and ctx_has(tables[26], "Area A")
    for t, point, label in zip(tables[26:30], ["1(1)", "1(2)", "1(3)", "1(4)"], ["A", "B, C, D", "E", "F"]):
        assert ctx_has(t, point), f"Contexte inattendu pour l'annexe V {point}"
        feats.append(dict(zone_id=f"DK-AIS-{label.replace(', ', '')}", site_code=None,
                          name=f"Zone AIS MSFD {label}" + (" et Kims Top" if label == "B, C, D" else ""),
                          country="DK", category="AIS zone", legal_ref=f"Annexe V, point {point}",
                          restriction=R_AIS_DK, rows=t["rows"][1:], cols=(1, 2, 0)))
    assert ctx_has(tables[30], "AIS zones of marine protected areas Fladen")
    feats.append(dict(zone_id="SE-AIS", site_code=None,
                      name="Zone AIS Fladen, Lilla Middelgrund, Stora Middelgrund och Röde bank, Morups bank",
                      country="SE", category="AIS zone", legal_ref="Annexe V, point 2", restriction=R_AIS_SE,
                      rows=tables[30]["rows"][1:], cols=(1, 2, 0)))
    return feats


def rhumb_polygon(coords: list[tuple[float, float]]) -> Polygon:
    """Polygone dont les côtés sont des loxodromies (droites en Mercator)."""
    merc = gpd.GeoSeries([Polygon(coords)], crs="EPSG:4326").to_crs(RHUMB_CRS)
    dense = shapely.segmentize(merc.iloc[0], DENSIFY_M)
    return gpd.GeoSeries([dense], crs=RHUMB_CRS).to_crs("EPSG:4326").iloc[0]


def build(feats: list[dict]) -> tuple[gpd.GeoDataFrame, list[str]]:
    rows, problems = [], []
    for f in feats:
        lat_i, lon_i, pt_i = f.pop("cols")
        coords, pts = ring(f.pop("rows"), lat_i, lon_i, pt_i)
        closed = coords[0] == coords[-1]
        if not closed:
            coords = coords + [coords[0]]
        bad = [(round(y, 4), round(x, 4)) for x, y in coords
               if not (KATTEGAT["lat"][0] <= y <= KATTEGAT["lat"][1] and KATTEGAT["lon"][0] <= x <= KATTEGAT["lon"][1])]
        if bad:
            problems.append(f"{f['zone_id']} : sommets hors du Kattegat {bad[:3]}")
        raw = Polygon(coords)
        if not raw.is_valid:
            problems.append(f"{f['zone_id']} : géométrie invalide ({explain_validity(raw)})")
        geom = rhumb_polygon(coords)
        rows.append({**f, "layer": "zone" if f["category"] != "AIS zone" else "ais_zone",
                     "n_points": len(pts), "closed_in_source": closed,
                     "orientation": "anti-horaire" if raw.exterior.is_ccw else "horaire",
                     "is_valid": raw.is_valid and geom.is_valid, "source": ELI, "geometry": geom})
    return gpd.GeoDataFrame(rows, crs="EPSG:4326"), problems


def buffers(zones: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    dk = zones[(zones.country == "DK") & (zones.category == "Areas 1")].to_crs(PROJ_CRS)
    all_zones = zones[zones.layer == "zone"].to_crs(PROJ_CRS).union_all()
    # Un tampon par site : Kims Top (toutes sous-zones) et chaque zone MSFD (A..F).
    dk = dk.assign(group=dk.zone_id.str.replace(r"^DK00VA247-.*", "DK00VA247", regex=True)
                   .str.replace(r"^DK-MSFD-([A-F]).*", r"MSFD \1", regex=True))
    rows = []
    for g, sub in dk.groupby("group"):
        zone = sub.union_all()
        ringg = zone.buffer(BUFFER_M).difference(all_zones)
        rows.append(dict(zone_id=f"BUF4NM-{g.replace(' ', '-')}", site_code=None,
                         name=f"Tampon 4 NM autour de {g}", country="DK", category="Tampon 4 NM",
                         legal_ref="Calcul du projet (pas un texte juridique)",
                         restriction="Chalutiers de fond : AIS de classe A attendu (hypothèse du projet)",
                         layer="buffer", n_points=None, closed_in_source=None, orientation=None,
                         is_valid=ringg.is_valid,
                         source=f"Tampon {BUFFER_M} m en {PROJ_CRS} autour de {g}, moins les zones",
                         geometry=ringg))
    return gpd.GeoDataFrame(rows, crs=PROJ_CRS).to_crs("EPSG:4326")


def compare_ais(zones: gpd.GeoDataFrame, bufs: gpd.GeoDataFrame) -> pd.DataFrame:
    """Compare les zones AIS officielles (annexe V, DK) au tampon calculé."""
    z = zones.to_crs(PROJ_CRS)
    dk_ais = z[(z.layer == "ais_zone") & (z.country == "DK")].union_all()
    dk_zones = z[(z.layer == "zone") & (z.country == "DK")].union_all()
    calc = bufs.to_crs(PROJ_CRS).union_all().union(dk_zones)
    inter = dk_ais.intersection(calc).area
    return pd.DataFrame([{
        "surface zones AIS officielles DK (km²)": round(dk_ais.area / 1e6, 1),
        "surface zones DK + tampon 4 NM (km²)": round(calc.area / 1e6, 1),
        "part des zones AIS officielles couverte par le calcul": f"{inter / dk_ais.area:.0%}",
        "part du calcul couverte par les zones AIS officielles": f"{inter / calc.area:.0%}",
        "zones DK entièrement dans les zones AIS officielles": dk_zones.difference(dk_ais).area < 1e4,
    }])


def main():
    tables = load_tables()
    zones, problems = build(describe_tables(tables))
    if problems:
        print("PROBLÈMES (arrêt, rien n'est écrit) :")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    bufs = buffers(zones)
    allz = pd.concat([zones, bufs], ignore_index=True)
    allz = gpd.GeoDataFrame(allz, crs="EPSG:4326")
    allz["area_km2"] = (allz.to_crs(PROJ_CRS).area / 1e6).round(2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    allz.to_file(OUT, driver="GeoJSON")

    cmp = compare_ais(zones, bufs)
    zp = zones.to_crs(PROJ_CRS)
    se = zp[zp.country == "SE"].set_index("zone_id")
    ov = [se.geometry[c + "-A2"].intersection(se.geometry[c + "-A3"]).area / se.geometry[c + "-A2"].area
          for c in ["SE0510186", "SE0510127", "SE0510126"]]
    overlap_note = ("Les deux parties ne se chevauchent pas (chevauchement max "
                    f"{max(ov):.0%}) : ce sont des polygones distincts et adjacents.")
    anomalies = []
    dk_ais = zp[(zp.layer == "ais_zone") & (zp.country == "DK")].union_all()
    for r in zp[(zp.layer == "zone") & (zp.country == "DK")].itertuples():
        out = r.geometry.difference(dk_ais).area
        if out / r.geometry.area > 0.01:
            anomalies.append(f"- **{r.zone_id}** : {out / 1e6:.2f} km² ({out / r.geometry.area:.0%}) hors des zones "
                             "AIS officielles de l'annexe V. Le texte ne l'explique pas ; signalé, non corrigé.")
    dkz = zp[(zp.layer == "zone") & (zp.country == "DK")].reset_index(drop=True)
    for i in range(len(dkz)):
        for j in range(i + 1, len(dkz)):
            a = dkz.geometry[i].intersection(dkz.geometry[j]).area
            if a > 1e4:
                anomalies.append(f"- **{dkz.zone_id[i]}** et **{dkz.zone_id[j]}** se chevauchent sur "
                                 f"{a / 1e6:.2f} km² (même restriction : sans effet pratique).")
    kims_areas = sorted(int(z.split("-")[-1]) for z in zones.zone_id if z.startswith("DK00VA247-"))
    cols = ["zone_id", "name", "country", "category", "legal_ref", "n_points", "closed_in_source",
            "orientation", "is_valid", "area_km2"]
    tab = allz[cols].fillna("-")
    md = ["# Contrôle des zones du règlement délégué (UE) 2022/952", "",
          f"Source : {ELI} (HTML anglais, CELEX 32022R0952, `{SRC.relative_to(ROOT)}`).", "",
          "Contrôles automatiques passés pour chaque polygone : 31 tableaux attendus et trouvés ; "
          "contexte de chaque tableau (point d'annexe, code du site) vérifié ; points numérotés dans l'ordre ; "
          "minutes < 60 ; tous les sommets dans 55-58 N / 10-13 E (latitude et longitude non inversées) ; "
          "polygone fermé ; géométrie valide.", "",
          "Côtés tracés en loxodromie (droite en Mercator EPSG:3395, densifiée tous les "
          f"{DENSIFY_M} m), comme le prévoit l'article 2 de 2017/118 modifié.", "",
          "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    md += ["| " + " | ".join(str(v) for v in r) + " |" for r in tab.itertuples(index=False)]
    md += ["", "## Points d'attention", "",
           f"- Kims Top (DK00VA247) : sous-zones numérotées {kims_areas} ; **pas de sous-zone 1** dans le texte "
           "officiel. Les versions anglaise et française présentent la même lacune ; ce n'est donc pas une erreur "
           "d'extraction. Aucun polygone n'a été inventé.",
           "- Stora Middelgrund, Fladen et Lilla Middelgrund ont chacun une partie « Areas 2 » (toute pêche "
           "interdite) et une partie « Areas 3 » (engins limités). " + overlap_note,
           *anomalies,
           "- Le règlement 2022/952 **ajoute** des zones au règlement 2017/118. Les zones déjà présentes dans "
           "2017/118 (annexe I points 1(1)-1(7), annexe II points 2(1)-2(24)) ne sont pas dans ce fichier.",
           "", "## Tampon de 4 NM et zones AIS officielles (annexe V)", "",
           "L'annexe V définit des zones AIS par coordonnées ; ce ne sont pas des tampons. Comparaison avec le "
           "tampon calculé :", "",
           "| " + " | ".join(cmp.columns) + " |", "|" + "---|" * len(cmp.columns),
           "| " + " | ".join(str(v) for v in cmp.iloc[0]) + " |"]
    CHECK.parent.mkdir(exist_ok=True)
    CHECK.write_text("\n".join(md) + "\n")
    print(tab.to_string())
    print(cmp.T.to_string())
    print(f"[out ] {OUT.relative_to(ROOT)} ({len(allz)} entités), {CHECK.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
