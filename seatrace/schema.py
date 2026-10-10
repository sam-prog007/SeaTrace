"""Format partagé des positions et import sécurisé.

Tout fichier importé est considéré comme NON FIABLE :
- seul le CSV UTF-8 est accepté, avec limites de taille, de lignes et de longueur de champ ;
- chaque valeur est validée (type, bornes, motif) ; rien n'est exécuté ni évalué ;
- le nom de fichier fourni n'est jamais utilisé comme chemin : la copie originale est
  conservée sous un nom interne dérivé de son empreinte SHA-256 ;
- les champs texte sont restreints à un motif simple et affichés comme du texte.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

COLONNES_OBLIGATOIRES = ["boat_id", "trip_id", "timestamp", "lat", "lon"]
COLONNES_OPTIONNELLES = ["sog_kn", "cog_deg", "accuracy_m", "source", "synthetic"]
COLONNES = COLONNES_OBLIGATOIRES + COLONNES_OPTIONNELLES

SOURCES_AUTORISEES = {"tracker", "ais", "rf_simulee", "declare"}
MOTIF_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")
CARACTERES_FORMULE = ("=", "+", "-", "@", "\t", "\r")
TAUX_REJET_MAX = 0.2


class ErreurImport(ValueError):
    """Fichier refusé dans son ensemble."""


@dataclass
class ResultatImport:
    donnees: pd.DataFrame
    empreinte: str
    nom_interne: str
    nom_fourni_affichable: str
    lignes_lues: int
    lignes_rejetees: int
    motifs_rejet: dict = field(default_factory=dict)
    avertissements: list = field(default_factory=list)


def empreinte_octets(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def nom_affichable(nom: str) -> str:
    """Version inoffensive du nom fourni, uniquement pour l'affichage et le journal."""
    nom = (nom or "").split("/")[-1].split("\\")[-1]
    nom = re.sub(r"[^A-Za-z0-9_.\- ]", "_", nom)
    return nom[:64] or "sans_nom"


def _parse_temps(texte: str) -> datetime:
    t = texte.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is None:
        raise ValueError("fuseau horaire absent (utiliser UTC, ex. 2026-07-14T09:00:00Z)")
    return dt.astimezone(timezone.utc)


def _flottant(texte: str, mini: float, maxi: float, nom: str) -> float:
    v = float(texte)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{nom} non fini")
    if not (mini <= v <= maxi):
        raise ValueError(f"{nom} hors bornes [{mini}, {maxi}]")
    return v


def _valider_ligne(brut: dict, maintenant: datetime) -> dict:
    out = {}
    for col in ("boat_id", "trip_id"):
        v = (brut.get(col) or "").strip()
        if not MOTIF_ID.match(v):
            raise ValueError(f"{col} invalide")
        out[col] = v
    ts = _parse_temps(brut.get("timestamp") or "")
    if ts < datetime(2000, 1, 1, tzinfo=timezone.utc) or ts > maintenant + timedelta(days=1):
        raise ValueError("horodatage hors de la plage plausible")
    out["timestamp"] = ts
    out["lat"] = _flottant(brut.get("lat") or "", -90, 90, "lat")
    out["lon"] = _flottant(brut.get("lon") or "", -180, 180, "lon")
    for col, mini, maxi in (("sog_kn", 0, 100), ("cog_deg", 0, 360), ("accuracy_m", 0, 10000)):
        v = (brut.get(col) or "").strip()
        out[col] = _flottant(v, mini, maxi, col) if v else float("nan")
    src = (brut.get("source") or "tracker").strip().lower()
    if src not in SOURCES_AUTORISEES:
        raise ValueError("source non reconnue")
    out["source"] = src
    syn = (brut.get("synthetic") or "").strip().lower()
    if syn not in ("", "true", "false", "1", "0", "vrai", "faux"):
        raise ValueError("synthetic invalide")
    out["synthetic"] = syn in ("true", "1", "vrai")
    return out


