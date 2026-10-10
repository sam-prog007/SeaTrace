"""Étape 1a : téléchargement des fichiers AIS journaliers de l'agence danoise.

Source : dépôt public aisdata.ais.dk (bucket S3 eu-central-1), un ZIP par jour
nommé aisdk-YYYY-MM-DD.zip. Un fichier déjà présent avec la bonne taille n'est
pas retéléchargé.

Usage : python src/01_download.py --start 2026-09-15 --end 2026-09-17
"""
import argparse
import datetime as dt
import pathlib
import sys

import requests

BASE_URL = "https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk"
RAW_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"


def days(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def download(day: dt.date) -> pathlib.Path:
    name = f"aisdk-{day.isoformat()}.zip"
    dest = RAW_DIR / name
    url = f"{BASE_URL}/{name}"
    head = requests.head(url, timeout=60)
    head.raise_for_status()
    size = int(head.headers["Content-Length"])
    if dest.exists() and dest.stat().st_size == size:
        print(f"[skip] {name} déjà présent ({size / 1e6:.0f} Mo)")
        return dest
    tmp = dest.with_suffix(".zip.part")
    print(f"[get ] {name} ({size / 1e6:.0f} Mo)")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=8 << 20):
                f.write(chunk)
    if tmp.stat().st_size != size:
        sys.exit(f"Taille incorrecte pour {name}")
    tmp.rename(dest)
    return dest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, type=dt.date.fromisoformat)
    p.add_argument("--end", required=True, type=dt.date.fromisoformat)
    a = p.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for d in days(a.start, a.end):
        download(d)


if __name__ == "__main__":
    main()
