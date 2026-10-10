"""Géographie de démonstration (entièrement fictive) et outils géométriques.

IMPORTANT : la côte, les îles et les zones définies ici sont inventées pour le
prototype. Elles sont placées au large, dans un repère local, et ne correspondent
à aucune limite réglementaire réelle. Aucune conclusion juridique ne doit être
tirée d'une zone de ce fichier.

Repère : x vers l'est, y vers le nord, en kilomètres, à partir d'un point
d'ancrage (LAT0, LON0). Projection équirectangulaire locale, suffisante pour
une zone d'environ 20 km (erreur négligeable devant la taille des cases).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from matplotlib.path import Path as MplPath

LAT0 = 47.20
LON0 = -7.60
KM_PAR_DEG_LAT = 110.574
KM_PAR_DEG_LON = 111.320 * math.cos(math.radians(LAT0))
KN_EN_KMH = 1.852


ORIGINE_DEMO = (LAT0, LON0)


def _km_par_deg_lon(lat0):
    return 111.320 * math.cos(math.radians(lat0))


def vers_km(lat, lon, origine=None):
    """Projection équirectangulaire locale autour de `origine` (lat0, lon0) ; par défaut la baie de démo.
    Pour des données réelles, l'origine est le centre du jeu (erreur < 1 % sur ~100 km)."""
    lat0, lon0 = origine or ORIGINE_DEMO
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    return (lon - lon0) * _km_par_deg_lon(lat0), (lat - lat0) * KM_PAR_DEG_LAT


def vers_latlon(x, y, origine=None):
    lat0, lon0 = origine or ORIGINE_DEMO
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    return lat0 + y / KM_PAR_DEG_LAT, lon0 + x / _km_par_deg_lon(lat0)


def distance_km(x1, y1, x2, y2):
    return np.hypot(np.asarray(x2) - np.asarray(x1), np.asarray(y2) - np.asarray(y1))


def _ellipse(cx, cy, a, b, n=48):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return [(float(cx + a * math.cos(v)), float(cy + b * math.sin(v))) for v in t]


@dataclass
class Zone:
    identifiant: str
    nom: str
    nature: str
    description: str
    polygone: list
    fictive: bool = True
    source: str = "fictive"

    def chemin(self) -> MplPath:
        return MplPath(np.array(self.polygone + [self.polygone[0]]))


@dataclass
class Geographie:
    nom: str
    bornes: tuple  # (xmin, xmax, ymin, ymax) en km
    terres: list = field(default_factory=list)  # liste de (nom, polygone)
    zones: list = field(default_factory=list)  # liste de Zone
    lieux: dict = field(default_factory=dict)  # nom -> (x, y)
    fictive: bool = True
    origine: tuple = ORIGINE_DEMO  # (lat0, lon0) de la projection locale
    sources: list = field(default_factory=list)  # provenance des couches (terres, zones)

    def chemins_terres(self):
        return [MplPath(np.array(p + [p[0]])) for _, p in self.terres]

    def est_terre(self, x, y) -> np.ndarray:
        pts = np.column_stack([np.atleast_1d(x), np.atleast_1d(y)])
        masque = np.zeros(len(pts), dtype=bool)
        for c in self.chemins_terres():
            masque |= c.contains_points(pts)
        return masque

    def segment_traverse_terre(self, x1, y1, x2, y2, pas_km=0.05) -> bool:
        d = math.hypot(x2 - x1, y2 - y1)
        n = max(2, int(d / pas_km) + 1)
        xs = np.linspace(x1, x2, n)
        ys = np.linspace(y1, y2, n)
        return bool(self.est_terre(xs, ys).any())

    def zone(self, identifiant: str) -> Zone:
        for z in self.zones:
            if z.identifiant == identifiant:
                return z
        raise KeyError(identifiant)


def geographie_demo() -> Geographie:
    """Baie fictive : une côte au nord, une grande île, un îlot, une zone Z1."""
    cote = [
        (-2.0, 14.6), (2.0, 14.4), (4.0, 14.0), (6.0, 14.2), (8.0, 14.6),
        (10.0, 14.3), (12.0, 13.2), (13.5, 13.4), (15.0, 14.0), (18.0, 14.4),
        (22.0, 14.5), (22.0, 19.0), (-2.0, 19.0),
    ]
    ile_longue = _ellipse(9.5, 7.5, 3.2, 1.4)
    ilot = _ellipse(15.5, 9.5, 0.8, 0.6, n=24)
    z1 = Zone(
        identifiant="Z1",
        nom="Zone Z1 (fictive)",
        nature="interdite_navigation",
        description=(
            "Zone FICTIVE définie pour la démonstration : interdite à la navigation "
            "pour le bateau et la période du scénario. Ne correspond à aucune "
            "réglementation réelle."
        ),
        polygone=[(10.0, 4.2), (13.5, 4.2), (13.5, 6.3), (10.0, 6.3)],
    )
    lieux = {
        "Port-Fictif": (5.0, 13.4),
        "Anse Nord": (9.5, 9.3),
        "Plage de l'Îlot": (15.5, 8.5),
        "Mouillage Ouest": (5.6, 7.5),
    }
    return Geographie(
        nom="Baie de démonstration (géographie fictive)",
        bornes=(0.0, 20.0, 0.0, 16.0),
        terres=[("Côte", cote), ("Île Longue", ile_longue), ("Îlot du Sud", ilot)],
        zones=[z1],
        lieux=lieux,
        fictive=True,
    )


