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

VERSION_REGLES = "regles-0.2.0"
VERSION_MODELE = "hmm-grille-pont-0.1.0"
VERSION_MODELES_V2 = "multi-models-0.2.0"


class ErreurParametres(ValueError):
    """Paramètres absents ou hors des bornes autorisées."""


# Bornes de sécurité : un paramètre hors bornes est refusé plutôt que corrigé en silence.
_BORNES = {
    ("detection", "marge_vitesse"): (1.0, 3.0),
    ("detection", "intervalle_attendu_s"): (1, 3600),
    ("detection", "facteur_interruption"): (1.5, 100.0),
    ("detection", "rejeu_variation_min_m"): (0.0, 1000.0),
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
    ("import", "taille_max_ais_octets"): (1000, 500_000_000),
    ("modeles", "particules", "n_particules"): (200, 20000),
    ("modeles", "particules", "n_lisses"): (10, 2000),
    ("modeles", "particules", "persistance_vitesse"): (0.0, 0.99),
    ("modeles", "particules", "bruit_position_km"): (0.005, 1.0),
    ("modeles", "particules", "inflation_guidage"): (1.0, 5.0),
    ("modeles", "pont_brownien", "n_chemins"): (10, 5000),
    ("modeles", "pont_brownien", "essais_max_facteur"): (1, 200),
    ("modeles", "pont_brownien", "sigma_m_defaut_km2_h"): (0.01, 500.0),
    ("modeles", "pont_brownien", "marge_vitesse"): (1.0, 5.0),
    ("modeles", "appris", "poids_densite"): (0.0, 5.0),
    ("modeles", "boosting", "n_arbres"): (10, 2000),
    ("modeles", "boosting", "profondeur"): (1, 8),
    ("modeles", "boosting", "sous_echantillon"): (0.1, 1.0),
    ("modeles", "boosting", "taux_apprentissage"): (0.001, 1.0),
    ("modeles", "boosting", "essais_optuna"): (5, 500),
    ("modeles", "segmentation", "nb_points"): (4, 200),
    ("modeles", "segmentation", "familles_max"): (1, 10),
    ("modeles", "segmentation", "silhouette_min"): (0.0, 1.0),
    ("modeles", "seuil_divergence_modeles"): (0.0, 1.0),
}


def _valeur(params, chemin):
    v = params
    for c in chemin:
        v = v[c]
    return v


def valider(params: dict) -> dict:
    for chemin, (mini, maxi) in _BORNES.items():
        nom = ".".join(chemin)
        try:
            valeur = _valeur(params, chemin)
        except (KeyError, TypeError) as exc:
            raise ErreurParametres(f"Paramètre manquant : {nom}") from exc
        if not isinstance(valeur, (int, float)) or isinstance(valeur, bool):
            raise ErreurParametres(f"Paramètre non numérique : {nom}")
        if not (mini <= valeur <= maxi):
            raise ErreurParametres(
                f"Paramètre hors bornes : {nom} = {valeur} (attendu entre {mini} et {maxi})"
            )
    courant = params["modeles"].get("courant_kmh", [0.0, 0.0])
    if (not isinstance(courant, list) or len(courant) != 2
            or not all(isinstance(c, (int, float)) and abs(c) <= 15 for c in courant)):
        raise ErreurParametres("modeles.courant_kmh doit être [u_est, v_nord] en km/h, |valeur| <= 15")
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
        "modeles_v2": VERSION_MODELES_V2,
        "parametres": params.get("version_parametres", "?"),
        "empreinte_parametres": empreinte(params),
    }


def copie(params: dict) -> dict:
    return copy.deepcopy(params)
