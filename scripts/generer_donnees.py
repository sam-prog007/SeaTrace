"""Génère les données de démonstration (synthétiques) et leurs étiquettes, dans des dossiers séparés.

Usage : python scripts/generer_donnees.py

- data/demo/        : ce que l'application analyse (positions, bateaux, trajets prévus,
                      observations RF simulées). Aucune étiquette ici.
- data/etiquettes/  : vérité terrain et manipulations injectées. Réservé à l'évaluation ;
                      jamais lu par la détection ni par la reconstruction.
"""

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import config, geo, scenarios, schema  # noqa: E402


def main():
    params = config.charger()
    d = scenarios.generer_demo(params)
    demo = RACINE / "data" / "demo"
    etiq = RACINE / "data" / "etiquettes"
    demo.mkdir(parents=True, exist_ok=True)
    etiq.mkdir(parents=True, exist_ok=True)

    (demo / "positions_demo.csv").write_bytes(schema.exporter_csv(d["positions"]))
    (demo / "bateaux_demo.json").write_text(json.dumps(d["bateaux"], ensure_ascii=False, indent=2), encoding="utf-8")
    routes = {}
    for trip_id, r in d["routes"].items():
        routes[trip_id] = {**r, "statut": "declaree", "synthetique": True}
    (demo / "routes_declarees.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")
    rf = d["rf"].copy()
    rf["timestamp"] = rf["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rf.to_csv(demo / "observations_rf_simulees.csv", index=False)

    e = d["etiquettes"].copy()
    for c in ("debut", "fin"):
        e[c] = e[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    e.to_csv(etiq / "manipulations_demo.csv", index=False)
    v = d["verite"].copy()
    v["lat"], v["lon"] = geo.vers_latlon(v["x"], v["y"])
    v["timestamp"] = v["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    v[["trip_id", "timestamp", "lat", "lon"]].iloc[::6].to_csv(etiq / "trajets_reels_demo.csv", index=False)
    print(f"Démo : {len(d['positions'])} positions, {d['positions']['trip_id'].nunique()} trajets -> {demo}")
    print(f"Étiquettes (évaluation uniquement) -> {etiq}")


if __name__ == "__main__":
    main()
