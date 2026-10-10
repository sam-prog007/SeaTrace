"""Convertit un fichier AIS ouvert (DMA danoise ou NOAA MarineCadastre) au format SeaTrace.

Les fichiers bruts sont lourds (souvent plusieurs centaines de Mo par jour) : ils sont lus par
morceaux et filtrés (emprise, types, MMSI, période) AVANT d'être chargés en mémoire.

Exemples :
  # Danemark, détroits du Sud (Fionie / Langeland), voiliers et plaisance
  python scripts/importer_ais.py aisdk-2024-06-15.zip --bbox 54.6 55.4 10.2 11.2 \
      --types ais_voilier ais_plaisance
  # NOAA, un MMSI précis
  python scripts/importer_ais.py AIS_2024_01_01.zip --mmsi 367000000

Téléchargement (à faire soi-même, conditions de réutilisation à vérifier) :
  DMA  : http://web.ais.dk/aisdata/  (fichiers aisdk-AAAA-MM-JJ.zip)
  NOAA : https://coast.noaa.gov/htdata/CMSP/AISDataHandler/AAAA/  (AIS_AAAA_MM_JJ.zip)
Le résultat est écrit dans data/ais/ (positions + métadonnées des bateaux) et peut être chargé
dans l'interface (liste « Jeux de données ») ou servir à apprendre le modèle appris.
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import config, schema, sources  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fichier", help="CSV ou ZIP AIS (DMA ou NOAA)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("LAT_MIN", "LAT_MAX", "LON_MIN", "LON_MAX"))
    ap.add_argument("--types", nargs="*", help="types SeaTrace : ais_voilier ais_plaisance ais_peche ais_autre")
    ap.add_argument("--mmsi", nargs="*")
    ap.add_argument("--debut", help="ISO 8601 UTC")
    ap.add_argument("--fin", help="ISO 8601 UTC")
    ap.add_argument("--ecart-trajet-min", type=float, default=30.0)
    ap.add_argument("--pas-min-s", type=float, default=30.0)
    ap.add_argument("--min-points", type=int, default=20)
    ap.add_argument("--nom", help="nom du jeu (défaut : nom du fichier)")
    args = ap.parse_args()
    params = config.charger()
    taille = Path(args.fichier).stat().st_size
    if taille > params["import"]["taille_max_ais_octets"] * 20:
        sys.exit("Fichier trop volumineux pour ce prototype ; le découper au préalable.")
    df, bateaux, info = sources.convertir_ais(
        args.fichier, bbox=args.bbox, types=args.types, mmsi=args.mmsi,
        debut=pd.Timestamp(args.debut) if args.debut else None, fin=pd.Timestamp(args.fin) if args.fin else None,
        ecart_trajet_min=args.ecart_trajet_min, min_points=args.min_points, pas_min_s=args.pas_min_s,
        lignes_max=params["import"]["lignes_max"])
    nom = schema.nom_affichable(args.nom or Path(args.fichier).stem).replace(" ", "_")
    sortie = RACINE / "data" / "ais"
    sortie.mkdir(parents=True, exist_ok=True)
    octets = df.to_csv(index=False).encode("utf-8")
    # contrôle : le fichier produit doit passer l'import sécurisé
    res = schema.lire_csv_securise(octets, f"{nom}.csv", params["import"])
    (sortie / f"{nom}.csv").write_bytes(octets)
    meta = {"bateaux": bateaux, "conversion": info, "source_brute": schema.nom_affichable(Path(args.fichier).name),
            "empreinte": res.empreinte}
    (sortie / f"{nom}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print(f"Écrit : data/ais/{nom}.csv ({res.lignes_lues} lignes valides) et data/ais/{nom}.json")


if __name__ == "__main__":
    main()
