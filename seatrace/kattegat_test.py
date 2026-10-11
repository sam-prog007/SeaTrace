"""The team's synthetic test boats A, B, C and D of the Kattegat study, as the detector sees them.

Each boat is a COPY of a real trawler track (Danish Maritime Authority AIS, 15-17 September 2026) in which a
single event was simulated. Only the REPORTED positions are read here, exactly what a detector would receive.
The files that hold the answer key are never opened by this module or by anything under web/ or seatrace/
(a test of the team's checks that rule); they are used only by the evaluation script in eval/.

The description of each trip is deliberately neutral: it does not say which event was simulated, so the
analyst, the models and the demo audience have to find out. The Evaluation tab shows the answers afterwards.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SCENARIOS = {"A": "999000001", "B": "999000002", "C": "999000003", "D": "999000004"}
COLONNES = ["mmsi", "timestamp", "lat", "lon", "sog", "cog"]


def dossier(racine: Path) -> Path:
    return Path(racine) / "data" / "synthetic"


def disponible(racine: Path) -> bool:
    return all((dossier(racine) / f"boat_{x}_reported.parquet").exists() for x in SCENARIOS)


def _decimer(r: pd.DataFrame, pas_min_s: float) -> pd.DataFrame:
    """Keep positions at least `pas_min_s` apart (class A AIS sends every ~10 s)."""
    t = (r["timestamp"] - r["timestamp"].iloc[0]).dt.total_seconds().to_numpy()
    garde = [0]
    for i in range(1, len(t)):
        if t[i] - t[garde[-1]] >= pas_min_s:
            garde.append(i)
    return r.iloc[garde]


def charger(racine: Path, pas_min_s: float = 30.0):
    """Returns (positions in SeaTrace format, boats metadata, trips description)."""
    lignes, bateaux, routes = [], {}, {}
    for x, mmsi in SCENARIOS.items():
        r = pd.read_parquet(dossier(racine) / f"boat_{x}_reported.parquet", columns=COLONNES)
        r = r.sort_values("timestamp").reset_index(drop=True)
        r["timestamp"] = pd.to_datetime(r["timestamp"], utc=True)
        r = _decimer(r, pas_min_s)
        trip = f"TEST-{x}"
        lignes.append(pd.DataFrame({
            "boat_id": mmsi, "trip_id": trip, "timestamp": r["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lat": r["lat"].round(6), "lon": r["lon"].round(6),
            "sog_kn": np.where(r["sog"] < 100, r["sog"], np.nan).round(2),
            "cog_deg": np.where(r["cog"] < 360, r["cog"], np.nan).round(1),
            "accuracy_m": 10.0, "source": "ais", "synthetic": True}))
        bateaux[mmsi] = {"type_bateau": "ais_peche", "nom": f"Test boat {x} (synthetic scenario)",
                         "flotte": "Kattegat synthetic test boats", "mmsi": mmsi}
        origine = "fully simulated trawler trip" if x == "D" else "copy of a real trawler track"  # D : src/boat_d.py
        routes[trip] = {"description": f"Synthetic test boat {x}: {origine} with one simulated "
                                       "event (which one is withheld: see the Evaluation tab).",
                        "synthetique": True}
    return pd.concat(lignes, ignore_index=True), bateaux, routes