def lire_csv_securise(octets: bytes, nom_fourni: str, params_import: dict) -> ResultatImport:
    taille_max = int(params_import["taille_max_octets"])
    lignes_max = int(params_import["lignes_max"])
    champ_max = int(params_import["longueur_champ_max"])

    if len(octets) == 0:
        raise ErreurImport("Fichier vide.")
    if len(octets) > taille_max:
        raise ErreurImport(f"Fichier trop volumineux ({len(octets)} octets, maximum {taille_max}).")
    if b"\x00" in octets:
        raise ErreurImport("Fichier binaire refusé (octet nul détecté).")
    try:
        texte = octets.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurImport("Encodage non UTF-8 : fichier refusé.") from exc

    lecteur = csv.reader(io.StringIO(texte, newline=""))
    try:
        entete = next(lecteur)
    except StopIteration as exc:
        raise ErreurImport("En-tête absent.") from exc
    entete = [c.strip().lower() for c in entete]
    if len(entete) != len(set(entete)):
        raise ErreurImport("Colonnes en double dans l'en-tête.")
    manquantes = [c for c in COLONNES_OBLIGATOIRES if c not in entete]
    if manquantes:
        raise ErreurImport(f"Colonnes obligatoires absentes : {', '.join(manquantes)}.")
    avertissements = []
    inconnues = [c for c in entete if c not in COLONNES]
    if inconnues:
        avertissements.append(f"{len(inconnues)} colonne(s) inconnue(s) ignorée(s).")

    maintenant = datetime.now(timezone.utc)
    lignes, motifs = [], {}
    lues = 0
    for brut in lecteur:
        lues += 1
        if lues > lignes_max:
            raise ErreurImport(f"Trop de lignes (maximum {lignes_max}).")
        if not any(c.strip() for c in brut):
            lues -= 1
            continue
        try:
            if len(brut) != len(entete):
                raise ValueError("nombre de colonnes incorrect")
            if any(len(c) > champ_max for c in brut):
                raise ValueError("champ trop long")
            d = dict(zip(entete, brut))
            lignes.append(_valider_ligne(d, maintenant))
        except ValueError as exc:
            motif = str(exc).split(":")[0][:60]
            motifs[motif] = motifs.get(motif, 0) + 1

    if lues == 0:
        raise ErreurImport("Aucune ligne de données.")
    rejetees = lues - len(lignes)
    if rejetees / lues > TAUX_REJET_MAX:
        raise ErreurImport(
            f"{rejetees} ligne(s) sur {lues} invalides (> {int(TAUX_REJET_MAX * 100)} %) : "
            "fichier refusé plutôt qu'analysé partiellement."
        )
    if rejetees:
        avertissements.append(f"{rejetees} ligne(s) invalide(s) écartée(s) de l'analyse.")

    df = pd.DataFrame(lignes, columns=COLONNES)
    df = df.sort_values(["boat_id", "trip_id", "timestamp"], kind="stable").reset_index(drop=True)
    emp = empreinte_octets(octets)
    return ResultatImport(
        donnees=df,
        empreinte=emp,
        nom_interne=f"import_{emp[:16]}.csv",
        nom_fourni_affichable=nom_affichable(nom_fourni),
        lignes_lues=lues,
        lignes_rejetees=rejetees,
        motifs_rejet=motifs,
        avertissements=avertissements,
    )


def conserver_original(octets: bytes, resultat: ResultatImport, dossier: Path) -> Path:
    """Copie de l'original, sous un nom interne, sans jamais l'écraser."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = (dossier / resultat.nom_interne).resolve()
    if dossier.resolve() not in chemin.parents:
        raise ErreurImport("Chemin interne invalide.")
    if not chemin.exists():
        chemin.write_bytes(octets)
    return chemin


def neutraliser_tableur(valeur):
    """Empêche l'interprétation d'une cellule comme formule dans un tableur."""
    if isinstance(valeur, str) and valeur.startswith(CARACTERES_FORMULE):
        return "'" + valeur
    return valeur


def exporter_csv(df: pd.DataFrame) -> bytes:
    sortie = df.copy()
    for col in sortie.columns:
        if sortie[col].dtype == object:
            sortie[col] = sortie[col].map(neutraliser_tableur)
    if "timestamp" in sortie.columns:
        sortie["timestamp"] = pd.to_datetime(sortie["timestamp"], utc=True).dt.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return sortie.to_csv(index=False).encode("utf-8")