def geographie_generique(xs, ys, marge_km: float, origine=None) -> Geographie:
    """Géographie sans terres ni zones, pour des données hors de la baie de démo."""
    return Geographie(
        origine=origine or ORIGINE_DEMO,
        nom="Géographie inconnue (aucun masque de terres, aucune zone)",
        bornes=(
            float(np.min(xs)) - marge_km,
            float(np.max(xs)) + marge_km,
            float(np.min(ys)) - marge_km,
            float(np.max(ys)) + marge_km,
        ),
        fictive=False,
    )


def dans_bornes(geo: Geographie, xs, ys) -> bool:
    xmin, xmax, ymin, ymax = geo.bornes
    xs = np.asarray(xs)
    ys = np.asarray(ys)
    return bool(((xs >= xmin) & (xs <= xmax) & (ys >= ymin) & (ys <= ymax)).all())


@dataclass
class Grille:
    """Discrétisation régulière de la zone d'étude en cases carrées."""

    x0: float
    y0: float
    taille: float
    nx: int
    ny: int
    mer: np.ndarray  # (ny, nx) booléen : case navigable
    zones: dict  # identifiant -> masque (ny, nx)
    origine: tuple = ORIGINE_DEMO  # projection locale (lat0, lon0)

    @property
    def xc(self):
        return self.x0 + (np.arange(self.nx) + 0.5) * self.taille

    @property
    def yc(self):
        return self.y0 + (np.arange(self.ny) + 0.5) * self.taille

    def indice(self, x, y):
        i = int(np.clip((y - self.y0) // self.taille, 0, self.ny - 1))
        j = int(np.clip((x - self.x0) // self.taille, 0, self.nx - 1))
        return i, j

    def centre(self, i, j):
        return self.x0 + (j + 0.5) * self.taille, self.y0 + (i + 0.5) * self.taille

    def indices_vec(self, x, y):
        """Indices (i, j) de cases pour des tableaux de points, et masque « dans la grille »."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        jf = np.floor((x - self.x0) / self.taille)
        if_ = np.floor((y - self.y0) / self.taille)
        dedans = (jf >= 0) & (jf < self.nx) & (if_ >= 0) & (if_ < self.ny) & np.isfinite(x) & np.isfinite(y)
        i = np.clip(np.nan_to_num(if_), 0, self.ny - 1).astype(int)
        j = np.clip(np.nan_to_num(jf), 0, self.nx - 1).astype(int)
        return i, j, dedans

    def en_mer(self, x, y):
        """Vrai si le point est dans la grille et sur une case navigable."""
        i, j, dedans = self.indices_vec(x, y)
        return dedans & self.mer[i, j]

    def dans_zone(self, zone_id, x, y):
        i, j, dedans = self.indices_vec(x, y)
        return dedans & self.zones[zone_id][i, j]


def construire_grille(geo: Geographie, taille_km: float, bornes=None) -> Grille:
    xmin, xmax, ymin, ymax = bornes or geo.bornes
    nx = max(4, int(math.ceil((xmax - xmin) / taille_km)))
    ny = max(4, int(math.ceil((ymax - ymin) / taille_km)))
    xc = xmin + (np.arange(nx) + 0.5) * taille_km
    yc = ymin + (np.arange(ny) + 0.5) * taille_km
    gx, gy = np.meshgrid(xc, yc)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    terre = np.zeros(len(pts), dtype=bool)
    for c in geo.chemins_terres():
        terre |= c.contains_points(pts)
    zones = {}
    for z in geo.zones:
        # Une case appartient à la zone si son centre est dans le polygone
        # (approximation documentée : résolution = taille de case).
        zones[z.identifiant] = z.chemin().contains_points(pts).reshape(ny, nx)
    return Grille(
        x0=xmin, y0=ymin, taille=taille_km, nx=nx, ny=ny,
        mer=~terre.reshape(ny, nx), zones=zones, origine=geo.origine,
    )


def densifier(xs, ys, sous_pas=4):
    """Points intermédiaires sur chaque segment d'un chemin (pour tester terres et zones)."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    f = np.linspace(0.0, 1.0, sous_pas, endpoint=False)
    if xs.ndim == 1:
        dx = np.diff(xs)[:, None] * f[None]
        dy = np.diff(ys)[:, None] * f[None]
        ox = np.concatenate([(xs[:-1, None] + dx).ravel(), xs[-1:]])
        oy = np.concatenate([(ys[:-1, None] + dy).ravel(), ys[-1:]])
        return ox, oy
    # lot de chemins (m, n+1)
    dx = np.diff(xs, axis=1)[:, :, None] * f[None, None]
    dy = np.diff(ys, axis=1)[:, :, None] * f[None, None]
    ox = np.concatenate([(xs[:, :-1, None] + dx).reshape(len(xs), -1), xs[:, -1:]], axis=1)
    oy = np.concatenate([(ys[:, :-1, None] + dy).reshape(len(ys), -1), ys[:, -1:]], axis=1)
    return ox, oy


def trajet_entre_dans_zone(zone: Zone, xs, ys) -> bool:
    """Vrai si une suite de points (dense) entre au moins une fois dans la zone."""
    pts = np.column_stack([np.atleast_1d(xs), np.atleast_1d(ys)])
    return bool(zone.chemin().contains_points(pts).any())


def distance_a_polyligne(xs, ys, poly) -> np.ndarray:
    """Distance (km) de chaque point à une polyligne [(x, y), ...]."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    poly = np.asarray(poly, dtype=float)
    best = np.full(xs.shape, np.inf)
    for (ax, ay), (bx, by) in zip(poly[:-1], poly[1:]):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        if l2 == 0:
            d = np.hypot(xs - ax, ys - ay)
        else:
            t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / l2, 0, 1)
            d = np.hypot(xs - (ax + t * dx), ys - (ay + t * dy))
        best = np.minimum(best, d)
    return best
