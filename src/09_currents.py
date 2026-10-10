"""Contexte : courants de surface du Kattegat (Copernicus Marine), pour la période de la carte.

Produit  : BALTICSEA_ANALYSISFORECAST_PHY_003_006 (Baltic Sea Physics Analysis and Forecast,
           NEMO v4.2.1, grille native d'environ 1 mille nautique), DOI 10.48670/moi-00010.
Jeu      : cmems_mod_bal_phy_anfc_PT15M-i, valeurs de surface instantanées toutes les 15 min.
           Le manuel du produit (PUM) distingue des jeux « détidés » à part (filtre de Doodson
           39 h, valeurs journalières) : ce jeu-ci n'est pas détidé, il contient donc la marée,
           c'est-à-dire le courant réel que subit un navire.
Variables: uo (vers l'est) et vo (vers le nord), en m/s.

Option --wind : télécharge aussi le vent de surface horaire du produit
WIND_GLO_PHY_L4_NRT_012_004 (jeu cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H,
DOI 10.48670/moi-00305), alternative si l'API Open-Meteo ne répond pas.

Identifiants : variables d'environnement COPERNICUSMARINE_SERVICE_USERNAME et
COPERNICUSMARINE_SERVICE_PASSWORD, ou fichier créé par « copernicusmarine login ».
S'ils manquent, le script s'arrête et dit quoi faire.

Sorties (ignorées par git) : data/raw/currents/*.nc, data/raw/wind/*.nc
Usage : python src/09_currents.py [--wind]
"""
import argparse
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
START, END = "2026-09-15T00:00:00", "2026-09-18T00:00:00"  # période de la carte (UTC), fin incluse
BBOX = {"minimum_longitude": 10.0, "maximum_longitude": 13.0, "minimum_latitude": 55.0, "maximum_latitude": 58.0}

DATASETS = {
    "currents": {"dataset_id": "cmems_mod_bal_phy_anfc_PT15M-i", "variables": ["uo", "vo"],
                 "dir": ROOT / "data" / "raw" / "currents", "max_abs": 3.0},  # m/s ; au-delà : aberrant
    "wind": {"dataset_id": "cmems_obs-wind_glo_phy_nrt_l4_0.125deg_PT1H",
             "variables": ["eastward_wind", "northward_wind"],
             "dir": ROOT / "data" / "raw" / "wind", "max_abs": 60.0},
}


def credentials_ok() -> bool:
    if os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME") and os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD"):
        return True
    cred_dir = pathlib.Path(os.environ.get("COPERNICUSMARINE_CREDENTIALS_DIRECTORY", pathlib.Path.home() / ".copernicusmarine"))
    return (cred_dir / ".copernicusmarine-credentials").exists()


def out_path(key: str) -> pathlib.Path:
    d = DATASETS[key]
    return d["dir"] / f"{d['dataset_id']}_{START[:10]}_{END[:10]}_kattegat.nc"


def download(key: str) -> pathlib.Path:
    import copernicusmarine

    d, out = DATASETS[key], out_path(key)
    if out.exists():
        print(f"[skip] {out.relative_to(ROOT)} déjà téléchargé")
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    copernicusmarine.subset(dataset_id=d["dataset_id"], variables=d["variables"], **BBOX,
                            start_datetime=START, end_datetime=END,
                            output_directory=str(out.parent), output_filename=out.name)
    print(f"[out ] {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.1f} Mo)")
    return out


def check(key: str, path: pathlib.Path) -> None:
    """Contrôles de cohérence : couverture temporelle, valeurs manquantes, vitesses aberrantes."""
    import numpy as np
    import xarray as xr

    d = DATASETS[key]
    ds = xr.open_dataset(path)
    u, v = (ds[d["variables"][0]], ds[d["variables"][1]])
    if "depth" in u.dims:
        u, v = u.isel(depth=0), v.isel(depth=0)
    speed = np.hypot(u, v)
    print(f"[chk ] {key} : temps {str(ds.time.values[0])[:16]} -> {str(ds.time.values[-1])[:16]} "
          f"({ds.sizes['time']} pas), grille {ds.sizes['latitude']} x {ds.sizes['longitude']}, "
          f"vitesse max {float(speed.max()):.2f} m/s, part de valeurs manquantes {float(speed.isnull().mean()):.1%}")
    n_bad = int((speed > d["max_abs"]).sum())
    if n_bad:
        print(f"[WARN] {key} : {n_bad} valeurs > {d['max_abs']} m/s, jugées aberrantes : à examiner avant usage")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--wind", action="store_true", help="télécharger aussi le vent Copernicus (alternative à Open-Meteo)")
    a = p.parse_args()
    if not credentials_ok():
        sys.exit(
            "Identifiants Copernicus Marine absents.\n"
            "  - En local : lancer « copernicusmarine login » une fois, ou définir les variables\n"
            "    COPERNICUSMARINE_SERVICE_USERNAME et COPERNICUSMARINE_SERVICE_PASSWORD.\n"
            "  - Dans l'environnement cloud : ajouter ces deux variables dans les paramètres de\n"
            "    l'environnement, puis ouvrir une nouvelle session.\n"
            "Compte gratuit : https://data.marine.copernicus.eu/register")
    for key in ["currents"] + (["wind"] if a.wind else []):
        check(key, download(key))


if __name__ == "__main__":
    main()
