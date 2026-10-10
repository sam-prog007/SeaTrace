"""Chargement et validation des paramètres essentiels.

Les paramètres vivent dans config/parametres.json. Ils sont versionnés et chaque
analyse enregistre l'empreinte des paramètres utilisés, pour que tout résultat
soit rattaché à une configuration identifiable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import __version__

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_PARAMETRES = RACINE / "config" / "parametres.json"

VERSION_REGLES = "regles-0.1.0"
VERSION_MODELE = "hmm-grille-pont-0.1.0"


class ErreurParametres(ValueError):
    """Paramètres absents ou hors des bornes autorisées."""


# Bornes de sécurité : un paramètre hors bornes est refusé plutôt que corrigé en silence.
_BORNES = {
    ("detection", "marge_vitesse"): (1.0, 3.0),
    ("detection", "intervalle_attendu_s"): (1, 3600),
    ("detection", "facteur_interruption"): (1.5, 100.0),
    ("modele", "taille_case_km"): (0.05, 2.0),
    ("modele", "pas_temps_s"): (30, 900),
    ("modele", "dispersion_relative"): (0.05, 3.0),
    ("modele", "nb_caps"): (1, 16),
    ("modele", "persistance_cap"): (0.0, 0.99),
    ("modele", "part_arret"): (0.0, 0.9),
    ("modele", "incertitude_bornes_m"): (1.0, 5000.0),
    ("modele", "duree_max_interruption_h"): (0.1, 24.0),
    ("modele", "poids_habitudes"): (0.0, 5.0),
    ("modele", "nb_trajets_dessines"): (10, 2000),
    ("import", "taille_max_octets"): (1000, 50_000_000),
    ("import", "lignes_max"): (10, 1_000_000),
    ("import", "longueur_champ_max"): (8, 256),
}


def valider(params: dict) -> dict:
    for (section, cle), (mini, maxi) in _BORNES.items():
        try:
            valeur = params[section][cle]
        except (KeyError, TypeError) as exc:
            raise ErreurParametres(f"Paramètre manquant : {section}.{cle}") from exc
        if not isinstance(valeur, (int, float)) or isinstance(valeur, bool):
            raise ErreurParametres(f"Paramètre non numérique : {section}.{cle}")
        if not (mini <= valeur <= maxi):
            raise ErreurParametres(
                f"Paramètre hors bornes : {section}.{cle} = {valeur} (attendu entre {mini} et {maxi})"
            )
    if not params.get("types_bateau"):
        raise ErreurParametres("Aucun type de bateau défini.")
    for nom, t in params["types_bateau"].items():
        vmax = t.get("vitesse_max_kn")
        vtyp = t.get("vitesse_typique_kn")
        if not isinstance(vmax, (int, float)) or not (1 <= vmax <= 80):
            raise ErreurParametres(f"Vitesse max invalide pour {nom}")
        if not isinstance(vtyp, (int, float)) or not (0.5 <= vtyp <= vmax):
            raise ErreurParametres(f"Vitesse typique invalide pour {nom}")
    return params


def charger(chemin: Path | None = None) -> dict:
    chemin = chemin or CHEMIN_PARAMETRES
    with open(chemin, encoding="utf-8") as f:
        params = json.load(f)
    return valider(params)


def empreinte(params: dict) -> str:
    """Empreinte courte et stable des paramètres (pour le rapport et le journal)."""
    texte = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:16]


def versions(params: dict) -> dict:
    return {
        "logiciel": __version__,
        "regles": VERSION_REGLES,
        "modele": VERSION_MODELE,
        "parametres": params.get("version_parametres", "?"),
        "empreinte_parametres": empreinte(params),
    }


def copie(params: dict) -> dict:
    return copy.deepcopy(params)
